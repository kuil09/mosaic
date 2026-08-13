"""Packaged Decision Pack schema as the single authored source."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from mosaic_harness.util import sha256_bytes, sha256_file


SCHEMA_RESOURCE = "templates/harness/claims/schemas/decision-pack.schema.json"


class SchemaDriftError(ValueError):
    pass


def packaged_schema_text() -> str:
    return (
        resources.files("mosaic_harness")
        .joinpath(*SCHEMA_RESOURCE.split("/"))
        .read_text(encoding="utf-8")
    )


def packaged_schema_digest() -> str:
    return sha256_bytes(packaged_schema_text().encode("utf-8"))


def workspace_schema_path(workspace: Path) -> Path:
    return workspace.resolve() / ".harness" / "claims" / "schemas" / "decision-pack.schema.json"


def check_schema_drift(workspace: Path) -> dict[str, Any]:
    path = workspace_schema_path(workspace)
    if not path.is_file():
        return {"checked": False, "reason": "workspace schema missing"}
    actual = sha256_file(path)
    expected = packaged_schema_digest()
    if actual != expected:
        raise SchemaDriftError("decision pack schema drift from packaged schema")
    return {"checked": True, "valid": True, "schema_sha256": actual}
