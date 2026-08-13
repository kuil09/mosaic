from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from mosaic_harness.cli import main
from mosaic_harness.domain import Outcome
from mosaic_harness.historian import EventLedger, LedgerIntegrityError
from mosaic_harness.scanner import scan_repository
from mosaic_harness.storage import CaseStore
from mosaic_harness.validation import DecisionPackValidationError, validate_decision_pack
from mosaic_harness.workflow import (
    add_evidence,
    challenge,
    initialize_workspace,
    investigate,
    propose,
    rebuild_case,
    verify_workspace,
)


class MosaicWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        (self.repository / "src").mkdir()
        (self.repository / "tests").mkdir()
        (self.repository / "docs").mkdir()
        (self.repository / ".git").mkdir()
        (self.repository / ".harness").mkdir()
        (self.repository / "src" / "coupon.py").write_text(
            "def restore_coupon():\n    return True\n", encoding="utf-8"
        )
        (self.repository / "tests" / "test_coupon.py").write_text(
            "def test_restore_coupon():\n    assert True\n", encoding="utf-8"
        )
        (self.repository / "docs" / "policy.md").write_text(
            "# Coupon policy\n", encoding="utf-8"
        )
        (self.repository / ".git" / "secret").write_text("ignored", encoding="utf-8")
        (self.repository / ".harness" / "runtime").write_text("ignored", encoding="utf-8")
        self.issue = self.root / "issue.md"
        self.issue.write_text(
            "# Coupon restoration\n\nCoupon restoration appears delayed after cancellation.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_case(self, case_id: str = "ISSUE-123") -> dict:
        pack, _ = investigate(self.root, case_id, self.issue, self.repository)
        return pack

    def test_init_scaffolds_contracts_without_overwriting_existing_agent_kernel(self) -> None:
        workspace = self.root / "new-workspace"
        workspace.mkdir()
        (workspace / "AGENTS.md").write_text("# Existing rules\n", encoding="utf-8")
        result = initialize_workspace(workspace)
        self.assertEqual(
            (workspace / "AGENTS.md").read_text(encoding="utf-8"),
            "# Existing rules\n",
        )
        self.assertTrue((workspace / "CLAUDE.md").is_file())
        self.assertTrue((workspace / ".harness" / "constitution" / "normal.md").is_file())
        schema_path = workspace / ".harness" / "claims" / "schemas" / "decision-pack.schema.json"
        self.assertEqual(json.loads(schema_path.read_text(encoding="utf-8"))["title"], "Mosaic Decision Pack")
        self.assertNotIn("AGENTS.md", result["created_templates"])

    def test_investigation_creates_conservative_pack_and_ledger(self) -> None:
        pack = self.create_case()
        self.assertEqual(pack["outcome"]["type"], Outcome.INSUFFICIENT_EVIDENCE.value)
        self.assertEqual(len(pack["claims"]), 8)
        self.assertEqual(
            {constraint["role"] for constraint in pack["constraints"]},
            {"target", "preservation", "boundary", "resource"},
        )
        inventory_paths = {
            artifact["path"] for artifact in pack["scope"]["inventory"]["artifacts"]
        }
        self.assertIn("src/coupon.py", inventory_paths)
        self.assertNotIn(".git/secret", inventory_paths)
        self.assertNotIn(".harness/runtime", inventory_paths)
        verification = verify_workspace(self.root, "ISSUE-123")
        self.assertEqual(verification["ledger"]["event_count"], 11)
        self.assertTrue(verification["projection_integrity"]["valid"])

    def test_supported_observability_gap_proposes_instrument_first(self) -> None:
        self.create_case()
        pack, evidence = add_evidence(
            self.root,
            "ISSUE-123",
            "H-OBSERVABILITY-GAP",
            direction="supporting",
            strength="strong",
            summary="The order and coupon logs have no shared correlation key.",
            source_type="log-inspection",
            source_ref="incident-42",
        )
        claim = next(item for item in pack["claims"] if item["id"] == "H-OBSERVABILITY-GAP")
        self.assertEqual(claim["status"], "supported")
        self.assertEqual(evidence["freshness"], "current")
        proposed = propose(self.root, "ISSUE-123")
        self.assertEqual(proposed["outcome"]["type"], Outcome.INSTRUMENT_FIRST.value)

    def test_code_change_is_not_selected_while_material_rivals_remain(self) -> None:
        self.create_case()
        add_evidence(
            self.root,
            "ISSUE-123",
            "H-CODE-DEFECT",
            direction="supporting",
            strength="strong",
            summary="A focused reproduction reaches an incorrect branch.",
            source_type="reproduction",
            source_ref="run-1",
        )
        proposed = propose(self.root, "ISSUE-123")
        self.assertEqual(proposed["outcome"]["type"], Outcome.INSUFFICIENT_EVIDENCE.value)
        self.assertIn("does not yet exclude material rivals", proposed["outcome"]["rationale"])

    def test_configuration_outcome_requires_its_material_rivals_refuted(self) -> None:
        self.create_case()
        add_evidence(
            self.root,
            "ISSUE-123",
            "H-CONFIGURATION",
            direction="supporting",
            strength="strong",
            summary="The behavior follows the tenant flag while code stays constant.",
            source_type="differential-run",
            source_ref="run-config",
        )
        for claim_id, source_ref in (
            ("H-CODE-DEFECT", "run-code-control"),
            ("H-OPERATIONAL", "run-operational-control"),
        ):
            add_evidence(
                self.root,
                "ISSUE-123",
                claim_id,
                direction="opposing",
                strength="strong",
                summary="The controlled comparison falsifies this rival.",
                source_type="differential-run",
                source_ref=source_ref,
            )
        proposed = propose(self.root, "ISSUE-123")
        self.assertEqual(proposed["outcome"]["type"], Outcome.CONFIGURATION_CHANGE.value)

    def test_stale_evidence_is_recorded_but_does_not_update_claim(self) -> None:
        self.create_case()
        pack, evidence = add_evidence(
            self.root,
            "ISSUE-123",
            "H-CODE-DEFECT",
            direction="supporting",
            strength="strong",
            summary="An old run supported a code defect.",
            source_type="historical-run",
            source_ref="old-run",
            valid_for_revision="old-revision",
        )
        claim = next(item for item in pack["claims"] if item["id"] == "H-CODE-DEFECT")
        self.assertEqual(evidence["freshness"], "stale")
        self.assertEqual(claim["status"], "proposed")
        self.assertIn(evidence["id"], claim["evidence_refs"])

    def test_challenge_is_idempotent_for_existing_claims(self) -> None:
        self.create_case()
        first = challenge(self.root, "ISSUE-123")
        second = challenge(self.root, "ISSUE-123")
        self.assertEqual(len(first["counterexamples"]), 8)
        self.assertEqual(len(second["counterexamples"]), 8)
        self.assertEqual(len(second["verification_plan"]), 8)

    def test_ledger_detects_payload_tampering(self) -> None:
        self.create_case()
        ledger = EventLedger(self.root / ".harness")
        lines = ledger.path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["payload"]["title"] = "tampered"
        lines[0] = json.dumps(first, separators=(",", ":"))
        ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(LedgerIntegrityError):
            ledger.verify()

    def test_verification_detects_projection_tampering(self) -> None:
        self.create_case()
        store = CaseStore(self.root / ".harness")
        pack = store.load("ISSUE-123")
        pack["issue"]["title"] = "tampered projection"
        store.save(pack)
        with self.assertRaisesRegex(ValueError, "projection hash mismatch"):
            verify_workspace(self.root, "ISSUE-123")

    def test_rebuild_restores_tampered_projection_from_verified_ledger(self) -> None:
        original = self.create_case()
        store = CaseStore(self.root / ".harness")
        damaged = store.load("ISSUE-123")
        damaged["claims"][0]["statement"] = "tampered"
        store.save(damaged)
        rebuilt, path = rebuild_case(self.root, "ISSUE-123")
        self.assertEqual(rebuilt["claims"][0]["statement"], original["claims"][0]["statement"])
        self.assertEqual(path.resolve(), store.path_for("ISSUE-123").resolve())
        self.assertTrue(verify_workspace(self.root, "ISSUE-123")["projection_integrity"]["valid"])

    def test_invalid_case_id_cannot_escape_case_store(self) -> None:
        with self.assertRaises(ValueError):
            CaseStore(self.root / ".harness").path_for("../escape")

    def test_validation_rejects_duplicate_claim_ids(self) -> None:
        pack = self.create_case()
        pack["claims"].append(dict(pack["claims"][0]))
        with self.assertRaises(DecisionPackValidationError):
            validate_decision_pack(pack)

    def test_scanner_is_bounded(self) -> None:
        inventory = scan_repository(self.repository, max_files=1)
        self.assertEqual(inventory["scanned_file_count"], 1)
        self.assertTrue(inventory["truncated"])

    def test_cli_returns_clean_error_for_unknown_case(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(["--root", str(self.root), "show", "MISSING"])
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("case not found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
