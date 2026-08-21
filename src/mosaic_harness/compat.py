"""Schema compatibility boundaries for mutable Mosaic operations."""

from __future__ import annotations

from typing import Any


LEGACY_SCHEMA_VERSION = "1.0.0"


class LegacyReadOnlyError(ValueError):
    """Raised when a state-changing command targets a legacy case."""


def require_writable_pack(pack: dict[str, Any]) -> None:
    if pack.get("schema_version") == LEGACY_SCHEMA_VERSION:
        raise LegacyReadOnlyError(
            "schema 1.0.0 cases are read-only; create a new v2 case id"
        )
