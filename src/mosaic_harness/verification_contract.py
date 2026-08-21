"""Versioned verification contracts and frozen floor materialization."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from mosaic_harness.executor import normalize_budget
from mosaic_harness.util import canonical_json, sha256_bytes, sha256_file


VERIFICATION_CONTRACT_VERSION = "1.0.0"
CHECK_ROLES = {"target", "preservation"}
CHECK_VISIBILITIES = {"public", "hidden"}
REQUIRED_ADAPTERS = {"mutation", "property", "hidden"}


class VerificationContractError(ValueError):
    """Raised when a verification contract cannot be trusted or frozen."""


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerificationContractError(f"{field} must be a non-empty string")
    return value.strip()


def _safe_relative(value: Any, field: str) -> str:
    text = _require_string(value, field)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise VerificationContractError(f"{field} must be a safe relative path")
    return path.as_posix()


def _normalize_input(value: Any, index: int) -> dict[str, str]:
    if isinstance(value, str):
        return {"root": "contract", "path": _safe_relative(value, f"frozen_inputs[{index}]")}
    if not isinstance(value, dict):
        raise VerificationContractError(f"frozen_inputs[{index}] must be a string or object")
    root = value.get("root", "contract")
    if root not in {"contract", "repository"}:
        raise VerificationContractError(
            f"frozen_inputs[{index}].root must be contract or repository"
        )
    return {
        "root": root,
        "path": _safe_relative(value.get("path"), f"frozen_inputs[{index}].path"),
    }


def load_verification_contract(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise VerificationContractError(f"verification contract does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise VerificationContractError(f"invalid verification contract JSON: {error.msg}") from error
    if not isinstance(value, dict):
        raise VerificationContractError("verification contract must be an object")
    if value.get("schema_version") != VERIFICATION_CONTRACT_VERSION:
        raise VerificationContractError(
            f"schema_version must equal {VERIFICATION_CONTRACT_VERSION}"
        )
    contract_id = _require_string(value.get("contract_id"), "contract_id")
    raw_checks = value.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise VerificationContractError("checks must be a non-empty array")
    checks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_checks):
        if not isinstance(raw, dict):
            raise VerificationContractError(f"checks[{index}] must be an object")
        check_id = _require_string(raw.get("id"), f"checks[{index}].id")
        if check_id in seen:
            raise VerificationContractError(f"duplicate check id: {check_id}")
        seen.add(check_id)
        role = raw.get("role")
        visibility = raw.get("visibility")
        if role not in CHECK_ROLES:
            raise VerificationContractError(f"checks[{index}].role must be target or preservation")
        if visibility not in CHECK_VISIBILITIES:
            raise VerificationContractError(f"checks[{index}].visibility must be public or hidden")
        normalized: dict[str, Any] = {
            "id": check_id,
            "role": role,
            "visibility": visibility,
        }
        if visibility == "public":
            argv = raw.get("argv")
            if not isinstance(argv, list) or not argv or not all(
                isinstance(item, str) and item for item in argv
            ):
                raise VerificationContractError(
                    f"checks[{index}].argv must be a non-empty string array"
                )
            raw_inputs = raw.get("frozen_inputs")
            if not isinstance(raw_inputs, list) or not raw_inputs:
                raise VerificationContractError(
                    f"checks[{index}].frozen_inputs must be a non-empty array"
                )
            inputs = [_normalize_input(item, item_index) for item_index, item in enumerate(raw_inputs)]
            if not any("{frozen_public}" in token for token in argv):
                raise VerificationContractError(
                    f"checks[{index}].argv must invoke an entrypoint under {{frozen_public}}"
                )
            normalized["argv"] = list(argv)
            normalized["frozen_inputs"] = inputs
        else:
            normalized["evaluator_id"] = _safe_relative(
                raw.get("evaluator_id"), f"checks[{index}].evaluator_id"
            )
        checks.append(normalized)
    if not any(item["role"] == "target" and item["visibility"] == "public" for item in checks):
        raise VerificationContractError("at least one public target check is required")
    raw_adapters = value.get("required_adapters", [])
    if not isinstance(raw_adapters, list) or not all(isinstance(item, str) for item in raw_adapters):
        raise VerificationContractError("required_adapters must be a string array")
    adapters = sorted(set(raw_adapters))
    invalid = set(adapters) - REQUIRED_ADAPTERS
    if invalid:
        raise VerificationContractError(
            "unsupported required adapters: " + ", ".join(sorted(invalid))
        )
    if "hidden" in adapters and not any(item["visibility"] == "hidden" for item in checks):
        raise VerificationContractError("hidden adapter requires at least one hidden check")
    budgets = normalize_budget(value.get("budgets"))
    return {
        "schema_version": VERIFICATION_CONTRACT_VERSION,
        "contract_id": contract_id,
        "checks": checks,
        "required_adapters": adapters,
        "budgets": budgets,
        "source_path": str(path),
    }


def _copy_input(source: Path, destination: Path) -> list[dict[str, Any]]:
    if not source.exists():
        raise VerificationContractError(f"frozen input does not exist: {source}")
    records: list[dict[str, Any]] = []
    if source.is_file() and not source.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        records.append({"path": destination, "sha256": sha256_file(destination)})
        return records
    if not source.is_dir():
        raise VerificationContractError(f"frozen input must be a regular file or directory: {source}")
    for path in sorted(item for item in source.rglob("*") if item.is_file() and not item.is_symlink()):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        records.append({"path": target, "sha256": sha256_file(target)})
    if not records:
        raise VerificationContractError(f"frozen input directory is empty: {source}")
    return records


def freeze_verification_contract(
    contract: dict[str, Any],
    *,
    repository: Path,
    harness_root: Path,
    pair_root: Path,
) -> dict[str, Any]:
    contract_dir = Path(contract["source_path"]).parent
    public_root = pair_root / "floor" / "public"
    public_root.mkdir(parents=True, exist_ok=True)
    frozen_records: list[dict[str, Any]] = []
    frozen_checks: list[dict[str, Any]] = []
    copied: set[tuple[str, str]] = set()
    hidden_records: list[dict[str, Any]] = []
    for check in contract["checks"]:
        frozen = {key: value for key, value in check.items() if key != "frozen_inputs"}
        if check["visibility"] == "public":
            normalized_inputs: list[dict[str, str]] = []
            for item in check["frozen_inputs"]:
                key = (item["root"], item["path"])
                destination = public_root / item["root"] / item["path"]
                if key not in copied:
                    base = repository if item["root"] == "repository" else contract_dir
                    for record in _copy_input(base / item["path"], destination):
                        frozen_records.append(
                            {
                                "path": record["path"].relative_to(public_root).as_posix(),
                                "sha256": record["sha256"],
                            }
                        )
                    copied.add(key)
                normalized_inputs.append(item)
            frozen["frozen_inputs"] = normalized_inputs
        else:
            evaluator = harness_root / "evaluators" / "hidden" / check["evaluator_id"]
            if evaluator.is_file():
                frozen_evaluator = pair_root / "floor" / "hidden" / check["evaluator_id"]
                frozen_evaluator.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(evaluator, frozen_evaluator)
                hidden_records.append(
                    {
                        "evaluator_id": check["evaluator_id"],
                        "sha256": sha256_file(frozen_evaluator),
                    }
                )
            else:
                hidden_records.append(
                    {"evaluator_id": check["evaluator_id"], "status": "ABSENT"}
                )
        frozen_checks.append(frozen)
    adapter_inputs: list[dict[str, Any]] = []
    if "property" in contract["required_adapters"]:
        property_source = harness_root / "evaluators" / "property"
        property_target = pair_root / "floor" / "adapters" / "property"
        scripts = (
            sorted(path for path in property_source.glob("*.py") if path.is_file())
            if property_source.is_dir()
            else []
        )
        for script in scripts:
            target = property_target / script.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(script, target)
            adapter_inputs.append(
                {
                    "adapter": "property",
                    "path": script.name,
                    "sha256": sha256_file(target),
                }
            )
        if not scripts:
            adapter_inputs.append({"adapter": "property", "status": "ABSENT"})
    definition = {
        "schema_version": contract["schema_version"],
        "contract_id": contract["contract_id"],
        "checks": frozen_checks,
        "required_adapters": contract["required_adapters"],
        "budgets": contract["budgets"],
        "public_inputs": sorted(frozen_records, key=lambda item: item["path"]),
        "hidden_inputs": sorted(hidden_records, key=lambda item: item["evaluator_id"]),
        "adapter_inputs": adapter_inputs,
        "adapter_definitions": {
            "mutation": "python-deterministic-return-or-module-raise-v1"
            if "mutation" in contract["required_adapters"]
            else None,
            "property": "frozen-python-scripts-v1"
            if "property" in contract["required_adapters"]
            else None,
        },
    }
    definition_sha256 = sha256_bytes(canonical_json(definition).encode("utf-8"))
    public_view = {
        **{
            key: value
            for key, value in definition.items()
            if key not in {"hidden_inputs", "adapter_inputs"}
        },
        "checks": [
            item
            if item["visibility"] == "public"
            else {
                "id": item["id"],
                "role": item["role"],
                "visibility": "hidden",
                "evaluator_id": item["evaluator_id"],
            }
            for item in frozen_checks
        ],
        "hidden_input_digest_count": len(hidden_records),
        "protected_adapter_input_count": len(adapter_inputs),
        "floor_definition_sha256": definition_sha256,
    }
    return {
        "definition": definition,
        "public_view": public_view,
        "public_root": str(public_root),
        "floor_definition_sha256": definition_sha256,
    }


def verify_frozen_floor(
    definition: dict[str, Any],
    *,
    public_root: Path,
    pair_root: Path,
) -> dict[str, Any]:
    """Recheck immutable verification inputs against their recorded digests."""

    definition_sha256 = sha256_bytes(canonical_json(definition).encode("utf-8"))
    problems: list[str] = []
    for record in definition.get("public_inputs", []):
        path = public_root / record["path"]
        if not path.is_file():
            problems.append(f"public input absent: {record['path']}")
        elif sha256_file(path) != record["sha256"]:
            problems.append(f"public input changed: {record['path']}")
    hidden_root = pair_root / "floor" / "hidden"
    for record in definition.get("hidden_inputs", []):
        evaluator_id = record["evaluator_id"]
        path = hidden_root / evaluator_id
        if record.get("status") == "ABSENT":
            if path.exists():
                problems.append(f"hidden evaluator appeared after freeze: {evaluator_id}")
        elif not path.is_file():
            problems.append(f"hidden evaluator absent: {evaluator_id}")
        elif sha256_file(path) != record["sha256"]:
            problems.append(f"hidden evaluator changed: {evaluator_id}")
    property_root = pair_root / "floor" / "adapters" / "property"
    for record in definition.get("adapter_inputs", []):
        if record.get("adapter") != "property":
            continue
        if record.get("status") == "ABSENT":
            if property_root.is_dir() and any(property_root.glob("*.py")):
                problems.append("property evaluator appeared after freeze")
            continue
        path = property_root / record["path"]
        if not path.is_file():
            problems.append(f"property evaluator absent: {record['path']}")
        elif sha256_file(path) != record["sha256"]:
            problems.append(f"property evaluator changed: {record['path']}")
    return {
        "valid": not problems,
        "definition_sha256": definition_sha256,
        "problems": problems,
    }
