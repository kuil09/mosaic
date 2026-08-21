from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from mosaic_harness.admission import AdmissionError
from mosaic_harness.builder import BuilderPathError
from mosaic_harness.candidate import build_candidate, create_candidate, verify_candidate
from mosaic_harness.cli import build_parser, main
from mosaic_harness.domain import Outcome
from mosaic_harness.isolation import isolation_available
from mosaic_harness.schema import SchemaDriftError
from mosaic_harness.workflow import (
    add_evidence,
    decide,
    initialize_workspace,
    investigate,
    propose,
    rebuild_case,
    verify_workspace,
)


def isolation_supported() -> bool:
    return isolation_available()


def git_available() -> bool:
    return shutil.which("git") is not None


class MosaicPhaseATest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "tests").mkdir()
        (self.repository / ".harness" / "constitution").mkdir(parents=True)
        (self.repository / ".harness" / "evaluators" / "hidden").mkdir(parents=True)
        (self.repository / ".harness" / "historian" / "events").mkdir(parents=True)
        (self.repository / "src" / "coupon.py").write_text(
            "def restore_coupon():\n    return True\n",
            encoding="utf-8",
        )
        (self.repository / "tests" / "test_coupon.py").write_text(
            "import unittest\n\n"
            "class CouponTest(unittest.TestCase):\n"
            "    def test_restore_coupon(self) -> None:\n"
            "        from coupon import restore_coupon\n"
            "        self.assertTrue(restore_coupon())\n",
            encoding="utf-8",
        )
        (self.repository / ".harness" / "constitution" / "normal.md").write_text(
            "# constitution\n",
            encoding="utf-8",
        )
        (self.repository / ".harness" / "evaluators" / "hidden" / "holdout.eval").write_text(
            "HIDDEN\n",
            encoding="utf-8",
        )
        (self.repository / ".harness" / "historian" / "events" / "events.jsonl").write_text(
            "{}\n",
            encoding="utf-8",
        )
        self.issue = self.root / "issue.md"
        self.issue.write_text("# Coupon restoration\n\nUnverified delay.\n", encoding="utf-8")
        initialize_workspace(self.root)
        investigate(self.root, "ISSUE-123", self.issue, self.repository)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _cli(self, argv: list[str]) -> tuple[int, dict | None, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(["--root", str(self.root), *argv])
        raw = stdout.getvalue()
        return status, json.loads(raw) if raw.strip() else None, stderr.getvalue()

    def test_schema_drift_is_detected(self) -> None:
        schema = self.root / ".harness" / "claims" / "schemas" / "decision-pack.schema.json"
        schema.write_text(schema.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(SchemaDriftError):
            verify_workspace(self.root, "ISSUE-123")

    def test_bounded_copy_when_repository_has_no_commit(self) -> None:
        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="code")
        self.assertEqual(manifest["materialization"], "bounded-copy")
        self.assertFalse((Path(manifest["workspace_root"]) / ".harness" / "constitution").exists())

    @unittest.skipUnless(git_available(), "git is required for worktree materialization")
    def test_git_worktree_materialization_leaves_source_clean(self) -> None:
        repo = self.repository
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "mosaic@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Mosaic"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        before = (repo / "src" / "coupon.py").read_text(encoding="utf-8")
        manifest = create_candidate(self.root, "ISSUE-123", repo, kind="code")
        self.assertEqual(manifest["materialization"], "git-worktree")
        workspace = Path(manifest["workspace_root"])
        self.assertTrue((workspace / "src" / "coupon.py").is_file())
        self.assertFalse((workspace / ".harness" / "constitution").exists())
        self.assertFalse((workspace / ".harness" / "evaluators" / "hidden").exists())
        self.assertEqual((repo / "src" / "coupon.py").read_text(encoding="utf-8"), before)
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertNotIn("src/coupon.py", status.stdout)

    def test_scripted_builder_rejects_escape_paths(self) -> None:
        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="code")
        script = self.root / "escape.json"
        script.write_text(
            json.dumps(
                [
                    {
                        "op": "write",
                        "path": "../.harness/constitution/normal.md",
                        "contents": "pwned",
                    }
                ]
            ),
            encoding="utf-8",
        )
        with self.assertRaises(BuilderPathError):
            build_candidate(self.root, "ISSUE-123", manifest["run_id"], script)

    def test_zero_change_refuses_builder_script(self) -> None:
        from mosaic_harness.candidate import RunStateError

        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="zero-change")
        script = self.root / "ok.json"
        script.write_text(
            json.dumps([{"op": "write", "path": "src/coupon.py", "contents": "x = 1\n"}]),
            encoding="utf-8",
        )
        with self.assertRaises(RunStateError):
            build_candidate(self.root, "ISSUE-123", manifest["run_id"], script)

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_scripted_builder_writes_inside_candidate_only(self) -> None:
        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="code")
        script = self.root / "edit.json"
        script.write_text(
            json.dumps(
                [
                    {
                        "op": "write",
                        "path": "src/coupon.py",
                        "contents": "def restore_coupon():\n    return True\n",
                    }
                ]
            ),
            encoding="utf-8",
        )
        result = build_candidate(self.root, "ISSUE-123", manifest["run_id"], script)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("builder-script-ok", result["stdout"])
        written = Path(manifest["workspace_root"]) / "src" / "coupon.py"
        self.assertTrue(written.is_file())
        self.assertNotIn(
            "pwned",
            (self.repository / ".harness" / "constitution" / "normal.md").read_text(encoding="utf-8"),
        )

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_verdict_is_bound_into_decision_pack_and_rebuilds(self) -> None:
        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="zero-change")
        verdict = verify_candidate(self.root, "ISSUE-123", manifest["run_id"])
        self.assertTrue(verdict["floor_passed"])
        verification = verify_workspace(self.root, "ISSUE-123")
        self.assertTrue(verification["projection_integrity"]["valid"])
        rebuilt, _ = rebuild_case(self.root, "ISSUE-123")
        run_ids = [item["run_id"] for item in rebuilt.get("experiments", [])]
        self.assertIn(manifest["run_id"], run_ids)
        self.assertTrue(rebuilt["experiments"][0]["floor_passed"])

    def test_decide_code_change_without_surviving_candidate_is_refused(self) -> None:
        proposal = propose(self.root, "ISSUE-123")
        with self.assertRaises(AdmissionError):
            decide(
                self.root,
                "ISSUE-123",
                Outcome.CODE_CHANGE,
                "ship it",
                "engineer@example.com",
                proposal_id=proposal["latest_proposal"]["proposal_id"],
            )
        pack = verify_workspace(self.root, "ISSUE-123")
        self.assertEqual(pack["decision_pack"]["outcome"], "INSUFFICIENT_EVIDENCE")

    def test_decide_no_change_remains_valid_without_candidate(self) -> None:
        for claim_id, direction in (
            ("H-EXPECTED-BEHAVIOR", "supporting"),
            ("H-CODE-DEFECT", "opposing"),
            ("H-POLICY-CONFLICT", "opposing"),
        ):
            add_evidence(
                self.root,
                "ISSUE-123",
                claim_id,
                direction=direction,
                strength="strong",
                summary=f"Evidence for {claim_id}.",
                source_type="test",
                source_ref=claim_id,
            )
        proposal = propose(self.root, "ISSUE-123")
        pack = decide(
            self.root,
            "ISSUE-123",
            Outcome.NO_CHANGE,
            "Current behavior matches policy.",
            "engineer@example.com",
            proposal_id=proposal["latest_proposal"]["proposal_id"],
        )
        self.assertEqual(pack["outcome"]["type"], Outcome.NO_CHANGE.value)

    def test_cli_has_no_admission_override(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "decide",
                    "ISSUE-123",
                    "CODE_CHANGE",
                    "--proposal",
                    "PR-test",
                    "--override",
                    "--actor",
                    "engineer@example.com",
                    "--rationale",
                    "attempt to bypass the gate",
                ]
            )


if __name__ == "__main__":
    unittest.main()
