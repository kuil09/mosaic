"""Application workflows for evidence-first case investigation."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from mosaic_harness.admission import collect_verdicts, require_admission
from mosaic_harness.domain import (
    Outcome,
    SCHEMA_VERSION,
    constraint_templates,
    hypothesis_claims,
    outcome_options,
)
from mosaic_harness.historian import EventLedger
from mosaic_harness.scanner import scan_repository
from mosaic_harness.schema import check_schema_drift
from mosaic_harness.storage import CaseStore
from mosaic_harness.util import (
    canonical_json,
    git_commit,
    sha256_bytes,
    sha256_file,
    utc_now,
    validate_case_id,
)
from mosaic_harness.validation import validate_decision_pack


RIVALS_REQUIRED_REFUTED: dict[str, set[str]] = {
    "H-CODE-DEFECT": {
        "H-EXPECTED-BEHAVIOR",
        "H-OBSERVABILITY-GAP",
        "H-CONFIGURATION",
        "H-DOCUMENTATION",
        "H-OPERATIONAL",
        "H-POLICY-CONFLICT",
        "H-INVALID-REPORT",
    },
    "H-EXPECTED-BEHAVIOR": {"H-CODE-DEFECT", "H-POLICY-CONFLICT"},
    "H-CONFIGURATION": {"H-CODE-DEFECT", "H-OPERATIONAL"},
    "H-DOCUMENTATION": {"H-CODE-DEFECT", "H-POLICY-CONFLICT"},
    "H-OPERATIONAL": {"H-CODE-DEFECT", "H-CONFIGURATION"},
}


def _harness_root(workspace: Path) -> Path:
    return workspace.resolve() / ".harness"


def _ensure_runtime(workspace: Path) -> tuple[Path, dict[str, Any]]:
    workspace = workspace.resolve()
    harness_root = _harness_root(workspace)
    directories = (
        "cases",
        "historian/events",
        "historian/projections",
        "historian/conflicts",
        "historian/invalidations",
        "evidence/observations",
        "evidence/counterexamples",
        "evidence/provenance",
        "experiments/candidates",
        "experiments/results",
        "experiments/zero-change",
        "evaluators/public",
        "evaluators/hidden",
        "evaluators/mutation",
        "evaluators/property",
        "future/scenarios",
        "future/forecasts",
        "future/tournaments",
    )
    for relative in directories:
        (harness_root / relative).mkdir(parents=True, exist_ok=True)
    state = EventLedger(harness_root).verify()
    return harness_root, state


def ensure_runtime(workspace: Path) -> tuple[Path, dict[str, Any]]:
    return _ensure_runtime(workspace)


def initialize_workspace(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    harness_root, state = _ensure_runtime(workspace)
    template_root = resources.files("mosaic_harness").joinpath("templates")
    template_mapping = {
        "AGENTS.md": workspace / "AGENTS.md",
        "CLAUDE.md": workspace / "CLAUDE.md",
        "GEMINI.md": workspace / "GEMINI.md",
        "harness/README.md": harness_root / "README.md",
        "harness/constitution/normal.md": harness_root / "constitution" / "normal.md",
        "harness/constitution/maintenance.md": harness_root / "constitution" / "maintenance.md",
        "harness/constitution/permissions.yaml": harness_root / "constitution" / "permissions.yaml",
        "harness/claims/schemas/decision-pack.schema.json": harness_root / "claims" / "schemas" / "decision-pack.schema.json",
        "harness/future/scenarios/SCHEMA.md": harness_root / "future" / "scenarios" / "SCHEMA.md",
    }
    created: list[str] = []
    for source_relative, destination in template_mapping.items():
        if destination.exists():
            continue
        source = template_root.joinpath(*source_relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        created.append(str(destination.relative_to(workspace)))
    return {
        "workspace": str(workspace),
        "harness_root": str(harness_root),
        "ledger": state,
        "created_templates": created,
    }


def _issue_title(content: str, case_id: str) -> str:
    for line in content.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:200]
    return case_id


def _revision_id(commit: str | None, inventory: dict[str, Any]) -> str:
    tree = inventory["fingerprint_sha256"]
    return f"git:{commit}+tree:{tree}" if commit else f"tree:{tree}"


def _projection_content(pack: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in pack.items() if key != "provenance"}


def _projection_digest(pack: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(_projection_content(pack)).encode("utf-8"))


def _anchor_projection(
    ledger: EventLedger,
    pack: dict[str, Any],
    case_id: str,
    actor: str,
) -> dict[str, Any]:
    projection = _projection_content(pack)
    return ledger.append(
        "projection_materialized",
        case_id,
        {
            "schema_version": pack["schema_version"],
            "projection_sha256": _projection_digest(pack),
            "projection": projection,
        },
        actor=actor,
    )


def investigate(
    workspace: Path,
    case_id: str,
    issue_path: Path,
    repository: Path,
    *,
    max_files: int = 500,
    replace: bool = False,
) -> tuple[dict[str, Any], Path]:
    validate_case_id(case_id)
    _ensure_runtime(workspace)
    harness_root = _harness_root(workspace)
    store = CaseStore(harness_root)
    if store.exists(case_id) and not replace:
        raise ValueError(f"case already exists: {case_id}; pass --replace to create a new projection")
    issue_path = issue_path.resolve()
    if not issue_path.is_file():
        raise ValueError(f"issue file does not exist: {issue_path}")
    content = issue_path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError("issue file is empty")
    repository = repository.resolve()
    commit = git_commit(repository)
    inventory = scan_repository(repository, max_files=max_files)
    revision = _revision_id(commit, inventory)
    timestamp = utc_now()
    claims = hypothesis_claims(str(repository), commit, revision)
    pack: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "generated_at": timestamp,
        "updated_at": timestamp,
        "issue": {
            "title": _issue_title(content, case_id),
            "statement": content,
            "source": str(issue_path),
            "sha256": sha256_bytes(content.encode("utf-8")),
            "epistemic_status": "asserted",
        },
        "scope": {
            "repository": str(repository),
            "base_commit": commit,
            "base_revision": revision,
            "inventory": inventory,
        },
        "constraints": constraint_templates(content),
        "claims": claims,
        "evidence": [],
        "counterexamples": [],
        "candidate_outcomes": outcome_options(),
        "outcome": {
            "type": Outcome.INSUFFICIENT_EVIDENCE.value,
            "status": "provisional",
            "source": "harness",
            "rationale": "The issue is an unverified claim and no discriminating evidence has been recorded.",
            "conditions": [
                "Collect fresh evidence against at least two material rival explanations.",
                "Define a reversible verification step before proposing a state-changing intervention.",
            ],
            "evidence_refs": [],
        },
        "verification_plan": [],
        "rollback_plan": [
            {
                "id": "RB-1",
                "action": "Do not perform a state-changing intervention until its rollback trigger and procedure are recorded.",
                "status": "required",
            }
        ],
        "observation_plan": [
            {
                "id": "OP-1",
                "observation": "Record the expected signal that would reveal the selected intervention is wrong.",
                "status": "required",
            }
        ],
        "unresolved_questions": [
            "What observation would falsify the issue's causal interpretation?",
            "Which policy or invariant is authoritative for the reported behavior?",
            "Which non-code explanation is cheapest to rule out safely?",
        ],
        "provenance": {
            "projection_of": "historian/events/events.jsonl",
            "event_head": None,
        },
    }
    validate_decision_pack(pack)
    ledger = EventLedger(harness_root)
    issue_event = ledger.append(
        "issue_received",
        case_id,
        {
            "title": pack["issue"]["title"],
            "source": str(issue_path),
            "sha256": pack["issue"]["sha256"],
            "repository": str(repository),
            "base_commit": commit,
            "base_revision": revision,
        },
    )
    head = issue_event
    for claim in claims:
        head = ledger.append(
            "claim_proposed",
            case_id,
            {
                "claim_id": claim["id"],
                "statement": claim["statement"],
                "falsifier": claim["falsifier"],
            },
        )
    head = ledger.append(
        "decision_proposed",
        case_id,
        {
            "outcome": pack["outcome"]["type"],
            "rationale": pack["outcome"]["rationale"],
        },
    )
    head = _anchor_projection(ledger, pack, case_id, "mosaic")
    pack["provenance"]["event_head"] = head["hash"]
    path = store.save(pack)
    return pack, path


def _evidence_index(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in pack["evidence"]}


def _fresh(item: dict[str, Any], base_revision: str) -> bool:
    return item.get("valid_for_revision") == base_revision


def _refresh_claim_status(claim: dict[str, Any], pack: dict[str, Any]) -> None:
    index = _evidence_index(pack)
    base_revision = pack["scope"]["base_revision"]
    invalidated_at = claim.get("invalidated_at")
    supporting = [
        index[reference]
        for reference in claim["evidence_refs"]
        if reference in index
        and _fresh(index[reference], base_revision)
        and (not invalidated_at or str(index[reference].get("observed_at") or "") > str(invalidated_at))
    ]
    opposing = [
        index[reference]
        for reference in claim["counterevidence_refs"]
        if reference in index and _fresh(index[reference], base_revision)
    ]
    strong_support = sum(item["strength"] == "strong" for item in supporting)
    moderate_support = sum(item["strength"] == "moderate" for item in supporting)
    strong_opposition = sum(item["strength"] == "strong" for item in opposing)
    moderate_opposition = sum(item["strength"] == "moderate" for item in opposing)

    if supporting and opposing:
        claim["status"] = "unresolved"
    elif strong_opposition >= 1 or moderate_opposition >= 2:
        claim["status"] = "refuted"
    elif strong_support >= 1 or moderate_support >= 2:
        claim["status"] = "supported"
    elif supporting:
        claim["status"] = "survived"
    elif opposing:
        claim["status"] = "unresolved"
    else:
        claim["status"] = "proposed"

    independent_attempts = len(
        {
            (item["source_type"], item["source_ref"])
            for item in supporting + opposing
        }
    )
    claim["confidence"] = (
        "high" if independent_attempts >= 3 else "medium" if independent_attempts == 2 else "low" if independent_attempts == 1 else "speculation"
    )


def add_evidence(
    workspace: Path,
    case_id: str,
    claim_id: str,
    *,
    direction: str,
    strength: str,
    summary: str,
    source_type: str,
    source_ref: str,
    artifact_path: Path | None = None,
    valid_for_revision: str | None = None,
    actor: str = "mosaic",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if direction not in {"supporting", "opposing", "neutral"}:
        raise ValueError("direction must be supporting, opposing, or neutral")
    if strength not in {"weak", "moderate", "strong"}:
        raise ValueError("strength must be weak, moderate, or strong")
    if not summary.strip():
        raise ValueError("summary must not be empty")
    if not source_type.strip() or not source_ref.strip():
        raise ValueError("source type and source reference must not be empty")
    harness_root = _harness_root(workspace)
    store = CaseStore(harness_root)
    pack = store.load(case_id)
    claim = next((item for item in pack["claims"] if item["id"] == claim_id), None)
    if claim is None:
        raise ValueError(f"claim not found: {claim_id}")
    artifact_hash: str | None = None
    if artifact_path is not None:
        artifact_path = artifact_path.resolve()
        if not artifact_path.is_file():
            raise ValueError(f"evidence artifact does not exist: {artifact_path}")
        artifact_hash = sha256_file(artifact_path)
        source_ref = str(artifact_path)
    base_commit = pack["scope"].get("base_commit")
    base_revision = pack["scope"]["base_revision"]
    evidence_revision = (
        valid_for_revision if valid_for_revision is not None else base_revision
    )
    evidence_id = f"E-{len(pack['evidence']) + 1:04d}"
    evidence = {
        "id": evidence_id,
        "claim_id": claim_id,
        "direction": direction,
        "strength": strength,
        "summary": summary,
        "source_type": source_type,
        "source_ref": source_ref,
        "artifact_sha256": artifact_hash,
        "observed_at": utc_now(),
        "valid_for_commit": base_commit if evidence_revision == base_revision else None,
        "valid_for_revision": evidence_revision,
        "freshness": "current" if evidence_revision == base_revision else "stale",
    }
    ledger = EventLedger(harness_root)
    event = ledger.append("evidence_observed", case_id, evidence, actor=actor)
    pack["evidence"].append(evidence)
    if direction == "supporting":
        claim["evidence_refs"].append(evidence_id)
    elif direction == "opposing":
        claim["counterevidence_refs"].append(evidence_id)
    _refresh_claim_status(claim, pack)
    status_event = ledger.append(
        "claim_status_updated",
        case_id,
        {
            "claim_id": claim_id,
            "status": claim["status"],
            "confidence": claim["confidence"],
            "trigger_event": event["hash"],
        },
        actor=actor,
    )
    pack["updated_at"] = utc_now()
    validate_decision_pack(pack)
    projection_event = _anchor_projection(ledger, pack, case_id, actor)
    pack["provenance"]["event_head"] = projection_event["hash"]
    store.save(pack)
    return pack, evidence


def _automatic_outcome(pack: dict[str, Any]) -> tuple[Outcome, str, list[str]]:
    claims = {claim["id"]: claim for claim in pack["claims"]}
    supported = [claim for claim in claims.values() if claim["status"] == "supported"]
    supported_ids = {claim["id"] for claim in supported}

    if "H-POLICY-CONFLICT" in supported_ids:
        return (
            Outcome.POLICY_CONFLICT,
            "Fresh evidence supports an applicable policy or invariant conflict.",
            claims["H-POLICY-CONFLICT"]["evidence_refs"],
        )
    if "H-INVALID-REPORT" in supported_ids:
        return (
            Outcome.ISSUE_REJECTED,
            "Fresh evidence refutes a material premise or reproduction condition in the issue.",
            claims["H-INVALID-REPORT"]["evidence_refs"],
        )
    if "H-OBSERVABILITY-GAP" in supported_ids:
        return (
            Outcome.INSTRUMENT_FIRST,
            "Available observations cannot discriminate safely between the active explanations.",
            claims["H-OBSERVABILITY-GAP"]["evidence_refs"],
        )
    if len(supported) != 1:
        reason = (
            "No hypothesis has sufficient fresh support."
            if not supported
            else "Multiple dispositions remain supported by fresh evidence."
        )
        return Outcome.INSUFFICIENT_EVIDENCE, reason, []

    winner = supported[0]
    required_rivals = RIVALS_REQUIRED_REFUTED.get(winner["id"], set())
    unresolved_rivals = sorted(
        rival_id
        for rival_id in required_rivals
        if claims[rival_id]["status"] != "refuted"
    )
    if unresolved_rivals:
        return (
            Outcome.INSUFFICIENT_EVIDENCE,
            "A supported hypothesis does not yet exclude material rivals: "
            + ", ".join(unresolved_rivals),
            winner["evidence_refs"],
        )
    return (
        Outcome(winner["linked_outcome"]),
        f"Fresh evidence supports {winner['id']} and its required material rivals are refuted.",
        winner["evidence_refs"],
    )


def propose(workspace: Path, case_id: str, actor: str = "mosaic") -> dict[str, Any]:
    harness_root = _harness_root(workspace)
    store = CaseStore(harness_root)
    pack = store.load(case_id)
    outcome, rationale, evidence_refs = _automatic_outcome(pack)
    proposal = {
        "type": outcome.value,
        "status": "provisional",
        "source": "harness",
        "rationale": rationale,
        "conditions": [],
        "evidence_refs": evidence_refs,
    }
    if outcome == Outcome.INSUFFICIENT_EVIDENCE:
        proposal["conditions"] = [
            "Run a fresh probe that distinguishes the material rival hypotheses.",
            "Do not treat a passing floor test as causal evidence by itself.",
        ]
    event = EventLedger(harness_root).append(
        "decision_proposed", case_id, proposal, actor=actor
    )
    pack["outcome"] = proposal
    pack["updated_at"] = utc_now()
    validate_decision_pack(pack)
    projection_event = _anchor_projection(
        EventLedger(harness_root), pack, case_id, actor
    )
    pack["provenance"]["event_head"] = projection_event["hash"]
    store.save(pack)
    return pack


def challenge(
    workspace: Path,
    case_id: str,
    actor: str = "challenger",
    *,
    emit_evaluators: bool = False,
) -> dict[str, Any]:
    harness_root = _harness_root(workspace)
    store = CaseStore(harness_root)
    pack = store.load(case_id)
    if emit_evaluators:
        from mosaic_harness.verifiers import emit_challenger_evaluators

        emit_challenger_evaluators(harness_root)
    existing_claim_ids = {item["claim_id"] for item in pack["counterexamples"]}
    added: list[dict[str, Any]] = []
    for claim in pack["claims"]:
        if claim["status"] == "refuted" or claim["id"] in existing_claim_ids:
            continue
        counterexample = {
            "id": f"CE-{len(pack['counterexamples']) + len(added) + 1:04d}",
            "claim_id": claim["id"],
            "description": claim["falsifier"]["description"],
            "status": "not_run",
        }
        added.append(counterexample)
        pack["verification_plan"].append(
            {
                "id": f"VP-{len(pack['verification_plan']) + 1:04d}",
                "claim_id": claim["id"],
                "method": claim["falsifier"]["description"],
                "prediction_if_true": claim["predictions"]["if_true"],
                "prediction_if_false": claim["predictions"]["if_false"],
                "admissibility": {
                    "read_only_preferred": True,
                    "requires_authority_review": True,
                    "reversibility": "unknown",
                },
                "status": "planned",
            }
        )
    pack["counterexamples"].extend(added)
    event = EventLedger(harness_root).append(
        "counterexamples_generated",
        case_id,
        {"counterexample_ids": [item["id"] for item in added]},
        actor=actor,
    )
    pack["updated_at"] = utc_now()
    validate_decision_pack(pack)
    projection_event = _anchor_projection(
        EventLedger(harness_root), pack, case_id, actor
    )
    pack["provenance"]["event_head"] = projection_event["hash"]
    store.save(pack)
    return pack


def record_experiment(
    workspace: Path,
    case_id: str,
    experiment: dict[str, Any],
    actor: str = "verifier",
) -> dict[str, Any]:
    harness_root = _harness_root(workspace)
    store = CaseStore(harness_root)
    pack = store.load(case_id)
    experiments = [
        item
        for item in pack.get("experiments", [])
        if item.get("run_id") != experiment.get("run_id")
    ]
    experiments.append(experiment)
    pack["experiments"] = experiments
    pack["updated_at"] = utc_now()
    validate_decision_pack(pack)
    projection_event = _anchor_projection(EventLedger(harness_root), pack, case_id, actor)
    pack["provenance"]["event_head"] = projection_event["hash"]
    store.save(pack)
    return pack


def decide(
    workspace: Path,
    case_id: str,
    outcome: Outcome,
    rationale: str,
    actor: str,
    conditions: list[str] | None = None,
    *,
    override: bool = False,
) -> dict[str, Any]:
    if not rationale.strip():
        raise ValueError("rationale must not be empty")
    if not actor.strip():
        raise ValueError("actor must not be empty")
    harness_root = _harness_root(workspace)
    store = CaseStore(harness_root)
    pack = store.load(case_id)
    admission = require_admission(
        outcome,
        collect_verdicts(harness_root, case_id),
        override=override,
        pack=pack,
    )
    pack["admission"] = admission
    if admission["status"] == "overridden":
        questions = list(pack.get("unresolved_questions", []))
        failures = ", ".join(admission["floor_failures"]) or "none recorded"
        note = f"Override accepted {outcome.value} despite floor failures: {failures}."
        if note not in questions:
            questions.append(note)
        pack["unresolved_questions"] = questions
    decision = {
        "type": outcome.value,
        "status": "accepted",
        "source": "human",
        "rationale": rationale,
        "conditions": conditions or [],
        "evidence_refs": [item["id"] for item in pack["evidence"]],
        "approved_by": actor,
    }
    event = EventLedger(harness_root).append(
        "decision_accepted", case_id, decision, actor=actor
    )
    pack["outcome"] = decision
    pack["updated_at"] = utc_now()
    validate_decision_pack(pack)
    projection_event = _anchor_projection(
        EventLedger(harness_root), pack, case_id, actor
    )
    pack["provenance"]["event_head"] = projection_event["hash"]
    store.save(pack)
    return pack


def verify_workspace(workspace: Path, case_id: str | None = None) -> dict[str, Any]:
    harness_root = _harness_root(workspace)
    ledger = EventLedger(harness_root)
    events = ledger.read()
    result: dict[str, Any] = {"ledger": ledger.verify_events(events)}
    result["schema"] = check_schema_drift(workspace)
    from mosaic_harness.memory import check_conflicts

    check_conflicts(harness_root)
    if case_id is not None:
        pack = CaseStore(harness_root).load(case_id)
        result["decision_pack"] = validate_decision_pack(pack)
        projection_events = [
            event
            for event in events
            if event.get("case_id") == case_id
            and event.get("type") == "projection_materialized"
        ]
        current_head = pack.get("provenance", {}).get("event_head")
        latest = projection_events[-1] if projection_events else None
        if latest is None:
            raise ValueError(f"case has no projection anchor: {case_id}")
        if current_head != latest["hash"]:
            raise ValueError(f"decision pack is not the latest anchored projection: {case_id}")
        expected_digest = latest["payload"].get("projection_sha256")
        actual_digest = _projection_digest(pack)
        if actual_digest != expected_digest:
            raise ValueError(f"decision pack projection hash mismatch: {case_id}")
        result["projection_integrity"] = {
            "valid": True,
            "event_hash": current_head,
            "projection_sha256": actual_digest,
        }
    return result


def rebuild_case(workspace: Path, case_id: str) -> tuple[dict[str, Any], Path]:
    harness_root = _harness_root(workspace)
    ledger = EventLedger(harness_root)
    events = ledger.read()
    ledger.verify_events(events)
    projection_events = [
        event
        for event in events
        if event.get("case_id") == case_id
        and event.get("type") == "projection_materialized"
    ]
    if not projection_events:
        raise ValueError(f"case has no projection snapshot: {case_id}")
    latest = projection_events[-1]
    projection = latest.get("payload", {}).get("projection")
    if not isinstance(projection, dict):
        raise ValueError(f"latest projection event has no rebuildable snapshot: {case_id}")
    expected_digest = latest["payload"].get("projection_sha256")
    actual_digest = sha256_bytes(canonical_json(projection).encode("utf-8"))
    if actual_digest != expected_digest:
        raise ValueError(f"projection snapshot hash mismatch in ledger: {case_id}")
    pack = {
        **projection,
        "provenance": {
            "projection_of": "historian/events/events.jsonl",
            "event_head": latest["hash"],
        },
    }
    validate_decision_pack(pack)
    path = CaseStore(harness_root).save(pack)
    return pack, path


def render_pack(pack: dict[str, Any]) -> str:
    return json.dumps(pack, ensure_ascii=False, indent=2) + "\n"
