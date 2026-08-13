"""Projection storage for mutable, derived decision packs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mosaic_harness.util import atomic_write_json, validate_case_id


class CaseNotFoundError(FileNotFoundError):
    pass


class CaseStore:
    def __init__(self, harness_root: Path) -> None:
        self.harness_root = harness_root

    def path_for(self, case_id: str) -> Path:
        validate_case_id(case_id)
        return self.harness_root / "cases" / case_id / "decision-pack.json"

    def exists(self, case_id: str) -> bool:
        return self.path_for(case_id).exists()

    def load(self, case_id: str) -> dict[str, Any]:
        path = self.path_for(case_id)
        if not path.exists():
            raise CaseNotFoundError(f"case not found: {case_id}")
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError(f"decision pack is not an object: {path}")
        return value

    def save(self, pack: dict[str, Any]) -> Path:
        case_id = str(pack["case_id"])
        path = self.path_for(case_id)
        atomic_write_json(path, pack)
        return path

