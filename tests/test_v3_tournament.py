from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from mosaic_harness.candidate import create_candidate, run_role, verify_candidate
from mosaic_harness.historian import EventLedger
from mosaic_harness.isolation import isolation_available
from mosaic_harness.tournament import TournamentError, run_tournament
from mosaic_harness.workflow import initialize_workspace, investigate


def isolation_supported() -> bool:
    return isolation_available()


class MosaicPhaseDTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "tests").mkdir()
        (self.repository / "src" / "coupon.py").write_text(
            "def restore_coupon():\n    return True\n", encoding="utf-8"
        )
        (self.repository / "tests" / "test_coupon.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        from coupon import restore_coupon\n"
            "        self.assertTrue(restore_coupon())\n",
            encoding="utf-8",
        )
        self.issue = self.root / "issue.md"
        self.issue.write_text("# tournament\n\nClaim.\n", encoding="utf-8")
        initialize_workspace(self.root)
        investigate(self.root, "ISSUE-D", self.issue, self.repository)
        self.scenario_id = "S-opaque-1"
        self.scenario_file = self.root / ".harness" / "future" / "scenarios" / f"{self.scenario_id}.json"
        self.scenario_file.parent.mkdir(parents=True, exist_ok=True)
        self.scenario_file.write_text(
            json.dumps(
                {
                    "scenario_id": self.scenario_id,
                    "provenance_class": "synthetic-holdout",
                    "base_revision": "tree:test",
                    "constraints": {"target": "preserve coupon restore", "preservation": "no hidden leak"},
                    "budgets": {
                        "model": "none",
                        "harness": "mosaic",
                        "seconds": 30,
                        "tokens": 0,
                        "context": 0,
                        "verifier": 30,
                    },
                    "floors": ["public", "hidden", "mutation"],
                    "contamination": {"deny_scenario_to_builder": True},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scenario_with_task_text_is_rejected(self) -> None:
        self.scenario_file.write_text(
            json.dumps(
                {
                    "scenario_id": self.scenario_id,
                    "provenance_class": "x",
                    "base_revision": "tree:test",
                    "constraints": {},
                    "budgets": {"seconds": 30},
                    "floors": ["public"],
                    "contamination": {},
                    "statement": "secret future task",
                }
            ),
            encoding="utf-8",
        )
        zero = create_candidate(self.root, "ISSUE-D", self.repository, kind="zero-change")
        code = create_candidate(self.root, "ISSUE-D", self.repository, kind="code")
        with self.assertRaises(TournamentError):
            run_tournament(self.root, "ISSUE-D", self.scenario_id, [zero["run_id"], code["run_id"]])

    def test_mismatched_budgets_are_refused(self) -> None:
        zero = create_candidate(
            self.root, "ISSUE-D", self.repository, kind="zero-change", budget={"max_seconds": 30}
        )
        code = create_candidate(
            self.root, "ISSUE-D", self.repository, kind="code", budget={"max_seconds": 10}
        )
        with self.assertRaisesRegex(TournamentError, "budgets differ"):
            run_tournament(self.root, "ISSUE-D", self.scenario_id, [zero["run_id"], code["run_id"]])

    @unittest.skipUnless(isolation_supported(), "V1 process isolation is tested on macOS sandbox-exec")
    def test_builder_cannot_read_scenario_and_events_are_appended(self) -> None:
        zero = create_candidate(self.root, "ISSUE-D", self.repository, kind="zero-change")
        code = create_candidate(self.root, "ISSUE-D", self.repository, kind="code")
        denied = run_role(
            self.root,
            "ISSUE-D",
            code["run_id"],
            role="builder",
            command=[sys.executable, "-c", f"print(open({str(self.scenario_file)!r}).read())"],
        )
        self.assertNotEqual(denied["exit_code"], 0)
        self.assertIn("denied", (denied.get("denial") or "").lower())
        result = run_tournament(
            self.root, "ISSUE-D", self.scenario_id, [zero["run_id"], code["run_id"]]
        )
        self.assertEqual(result["scenario_id"], self.scenario_id)
        types = [event["type"] for event in EventLedger(self.root / ".harness").read()]
        self.assertIn("tournament_started", types)
        self.assertIn("scenario_evaluated", types)
        self.assertIn("tournament_completed", types)
        self.assertTrue(
            (self.root / ".harness" / "future" / "tournaments" / f"ISSUE-D-{self.scenario_id}.json").is_file()
        )


if __name__ == "__main__":
    unittest.main()
