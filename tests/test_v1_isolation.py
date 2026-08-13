from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from mosaic_harness.cli import main
from mosaic_harness.historian import EventLedger
from mosaic_harness.workflow import initialize_workspace, investigate, rebuild_case, verify_workspace


HIDDEN_TOKEN = "MOSAIC-HIDDEN-EVALUATOR-TOKEN-7f3c2a91"


def isolation_supported() -> bool:
    return sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file()


class MosaicV1IsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "tests").mkdir()
        (self.repository / "docs").mkdir()
        (self.repository / ".harness" / "constitution").mkdir(parents=True)
        (self.repository / ".harness" / "evaluators" / "hidden").mkdir(parents=True)
        (self.repository / ".harness" / "evaluators" / "public").mkdir(parents=True)
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
        (self.repository / "docs" / "policy.md").write_text("# Coupon policy\n", encoding="utf-8")
        (self.repository / ".harness" / "constitution" / "normal.md").write_text(
            "# secret constitution\n",
            encoding="utf-8",
        )
        (self.repository / ".harness" / "evaluators" / "hidden" / "holdout.eval").write_text(
            HIDDEN_TOKEN + "\n",
            encoding="utf-8",
        )
        (self.repository / ".harness" / "historian" / "events" / "events.jsonl").write_text(
            '{"secret": true}\n',
            encoding="utf-8",
        )
        (self.repository / ".harness" / "evaluators" / "public" / "floor.py").write_text(
            "print('public-floor-ok')\n",
            encoding="utf-8",
        )
        self.issue = self.root / "issue.md"
        self.issue.write_text(
            "# Coupon restoration\n\nCoupon restoration appears delayed after cancellation.\n",
            encoding="utf-8",
        )
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
        parsed = json.loads(raw) if raw.strip() else None
        return status, parsed, stderr.getvalue()

    def _source_fingerprint(self) -> dict[str, str]:
        tracked = {
            "src/coupon.py": (self.repository / "src" / "coupon.py").read_text(encoding="utf-8"),
            "tests/test_coupon.py": (self.repository / "tests" / "test_coupon.py").read_text(
                encoding="utf-8"
            ),
            "constitution": (
                self.repository / ".harness" / "constitution" / "normal.md"
            ).read_text(encoding="utf-8"),
            "historian": (
                self.repository / ".harness" / "historian" / "events" / "events.jsonl"
            ).read_text(encoding="utf-8"),
            "hidden": (
                self.repository / ".harness" / "evaluators" / "hidden" / "holdout.eval"
            ).read_text(encoding="utf-8"),
        }
        return tracked

    def test_candidate_workspace_excludes_protected_paths(self) -> None:
        from mosaic_harness.candidate import create_candidate

        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="code")
        workspace = Path(manifest["workspace_root"])
        self.assertTrue(workspace.is_dir())
        self.assertTrue((workspace / "src" / "coupon.py").is_file())
        self.assertTrue((workspace / "tests" / "test_coupon.py").is_file())
        self.assertTrue((workspace / ".mosaic" / "decision-pack.json").is_file())
        self.assertFalse((workspace / ".harness" / "constitution").exists())
        self.assertFalse((workspace / ".harness" / "evaluators" / "hidden").exists())
        self.assertFalse((workspace / ".harness" / "historian").exists())
        self.assertNotIn(HIDDEN_TOKEN, workspace.joinpath("src/coupon.py").read_text(encoding="utf-8"))
        leaked = any(
            HIDDEN_TOKEN in path.read_text(encoding="utf-8", errors="ignore")
            for path in workspace.rglob("*")
            if path.is_file()
        )
        self.assertFalse(leaked)

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_builder_read_of_hidden_evaluator_is_denied(self) -> None:
        from mosaic_harness.candidate import create_candidate, run_role

        hidden = self.repository / ".harness" / "evaluators" / "hidden" / "holdout.eval"
        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="code")
        result = run_role(
            self.root,
            "ISSUE-123",
            manifest["run_id"],
            role="builder",
            command=[sys.executable, "-c", f"print(open({str(hidden)!r}).read())"],
        )
        self.assertNotEqual(result["exit_code"], 0)
        self.assertNotIn(HIDDEN_TOKEN, result.get("stdout", ""))
        self.assertNotIn(HIDDEN_TOKEN, result.get("stderr", ""))
        self.assertIn("denied", result["denial"].lower())

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_builder_write_to_constitution_and_historian_is_denied(self) -> None:
        from mosaic_harness.candidate import create_candidate, run_role

        constitution = self.repository / ".harness" / "constitution" / "normal.md"
        historian = self.repository / ".harness" / "historian" / "events" / "events.jsonl"
        before = self._source_fingerprint()
        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="code")
        const_result = run_role(
            self.root,
            "ISSUE-123",
            manifest["run_id"],
            role="builder",
            command=[sys.executable, "-c", f"open({str(constitution)!r}, 'a').write('pwned')"],
        )
        hist_result = run_role(
            self.root,
            "ISSUE-123",
            manifest["run_id"],
            role="builder",
            command=[sys.executable, "-c", f"open({str(historian)!r}, 'a').write('pwned')"],
        )
        self.assertNotEqual(const_result["exit_code"], 0)
        self.assertNotEqual(hist_result["exit_code"], 0)
        self.assertEqual(self._source_fingerprint(), before)
        self.assertIn("denied", const_result["denial"].lower())
        self.assertIn("denied", hist_result["denial"].lower())

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_verifier_cannot_mutate_candidate_during_evaluation(self) -> None:
        from mosaic_harness.candidate import create_candidate, verify_candidate

        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="zero-change")
        marker = Path(manifest["workspace_root"]) / "src" / "coupon.py"
        original = marker.read_text(encoding="utf-8")
        verdict = verify_candidate(
            self.root,
            "ISSUE-123",
            manifest["run_id"],
            mutation_probe=[
                sys.executable,
                "-c",
                f"open({str(marker)!r}, 'a').write('mutated-by-verifier')",
            ],
        )
        self.assertEqual(marker.read_text(encoding="utf-8"), original)
        self.assertTrue(verdict["candidate_unmodified"])
        self.assertTrue(verdict["mutation_denied"])
        self.assertEqual(verdict["snapshot_before"], verdict["snapshot_after"])

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_interrupt_leaves_source_repository_unchanged(self) -> None:
        from mosaic_harness.candidate import create_candidate, run_role

        before = self._source_fingerprint()
        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="code")
        result = run_role(
            self.root,
            "ISSUE-123",
            manifest["run_id"],
            role="builder",
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            budget={"max_seconds": 1, "max_output_bytes": 65536},
        )
        self.assertTrue(result["interrupted"])
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(self._source_fingerprint(), before)
        self.assertTrue(Path(manifest["workspace_root"]).is_dir())
        types = [event["type"] for event in EventLedger(self.root / ".harness").read()]
        self.assertIn("run_interrupted", types)

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_zero_change_and_code_candidates_share_the_same_floor(self) -> None:
        from mosaic_harness.candidate import compare_candidates, create_candidate, run_role, verify_candidate

        zero = create_candidate(self.root, "ISSUE-123", self.repository, kind="zero-change")
        code = create_candidate(self.root, "ISSUE-123", self.repository, kind="code")
        run_role(
            self.root,
            "ISSUE-123",
            code["run_id"],
            role="builder",
            command=[
                sys.executable,
                "-c",
                "from pathlib import Path; "
                "Path('src/coupon.py').write_text('def restore_coupon():\\n    return True\\n')",
            ],
        )
        zero_verdict = verify_candidate(self.root, "ISSUE-123", zero["run_id"])
        code_verdict = verify_candidate(self.root, "ISSUE-123", code["run_id"])
        comparison = compare_candidates(self.root, "ISSUE-123", [zero["run_id"], code["run_id"]])
        self.assertTrue(zero_verdict["floor_passed"])
        self.assertTrue(code_verdict["floor_passed"])
        self.assertEqual(comparison["floor_definition"], zero_verdict["floor_definition"])
        self.assertEqual(comparison["floor_definition"], code_verdict["floor_definition"])
        self.assertEqual(comparison["left"]["floor_passed"], comparison["right"]["floor_passed"])

    def test_candidate_and_verdict_events_rebuild_decision_pack(self) -> None:
        from mosaic_harness.candidate import create_candidate

        before = verify_workspace(self.root, "ISSUE-123")
        manifest = create_candidate(self.root, "ISSUE-123", self.repository, kind="zero-change")
        ledger = EventLedger(self.root / ".harness")
        types = [event["type"] for event in ledger.read() if event["case_id"] == "ISSUE-123"]
        self.assertIn("candidate_created", types)
        self.assertIn("builder_finished", types)
        rebuilt, _ = rebuild_case(self.root, "ISSUE-123")
        self.assertEqual(rebuilt["case_id"], "ISSUE-123")
        after = verify_workspace(self.root, "ISSUE-123")
        self.assertTrue(after["ledger"]["valid"])
        self.assertGreater(after["ledger"]["event_count"], before["ledger"]["event_count"])
        self.assertEqual(manifest["kind"], "zero-change")

    def test_cli_prepare_inspects_filesystem_and_environment(self) -> None:
        status, payload, stderr = self._cli(
            [
                "candidate",
                "prepare",
                "ISSUE-123",
                "--repo",
                str(self.repository),
                "--kind",
                "zero-change",
            ]
        )
        self.assertEqual(status, 0, stderr)
        assert payload is not None
        workspace = Path(payload["workspace_root"])
        self.assertTrue((workspace / "src" / "coupon.py").is_file())
        self.assertFalse((workspace / ".harness" / "evaluators" / "hidden").exists())
        self.assertEqual(payload["isolation"]["mechanism"], "sandbox-exec")
        self.assertEqual(payload["isolation"]["tested_platform"], "darwin")
        self.assertIn("MOSAIC_ROLE", payload["environment"])
        self.assertEqual(payload["environment"]["MOSAIC_ROLE"], "builder")

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_cli_exec_denies_hidden_evaluator_read(self) -> None:
        status, prepared, stderr = self._cli(
            [
                "candidate",
                "prepare",
                "ISSUE-123",
                "--repo",
                str(self.repository),
                "--kind",
                "code",
            ]
        )
        self.assertEqual(status, 0, stderr)
        assert prepared is not None
        hidden = self.repository / ".harness" / "evaluators" / "hidden" / "holdout.eval"
        status, payload, stderr = self._cli(
            [
                "candidate",
                "exec",
                "ISSUE-123",
                prepared["run_id"],
                "--role",
                "builder",
                "--",
                sys.executable,
                "-c",
                f"print(open({str(hidden)!r}).read())",
            ]
        )
        self.assertEqual(status, 0, stderr)
        assert payload is not None
        self.assertNotEqual(payload["exit_code"], 0)
        self.assertNotIn(HIDDEN_TOKEN, payload.get("stdout", ""))
        self.assertIn("denied", (payload.get("denial") or "").lower())

    def test_v0_dispositions_and_verify_remain_valid(self) -> None:
        from mosaic_harness.candidate import create_candidate

        create_candidate(self.root, "ISSUE-123", self.repository, kind="zero-change")
        verification = verify_workspace(self.root, "ISSUE-123")
        self.assertTrue(verification["decision_pack"]["valid"])
        self.assertEqual(verification["decision_pack"]["outcome"], "INSUFFICIENT_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
