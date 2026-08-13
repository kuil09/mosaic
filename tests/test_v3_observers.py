from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from mosaic_harness.admission import AdmissionError
from mosaic_harness.cli import main
from mosaic_harness.domain import Outcome
from mosaic_harness.observers import observe
from mosaic_harness.storage import CaseStore
from mosaic_harness.workflow import (
    decide,
    initialize_workspace,
    investigate,
    propose,
    verify_workspace,
)


class MosaicPhaseETest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.issue = self.root / "issue.md"
        self.issue.write_text("# observe\n\nClaim.\n", encoding="utf-8")
        initialize_workspace(self.root)
        investigate(self.root, "ISSUE-E", self.issue, self.repository)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_observation_plan_command_anchors_projection(self) -> None:
        stdout = __import__("io").StringIO()
        stderr = __import__("io").StringIO()
        from contextlib import redirect_stderr, redirect_stdout

        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(
                [
                    "--root",
                    str(self.root),
                    "observation-plan",
                    "ISSUE-E",
                    "--signal",
                    "restore test passes",
                    "--rollback-trigger",
                    "dispose candidate",
                ]
            )
        self.assertEqual(status, 0, stderr.getvalue())
        pack = CaseStore(self.root / ".harness").load("ISSUE-E")
        self.assertEqual(pack["observation_plan"][0]["signal"], "restore test passes")
        self.assertEqual(pack["observation_plan"][0]["rollback_trigger"], "dispose candidate")
        self.assertTrue(verify_workspace(self.root, "ISSUE-E")["projection_integrity"]["valid"])

    def test_code_change_requires_observation_signal(self) -> None:
        with self.assertRaisesRegex(AdmissionError, "observation plan"):
            decide(
                self.root,
                "ISSUE-E",
                Outcome.CODE_CHANGE,
                "ship",
                "eng@example.com",
            )

    def test_override_with_signal_then_incident_supersedes(self) -> None:
        store = CaseStore(self.root / ".harness")
        pack = store.load("ISSUE-E")
        pack["observation_plan"] = [
            {
                "id": "OP-1",
                "observation": "coupon restore latency",
                "signal": "p95 restore > 2s",
                "rollback_trigger": "revert candidate branch",
                "status": "required",
            }
        ]
        store.save(pack)
        decided = decide(
            self.root,
            "ISSUE-E",
            Outcome.CODE_CHANGE,
            "accept despite missing candidate",
            "eng@example.com",
            override=True,
        )
        self.assertEqual(decided["outcome"]["status"], "accepted")
        observe(
            self.root,
            "ISSUE-E",
            kind="incident",
            summary="Restore latency regressed after deploy.",
            source_ref="pager-1",
            direction="opposing",
        )
        after = store.load("ISSUE-E")
        self.assertEqual(after["outcome"]["status"], "superseded")
        self.assertEqual(after["outcome"]["type"], Outcome.CODE_CHANGE.value)
        self.assertTrue(all(item["freshness"] == "stale" for item in after["evidence"]))
        self.assertTrue(
            any("Post-change observation" in item for item in after["unresolved_questions"])
        )
        proposed = propose(self.root, "ISSUE-E")
        self.assertEqual(proposed["outcome"]["type"], Outcome.INSUFFICIENT_EVIDENCE.value)


if __name__ == "__main__":
    unittest.main()
