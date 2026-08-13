from __future__ import annotations

import unittest

from mosaic_harness.admission import AdmissionError, dominate, rank_candidates, require_admission
from mosaic_harness.domain import Outcome


def _verdict(run_id: str, kind: str, *, passed: bool, files: int, seconds: float = 1.0) -> dict:
    return {
        "run_id": run_id,
        "kind": kind,
        "floor_passed": passed,
        "candidate_unmodified": True,
        "measures": {
            "change_surface_files": files,
            "reversible": True,
            "budget_seconds_used": seconds,
            "human_intervention": False,
        },
        "score": 99,
    }


class MosaicPhaseCTest(unittest.TestCase):
    def test_floor_failure_is_removed_before_pareto(self) -> None:
        hidden_fail = _verdict("R-hidden", "code", passed=False, files=1)
        large = _verdict("R-large", "code", passed=True, files=5)
        small = _verdict("R-small", "code", passed=True, files=1)
        ranking = rank_candidates([hidden_fail, large, small])
        self.assertEqual(ranking["eliminated_by_floor"], ["R-hidden"])
        self.assertEqual(ranking["dominated"], ["R-large"])
        self.assertEqual(ranking["frontier"], ["R-small"])

    def test_numeric_score_cannot_revive_failed_floor(self) -> None:
        failed = _verdict("R-fail", "code", passed=False, files=0)
        failed["score"] = 10_000
        passed = _verdict("R-ok", "code", passed=True, files=3)
        ranking = rank_candidates([failed, passed])
        self.assertIn("R-fail", ranking["eliminated_by_floor"])
        self.assertNotIn("R-fail", ranking["frontier"])
        self.assertTrue(dominate(passed, {**failed, "floor_passed": True, "measures": {**failed["measures"], "change_surface_files": 4}}))

    def test_only_zero_change_on_frontier_refuses_code_change(self) -> None:
        zero = _verdict("R-zero", "zero-change", passed=True, files=0)
        failed = _verdict("R-code", "code", passed=False, files=1)
        with self.assertRaises(AdmissionError):
            require_admission(Outcome.CODE_CHANGE, [zero, failed])
        allowed = require_admission(Outcome.NO_CHANGE, [zero, failed])
        self.assertEqual(allowed["surviving_run_ids"], ["R-zero"])

    def test_code_on_frontier_is_eligible(self) -> None:
        zero = _verdict("R-zero", "zero-change", passed=True, files=0)
        code = _verdict("R-code", "code", passed=True, files=1)
        result = require_admission(Outcome.CODE_CHANGE, [zero, code])
        self.assertEqual(result["status"], "eligible")
        self.assertIn("R-code", result["surviving_run_ids"])


if __name__ == "__main__":
    unittest.main()
