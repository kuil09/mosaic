"""Dependency-free validation for the public Decision Pack contract."""

from __future__ import annotations

from typing import Any

from mosaic_harness.domain import Outcome, SCHEMA_VERSION


class DecisionPackValidationError(ValueError):
    pass


def _require(mapping: dict[str, Any], key: str, expected: type, errors: list[str]) -> Any:
    value = mapping.get(key)
    if not isinstance(value, expected):
        errors.append(f"{key} must be {expected.__name__}")
    return value


def validate_decision_pack(pack: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if pack.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must equal {SCHEMA_VERSION}")
    _require(pack, "case_id", str, errors)
    _require(pack, "issue", dict, errors)
    _require(pack, "scope", dict, errors)
    constraints = _require(pack, "constraints", list, errors)
    claims = _require(pack, "claims", list, errors)
    evidence = _require(pack, "evidence", list, errors)
    _require(pack, "verification_plan", list, errors)
    _require(pack, "rollback_plan", list, errors)
    _require(pack, "observation_plan", list, errors)
    _require(pack, "unresolved_questions", list, errors)
    outcome = _require(pack, "outcome", dict, errors)

    if isinstance(outcome, dict):
        allowed_outcomes = {member.value for member in Outcome}
        if outcome.get("type") not in allowed_outcomes:
            errors.append("outcome.type is not a supported disposition")

    claim_ids: set[str] = set()
    if isinstance(claims, list):
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                errors.append(f"claims[{index}] must be object")
                continue
            claim_id = claim.get("id")
            if not isinstance(claim_id, str):
                errors.append(f"claims[{index}].id must be string")
            elif claim_id in claim_ids:
                errors.append(f"duplicate claim id: {claim_id}")
            else:
                claim_ids.add(claim_id)

    evidence_ids: set[str] = set()
    if isinstance(evidence, list):
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"evidence[{index}] must be object")
                continue
            evidence_id = item.get("id")
            if not isinstance(evidence_id, str):
                errors.append(f"evidence[{index}].id must be string")
            elif evidence_id in evidence_ids:
                errors.append(f"duplicate evidence id: {evidence_id}")
            else:
                evidence_ids.add(evidence_id)

    if isinstance(evidence, list):
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                continue
            if item.get("claim_id") not in claim_ids:
                errors.append(
                    f"evidence[{index}].claim_id does not reference an existing claim"
                )

    if isinstance(claims, list):
        evidence_by_id = {
            item.get("id"): item
            for item in evidence
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        } if isinstance(evidence, list) else {}
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                continue
            for reference in claim.get("evidence_refs", []):
                item = evidence_by_id.get(reference)
                if item is None:
                    errors.append(f"claims[{index}] references missing evidence: {reference}")
                elif item.get("claim_id") != claim.get("id") or item.get("direction") != "supporting":
                    errors.append(f"claims[{index}] has invalid supporting evidence reference: {reference}")
            for reference in claim.get("counterevidence_refs", []):
                item = evidence_by_id.get(reference)
                if item is None:
                    errors.append(f"claims[{index}] references missing counterevidence: {reference}")
                elif item.get("claim_id") != claim.get("id") or item.get("direction") != "opposing":
                    errors.append(f"claims[{index}] has invalid opposing evidence reference: {reference}")

    if isinstance(constraints, list):
        roles = {
            item.get("role")
            for item in constraints
            if isinstance(item, dict)
        }
        missing_roles = {"target", "preservation", "boundary", "resource"} - roles
        if missing_roles:
            errors.append(f"missing constraint roles: {', '.join(sorted(missing_roles))}")

    if isinstance(outcome, dict) and isinstance(evidence, list):
        for reference in outcome.get("evidence_refs", []):
            if reference not in evidence_ids:
                errors.append(f"outcome references missing evidence: {reference}")

    if errors:
        raise DecisionPackValidationError("; ".join(errors))
    return {
        "valid": True,
        "case_id": pack["case_id"],
        "claim_count": len(claims),
        "evidence_count": len(evidence),
        "outcome": outcome["type"],
    }
