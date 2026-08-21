"""Single-worker, event-sourced execution state for Mosaic V3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mosaic_harness.admission import require_v2_admission
from mosaic_harness.compat import require_writable_pack
from mosaic_harness.domain import Outcome
from mosaic_harness.executor import load_json, manifest_path, save_json
from mosaic_harness.historian import EventLedger
from mosaic_harness.pair import PairStateError, freeze_code_run, load_pair
from mosaic_harness.storage import CaseStore
from mosaic_harness.util import canonical_json, sha256_bytes, utc_now
from mosaic_harness.workflow import _anchor_projection, ensure_runtime
from mosaic_harness.workspace import snapshot_tree


WORK_CONTRACT_VERSION = "1.0.0"
WORK_STATE_VERSION = "1.0.0"
TASK_KINDS = {"investigate", "implement"}
CONSTRAINT_ROLES = {"target", "preservation", "boundary", "resource"}
CONSTRAINT_FORCES = {"hard", "soft"}
TERMINAL_OUTCOMES = {member.value for member in Outcome}


class WorkContractError(ValueError):
    """Raised when a work contract is invalid or an immutable field changes."""


class WorkStateError(ValueError):
    """Raised when a work state transition is not admissible."""


def work_contract_path(harness_root: Path, case_id: str) -> Path:
    return harness_root / "cases" / case_id / "work-contract.json"


def work_state_path(harness_root: Path, case_id: str) -> Path:
    return harness_root / "cases" / case_id / "work-state.json"


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkContractError(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise WorkContractError(f"{field} must be a string array")
    return list(value)


def _validate_dag(tasks: list[dict[str, Any]]) -> None:
    graph = {item["id"]: item["depends_on"] for item in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise WorkContractError("task graph must be acyclic")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in graph[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in graph:
        visit(task_id)


def validate_work_contract(value: dict[str, Any], case_id: str) -> dict[str, Any]:
    if value.get("schema_version") != WORK_CONTRACT_VERSION:
        raise WorkContractError(f"schema_version must equal {WORK_CONTRACT_VERSION}")
    contract_id = _nonempty(value.get("contract_id"), "contract_id")
    if value.get("case_id") != case_id:
        raise WorkContractError("contract case_id must match the case")
    objective = _nonempty(value.get("objective"), "objective")
    non_goals = _string_list(value.get("non_goals"), "non_goals")

    raw_constraints = value.get("constraints")
    if not isinstance(raw_constraints, list) or not raw_constraints:
        raise WorkContractError("constraints must be a non-empty array")
    constraints: list[dict[str, Any]] = []
    constraint_ids: set[str] = set()
    for index, item in enumerate(raw_constraints):
        if not isinstance(item, dict):
            raise WorkContractError(f"constraints[{index}] must be an object")
        constraint_id = _nonempty(item.get("id"), f"constraints[{index}].id")
        if constraint_id in constraint_ids:
            raise WorkContractError(f"duplicate constraint id: {constraint_id}")
        constraint_ids.add(constraint_id)
        if item.get("role") not in CONSTRAINT_ROLES:
            raise WorkContractError(f"constraints[{index}].role is invalid")
        if item.get("force") not in CONSTRAINT_FORCES:
            raise WorkContractError(f"constraints[{index}].force is invalid")
        constraints.append(
            {
                "id": constraint_id,
                "role": item["role"],
                "force": item["force"],
                "statement": _nonempty(item.get("statement"), f"constraints[{index}].statement"),
                "evidence_condition": _nonempty(
                    item.get("evidence_condition"),
                    f"constraints[{index}].evidence_condition",
                ),
            }
        )
    if not {"target", "preservation", "boundary", "resource"}.issubset(
        {item["role"] for item in constraints}
    ):
        raise WorkContractError("constraints must include target, preservation, boundary, and resource roles")

    raw_criteria = value.get("acceptance_criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise WorkContractError("acceptance_criteria must be a non-empty array")
    criteria: list[dict[str, Any]] = []
    criterion_ids: set[str] = set()
    for index, item in enumerate(raw_criteria):
        if not isinstance(item, dict):
            raise WorkContractError(f"acceptance_criteria[{index}] must be an object")
        criterion_id = _nonempty(item.get("id"), f"acceptance_criteria[{index}].id")
        if criterion_id in criterion_ids:
            raise WorkContractError(f"duplicate acceptance criterion id: {criterion_id}")
        criterion_ids.add(criterion_id)
        checks = _string_list(
            item.get("verification_check_ids"),
            f"acceptance_criteria[{index}].verification_check_ids",
        )
        if not checks:
            raise WorkContractError(f"acceptance_criteria[{index}] needs a verification check")
        criteria.append(
            {
                "id": criterion_id,
                "statement": _nonempty(item.get("statement"), f"acceptance_criteria[{index}].statement"),
                "verification_check_ids": checks,
            }
        )

    raw_tasks = value.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise WorkContractError("tasks must be a non-empty array")
    tasks: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for index, item in enumerate(raw_tasks):
        if not isinstance(item, dict):
            raise WorkContractError(f"tasks[{index}] must be an object")
        task_id = _nonempty(item.get("id"), f"tasks[{index}].id")
        if task_id in task_ids:
            raise WorkContractError(f"duplicate task id: {task_id}")
        task_ids.add(task_id)
        if item.get("kind") not in TASK_KINDS:
            raise WorkContractError(f"tasks[{index}].kind is invalid")
        task = {
            "id": task_id,
            "kind": item["kind"],
            "description": _nonempty(item.get("description"), f"tasks[{index}].description"),
            "depends_on": _string_list(item.get("depends_on", []), f"tasks[{index}].depends_on"),
            "read_set": _string_list(item.get("read_set", []), f"tasks[{index}].read_set"),
            "write_set": _string_list(item.get("write_set", []), f"tasks[{index}].write_set"),
            "acceptance_criteria_ids": _string_list(
                item.get("acceptance_criteria_ids", []),
                f"tasks[{index}].acceptance_criteria_ids",
            ),
            "supersedes": _string_list(item.get("supersedes", []), f"tasks[{index}].supersedes"),
        }
        tasks.append(task)
    for task in tasks:
        missing_dependencies = set(task["depends_on"]) - task_ids
        if missing_dependencies:
            raise WorkContractError(
                f"task {task['id']} references missing dependencies: {', '.join(sorted(missing_dependencies))}"
            )
        missing_criteria = set(task["acceptance_criteria_ids"]) - criterion_ids
        if missing_criteria:
            raise WorkContractError(
                f"task {task['id']} references missing criteria: {', '.join(sorted(missing_criteria))}"
            )
    _validate_dag(tasks)

    completion = value.get("completion_policy")
    if not isinstance(completion, dict):
        raise WorkContractError("completion_policy must be an object")
    success = _string_list(completion.get("success_outcomes"), "completion_policy.success_outcomes")
    stop = _string_list(completion.get("stop_outcomes"), "completion_policy.stop_outcomes")
    if not success or not stop:
        raise WorkContractError("completion policy needs success and stop outcomes")
    if (set(success) | set(stop)) - TERMINAL_OUTCOMES:
        raise WorkContractError("completion policy contains an unsupported outcome")
    if set(success) & set(stop):
        raise WorkContractError("success and stop outcomes must not overlap")
    return {
        "schema_version": WORK_CONTRACT_VERSION,
        "contract_id": contract_id,
        "case_id": case_id,
        "objective": objective,
        "constraints": constraints,
        "non_goals": non_goals,
        "acceptance_criteria": criteria,
        "tasks": tasks,
        "completion_policy": {
            "success_outcomes": success,
            "stop_outcomes": stop,
        },
    }


def load_work_contract(path: Path, case_id: str) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise WorkContractError(f"work contract does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise WorkContractError(f"invalid work contract JSON: {error.msg}") from error
    if not isinstance(value, dict):
        raise WorkContractError("work contract must be an object")
    return validate_work_contract(value, case_id)


def _contract_digest(contract: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(contract).encode("utf-8"))


def _case_events(harness_root: Path, case_id: str) -> list[dict[str, Any]]:
    return [item for item in EventLedger(harness_root).read() if item.get("case_id") == case_id]


def _require_work_writable(workspace: Path, case_id: str) -> dict[str, Any]:
    pack = CaseStore(workspace.resolve() / ".harness").load(case_id)
    require_writable_pack(pack)
    return pack


def _ready_tasks(state: dict[str, Any]) -> list[str]:
    completed = {
        task_id for task_id, item in state["tasks"].items() if item["status"] == "COMPLETED"
    }
    active = any(
        item["status"] in {"RUNNING", "BLOCKED"} for item in state["tasks"].values()
    )
    if active or state["state"] in {"IMPLEMENTED", "VERIFYING", "VERIFIED", "ADMISSIBLE", "DONE", "STOPPED"}:
        return []
    return [
        task["id"]
        for task in state["contract"]["tasks"]
        if state["tasks"][task["id"]]["status"] == "PENDING"
        and set(task["depends_on"]).issubset(completed)
    ]


def rebuild_work_state(workspace: Path, case_id: str) -> dict[str, Any]:
    harness_root = workspace.resolve() / ".harness"
    events = _case_events(harness_root, case_id)
    registered = next(
        (item for item in events if item.get("type") == "work_contract_registered"),
        None,
    )
    if registered is None:
        raise WorkStateError(f"work contract not found for case: {case_id}")
    contract = registered["payload"]["contract"]
    state: dict[str, Any] = {
        "schema_version": WORK_STATE_VERSION,
        "case_id": case_id,
        "contract_id": contract["contract_id"],
        "contract_revision": 1,
        "contract_sha256": registered["payload"]["contract_sha256"],
        "contract": contract,
        "state": "PLANNED",
        "tasks": {
            item["id"]: {
                "status": "PENDING",
                "evidence_refs": [],
                "snapshot": None,
                "blocker": None,
            }
            for item in contract["tasks"]
        },
        "superseded_tasks": {},
        "active_pair_id": None,
        "active_code_run_id": None,
        "candidate_snapshot": None,
        "latest_proposal_id": None,
        "accepted_decision": None,
        "stale_artifacts": [],
        "blockers": [],
        "finished_at": None,
    }
    registered_sequence = int(registered["sequence"])
    for event in events:
        if int(event.get("sequence", 0)) <= registered_sequence:
            continue
        payload = event.get("payload") or {}
        event_type = event.get("type")
        if event_type == "work_contract_replanned":
            old_tasks = state["tasks"]
            new_contract = payload["contract"]
            new_tasks: dict[str, Any] = {}
            superseded = {
                old_id
                for task in new_contract["tasks"]
                for old_id in task.get("supersedes", [])
            }
            for old_id in superseded:
                if old_id in old_tasks:
                    state["superseded_tasks"][old_id] = {
                        **old_tasks[old_id],
                        "status": "SUPERSEDED",
                    }
            for task in new_contract["tasks"]:
                previous = old_tasks.get(task["id"])
                new_tasks[task["id"]] = (
                    previous
                    if previous and previous["status"] == "COMPLETED"
                    else {
                        "status": "PENDING",
                        "evidence_refs": [],
                        "snapshot": None,
                        "blocker": None,
                    }
                )
            state["tasks"] = new_tasks
            state["contract"] = new_contract
            state["contract_revision"] = payload["revision"]
            state["contract_sha256"] = payload["contract_sha256"]
            if state["state"] in {"IMPLEMENTED", "VERIFYING", "VERIFIED", "ADMISSIBLE"}:
                state["stale_artifacts"].extend(
                    item
                    for item in [
                        (
                            f"verdict:{state['active_code_run_id']}"
                            if state["active_code_run_id"]
                            else None
                        ),
                        state["latest_proposal_id"],
                    ]
                    if item
                )
            state["state"] = "ACTIVE"
            state["latest_proposal_id"] = None
            state["accepted_decision"] = None
        elif event_type == "work_pair_attached":
            state["active_pair_id"] = payload["pair_id"]
            state["active_code_run_id"] = payload["code_run_id"]
            state["candidate_snapshot"] = payload.get("candidate_snapshot")
            if state["state"] not in {"PLANNED", "ACTIVE"}:
                state["state"] = "ACTIVE"
        elif event_type == "work_task_started":
            state["tasks"][payload["task_id"]]["status"] = "RUNNING"
            state["state"] = "ACTIVE"
        elif event_type == "work_task_blocked":
            item = state["tasks"][payload["task_id"]]
            item["status"] = "BLOCKED"
            item["blocker"] = payload["reason"]
            state["state"] = "BLOCKED"
        elif event_type == "work_task_unblocked":
            item = state["tasks"][payload["task_id"]]
            item["status"] = "PENDING"
            item["blocker"] = None
            state["state"] = "ACTIVE"
        elif event_type == "work_task_completed":
            item = state["tasks"][payload["task_id"]]
            item["status"] = "COMPLETED"
            item["evidence_refs"] = payload["evidence_refs"]
            item["snapshot"] = payload.get("candidate_snapshot")
            item["blocker"] = None
            state["state"] = "ACTIVE"
        elif event_type == "work_implementation_completed":
            state["state"] = "IMPLEMENTED"
            state["candidate_snapshot"] = payload.get("candidate_snapshot")
        elif event_type == "pair_verification_started":
            if (
                payload.get("pair_id") == state["active_pair_id"]
                and payload.get("code_run_id") == state["active_code_run_id"]
                and state["state"] == "IMPLEMENTED"
            ):
                state["state"] = "VERIFYING"
        elif event_type == "pair_verdict_recorded":
            if (
                payload.get("pair_id") == state["active_pair_id"]
                and payload.get("code_run_id") == state["active_code_run_id"]
                and state["state"] == "VERIFYING"
            ):
                if payload.get("eligible"):
                    state["state"] = "VERIFIED"
                    state["candidate_snapshot"] = payload.get("code_snapshot")
                else:
                    state["state"] = "BLOCKED"
                    state["blockers"] = ["verification_failed"]
        elif event_type == "decision_proposed" and payload.get("proposal_id"):
            state["latest_proposal_id"] = payload.get("proposal_id")
            unsupported = {
                Outcome.CONFIGURATION_CHANGE.value,
                Outcome.DOCUMENTATION_CHANGE.value,
                Outcome.OPERATIONAL_ACTION.value,
            }
            if payload.get("outcome") not in unsupported and (
                (
                    payload.get("outcome") != Outcome.CODE_CHANGE.value
                    and state["state"] in {"IMPLEMENTED", "VERIFIED"}
                )
                or (
                    payload.get("outcome") == Outcome.CODE_CHANGE.value
                    and state["state"] == "VERIFIED"
                    and payload.get("pair_id") == state["active_pair_id"]
                    and payload.get("code_run_id") == state["active_code_run_id"]
                )
            ):
                state["state"] = "ADMISSIBLE"
        elif event_type == "decision_accepted":
            state["accepted_decision"] = payload
        elif event_type == "work_finished":
            state["state"] = payload["terminal_state"]
            state["finished_at"] = event["timestamp"]
    state["ready_task_ids"] = _ready_tasks(state)
    state["selected_task_id"] = state["ready_task_ids"][0] if state["ready_task_ids"] else None
    state["active_task_id"] = next(
        (
            task_id
            for task_id, item in state["tasks"].items()
            if item["status"] in {"RUNNING", "BLOCKED"}
        ),
        None,
    )
    state["blockers"] = [
        item["blocker"]
        for item in state["tasks"].values()
        if item.get("blocker")
    ] or state["blockers"]
    state["ledger_head"] = EventLedger(harness_root).verify()["head_hash"]
    return state


def _save_projections(harness_root: Path, case_id: str, state: dict[str, Any]) -> None:
    save_json(work_contract_path(harness_root, case_id), state["contract"])
    save_json(work_state_path(harness_root, case_id), state)


def create_work(workspace: Path, case_id: str, contract_path: Path, actor: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    harness_root, _ = ensure_runtime(workspace)
    pack = CaseStore(harness_root).load(case_id)
    require_writable_pack(pack)
    if any(item.get("type") == "work_contract_registered" for item in _case_events(harness_root, case_id)):
        raise WorkStateError(f"work contract already exists for case: {case_id}")
    contract = load_work_contract(contract_path, case_id)
    digest = _contract_digest(contract)
    EventLedger(harness_root).append(
        "work_contract_registered",
        case_id,
        {"revision": 1, "contract_sha256": digest, "contract": contract},
        actor=actor,
    )
    pack["work_contract_ref"] = {
        "contract_id": contract["contract_id"],
        "revision": 1,
        "sha256": digest,
    }
    pack["updated_at"] = utc_now()
    projection = _anchor_projection(EventLedger(harness_root), pack, case_id, actor)
    pack["provenance"]["event_head"] = projection["hash"]
    CaseStore(harness_root).save(pack)
    state = rebuild_work_state(workspace, case_id)
    _save_projections(harness_root, case_id, state)
    return state


def status_work(workspace: Path, case_id: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    _require_work_writable(workspace, case_id)
    harness_root = workspace / ".harness"
    state = rebuild_work_state(workspace, case_id)
    _save_projections(harness_root, case_id, state)
    return state


def next_work(workspace: Path, case_id: str) -> dict[str, Any]:
    _require_work_writable(workspace, case_id)
    state = status_work(workspace, case_id)
    return {
        "case_id": case_id,
        "state": state["state"],
        "selected_task_id": state["selected_task_id"],
        "ready_task_ids": state["ready_task_ids"],
    }


def _check_contract_against_pair(state: dict[str, Any], pair: dict[str, Any]) -> None:
    check_ids = {item["id"] for item in pair["verification_contract"]["checks"]}
    required = {
        check_id
        for criterion in state["contract"]["acceptance_criteria"]
        for check_id in criterion["verification_check_ids"]
    }
    missing = required - check_ids
    if missing:
        raise WorkStateError(
            "work contract references checks absent from the pair: " + ", ".join(sorted(missing))
        )


def attach_pair(
    workspace: Path,
    case_id: str,
    pair_id: str,
    code_run_id: str,
    actor: str,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    _require_work_writable(workspace, case_id)
    harness_root = workspace / ".harness"
    state = rebuild_work_state(workspace, case_id)
    if state["state"] not in {"PLANNED", "ACTIVE"}:
        raise WorkStateError("a pair can be attached only before implementation completion")
    pair = load_pair(harness_root, pair_id)
    if pair["case_id"] != case_id or code_run_id not in pair["code_run_ids"]:
        raise PairStateError("pair and code run must belong to the work case")
    if pair["base_revision"] != CaseStore(harness_root).load(case_id)["scope"]["base_revision"]:
        raise WorkStateError("pair revision does not match the case")
    manifest = load_json(manifest_path(harness_root, code_run_id))
    if manifest.get("kind") != "code":
        raise WorkStateError("work can attach only a code candidate")
    if manifest.get("frozen_snapshot") is not None:
        raise WorkStateError("work can attach only a mutable code attempt")
    _check_contract_against_pair(state, pair)
    EventLedger(harness_root).append(
        "work_pair_attached",
        case_id,
        {
            "pair_id": pair_id,
            "code_run_id": code_run_id,
            "candidate_snapshot": snapshot_tree(Path(manifest["workspace_root"]))["sha256"],
        },
        actor=actor,
    )
    return status_work(workspace, case_id)


def _task(state: dict[str, Any], task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    task = next((item for item in state["contract"]["tasks"] if item["id"] == task_id), None)
    if task is None or task_id not in state["tasks"]:
        raise WorkStateError(f"task not found: {task_id}")
    return task, state["tasks"][task_id]


def start_task(workspace: Path, case_id: str, task_id: str, actor: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    _require_work_writable(workspace, case_id)
    harness_root = workspace / ".harness"
    state = rebuild_work_state(workspace, case_id)
    task, task_state = _task(state, task_id)
    if task_id != state["selected_task_id"] or task_state["status"] != "PENDING":
        raise WorkStateError("only the deterministically selected next task can start")
    if task["kind"] == "implement":
        if not state["active_pair_id"] or not state["active_code_run_id"]:
            raise WorkStateError("implementation tasks require an active code candidate")
        manifest = load_json(manifest_path(harness_root, state["active_code_run_id"]))
        if manifest.get("frozen_snapshot") is not None:
            raise WorkStateError("implementation tasks require a mutable code attempt")
    EventLedger(harness_root).append(
        "work_task_started",
        case_id,
        {"task_id": task_id, "contract_revision": state["contract_revision"]},
        actor=actor,
    )
    return status_work(workspace, case_id)


def block_task(workspace: Path, case_id: str, task_id: str, reason: str, actor: str) -> dict[str, Any]:
    if not reason.strip():
        raise WorkStateError("block reason must not be empty")
    workspace = workspace.resolve()
    _require_work_writable(workspace, case_id)
    state = rebuild_work_state(workspace, case_id)
    _, task_state = _task(state, task_id)
    if task_state["status"] != "RUNNING":
        raise WorkStateError("only a running task can be blocked")
    EventLedger(workspace / ".harness").append(
        "work_task_blocked", case_id, {"task_id": task_id, "reason": reason.strip()}, actor=actor
    )
    return status_work(workspace, case_id)


def unblock_task(workspace: Path, case_id: str, task_id: str, actor: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    _require_work_writable(workspace, case_id)
    state = rebuild_work_state(workspace, case_id)
    _, task_state = _task(state, task_id)
    if task_state["status"] != "BLOCKED":
        raise WorkStateError("only a blocked task can be unblocked")
    EventLedger(workspace / ".harness").append(
        "work_task_unblocked", case_id, {"task_id": task_id}, actor=actor
    )
    return status_work(workspace, case_id)


def complete_task(
    workspace: Path,
    case_id: str,
    task_id: str,
    evidence_refs: list[str],
    actor: str,
) -> dict[str, Any]:
    if not evidence_refs:
        raise WorkStateError("task completion requires at least one evidence reference")
    workspace = workspace.resolve()
    _require_work_writable(workspace, case_id)
    harness_root = workspace / ".harness"
    state = rebuild_work_state(workspace, case_id)
    task, task_state = _task(state, task_id)
    if task_state["status"] != "RUNNING":
        raise WorkStateError("only a running task can be completed")
    pack = CaseStore(harness_root).load(case_id)
    evidence_ids = {item["id"] for item in pack.get("evidence", [])}
    valid_refs = set(evidence_ids)
    if state.get("active_code_run_id"):
        valid_refs.add(state["active_code_run_id"])
        valid_refs.add(f"run:{state['active_code_run_id']}")
    missing = set(evidence_refs) - valid_refs
    if missing:
        raise WorkStateError("unknown evidence references: " + ", ".join(sorted(missing)))
    if task["kind"] == "investigate" and not set(evidence_refs).issubset(evidence_ids):
        raise WorkStateError("investigation tasks require Decision Pack evidence references")
    candidate_snapshot = None
    if task["kind"] == "implement":
        if not state["active_code_run_id"]:
            raise WorkStateError("implementation completion requires an active candidate")
        manifest = load_json(manifest_path(harness_root, state["active_code_run_id"]))
        candidate_snapshot = snapshot_tree(Path(manifest["workspace_root"]))["sha256"]
    EventLedger(harness_root).append(
        "work_task_completed",
        case_id,
        {
            "task_id": task_id,
            "evidence_refs": evidence_refs,
            "candidate_snapshot": candidate_snapshot,
        },
        actor=actor,
    )
    return status_work(workspace, case_id)


def implementation_complete(workspace: Path, case_id: str, actor: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    _require_work_writable(workspace, case_id)
    harness_root = workspace / ".harness"
    state = rebuild_work_state(workspace, case_id)
    if state["state"] != "ACTIVE":
        raise WorkStateError("implementation completion requires ACTIVE work state")
    if not all(item["status"] == "COMPLETED" for item in state["tasks"].values()):
        raise WorkStateError("all tasks must be completed before implementation completion")
    if state["blockers"]:
        raise WorkStateError("unresolved blockers prevent implementation completion")
    implement_tasks = [item for item in state["contract"]["tasks"] if item["kind"] == "implement"]
    candidate_snapshot = None
    if implement_tasks:
        if not state["active_code_run_id"]:
            raise WorkStateError("implementation completion requires an active code candidate")
        manifest = freeze_code_run(workspace, case_id, state["active_code_run_id"])
        candidate_snapshot = manifest["frozen_snapshot"]
    EventLedger(harness_root).append(
        "work_implementation_completed",
        case_id,
        {
            "pair_id": state["active_pair_id"],
            "code_run_id": state["active_code_run_id"],
            "candidate_snapshot": candidate_snapshot,
        },
        actor=actor,
    )
    return status_work(workspace, case_id)


def _immutable_contract_view(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        key: contract[key]
        for key in (
            "schema_version",
            "contract_id",
            "case_id",
            "objective",
            "constraints",
            "non_goals",
            "acceptance_criteria",
            "completion_policy",
        )
    }


def replan_work(
    workspace: Path,
    case_id: str,
    contract_path: Path,
    reason: str,
    actor: str,
) -> dict[str, Any]:
    if not reason.strip():
        raise WorkStateError("replan reason must not be empty")
    workspace = workspace.resolve()
    _require_work_writable(workspace, case_id)
    harness_root = workspace / ".harness"
    state = rebuild_work_state(workspace, case_id)
    if state["state"] in {"DONE", "STOPPED"}:
        raise WorkStateError("terminal work cannot be replanned")
    replacement = load_work_contract(contract_path, case_id)
    if _immutable_contract_view(replacement) != _immutable_contract_view(state["contract"]):
        raise WorkContractError("replan cannot change objective, constraints, criteria, or completion policy")
    replacement_by_id = {item["id"]: item for item in replacement["tasks"]}
    superseded = {
        old_id for item in replacement["tasks"] for old_id in item.get("supersedes", [])
    }
    old_by_id = {item["id"]: item for item in state["contract"]["tasks"]}
    for task_id, task_state in state["tasks"].items():
        if task_state["status"] != "COMPLETED":
            continue
        if task_id in replacement_by_id:
            if replacement_by_id[task_id] != old_by_id[task_id]:
                raise WorkContractError(f"completed task cannot change: {task_id}")
        elif task_id not in superseded:
            raise WorkContractError(f"completed task must be retained or explicitly superseded: {task_id}")
    digest = _contract_digest(replacement)
    EventLedger(harness_root).append(
        "work_contract_replanned",
        case_id,
        {
            "revision": state["contract_revision"] + 1,
            "previous_contract_sha256": state["contract_sha256"],
            "contract_sha256": digest,
            "reason": reason.strip(),
            "contract": replacement,
        },
        actor=actor,
    )
    pack = CaseStore(harness_root).load(case_id)
    pack["work_contract_ref"] = {
        "contract_id": replacement["contract_id"],
        "revision": state["contract_revision"] + 1,
        "sha256": digest,
    }
    pack["updated_at"] = utc_now()
    projection = _anchor_projection(EventLedger(harness_root), pack, case_id, actor)
    pack["provenance"]["event_head"] = projection["hash"]
    CaseStore(harness_root).save(pack)
    return status_work(workspace, case_id)


def _acceptance_status(state: dict[str, Any]) -> list[dict[str, Any]]:
    completed_criteria = {
        criterion_id
        for task in state["contract"]["tasks"]
        if state["tasks"][task["id"]]["status"] == "COMPLETED"
        for criterion_id in task["acceptance_criteria_ids"]
    }
    verified = state["state"] in {"VERIFIED", "ADMISSIBLE", "DONE"}
    return [
        {
            "id": item["id"],
            "status": (
                "verified"
                if verified and item["id"] in completed_criteria
                else "implemented"
                if item["id"] in completed_criteria
                else "pending"
            ),
            "verification_check_ids": item["verification_check_ids"],
        }
        for item in state["contract"]["acceptance_criteria"]
    ]


def resume_packet(workspace: Path, case_id: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    _require_work_writable(workspace, case_id)
    harness_root = workspace / ".harness"
    state = rebuild_work_state(workspace, case_id)
    pack = CaseStore(harness_root).load(case_id)
    active_snapshot = None
    if state.get("active_code_run_id"):
        manifest = load_json(manifest_path(harness_root, state["active_code_run_id"]))
        active_snapshot = snapshot_tree(Path(manifest["workspace_root"]))["sha256"]
    allowed = ["mosaic work status", "mosaic work resume"]
    if state["selected_task_id"]:
        allowed.append(f"mosaic work task start {case_id} {state['selected_task_id']}")
    if any(item["status"] == "RUNNING" for item in state["tasks"].values()):
        allowed.extend(["mosaic work task block", "mosaic work task complete"])
    if state["state"] == "ACTIVE" and all(
        item["status"] == "COMPLETED" for item in state["tasks"].values()
    ):
        allowed.append("mosaic work implementation-complete")
    if state["state"] == "IMPLEMENTED" and state["active_pair_id"]:
        allowed.append("mosaic candidate verify-pair")
    if state["state"] == "VERIFIED":
        allowed.append("mosaic propose")
    if state["state"] == "ADMISSIBLE":
        if state.get("accepted_decision"):
            allowed.append("mosaic work finish")
        else:
            allowed.append("mosaic decide")
    hard_constraints = [
        item for item in state["contract"]["constraints"] if item["force"] == "hard"
    ]
    return {
        "packet_version": "1.0.0",
        "case_id": case_id,
        "contract_id": state["contract_id"],
        "contract_revision": state["contract_revision"],
        "contract_sha256": state["contract_sha256"],
        "ledger_head": state["ledger_head"],
        "repository_revision": pack["scope"]["base_revision"],
        "objective": state["contract"]["objective"],
        "hard_constraints": hard_constraints,
        "non_goals": state["contract"]["non_goals"],
        "work_state": state["state"],
        "active_pair_id": state["active_pair_id"],
        "active_code_run_id": state["active_code_run_id"],
        "candidate_snapshot": active_snapshot,
        "current_task_id": state["active_task_id"],
        "next_task_id": state["selected_task_id"],
        "selected_task_id": state["selected_task_id"],
        "ready_task_ids": state["ready_task_ids"],
        "blockers": state["blockers"],
        "completed_tasks": [
            {"id": task_id, "evidence_refs": item["evidence_refs"]}
            for task_id, item in state["tasks"].items()
            if item["status"] == "COMPLETED"
        ],
        "acceptance_criteria": _acceptance_status(state),
        "stale_artifacts": sorted(set(state["stale_artifacts"])),
        "allowed_next_commands": allowed,
        "issued_at": utc_now(),
        "invalidation": {
            "ledger_head": state["ledger_head"],
            "contract_revision": state["contract_revision"],
            "candidate_snapshot": active_snapshot,
        },
    }


def render_resume_markdown(packet: dict[str, Any]) -> str:
    constraints = "\n".join(
        f"- {item['id']}: {item['statement']}" for item in packet["hard_constraints"]
    ) or "- None"
    non_goals = "\n".join(f"- {item}" for item in packet["non_goals"]) or "- None"
    completed = "\n".join(
        f"- {item['id']}: {', '.join(item['evidence_refs'])}" for item in packet["completed_tasks"]
    ) or "- None"
    commands = "\n".join(f"- `{item}`" for item in packet["allowed_next_commands"])
    return (
        f"# Mosaic Resume Packet\n\n"
        f"Case: `{packet['case_id']}`  \n"
        f"State: `{packet['work_state']}`  \n"
        f"Contract revision: `{packet['contract_revision']}`  \n"
        f"Ledger head: `{packet['ledger_head']}`\n\n"
        f"## Objective\n\n{packet['objective']}\n\n"
        f"## Hard constraints\n\n{constraints}\n\n"
        f"## Non-goals\n\n{non_goals}\n\n"
        f"## Current task\n\n`{packet['current_task_id'] or 'none'}`\n\n"
        f"## Next task\n\n`{packet['next_task_id'] or 'none'}`\n\n"
        f"## Completed tasks\n\n{completed}\n\n"
        f"## Allowed next commands\n\n{commands}\n"
    )


def finish_work(workspace: Path, case_id: str, actor: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    _require_work_writable(workspace, case_id)
    harness_root = workspace / ".harness"
    state = rebuild_work_state(workspace, case_id)
    if state["state"] != "ADMISSIBLE":
        raise WorkStateError("work can finish only from ADMISSIBLE")
    if not all(item["status"] == "COMPLETED" for item in state["tasks"].values()):
        raise WorkStateError("all tasks must be completed before work can finish")
    pack = CaseStore(harness_root).load(case_id)
    decision = pack.get("outcome") or {}
    if decision.get("status") != "accepted" or decision.get("source") != "human":
        raise WorkStateError("an accepted human decision is required")
    if decision.get("proposal_id") != state["latest_proposal_id"]:
        raise WorkStateError("accepted decision does not match the latest proposal")
    ledger_events = EventLedger(harness_root).read()
    decision_index = next(
        (
            index
            for index in range(len(ledger_events) - 1, -1, -1)
            if ledger_events[index].get("type") == "decision_accepted"
            and (ledger_events[index].get("payload") or {}).get("proposal_id")
            == decision["proposal_id"]
        ),
        None,
    )
    if decision_index is None:
        raise WorkStateError("accepted decision event is missing from the ledger")
    later = ledger_events[decision_index + 1 :]
    if len(later) != 1 or later[0].get("type") != "projection_materialized":
        raise WorkStateError("accepted decision is stale after a later state change")
    require_v2_admission(
        workspace,
        pack,
        Outcome(decision["type"]),
        decision["proposal_id"],
    )
    if decision.get("type") == Outcome.CODE_CHANGE.value:
        if (
            decision.get("pair_id") != state["active_pair_id"]
            or decision.get("code_run_id") != state["active_code_run_id"]
            or decision.get("code_snapshot") != state["candidate_snapshot"]
        ):
            raise WorkStateError("accepted code decision is not bound to the active candidate")
    success = state["contract"]["completion_policy"]["success_outcomes"]
    stop = state["contract"]["completion_policy"]["stop_outcomes"]
    if decision["type"] in success:
        terminal = "DONE"
    elif decision["type"] in stop:
        terminal = "STOPPED"
    else:
        raise WorkStateError("accepted outcome is not permitted by the completion policy")
    EventLedger(harness_root).append(
        "work_finished",
        case_id,
        {
            "terminal_state": terminal,
            "outcome": decision["type"],
            "proposal_id": decision["proposal_id"],
            "pair_id": decision.get("pair_id"),
            "code_run_id": decision.get("code_run_id"),
            "code_snapshot": decision.get("code_snapshot"),
        },
        actor=actor,
    )
    return status_work(workspace, case_id)
