"""Post-change observers. They append evidence; they cannot decide."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mosaic_harness.domain import Outcome
from mosaic_harness.compat import require_writable_pack
from mosaic_harness.historian import EventLedger
from mosaic_harness.storage import CaseStore
from mosaic_harness.util import utc_now
from mosaic_harness.validation import validate_decision_pack
from mosaic_harness.workflow import _anchor_projection, _harness_root, add_evidence


OBSERVATION_KINDS = {"deployment", "incident", "rollback", "human-override"}
STATE_CHANGING = {
    Outcome.CODE_CHANGE.value,
    Outcome.CONFIGURATION_CHANGE.value,
    Outcome.DOCUMENTATION_CHANGE.value,
    Outcome.OPERATIONAL_ACTION.value,
}


class ObserverError(ValueError):
    pass


def apply_contradicting_observation(workspace: Path, case_id: str, actor: str) -> dict[str, Any]:
    harness_root = _harness_root(workspace)
    store = CaseStore(harness_root)
    pack = store.load(case_id)
    require_writable_pack(pack)
    outcome = pack.get("outcome") or {}
    if outcome.get("status") != "accepted" or outcome.get("type") not in STATE_CHANGING:
        return pack
    for item in pack.get("evidence") or []:
        item["freshness"] = "stale"
    for claim in pack.get("claims") or []:
        if claim.get("status") == "supported":
            claim["status"] = "unresolved"
    pack["outcome"]["status"] = "superseded"
    note = "Post-change observation contradicts the accepted intervention."
    questions = list(pack.get("unresolved_questions") or [])
    if note not in questions:
        questions.append(note)
    pack["unresolved_questions"] = questions
    pack["updated_at"] = utc_now()
    validate_decision_pack(pack)
    event = _anchor_projection(EventLedger(harness_root), pack, case_id, actor)
    pack["provenance"]["event_head"] = event["hash"]
    store.save(pack)
    return pack


def observe(
    workspace: Path,
    case_id: str,
    *,
    kind: str,
    summary: str,
    source_ref: str,
    direction: str,
    claim_id: str | None = None,
    actor: str = "observer",
) -> dict[str, Any]:
    if kind not in OBSERVATION_KINDS:
        raise ObserverError(f"unsupported observation kind: {kind}")
    harness_root = _harness_root(workspace)
    pack = CaseStore(harness_root).load(case_id)
    require_writable_pack(pack)
    claim = claim_id or pack["claims"][0]["id"]
    pack, evidence = add_evidence(
        workspace,
        case_id,
        claim,
        direction=direction,
        strength="strong",
        summary=summary,
        source_type=kind,
        source_ref=source_ref,
        actor=actor,
    )
    EventLedger(harness_root).append(
        "observation_recorded",
        case_id,
        {"kind": kind, "evidence_id": evidence["id"], "direction": direction},
        actor=actor,
    )
    if kind == "incident" and direction == "opposing":
        pack = apply_contradicting_observation(workspace, case_id, actor)
    return {"observation": evidence, "case_id": case_id, "outcome": pack["outcome"]}
