"""Hard-floor admission rules for state-changing dispositions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from mosaic_harness.domain import Outcome
from mosaic_harness.executor import load_json
from mosaic_harness.util import sha256_file


STATE_CHANGING_OUTCOMES = {
    Outcome.CODE_CHANGE,
    Outcome.CONFIGURATION_CHANGE,
    Outcome.DOCUMENTATION_CHANGE,
    Outcome.OPERATIONAL_ACTION,
}

ADMISSION_RULE = "hard-floor-then-pareto"


class AdmissionError(ValueError):
    pass


def observation_plan_ready(pack: dict[str, Any]) -> bool:
    for item in pack.get("observation_plan") or []:
        if item.get("signal") and item.get("rollback_trigger"):
            return True
    return False


def change_surface_files(workspace_root: Path, peer: Path | None, kind: str) -> int:
    if kind == "zero-change":
        return 0
    if peer is None or not peer.is_dir():
        return 1
    count = 0
    for path in workspace_root.rglob("*"):
        if not path.is_file() or ".tmp" in path.parts or "__pycache__" in path.parts:
            continue
        other = peer / path.relative_to(workspace_root)
        if not other.is_file() or sha256_file(path) != sha256_file(other):
            count += 1
    return count


def verdict_measures(
    *,
    kind: str,
    workspace_root: Path,
    peer: Path | None,
    budget: dict[str, Any],
    reversible: bool = True,
    human_intervention: bool = False,
) -> dict[str, Any]:
    return {
        "change_surface_files": change_surface_files(workspace_root, peer, kind),
        "reversible": reversible,
        "budget_seconds_used": float(budget.get("used_seconds") or budget.get("max_seconds") or 0),
        "human_intervention": human_intervention,
    }


def collect_verdicts(harness_root: Path, case_id: str) -> list[dict[str, Any]]:
    root = harness_root / "experiments" / "candidates"
    verdicts: list[dict[str, Any]] = []
    if not root.is_dir():
        return verdicts
    for verdict_file in sorted(root.glob("*/verdict.json")):
        try:
            payload = load_json(verdict_file)
        except (OSError, ValueError):
            continue
        if payload.get("case_id") == case_id:
            verdicts.append(payload)
    return verdicts


def _measure_vector(verdict: dict[str, Any]) -> tuple[float, float, int, int]:
    measures = verdict.get("measures") or {}
    return (
        -float(measures.get("change_surface_files", 0)),
        -float(measures.get("budget_seconds_used", 0)),
        1 if measures.get("reversible", True) else 0,
        0 if measures.get("human_intervention", False) else 1,
    )


def dominate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_vector = _measure_vector(left)
    right_vector = _measure_vector(right)
    at_least = all(a >= b for a, b in zip(left_vector, right_vector))
    better = any(a > b for a, b in zip(left_vector, right_vector))
    return at_least and better


def rank_candidates(verdicts: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    items = list(verdicts)
    eliminated = [
        item["run_id"]
        for item in items
        if not (item.get("floor_passed") and item.get("candidate_unmodified", True))
    ]
    remaining = [
        item
        for item in items
        if item.get("floor_passed") and item.get("candidate_unmodified", True)
    ]
    dominated: list[str] = []
    frontier: list[str] = []
    for item in remaining:
        if any(
            other.get("kind") == item.get("kind") and dominate(other, item)
            for other in remaining
            if other.get("run_id") != item.get("run_id")
        ):
            dominated.append(item["run_id"])
        else:
            frontier.append(item["run_id"])
    return {
        "eliminated_by_floor": eliminated,
        "dominated": dominated,
        "frontier": frontier,
    }


def evaluate_admission(verdicts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    ranking = rank_candidates(verdicts)
    status = "eligible" if ranking["frontier"] else "ungated"
    return {
        "status": status,
        "surviving_run_ids": ranking["frontier"],
        "dominated_run_ids": ranking["dominated"],
        "floor_failures": ranking["eliminated_by_floor"],
        "rule": ADMISSION_RULE,
        **ranking,
    }


def require_admission(
    outcome: Outcome,
    verdicts: list[dict[str, Any]],
    *,
    override: bool = False,
    pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evaluation = evaluate_admission(verdicts)
    if outcome not in STATE_CHANGING_OUTCOMES:
        if evaluation["status"] == "ungated":
            return evaluation
        return evaluation
    ranking = rank_candidates(verdicts)
    frontier = [
        item
        for item in verdicts
        if item.get("run_id") in ranking["frontier"]
    ]
    code_on_frontier = [item for item in frontier if item.get("kind") == "code"]
    only_zero = frontier and all(item.get("kind") == "zero-change" for item in frontier)
    if override:
        evaluation["status"] = "overridden"
        return evaluation
    if pack is not None and not observation_plan_ready(pack):
            evaluation["status"] = "refused"
            raise AdmissionError(
                "state-changing disposition refused: observation plan needs signal and rollback_trigger"
            )
    if not code_on_frontier or only_zero:
        evaluation["status"] = "refused"
        raise AdmissionError(
            "state-changing disposition refused: no undominated code candidate survives the floors"
        )
    evaluation["status"] = "eligible"
    evaluation["surviving_run_ids"] = ranking["frontier"]
    evaluation["dominated_run_ids"] = ranking["dominated"]
    evaluation["floor_failures"] = ranking["eliminated_by_floor"]
    return evaluation


def experiment_ref(manifest: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": verdict["run_id"],
        "kind": verdict["kind"],
        "revision": manifest["revision"],
        "verdict_ref": str(Path(manifest["candidate_root"]) / "verdict.json"),
        "floor_passed": bool(verdict["floor_passed"]),
        "isolation": verdict.get("isolation") or manifest.get("isolation") or {},
    }
