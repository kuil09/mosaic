"""Hard-floor admission rules for state-changing dispositions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from mosaic_harness.domain import Outcome
from mosaic_harness.executor import load_json, manifest_path
from mosaic_harness.scanner import scan_repository
from mosaic_harness.util import git_commit, sha256_file
from mosaic_harness.verification_contract import verify_frozen_floor
from mosaic_harness.workspace import snapshot_tree


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
    def files(root: Path) -> dict[str, Path]:
        return {
            path.relative_to(root).as_posix(): path
            for path in root.rglob("*")
            if path.is_file() and ".tmp" not in path.parts and "__pycache__" not in path.parts
        }

    current = files(workspace_root)
    baseline = files(peer)
    names = set(current) | set(baseline)
    return sum(
        name not in current
        or name not in baseline
        or sha256_file(current[name]) != sha256_file(baseline[name])
        for name in names
    )


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
        "budget_seconds_used": float(budget.get("used_seconds") or 0),
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


def require_v2_admission(
    workspace: Path,
    pack: dict[str, Any],
    outcome: Outcome,
    proposal_id: str,
) -> dict[str, Any]:
    """Validate a v2 human decision against its exact harness proposal."""

    proposal = pack.get("latest_proposal") or {}
    if proposal.get("proposal_id") != proposal_id:
        raise AdmissionError("decision must reference the latest proposal id")
    if proposal.get("outcome") != outcome.value:
        raise AdmissionError("decision outcome must match the latest harness proposal")
    unsupported = {
        Outcome.CONFIGURATION_CHANGE,
        Outcome.DOCUMENTATION_CHANGE,
        Outcome.OPERATIONAL_ACTION,
    }
    if outcome in unsupported:
        raise AdmissionError(
            f"{outcome.value} is not admissible until a typed verification adapter exists"
        )
    if outcome != Outcome.CODE_CHANGE:
        return {
            "status": "eligible",
            "rule": "proposal-bound-non-state-changing",
            "proposal_id": proposal_id,
            "surviving_run_ids": [],
            "dominated_run_ids": [],
            "floor_failures": [],
        }
    if not observation_plan_ready(pack):
        raise AdmissionError(
            "state-changing disposition refused: observation plan needs signal and rollback_trigger"
        )
    pair_id = proposal.get("pair_id")
    code_run_id = proposal.get("code_run_id")
    if not pair_id or not code_run_id:
        raise AdmissionError("CODE_CHANGE proposal is not bound to an experiment pair")
    from mosaic_harness.pair import pair_verdict_path
    from mosaic_harness.pair import load_pair, pair_path
    from mosaic_harness.pair_verifier import compare_pair

    harness_root = workspace.resolve() / ".harness"
    verdict_file = pair_verdict_path(harness_root, pair_id, code_run_id)
    if not verdict_file.is_file():
        raise AdmissionError("proposal pair verdict is missing")
    verdict = load_json(verdict_file)
    if not verdict.get("eligible"):
        raise AdmissionError("proposal code attempt did not satisfy the verification contract")
    if verdict.get("base_revision") != proposal.get("base_revision"):
        raise AdmissionError("proposal and verdict revisions differ")
    if verdict.get("code_snapshot") != proposal.get("code_snapshot"):
        raise AdmissionError("proposal and verdict snapshots differ")
    if verdict.get("floor_definition_sha256") != proposal.get("floor_definition_sha256"):
        raise AdmissionError("proposal and verdict floor definitions differ")
    pair = load_pair(harness_root, pair_id)
    manifest = load_json(manifest_path(harness_root, code_run_id))
    current_snapshot = snapshot_tree(Path(manifest["workspace_root"]))["sha256"]
    if current_snapshot != verdict.get("code_snapshot"):
        raise AdmissionError("candidate changed after verification")
    internal_contract = load_json(
        pair_path(harness_root, pair_id) / "floor" / "internal-contract.json"
    )
    floor = verify_frozen_floor(
        internal_contract,
        public_root=Path(pair["public_root"]),
        pair_root=pair_path(harness_root, pair_id),
    )
    if (
        not floor["valid"]
        or floor["definition_sha256"] != verdict.get("floor_definition_sha256")
    ):
        raise AdmissionError("verification floor changed after verification")
    repository = Path(pack["scope"]["repository"])
    commit = git_commit(repository)
    limits = pack["scope"]["inventory"]["limits"]
    inventory = scan_repository(
        repository,
        max_files=int(limits["max_files"]),
        max_file_bytes=int(limits["max_file_bytes"]),
    )
    current_revision = (
        f"git:{commit}+tree:{inventory['fingerprint_sha256']}"
        if commit
        else f"tree:{inventory['fingerprint_sha256']}"
    )
    if current_revision != pack["scope"]["base_revision"]:
        raise AdmissionError("repository revision changed after investigation")
    comparison = compare_pair(workspace, pack["case_id"], pair_id)
    if code_run_id not in comparison["frontier_run_ids"]:
        raise AdmissionError("proposal code attempt is not on the eligible Pareto frontier")
    return {
        "status": "eligible",
        "rule": "target-improvement-then-code-pareto",
        "proposal_id": proposal_id,
        "pair_id": pair_id,
        "surviving_run_ids": comparison["frontier_run_ids"],
        "dominated_run_ids": sorted(
            set(comparison["eligible_run_ids"]) - set(comparison["frontier_run_ids"])
        ),
        "floor_failures": [],
        "selected_run_id": code_run_id,
        "code_snapshot": verdict["code_snapshot"],
        "floor_definition_sha256": verdict["floor_definition_sha256"],
    }


def experiment_ref(manifest: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": verdict["run_id"],
        "kind": verdict["kind"],
        "revision": manifest["revision"],
        "verdict_ref": str(Path(manifest["candidate_root"]) / "verdict.json"),
        "floor_passed": bool(verdict["floor_passed"]),
        "isolation": verdict.get("isolation") or manifest.get("isolation") or {},
    }
