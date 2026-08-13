"""Maintenance Mode amendment sequence. Normal Mode must refuse."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from mosaic_harness.historian import EventLedger
from mosaic_harness.util import atomic_write_json, utc_now
from mosaic_harness.workflow import _harness_root, ensure_runtime


class AmendmentError(ValueError):
    pass


def _record_path(harness_root: Path, amendment_id: str) -> Path:
    path = harness_root / "constitution" / "amendments" / f"{amendment_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def propose_amendment(
    workspace: Path,
    *,
    mode: str,
    worktree: Path,
    amendment_id: str,
    rationale: str,
    falsifier: str,
) -> dict[str, Any]:
    if mode != "maintenance":
        raise AmendmentError("Maintenance Mode required")
    worktree = worktree.resolve()
    if not worktree.is_dir():
        raise AmendmentError("separate worktree required")
    if worktree.resolve() == workspace.resolve():
        raise AmendmentError("amendment worktree must be distinct from the source workspace")
    if not rationale.strip() or not falsifier.strip():
        raise AmendmentError("rationale and falsifier are required")
    harness_root, _ = ensure_runtime(workspace)
    source_constitution = harness_root / "constitution" / "normal.md"
    before = source_constitution.read_text(encoding="utf-8") if source_constitution.is_file() else ""
    record = {
        "id": amendment_id,
        "rationale": rationale,
        "falsifier": falsifier,
        "worktree": str(worktree),
        "status": "proposed",
        "proposed_at": utc_now(),
        "source_digest": before,
    }
    atomic_write_json(_record_path(harness_root, amendment_id), record)
    EventLedger(harness_root).append(
        "amendment_proposed",
        amendment_id,
        {"rationale": rationale, "falsifier": falsifier, "worktree": str(worktree)},
        actor="amender",
    )
    if source_constitution.is_file() and source_constitution.read_text(encoding="utf-8") != before:
        raise AmendmentError("propose must not mutate source constitution")
    return record


def _holdout_passed(worktree: Path, amendment_id: str) -> bool:
    path = worktree / ".harness" / "constitution" / "amendments" / f"{amendment_id}.holdout.json"
    if not path.is_file():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    return bool(payload.get("passed"))


def ratify_amendment(
    workspace: Path,
    *,
    mode: str,
    worktree: Path,
    amendment_id: str,
) -> dict[str, Any]:
    if mode != "maintenance":
        raise AmendmentError("Maintenance Mode required")
    worktree = worktree.resolve()
    harness_root, _ = ensure_runtime(workspace)
    record_file = _record_path(harness_root, amendment_id)
    if not record_file.is_file():
        raise AmendmentError(f"amendment not proposed: {amendment_id}")
    record = json.loads(record_file.read_text(encoding="utf-8"))
    if not _holdout_passed(worktree, amendment_id):
        raise AmendmentError("amendment cannot be ratified: hidden holdout failed")
    source_const = harness_root / "constitution"
    previous = source_const / "previous"
    previous.mkdir(parents=True, exist_ok=True)
    for name in ("normal.md", "maintenance.md", "permissions.yaml"):
        current = source_const / name
        if current.is_file():
            shutil.copy2(current, previous / name)
        proposed = worktree / ".harness" / "constitution" / name
        if proposed.is_file():
            shutil.copy2(proposed, current)
    record["status"] = "ratified"
    record["ratified_at"] = utc_now()
    atomic_write_json(record_file, record)
    EventLedger(harness_root).append(
        "amendment_ratified",
        amendment_id,
        {"worktree": str(worktree)},
        actor="amender",
    )
    return record


def rollback_amendment(workspace: Path, *, mode: str, amendment_id: str) -> dict[str, Any]:
    if mode != "maintenance":
        raise AmendmentError("Maintenance Mode required")
    harness_root, _ = ensure_runtime(workspace)
    previous = harness_root / "constitution" / "previous"
    if not previous.is_dir():
        raise AmendmentError("no previous constitution to restore")
    for name in ("normal.md", "maintenance.md", "permissions.yaml"):
        archived = previous / name
        if archived.is_file():
            shutil.copy2(archived, harness_root / "constitution" / name)
    record_file = _record_path(harness_root, amendment_id)
    record = json.loads(record_file.read_text(encoding="utf-8")) if record_file.is_file() else {"id": amendment_id}
    record["status"] = "rolled_back"
    record["rolled_back_at"] = utc_now()
    atomic_write_json(record_file, record)
    EventLedger(harness_root).append(
        "amendment_rolled_back",
        amendment_id,
        {"restored": "constitution/previous"},
        actor="amender",
    )
    return record
