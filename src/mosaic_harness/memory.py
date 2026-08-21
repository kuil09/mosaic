"""Memory projections with explicit invalidation and conflict records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mosaic_harness.historian import EventLedger
from mosaic_harness.compat import require_writable_pack
from mosaic_harness.storage import CaseStore
from mosaic_harness.util import atomic_write_json, utc_now
from mosaic_harness.validation import validate_decision_pack
from mosaic_harness.workflow import _anchor_projection, _harness_root


class MemoryError(ValueError):
    pass


def conflict_path(harness_root: Path, case_id: str) -> Path:
    return harness_root / "historian" / "conflicts" / f"{case_id}.json"


def check_conflicts(harness_root: Path) -> None:
    root = harness_root / "historian" / "conflicts"
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not payload.get("acknowledged"):
            raise MemoryError(f"unresolved historian conflict: {payload.get('case_id')}")


def acknowledge_conflict(workspace: Path, case_id: str) -> dict[str, Any]:
    require_writable_pack(CaseStore(_harness_root(workspace)).load(case_id))
    path = conflict_path(_harness_root(workspace), case_id)
    if not path.is_file():
        raise MemoryError(f"no conflict recorded for {case_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["acknowledged"] = True
    atomic_write_json(path, payload)
    return payload


def invalidate_claim(
    workspace: Path,
    case_id: str,
    claim_id: str,
    *,
    reason: str,
    actor: str,
) -> dict[str, Any]:
    if not reason.strip():
        raise MemoryError("invalidation reason must not be empty")
    harness_root = _harness_root(workspace)
    store = CaseStore(harness_root)
    pack = store.load(case_id)
    require_writable_pack(pack)
    claim = next((item for item in pack["claims"] if item["id"] == claim_id), None)
    if claim is None:
        raise MemoryError(f"claim not found: {claim_id}")
    previous = claim.get("invalidated_reason")
    if previous and previous != reason:
        atomic_write_json(
            conflict_path(harness_root, case_id),
            {
                "case_id": case_id,
                "claim_id": claim_id,
                "detail": f"conflicting invalidation reasons: {previous!r} vs {reason!r}",
                "acknowledged": False,
            },
        )
    stamp = utc_now()
    claim["invalidated_at"] = stamp
    claim["invalidated_reason"] = reason
    if claim["status"] in {"supported", "survived"}:
        claim["status"] = "unresolved"
    pack["updated_at"] = stamp
    validate_decision_pack(pack)
    event = EventLedger(harness_root).append(
        "memory_invalidated",
        case_id,
        {"claim_id": claim_id, "reason": reason, "invalidated_at": stamp},
        actor=actor,
    )
    projection = _anchor_projection(EventLedger(harness_root), pack, case_id, actor)
    pack["provenance"]["event_head"] = projection["hash"]
    store.save(pack)
    invalidation = harness_root / "historian" / "invalidations" / f"{case_id}-{claim_id}.json"
    atomic_write_json(
        invalidation,
        {"case_id": case_id, "claim_id": claim_id, "reason": reason, "event": event["hash"]},
    )
    return pack
