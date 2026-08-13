"""Equal-budget Future Maintainer Tournament runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from mosaic_harness.admission import rank_candidates
from mosaic_harness.candidate import verify_candidate
from mosaic_harness.executor import load_json, manifest_path
from mosaic_harness.historian import EventLedger
from mosaic_harness.util import atomic_write_json, utc_now
from mosaic_harness.workflow import ensure_runtime
from mosaic_harness.workspace import scan_for_tokens, workspace_texts


REQUIRED_SCENARIO_FIELDS = {
    "scenario_id",
    "provenance_class",
    "base_revision",
    "constraints",
    "budgets",
    "floors",
    "contamination",
}

FORBIDDEN_BUILDER_FIELDS = {"statement", "task", "prompt", "hidden_instruction"}


class TournamentError(ValueError):
    pass


def scenario_path(workspace: Path, scenario_id: str) -> Path:
    return workspace.resolve() / ".harness" / "future" / "scenarios" / f"{scenario_id}.json"


def load_scenario(workspace: Path, scenario_id: str) -> dict[str, Any]:
    path = scenario_path(workspace, scenario_id)
    if not path.is_file():
        raise TournamentError(f"scenario not found: {scenario_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TournamentError("scenario must be an object")
    missing = REQUIRED_SCENARIO_FIELDS - payload.keys()
    if missing:
        raise TournamentError(f"scenario missing fields: {', '.join(sorted(missing))}")
    leaked = [field for field in FORBIDDEN_BUILDER_FIELDS if field in payload]
    if leaked:
        raise TournamentError(
            "scenario contains builder-visible task text: " + ", ".join(leaked)
        )
    return payload


def _manifests(harness_root: Path, run_ids: Sequence[str]) -> list[dict[str, Any]]:
    return [load_json(manifest_path(harness_root, run_id)) for run_id in run_ids]


def run_tournament(
    workspace: Path,
    case_id: str,
    scenario_id: str,
    run_ids: Sequence[str],
) -> dict[str, Any]:
    if len(run_ids) < 2:
        raise TournamentError("tournament requires at least two runs")
    workspace = workspace.resolve()
    harness_root, _ = ensure_runtime(workspace)
    scenario = load_scenario(workspace, scenario_id)
    manifests = _manifests(harness_root, run_ids)
    if any(item.get("case_id") != case_id for item in manifests):
        raise TournamentError("tournament runs must belong to the case")
    mechanisms = {item.get("isolation", {}).get("mechanism") for item in manifests}
    if len(mechanisms) != 1:
        raise TournamentError("tournament refused: isolation mechanisms differ")
    seconds = {int(item.get("budget", {}).get("max_seconds", 0)) for item in manifests}
    if len(seconds) != 1:
        raise TournamentError("tournament refused: run budgets differ")
    scenario_seconds = int(scenario["budgets"].get("seconds") or scenario["budgets"].get("verifier") or 0)
    shared_seconds = next(iter(seconds))
    if scenario_seconds and scenario_seconds != shared_seconds:
        raise TournamentError("tournament refused: run budget does not match scenario budget")

    ledger = EventLedger(harness_root)
    started = ledger.append(
        "tournament_started",
        case_id,
        {"scenario_id": scenario_id, "run_ids": list(run_ids), "budgets": scenario["budgets"]},
        actor="challenger",
    )
    evaluations = []
    for run_id, manifest in zip(run_ids, manifests):
        verdict = verify_candidate(workspace, case_id, run_id)
        leaked = scan_for_tokens(
            workspace_texts(Path(manifest["workspace_root"])),
            [scenario["scenario_id"]],
        )
        record = {
            "run_id": run_id,
            "floor_passed": verdict["floor_passed"],
            "floors": verdict.get("floors"),
            "contaminated": bool(leaked),
            "budget_exceeded": False,
        }
        evaluations.append(record)
        ledger.append(
            "scenario_evaluated",
            case_id,
            {"scenario_id": scenario_id, **record},
            actor="verifier",
        )
    ranking = rank_candidates(
        [
            load_json(harness_root / "experiments" / "candidates" / item["run_id"] / "verdict.json")
            for item in evaluations
        ]
    )
    result = {
        "case_id": case_id,
        "scenario_id": scenario_id,
        "provenance_class": scenario["provenance_class"],
        "run_ids": list(run_ids),
        "evaluations": evaluations,
        **ranking,
        "started_event": started["hash"],
        "completed_at": utc_now(),
    }
    out = harness_root / "future" / "tournaments" / f"{case_id}-{scenario_id}.json"
    atomic_write_json(out, result)
    ledger.append("tournament_completed", case_id, {"path": str(out), **ranking}, actor="challenger")
    return result
