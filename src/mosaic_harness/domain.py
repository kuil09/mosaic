"""Domain contracts for Mosaic decision packs."""

from __future__ import annotations

from enum import Enum
from typing import Any


SCHEMA_VERSION = "1.0.0"


class Outcome(str, Enum):
    CODE_CHANGE = "CODE_CHANGE"
    NO_CHANGE = "NO_CHANGE"
    INSTRUMENT_FIRST = "INSTRUMENT_FIRST"
    CONFIGURATION_CHANGE = "CONFIGURATION_CHANGE"
    DOCUMENTATION_CHANGE = "DOCUMENTATION_CHANGE"
    OPERATIONAL_ACTION = "OPERATIONAL_ACTION"
    POLICY_CONFLICT = "POLICY_CONFLICT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    ISSUE_REJECTED = "ISSUE_REJECTED"


HYPOTHESIS_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "H-CODE-DEFECT",
        "statement": "A defect in application code causes the reported behavior.",
        "outcome": Outcome.CODE_CHANGE.value,
        "falsifier": "Reproduce the behavior with the suspected code path bypassed, or show that the implementation satisfies the authoritative contract.",
        "risk_domains": ["application", "regression"],
    },
    {
        "id": "H-EXPECTED-BEHAVIOR",
        "statement": "The reported behavior is expected under the authoritative product policy.",
        "outcome": Outcome.NO_CHANGE.value,
        "falsifier": "Find an authoritative policy or accepted behavior contract that contradicts the observed behavior.",
        "risk_domains": ["product-policy"],
    },
    {
        "id": "H-OBSERVABILITY-GAP",
        "statement": "Available observations cannot distinguish the plausible causes safely.",
        "outcome": Outcome.INSTRUMENT_FIRST.value,
        "falsifier": "Produce fresh, attributable observations that discriminate between the active causal hypotheses.",
        "risk_domains": ["diagnosis", "observability"],
    },
    {
        "id": "H-CONFIGURATION",
        "statement": "Configuration or feature state causes the reported behavior without an application code defect.",
        "outcome": Outcome.CONFIGURATION_CHANGE.value,
        "falsifier": "Reproduce the behavior with verified-good configuration while holding application code constant.",
        "risk_domains": ["configuration", "operations"],
    },
    {
        "id": "H-DOCUMENTATION",
        "statement": "The implementation is correct but user-facing documentation or explanatory text is wrong.",
        "outcome": Outcome.DOCUMENTATION_CHANGE.value,
        "falsifier": "Show that documentation matches the authoritative contract and the implementation violates it.",
        "risk_domains": ["documentation", "user-expectation"],
    },
    {
        "id": "H-OPERATIONAL",
        "statement": "A reversible operational action can resolve the condition without changing code or durable configuration.",
        "outcome": Outcome.OPERATIONAL_ACTION.value,
        "falsifier": "Show that the condition persists after the proposed operational action under the same observed boundary.",
        "risk_domains": ["operations", "availability"],
    },
    {
        "id": "H-POLICY-CONFLICT",
        "statement": "The requested behavior conflicts with an applicable policy or invariant.",
        "outcome": Outcome.POLICY_CONFLICT.value,
        "falsifier": "Show that the request and the applicable authoritative policy can both be satisfied within the stated boundary.",
        "risk_domains": ["policy", "compliance"],
    },
    {
        "id": "H-INVALID-REPORT",
        "statement": "A material premise or reproduction condition in the issue is false.",
        "outcome": Outcome.ISSUE_REJECTED.value,
        "falsifier": "Reproduce the issue from the stated conditions with independently verified inputs.",
        "risk_domains": ["triage"],
    },
)


def constraint_templates(issue_statement: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "C-TARGET",
            "role": "target",
            "force": "hard",
            "epistemic_status": "given",
            "mutability": "fixed",
            "provenance": "issue",
            "statement": f"Reach an evidence-backed disposition for: {issue_statement}",
            "evidence_condition": "The selected outcome is linked to fresh evidence and unresolved rival explanations are explicit.",
            "status": "unresolved",
            "related_hypotheses": [template["id"] for template in HYPOTHESIS_TEMPLATES],
        },
        {
            "id": "C-PRESERVE-BEHAVIOR",
            "role": "preservation",
            "force": "hard",
            "epistemic_status": "provisional",
            "mutability": "unknown",
            "provenance": "harness",
            "statement": "Preserve unrelated behavior and applicable domain invariants.",
            "evidence_condition": "No applicable floor check, invariant check, or regression probe fails.",
            "status": "unresolved",
            "related_hypotheses": [],
        },
        {
            "id": "C-BOUNDARY",
            "role": "boundary",
            "force": "hard",
            "epistemic_status": "observed",
            "mutability": "fixed",
            "provenance": "repository",
            "statement": "Limit investigation and proposed interventions to the recorded repository and case artifacts.",
            "evidence_condition": "Every observation and intervention declares its source and repository revision.",
            "status": "persistent",
            "related_hypotheses": [],
        },
        {
            "id": "C-RESOURCE",
            "role": "resource",
            "force": "soft",
            "epistemic_status": "provisional",
            "mutability": "changeable",
            "provenance": "harness",
            "statement": "Prefer the smallest reversible investigation that can discriminate between active hypotheses.",
            "evidence_condition": "The verification plan records expected information value, risk, and reversibility.",
            "status": "persistent",
            "related_hypotheses": [],
        },
    ]


def hypothesis_claims(
    repository: str, commit: str | None, revision: str
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for template in HYPOTHESIS_TEMPLATES:
        claims.append(
            {
                "id": template["id"],
                "kind": "hypothesis",
                "statement": template["statement"],
                "status": "proposed",
                "source_layer": "L2",
                "confidence": "speculation",
                "scope": {
                    "repository": repository,
                    "valid_for_commit": commit,
                    "valid_for_revision": revision,
                },
                "evidence_refs": [],
                "counterevidence_refs": [],
                "falsifier": {"description": template["falsifier"]},
                "predictions": {
                    "if_true": f"Fresh evidence will uniquely support {template['id']} within the recorded boundary.",
                    "if_false": f"A severe probe will refute {template['id']} or support a rival explanation.",
                },
                "linked_outcome": template["outcome"],
                "risk": {
                    "blast_radius": "unknown",
                    "affected_domains": template["risk_domains"],
                },
                "rollback": {"strategy": "Define before any state-changing intervention."},
            }
        )
    return claims


def outcome_options() -> list[dict[str, Any]]:
    requirements = {
        Outcome.CODE_CHANGE: "A code-causal hypothesis survives a severe probe and material non-code rivals are refuted.",
        Outcome.NO_CHANGE: "Fresh evidence shows current behavior satisfies the authoritative contract.",
        Outcome.INSTRUMENT_FIRST: "Existing evidence cannot safely distinguish material rivals and a discriminating observation is available.",
        Outcome.CONFIGURATION_CHANGE: "Fresh evidence isolates configuration while application code is held constant.",
        Outcome.DOCUMENTATION_CHANGE: "Implementation and policy agree while user-facing documentation conflicts.",
        Outcome.OPERATIONAL_ACTION: "A reversible operational intervention addresses the observed condition.",
        Outcome.POLICY_CONFLICT: "An applicable policy or invariant conflicts with the request.",
        Outcome.INSUFFICIENT_EVIDENCE: "No unique disposition is justified by fresh evidence.",
        Outcome.ISSUE_REJECTED: "A material issue premise is independently refuted.",
    }
    return [
        {"type": outcome.value, "evidence_requirement": requirements[outcome]}
        for outcome in Outcome
    ]
