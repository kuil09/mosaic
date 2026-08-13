from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from mosaic_harness.amendment import AmendmentError, propose_amendment, ratify_amendment, rollback_amendment
from mosaic_harness.anchor import compare_head, export_head
from mosaic_harness.cli import main
from mosaic_harness.historian import EventLedger
from mosaic_harness.memory import MemoryError, invalidate_claim
from mosaic_harness.workflow import add_evidence, initialize_workspace, investigate, verify_workspace


class MosaicPhaseFTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.issue = self.root / "issue.md"
        self.issue.write_text("# memory\n\nClaim.\n", encoding="utf-8")
        initialize_workspace(self.root)
        investigate(self.root, "ISSUE-F", self.issue, self.repository)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _cli(self, argv: list[str]) -> tuple[int, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(["--root", str(self.root), *argv])
        return status, stderr.getvalue()

    def test_invalidated_claim_does_not_stay_supported(self) -> None:
        add_evidence(
            self.root,
            "ISSUE-F",
            "H-CODE-DEFECT",
            direction="supporting",
            strength="strong",
            summary="old support",
            source_type="historical-run",
            source_ref="old",
        )
        pack = invalidate_claim(
            self.root,
            "ISSUE-F",
            "H-CODE-DEFECT",
            reason="revision moved",
            actor="historian",
        )
        claim = next(item for item in pack["claims"] if item["id"] == "H-CODE-DEFECT")
        self.assertEqual(claim["status"], "unresolved")
        add_evidence(
            self.root,
            "ISSUE-F",
            "H-CODE-DEFECT",
            direction="supporting",
            strength="strong",
            summary="still old revision evidence",
            source_type="historical-run",
            source_ref="old-2",
            valid_for_revision="old-revision",
        )
        from mosaic_harness.storage import CaseStore

        pack = CaseStore(self.root / ".harness").load("ISSUE-F")
        claim = next(item for item in pack["claims"] if item["id"] == "H-CODE-DEFECT")
        self.assertNotEqual(claim["status"], "supported")

    def test_conflicting_invalidations_fail_verify_until_acknowledged(self) -> None:
        invalidate_claim(self.root, "ISSUE-F", "H-CODE-DEFECT", reason="first", actor="a")
        invalidate_claim(self.root, "ISSUE-F", "H-CODE-DEFECT", reason="second", actor="a")
        with self.assertRaises(MemoryError):
            verify_workspace(self.root, "ISSUE-F")
        status, stderr = self._cli(["memory", "acknowledge", "ISSUE-F"])
        self.assertEqual(status, 0, stderr)
        self.assertTrue(verify_workspace(self.root, "ISSUE-F")["ledger"]["valid"])

    def test_amend_refuses_normal_mode_and_failed_holdout(self) -> None:
        constitution = self.root / ".harness" / "constitution" / "normal.md"
        before = constitution.read_text(encoding="utf-8")
        status, stderr = self._cli(
            ["amend", "propose", "--id", "AMD-1", "--rationale", "x", "--falsifier", "y"]
        )
        self.assertEqual(status, 2)
        self.assertIn("Maintenance Mode", stderr)
        self.assertEqual(constitution.read_text(encoding="utf-8"), before)
        other = self.root / "amend-tree"
        other.mkdir()
        propose_amendment(
            self.root,
            mode="maintenance",
            worktree=other,
            amendment_id="AMD-1",
            rationale="tighten isolation wording",
            falsifier="holdout must still pass",
        )
        self.assertEqual(constitution.read_text(encoding="utf-8"), before)
        with self.assertRaisesRegex(AmendmentError, "holdout"):
            ratify_amendment(
                self.root,
                mode="maintenance",
                worktree=other,
                amendment_id="AMD-1",
            )
        self.assertEqual(constitution.read_text(encoding="utf-8"), before)

    def test_ratify_then_rollback_restores_constitution_without_rewriting_events(self) -> None:
        other = self.root / "amend-tree"
        (other / ".harness" / "constitution" / "amendments").mkdir(parents=True)
        (other / ".harness" / "constitution" / "normal.md").write_text(
            "# amended constitution\n", encoding="utf-8"
        )
        (other / ".harness" / "constitution" / "amendments" / "AMD-2.holdout.json").write_text(
            json.dumps({"passed": True}), encoding="utf-8"
        )
        propose_amendment(
            self.root,
            mode="maintenance",
            worktree=other,
            amendment_id="AMD-2",
            rationale="amend",
            falsifier="holdout",
        )
        events_before = EventLedger(self.root / ".harness").read()
        ratify_amendment(self.root, mode="maintenance", worktree=other, amendment_id="AMD-2")
        self.assertEqual(
            (self.root / ".harness" / "constitution" / "normal.md").read_text(encoding="utf-8"),
            "# amended constitution\n",
        )
        rollback_amendment(self.root, mode="maintenance", amendment_id="AMD-2")
        self.assertIn("# Normal Mode Constitution", (self.root / ".harness" / "constitution" / "normal.md").read_text(encoding="utf-8"))
        events_after = EventLedger(self.root / ".harness").read()
        self.assertEqual(events_before, events_after[: len(events_before)])
        types = [event["type"] for event in events_after]
        self.assertIn("amendment_proposed", types)
        self.assertIn("amendment_ratified", types)
        self.assertIn("amendment_rolled_back", types)

    def test_ledger_export_detects_replacement(self) -> None:
        export = self.root / "head.json"
        payload = export_head(self.root, export)
        self.assertEqual(payload["event_count"], compare_head(self.root, export) and EventLedger(self.root / ".harness").verify()["event_count"])
        self.assertEqual(compare_head(self.root, export)["relation"], "equal")
        add_evidence(
            self.root,
            "ISSUE-F",
            "H-OBSERVABILITY-GAP",
            direction="supporting",
            strength="weak",
            summary="later event",
            source_type="note",
            source_ref="n1",
        )
        self.assertEqual(compare_head(self.root, export)["relation"], "descendant")
        tampered = json.loads(export.read_text(encoding="utf-8"))
        tampered["head_hash"] = "0" * 64
        export.write_text(json.dumps(tampered), encoding="utf-8")
        from mosaic_harness.anchor import AnchorError

        with self.assertRaises(AnchorError):
            compare_head(self.root, export)


if __name__ == "__main__":
    unittest.main()
