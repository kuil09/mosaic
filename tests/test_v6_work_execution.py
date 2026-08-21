from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from mosaic_harness.admission import AdmissionError
from mosaic_harness.compat import LegacyReadOnlyError
from mosaic_harness.domain import Outcome
from mosaic_harness.executor import load_json, manifest_path
from mosaic_harness.historian import EventLedger
from mosaic_harness.pair import PairStateError, prepare_pair, retry_code
from mosaic_harness.pair_verifier import verify_pair
from mosaic_harness.storage import CaseStore
from mosaic_harness.workflow import (
    _anchor_projection,
    add_evidence,
    decide,
    initialize_workspace,
    investigate,
    propose,
    verify_workspace,
)
from mosaic_harness.work import (
    WorkContractError,
    WorkStateError,
    attach_pair,
    complete_task,
    create_work,
    finish_work,
    implementation_complete,
    next_work,
    rebuild_work_state,
    replan_work,
    resume_packet,
    start_task,
    status_work,
)
from tests.test_v5_admission_pairs import PortableCommandAdapter


class WorkExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        (self.repository / "src").mkdir(parents=True)
        (self.repository / "src" / "feature.py").write_text(
            "def is_ready():\n    return False\n\n"
            "def stable_value():\n    return 'stable'\n",
            encoding="utf-8",
        )
        self.issue = self.root / "issue.md"
        self.issue.write_text("# Durable feature work\n", encoding="utf-8")
        self.contract_root = self.root / "verification"
        self.contract_root.mkdir()
        (self.contract_root / "target.py").write_text(
            "from feature import is_ready\n"
            "raise SystemExit(0 if is_ready() else 1)\n",
            encoding="utf-8",
        )
        (self.contract_root / "preservation.py").write_text(
            "from feature import stable_value\n"
            "raise SystemExit(0 if stable_value() == 'stable' else 1)\n",
            encoding="utf-8",
        )
        self.verification_contract = self.contract_root / "contract.json"
        self.verification_contract.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "contract_id": "VC-WORK",
                    "checks": [
                        {
                            "id": "target-ready",
                            "role": "target",
                            "visibility": "public",
                            "argv": [
                                sys.executable,
                                "{frozen_public}/contract/target.py",
                            ],
                            "frozen_inputs": ["target.py"],
                        },
                        {
                            "id": "preserve-stable",
                            "role": "preservation",
                            "visibility": "public",
                            "argv": [
                                sys.executable,
                                "{frozen_public}/contract/preservation.py",
                            ],
                            "frozen_inputs": ["preservation.py"],
                        },
                    ],
                    "required_adapters": [],
                    "budgets": {"max_seconds": 5, "max_output_bytes": 65536},
                }
            ),
            encoding="utf-8",
        )
        self.work_contract = self.root / "work-contract.json"
        self._write_work_contract(self.work_contract)
        initialize_workspace(self.root)
        investigate(
            self.root,
            "CASE-WORK",
            self.issue,
            self.repository,
            signal="The frozen target fails after deployment.",
            rollback_trigger="The preservation check fails.",
        )
        self.adapter = PortableCommandAdapter()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _contract_value(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "contract_id": "WC-WORK",
            "case_id": "CASE-WORK",
            "objective": "Make the frozen readiness target pass without changing stable behavior.",
            "constraints": [
                {
                    "id": "C-TARGET",
                    "role": "target",
                    "force": "hard",
                    "statement": "The readiness target must pass.",
                    "evidence_condition": "target-ready changes from FAIL to PASS.",
                },
                {
                    "id": "C-PRESERVE",
                    "role": "preservation",
                    "force": "hard",
                    "statement": "Stable behavior must remain unchanged.",
                    "evidence_condition": "preserve-stable passes for both candidates.",
                },
                {
                    "id": "C-BOUNDARY",
                    "role": "boundary",
                    "force": "hard",
                    "statement": "Only CASE-WORK is in scope.",
                    "evidence_condition": "All artifacts reference CASE-WORK.",
                },
                {
                    "id": "C-RESOURCE",
                    "role": "resource",
                    "force": "soft",
                    "statement": "Use the contract execution budget.",
                    "evidence_condition": "Actual execution use is recorded.",
                },
            ],
            "non_goals": ["Parallel workers", "Agent-specific prompting"],
            "acceptance_criteria": [
                {
                    "id": "AC-READY",
                    "statement": "The readiness defect is fixed.",
                    "verification_check_ids": ["target-ready"],
                },
                {
                    "id": "AC-STABLE",
                    "statement": "Stable behavior is preserved.",
                    "verification_check_ids": ["preserve-stable"],
                },
            ],
            "tasks": [
                {
                    "id": "T-INVESTIGATE",
                    "kind": "investigate",
                    "description": "Establish the code cause.",
                    "depends_on": [],
                    "read_set": ["src/feature.py"],
                    "write_set": [],
                    "acceptance_criteria_ids": ["AC-READY"],
                },
                {
                    "id": "T-IMPLEMENT",
                    "kind": "implement",
                    "description": "Implement the readiness fix.",
                    "depends_on": ["T-INVESTIGATE"],
                    "read_set": ["src/feature.py"],
                    "write_set": ["src/feature.py"],
                    "acceptance_criteria_ids": ["AC-READY", "AC-STABLE"],
                },
            ],
            "completion_policy": {
                "success_outcomes": ["CODE_CHANGE", "NO_CHANGE"],
                "stop_outcomes": [
                    "INSTRUMENT_FIRST",
                    "POLICY_CONFLICT",
                    "INSUFFICIENT_EVIDENCE",
                    "ISSUE_REJECTED",
                ],
            },
        }

    def _write_work_contract(
        self,
        path: Path,
        *,
        value: dict[str, object] | None = None,
    ) -> None:
        path.write_text(json.dumps(value or self._contract_value()), encoding="utf-8")

    def _add_code_evidence(self) -> str:
        _, evidence = add_evidence(
            self.root,
            "CASE-WORK",
            "H-CODE-DEFECT",
            direction="supporting",
            strength="strong",
            summary="The frozen target isolates the code cause.",
            source_type="pair",
            source_ref="target-fail-pass",
        )
        for claim_id in (
            "H-EXPECTED-BEHAVIOR",
            "H-OBSERVABILITY-GAP",
            "H-CONFIGURATION",
            "H-DOCUMENTATION",
            "H-OPERATIONAL",
            "H-POLICY-CONFLICT",
            "H-INVALID-REPORT",
        ):
            add_evidence(
                self.root,
                "CASE-WORK",
                claim_id,
                direction="opposing",
                strength="strong",
                summary=f"Evidence refutes {claim_id} within the contract boundary.",
                source_type="pair",
                source_ref=f"refute-{claim_id}",
            )
        return evidence["id"]

    def _prepare_work_through_verification(self) -> tuple[dict[str, object], str]:
        create_work(
            self.root,
            "CASE-WORK",
            self.work_contract,
            "planner@example.com",
        )
        evidence_id = self._add_code_evidence()
        self.assertEqual(next_work(self.root, "CASE-WORK")["selected_task_id"], "T-INVESTIGATE")
        start_task(
            self.root,
            "CASE-WORK",
            "T-INVESTIGATE",
            "worker@example.com",
        )
        complete_task(
            self.root,
            "CASE-WORK",
            "T-INVESTIGATE",
            [evidence_id],
            "worker@example.com",
        )
        pair = prepare_pair(
            self.root,
            "CASE-WORK",
            self.repository,
            self.verification_contract,
        )
        attach_pair(
            self.root,
            "CASE-WORK",
            pair["pair_id"],
            pair["code_run_id"],
            "worker@example.com",
        )
        start_task(
            self.root,
            "CASE-WORK",
            "T-IMPLEMENT",
            "worker@example.com",
        )
        manifest = load_json(
            manifest_path(self.root / ".harness", pair["code_run_id"])
        )
        (Path(manifest["workspace_root"]) / "src" / "feature.py").write_text(
            "def is_ready():\n    return True\n\n"
            "def stable_value():\n    return 'stable'\n",
            encoding="utf-8",
        )
        complete_task(
            self.root,
            "CASE-WORK",
            "T-IMPLEMENT",
            [f"run:{pair['code_run_id']}"],
            "worker@example.com",
        )
        implemented = implementation_complete(
            self.root,
            "CASE-WORK",
            "worker@example.com",
        )
        self.assertEqual(implemented["state"], "IMPLEMENTED")
        verdict = verify_pair(
            self.root,
            "CASE-WORK",
            pair["pair_id"],
            pair["code_run_id"],
            adapter=self.adapter,
            require_real_isolation=False,
        )
        self.assertTrue(verdict["eligible"])
        return pair, evidence_id

    def test_end_to_end_state_machine_requires_human_decision(self) -> None:
        pair, _ = self._prepare_work_through_verification()
        verified = status_work(self.root, "CASE-WORK")
        self.assertEqual(verified["state"], "VERIFIED")
        proposed = propose(self.root, "CASE-WORK", actor="mosaic")
        self.assertEqual(proposed["outcome"]["type"], "CODE_CHANGE")
        proposal_id = proposed["latest_proposal"]["proposal_id"]
        admissible = status_work(self.root, "CASE-WORK")
        self.assertEqual(admissible["state"], "ADMISSIBLE")
        with self.assertRaises(WorkStateError):
            finish_work(self.root, "CASE-WORK", "worker@example.com")
        decide(
            self.root,
            "CASE-WORK",
            Outcome.CODE_CHANGE,
            "Accept the proposal-bound candidate.",
            "human@example.com",
            proposal_id=proposal_id,
        )
        manifest = load_json(
            manifest_path(self.root / ".harness", pair["code_run_id"])
        )
        candidate_file = Path(manifest["workspace_root"]) / "src" / "feature.py"
        verified_source = candidate_file.read_text(encoding="utf-8")
        candidate_file.write_text(verified_source + "# tampered\n", encoding="utf-8")
        with self.assertRaises(AdmissionError):
            finish_work(self.root, "CASE-WORK", "worker@example.com")
        candidate_file.write_text(verified_source, encoding="utf-8")
        finished = finish_work(self.root, "CASE-WORK", "worker@example.com")
        self.assertEqual(finished["state"], "DONE")
        self.assertEqual(finished["active_pair_id"], pair["pair_id"])

        packet = resume_packet(self.root, "CASE-WORK")
        rendered = json.dumps(packet)
        self.assertEqual(packet["work_state"], "DONE")
        self.assertEqual(
            packet["allowed_next_commands"],
            ["mosaic work status", "mosaic work resume"],
        )
        self.assertNotIn("reasoning", rendered.lower())
        self.assertNotIn("hidden", rendered.lower())
        self.assertEqual(
            (self.repository / "src" / "feature.py").read_text(encoding="utf-8"),
            "def is_ready():\n    return False\n\n"
            "def stable_value():\n    return 'stable'\n",
        )

    def test_replan_stales_verdict_and_requires_retry_attempt(self) -> None:
        pair, _ = self._prepare_work_through_verification()
        before = resume_packet(self.root, "CASE-WORK")
        replacement = self._contract_value()
        replacement["tasks"] = [
            *replacement["tasks"],
            {
                "id": "T-FOLLOWUP",
                "kind": "implement",
                "description": "Apply a follow-up implementation adjustment.",
                "depends_on": ["T-IMPLEMENT"],
                "read_set": ["src/feature.py"],
                "write_set": ["src/feature.py"],
                "acceptance_criteria_ids": ["AC-READY", "AC-STABLE"],
            },
        ]
        replacement_path = self.root / "work-contract-r2.json"
        self._write_work_contract(replacement_path, value=replacement)
        replanned = replan_work(
            self.root,
            "CASE-WORK",
            replacement_path,
            "A follow-up task is required.",
            "planner@example.com",
        )
        self.assertEqual(replanned["state"], "ACTIVE")
        self.assertIn(f"verdict:{pair['code_run_id']}", replanned["stale_artifacts"])
        self.assertEqual(replanned["selected_task_id"], "T-FOLLOWUP")
        with self.assertRaises(WorkStateError):
            start_task(
                self.root,
                "CASE-WORK",
                "T-FOLLOWUP",
                "worker@example.com",
            )

        retry = retry_code(
            self.root,
            "CASE-WORK",
            pair["pair_id"],
            pair["code_run_id"],
        )
        attach_pair(
            self.root,
            "CASE-WORK",
            pair["pair_id"],
            retry["run_id"],
            "worker@example.com",
        )
        running = start_task(
            self.root,
            "CASE-WORK",
            "T-FOLLOWUP",
            "worker@example.com",
        )
        self.assertEqual(running["active_task_id"], "T-FOLLOWUP")
        after = resume_packet(self.root, "CASE-WORK")
        self.assertNotEqual(
            before["invalidation"]["ledger_head"],
            after["invalidation"]["ledger_head"],
        )
        self.assertNotEqual(
            before["invalidation"]["contract_revision"],
            after["invalidation"]["contract_revision"],
        )

    def test_stop_outcome_finishes_as_stopped(self) -> None:
        contract = self._contract_value()
        contract["tasks"] = [contract["tasks"][0]]
        stop_contract = self.root / "stop-contract.json"
        self._write_work_contract(stop_contract, value=contract)
        create_work(
            self.root,
            "CASE-WORK",
            stop_contract,
            "planner@example.com",
        )
        _, evidence = add_evidence(
            self.root,
            "CASE-WORK",
            "H-OBSERVABILITY-GAP",
            direction="supporting",
            strength="strong",
            summary="Current signals cannot distinguish the material causes.",
            source_type="inspection",
            source_ref="missing-correlation",
        )
        start_task(
            self.root,
            "CASE-WORK",
            "T-INVESTIGATE",
            "worker@example.com",
        )
        complete_task(
            self.root,
            "CASE-WORK",
            "T-INVESTIGATE",
            [evidence["id"]],
            "worker@example.com",
        )
        implementation_complete(
            self.root,
            "CASE-WORK",
            "worker@example.com",
        )
        proposed = propose(self.root, "CASE-WORK")
        self.assertEqual(proposed["outcome"]["type"], "INSTRUMENT_FIRST")
        proposal_id = proposed["latest_proposal"]["proposal_id"]
        decide(
            self.root,
            "CASE-WORK",
            Outcome.INSTRUMENT_FIRST,
            "Stop implementation and add instrumentation.",
            "human@example.com",
            proposal_id=proposal_id,
        )
        stopped = finish_work(self.root, "CASE-WORK", "worker@example.com")
        self.assertEqual(stopped["state"], "STOPPED")

    def test_state_change_after_human_decision_prevents_finish(self) -> None:
        self._prepare_work_through_verification()
        proposed = propose(self.root, "CASE-WORK")
        proposal_id = proposed["latest_proposal"]["proposal_id"]
        decide(
            self.root,
            "CASE-WORK",
            Outcome.CODE_CHANGE,
            "Accept the verified candidate.",
            "human@example.com",
            proposal_id=proposal_id,
        )
        add_evidence(
            self.root,
            "CASE-WORK",
            "H-CODE-DEFECT",
            direction="neutral",
            strength="weak",
            summary="A later observation changes the ledger head.",
            source_type="observation",
            source_ref="post-decision",
        )
        with self.assertRaisesRegex(WorkStateError, "decision is stale"):
            finish_work(self.root, "CASE-WORK", "worker@example.com")

    def test_contract_and_transition_shortcuts_fail_closed(self) -> None:
        invalid = self._contract_value()
        invalid["tasks"][0]["depends_on"] = ["T-IMPLEMENT"]
        cyclic = self.root / "cyclic.json"
        self._write_work_contract(cyclic, value=invalid)
        with self.assertRaises(WorkContractError):
            create_work(
                self.root,
                "CASE-WORK",
                cyclic,
                "planner@example.com",
            )

        create_work(
            self.root,
            "CASE-WORK",
            self.work_contract,
            "planner@example.com",
        )
        pair = prepare_pair(
            self.root,
            "CASE-WORK",
            self.repository,
            self.verification_contract,
        )
        attach_pair(
            self.root,
            "CASE-WORK",
            pair["pair_id"],
            pair["code_run_id"],
            "worker@example.com",
        )
        with self.assertRaisesRegex(PairStateError, "must be IMPLEMENTED"):
            verify_pair(
                self.root,
                "CASE-WORK",
                pair["pair_id"],
                pair["code_run_id"],
                adapter=self.adapter,
                require_real_isolation=False,
            )
        with self.assertRaises(WorkStateError):
            start_task(
                self.root,
                "CASE-WORK",
                "T-IMPLEMENT",
                "worker@example.com",
            )
        start_task(
            self.root,
            "CASE-WORK",
            "T-INVESTIGATE",
            "worker@example.com",
        )
        with self.assertRaises(WorkStateError):
            complete_task(
                self.root,
                "CASE-WORK",
                "T-INVESTIGATE",
                [],
                "worker@example.com",
            )
        with self.assertRaises(WorkStateError):
            implementation_complete(
                self.root,
                "CASE-WORK",
                "worker@example.com",
            )

        changed = self._contract_value()
        changed["objective"] = "A materially different objective."
        changed_path = self.root / "changed-objective.json"
        self._write_work_contract(changed_path, value=changed)
        with self.assertRaises(WorkContractError):
            replan_work(
                self.root,
                "CASE-WORK",
                changed_path,
                "Change the objective.",
                "planner@example.com",
            )

    def test_saved_projection_replays_deterministically(self) -> None:
        self.assertTrue(
            (
                self.root
                / ".harness"
                / "contracts"
                / "work-contract.template.json"
            ).is_file()
        )
        create_work(
            self.root,
            "CASE-WORK",
            self.work_contract,
            "planner@example.com",
        )
        first = next_work(self.root, "CASE-WORK")
        second = next_work(self.root, "CASE-WORK")
        self.assertEqual(first, second)
        current = status_work(self.root, "CASE-WORK")
        rebuilt = rebuild_work_state(self.root, "CASE-WORK")
        state_path = (
            self.root / ".harness" / "cases" / "CASE-WORK" / "work-state.json"
        )
        stored = load_json(state_path)
        self.assertEqual(current, rebuilt)
        self.assertEqual(stored, rebuilt)
        state_path.write_text(
            json.dumps({**stored, "state": "DONE"}),
            encoding="utf-8",
        )
        self.assertEqual(rebuild_work_state(self.root, "CASE-WORK")["state"], "PLANNED")
        self.assertEqual(status_work(self.root, "CASE-WORK")["state"], "PLANNED")

    def test_legacy_case_is_read_only_but_show_and_verify_remain_available(self) -> None:
        harness_root = self.root / ".harness"
        store = CaseStore(harness_root)
        pack = store.load("CASE-WORK")
        pack["schema_version"] = "1.0.0"
        event = _anchor_projection(
            EventLedger(harness_root),
            pack,
            "CASE-WORK",
            "migration-test",
        )
        pack["provenance"]["event_head"] = event["hash"]
        store.save(pack)
        verified = verify_workspace(self.root, "CASE-WORK")
        self.assertTrue(verified["decision_pack"]["legacy_read_only"])
        self.assertEqual(store.load("CASE-WORK")["schema_version"], "1.0.0")

        with self.assertRaises(LegacyReadOnlyError):
            add_evidence(
                self.root,
                "CASE-WORK",
                "H-CODE-DEFECT",
                direction="supporting",
                strength="strong",
                summary="Legacy write attempt.",
                source_type="test",
                source_ref="legacy",
            )
        with self.assertRaises(LegacyReadOnlyError):
            propose(self.root, "CASE-WORK")
        with self.assertRaises(LegacyReadOnlyError):
            create_work(
                self.root,
                "CASE-WORK",
                self.work_contract,
                "planner@example.com",
            )
        with self.assertRaises(LegacyReadOnlyError):
            prepare_pair(
                self.root,
                "CASE-WORK",
                self.repository,
                self.verification_contract,
            )


if __name__ == "__main__":
    unittest.main()
