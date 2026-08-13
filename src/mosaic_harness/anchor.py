"""Ledger-head export for external comparison. Not a timestamp authority."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mosaic_harness.historian import EventLedger
from mosaic_harness.util import atomic_write_json, utc_now
from mosaic_harness.workflow import _harness_root


class AnchorError(ValueError):
    pass


def export_head(workspace: Path, destination: Path) -> dict[str, Any]:
    harness_root = _harness_root(workspace)
    state = EventLedger(harness_root).verify()
    payload = {
        "head_hash": state["head_hash"],
        "event_count": state["event_count"],
        "exported_at": utc_now(),
        "workspace": str(workspace.resolve()),
    }
    atomic_write_json(destination, payload)
    return payload


def compare_head(workspace: Path, against: Path) -> dict[str, Any]:
    if not against.is_file():
        raise AnchorError(f"export not found: {against}")
    saved = json.loads(against.read_text(encoding="utf-8"))
    ledger = EventLedger(_harness_root(workspace))
    live = ledger.verify()
    events = ledger.read()
    hashes = [event["hash"] for event in events]
    saved_hash = saved.get("head_hash")
    if saved_hash == live["head_hash"]:
        return {"valid": True, "relation": "equal", "head_hash": live["head_hash"]}
    if saved_hash in hashes:
        return {"valid": True, "relation": "descendant", "head_hash": live["head_hash"]}
    raise AnchorError("ledger head mismatch")
