"""Tamper-evident append-only event ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mosaic_harness.util import canonical_json, sha256_bytes, utc_now

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows.
    fcntl = None  # type: ignore[assignment]


class LedgerIntegrityError(RuntimeError):
    """Raised when an event ledger no longer verifies."""


class EventLedger:
    def __init__(self, harness_root: Path) -> None:
        self.path = harness_root / "historian" / "events" / "events.jsonl"

    @staticmethod
    def _event_hash(event_without_hash: dict[str, Any]) -> str:
        return sha256_bytes(canonical_json(event_without_hash).encode("utf-8"))

    @classmethod
    def verify_events(cls, events: list[dict[str, Any]]) -> dict[str, Any]:
        previous_hash: str | None = None
        for expected_sequence, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                raise LedgerIntegrityError(
                    f"ledger event at line {expected_sequence} is not an object"
                )
            actual_hash = event.get("hash")
            unsigned = {key: value for key, value in event.items() if key != "hash"}
            if event.get("sequence") != expected_sequence:
                raise LedgerIntegrityError(
                    f"event sequence mismatch at line {expected_sequence}: {event.get('sequence')!r}"
                )
            if event.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"previous hash mismatch at line {expected_sequence}"
                )
            expected_hash = cls._event_hash(unsigned)
            if actual_hash != expected_hash:
                raise LedgerIntegrityError(f"event hash mismatch at line {expected_sequence}")
            previous_hash = actual_hash
        return {
            "valid": True,
            "event_count": len(events),
            "head_hash": previous_hash,
        }

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"invalid JSON in ledger at line {line_number}: {error.msg}"
                    ) from error
                if not isinstance(event, dict):
                    raise LedgerIntegrityError(
                        f"ledger event at line {line_number} is not an object"
                    )
                events.append(event)
        return events

    def verify(self) -> dict[str, Any]:
        return self.verify_events(self.read())

    def append(
        self,
        event_type: str,
        case_id: str,
        payload: dict[str, Any],
        actor: str = "mosaic",
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            events: list[dict[str, Any]] = []
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"invalid JSON in ledger at line {line_number}: {error.msg}"
                    ) from error
                if not isinstance(event, dict):
                    raise LedgerIntegrityError(
                        f"ledger event at line {line_number} is not an object"
                    )
                events.append(event)
            state = self.verify_events(events)
            unsigned = {
                "sequence": len(events) + 1,
                "timestamp": utc_now(),
                "type": event_type,
                "actor": actor,
                "case_id": case_id,
                "payload": payload,
                "previous_hash": state["head_hash"],
            }
            event = {**unsigned, "hash": self._event_hash(unsigned)}
            handle.seek(0, os.SEEK_END)
            handle.write(canonical_json(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return event
