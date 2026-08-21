"""V1 Builder–Verifier candidate workflows."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from mosaic_harness.admission import experiment_ref, verdict_measures
from mosaic_harness.builder import apply_script_source, confined_writes, load_instructions
from mosaic_harness.compat import require_writable_pack
from mosaic_harness.executor import (
    MANIFEST_VERSION,
    CandidateNotFoundError,
    LocalCommandAdapter,
    RunStateError,
    load_json,
    manifest_path,
    normalize_budget,
    run_path,
    save_json,
    verdict_path,
)
from mosaic_harness.historian import EventLedger
from mosaic_harness.isolation import (
    IsolationUnavailableError,
    describe_isolation,
    render_builder_profile,
    render_verifier_profile,
    require_isolation,
    resolve_protected_paths,
)
from mosaic_harness.storage import CaseStore
from mosaic_harness.util import utc_now, validate_case_id
from mosaic_harness.verifiers import find_zero_workspace, run_verification_suite
from mosaic_harness.workflow import ensure_runtime, record_experiment
from mosaic_harness.workspace import (
    copy_tree_if_present,
    dispose_tree,
    hidden_tokens,
    materialize_repository,
    protected_paths_present,
    scan_for_tokens,
    snapshot_tree,
    workspace_texts,
)


def _new_run_id() -> str:
    return f"R-{secrets.token_hex(6)}"


def _harness_root(workspace: Path) -> Path:
    return workspace.resolve() / ".harness"


def _load_manifest(harness_root: Path, run_id: str) -> dict[str, Any]:
    return load_json(manifest_path(harness_root, run_id))


def _save_manifest(harness_root: Path, manifest: dict[str, Any]) -> None:
    save_json(manifest_path(harness_root, manifest["run_id"]), manifest)


def _provenance(manifest: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload = {
        "run_id": manifest["run_id"],
        "kind": manifest["kind"],
        "role": manifest.get("role"),
        "revision": manifest["revision"],
        "source_repository": manifest["source_repository"],
        "candidate_root": manifest["candidate_root"],
        "workspace_root": manifest["workspace_root"],
        "accessible_paths": manifest["accessible_paths"],
        "denied_paths": manifest["denied_paths"],
        "isolation": manifest["isolation"],
        "budget": manifest["budget"],
        "command": manifest.get("command"),
    }
    payload.update(extra)
    return payload


def _append(workspace: Path, event_type: str, case_id: str, payload: dict[str, Any], actor: str) -> None:
    EventLedger(_harness_root(workspace)).append(event_type, case_id, payload, actor=actor)


def _copy_evaluators(source: Path, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    return copy_tree_if_present(source, destination)


def create_candidate(
    workspace: Path,
    case_id: str,
    repository: Path,
    *,
    kind: str = "code",
    budget: Mapping[str, int] | None = None,
    max_files: int = 500,
) -> dict[str, Any]:
    validate_case_id(case_id)
    if kind not in {"code", "zero-change"}:
        raise ValueError("kind must be code or zero-change")
    workspace = workspace.resolve()
    repository = repository.resolve()
    harness_root, _ = ensure_runtime(workspace)
    pack = CaseStore(harness_root).load(case_id)
    require_writable_pack(pack)
    run_id = _new_run_id()
    run_root = run_path(harness_root, run_id)
    workspace_root = run_root / "workspace"
    public_root = run_root / "evaluators" / "public"
    hidden_root = run_root / "evaluators" / "hidden"
    mutation_root = run_root / "evaluators" / "mutation"
    property_root = run_root / "evaluators" / "property"
    verifier_root = run_root / "verifier"
    for path in (public_root, hidden_root, mutation_root, property_root, verifier_root):
        path.mkdir(parents=True, exist_ok=True)
    materialization = materialize_repository(repository, workspace_root, max_files=max_files)
    (workspace_root / ".tmp").mkdir(parents=True, exist_ok=True)
    pack_source = CaseStore(harness_root).path_for(case_id)
    mosaic_dir = workspace_root / ".mosaic"
    mosaic_dir.mkdir(exist_ok=True)
    mosaic_dir.joinpath("decision-pack.json").write_text(
        pack_source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _copy_evaluators(repository / ".harness" / "evaluators" / "public", public_root)
    _copy_evaluators(harness_root / "evaluators" / "public", public_root)
    _copy_evaluators(repository / ".harness" / "evaluators" / "hidden", hidden_root)
    _copy_evaluators(harness_root / "evaluators" / "hidden", hidden_root)
    _copy_evaluators(repository / ".harness" / "evaluators" / "mutation", mutation_root)
    _copy_evaluators(harness_root / "evaluators" / "mutation", mutation_root)
    _copy_evaluators(repository / ".harness" / "evaluators" / "property", property_root)
    _copy_evaluators(harness_root / "evaluators" / "property", property_root)

    leaked_paths = protected_paths_present(workspace_root)
    if leaked_paths:
        raise RuntimeError(f"protected paths materialized in candidate: {', '.join(leaked_paths)}")

    denied = resolve_protected_paths(workspace, repository, run_root)
    denied.append(hidden_root.resolve())
    unique_denied = []
    seen: set[Path] = set()
    for path in denied:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_denied.append(resolved)

    environment = {
        "MOSAIC_ROLE": "builder",
        "MOSAIC_CASE_ID": case_id,
        "MOSAIC_RUN_ID": run_id,
        "MOSAIC_CANDIDATE_ROOT": str(workspace_root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(workspace_root / ".tmp"),
    }
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_VERSION,
        "run_id": run_id,
        "case_id": case_id,
        "kind": kind,
        "role": "builder",
        "revision": pack["scope"]["base_revision"],
        "source_repository": str(repository),
        "candidate_root": str(run_root),
        "workspace_root": str(workspace_root),
        "public_evaluator_root": str(public_root),
        "hidden_evaluator_root": str(hidden_root),
        "mutation_evaluator_root": str(mutation_root),
        "property_evaluator_root": str(property_root),
        "verifier_root": str(verifier_root),
        "accessible_paths": [str(workspace_root), str(public_root), str(mosaic_dir)],
        "denied_paths": [str(path) for path in unique_denied],
        "command": None,
        "budget": normalize_budget(budget),
        "environment": environment,
        "expected_artifacts": ["run-manifest.json", "verdict.json"],
        "isolation": describe_isolation(),
        "materialization": materialization["method"],
        "state": "created",
        "created_at": utc_now(),
        "rollback_plan": {
            "trigger": "Builder or Verifier violation, timeout, or user interrupt",
            "action": "Leave the source repository unchanged and dispose or recover the candidate workspace",
        },
        "observation_plan": {
            "observation": "A denied capability, snapshot mismatch, or failed public floor reveals the run is unsafe",
        },
    }
    _save_manifest(harness_root, manifest)
    _append(workspace, "candidate_created", case_id, _provenance(manifest), "mosaic")
    if kind == "zero-change":
        _append(
            workspace,
            "builder_started",
            case_id,
            _provenance(manifest, note="zero-change candidate skips builder mutation"),
            "builder",
        )
        _append(
            workspace,
            "builder_finished",
            case_id,
            _provenance(manifest, skipped=True, exit_code=0),
            "builder",
        )
        manifest["state"] = "builder_finished"
        manifest["updated_at"] = utc_now()
        _save_manifest(harness_root, manifest)
    return manifest


def _builder_profile(manifest: dict[str, Any]) -> str:
    return render_builder_profile(
        writable=Path(manifest["workspace_root"]),
        denied_read=[Path(path) for path in manifest["denied_paths"]],
    )


def _verifier_profile(manifest: dict[str, Any]) -> str:
    return render_verifier_profile(
        writable=Path(manifest["verifier_root"]),
        candidate=Path(manifest["workspace_root"]),
    )


def run_role(
    workspace: Path,
    case_id: str,
    run_id: str,
    *,
    role: str,
    command: Sequence[str],
    budget: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    if role not in {"builder", "verifier"}:
        raise ValueError("role must be builder or verifier")
    if not command:
        raise ValueError("command must not be empty")
    workspace = workspace.resolve()
    harness_root, _ = ensure_runtime(workspace)
    manifest = _load_manifest(harness_root, run_id)
    require_writable_pack(CaseStore(harness_root).load(case_id))
    require_isolation()
    if manifest["case_id"] != case_id:
        raise RunStateError(f"run {run_id} does not belong to case {case_id}")
    if role == "builder" and manifest["kind"] == "zero-change":
        raise RunStateError("zero-change candidates do not run a builder command")
    if role == "builder" and manifest.get("frozen_snapshot") is not None:
        raise RunStateError("frozen candidates cannot run another builder command")
    if manifest["state"] == "disposed":
        raise RunStateError(f"run already disposed: {run_id}")

    merged_budget = dict(manifest["budget"])
    if budget:
        merged_budget.update(budget)
    active_budget = normalize_budget(merged_budget)
    manifest["role"] = role
    manifest["command"] = list(command)
    manifest["budget"] = active_budget
    environment = dict(manifest["environment"])
    environment["MOSAIC_ROLE"] = role
    if role == "verifier":
        tmp = Path(manifest["verifier_root"]) / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        environment["TMPDIR"] = str(tmp)
        profile = _verifier_profile(manifest)
        started_event = "verification_started"
        finished_event = "verdict_recorded"
    else:
        Path(manifest["workspace_root"], ".tmp").mkdir(parents=True, exist_ok=True)
        profile = _builder_profile(manifest)
        started_event = "builder_started"
        finished_event = "builder_finished"

    manifest["state"] = "builder_running" if role == "builder" else "verifying"
    manifest["updated_at"] = utc_now()
    _save_manifest(harness_root, manifest)
    _append(workspace, started_event, case_id, _provenance(manifest), role)

    result = LocalCommandAdapter().run(
        command,
        cwd=Path(manifest["workspace_root"]),
        profile=profile,
        budget=active_budget,
        env=environment,
    )
    hidden_root = Path(
        manifest.get("hidden_evaluator_root")
        or (harness_root / "evaluators" / "hidden")
    )
    leaked = scan_for_tokens(
        [result["stdout"], result["stderr"], *workspace_texts(Path(manifest["workspace_root"]))],
        hidden_tokens(hidden_root),
    )
    result["hidden_content_leaked"] = bool(leaked)
    result["state"] = "interrupted" if result["interrupted"] else f"{role}_finished"
    result["run_id"] = run_id
    result["role"] = role
    if result["hidden_content_leaked"]:
        result["denial"] = result["denial"] or "hidden evaluator contents leaked into builder artifacts"

    if result["interrupted"]:
        manifest["state"] = "interrupted"
        manifest["updated_at"] = utc_now()
        _save_manifest(harness_root, manifest)
        _append(
            workspace,
            "run_interrupted",
            case_id,
            _provenance(manifest, reason="timeout", result={k: result[k] for k in ("exit_code", "timed_out")}),
            role,
        )
        return result

    used = manifest.setdefault("budget_used", {"seconds": 0.0, "output_bytes": 0})
    used["seconds"] = round(float(used.get("seconds", 0.0)) + float(result["budget"]["used_seconds"]), 3)
    used["output_bytes"] = int(used.get("output_bytes", 0)) + int(
        result["budget"]["used_output_bytes"]
    )
    manifest["state"] = (
        "builder_active"
        if role == "builder" and manifest.get("pair_id")
        else "builder_finished"
        if role == "builder"
        else "verdict_recorded"
    )
    manifest["updated_at"] = utc_now()
    _save_manifest(harness_root, manifest)
    if role == "builder":
        _append(
            workspace,
            finished_event,
            case_id,
            _provenance(
                manifest,
                exit_code=result["exit_code"],
                denial=result["denial"],
                hidden_content_leaked=result["hidden_content_leaked"],
            ),
            role,
        )
    return result


def verify_candidate(
    workspace: Path,
    case_id: str,
    run_id: str,
    *,
    mutation_probe: Sequence[str] | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    harness_root, _ = ensure_runtime(workspace)
    manifest = _load_manifest(harness_root, run_id)
    require_writable_pack(CaseStore(harness_root).load(case_id))
    require_isolation()
    if manifest["case_id"] != case_id:
        raise RunStateError(f"run {run_id} does not belong to case {case_id}")
    if manifest["state"] == "disposed":
        raise RunStateError(f"run already disposed: {run_id}")

    workspace_root = Path(manifest["workspace_root"])
    verifier_root = Path(manifest["verifier_root"])
    (verifier_root / "tmp").mkdir(parents=True, exist_ok=True)
    snapshot_before = snapshot_tree(workspace_root)
    save_json(verifier_root / "snapshot-before.json", {"sha256": snapshot_before["sha256"]})

    manifest["role"] = "verifier"
    manifest["state"] = "verifying"
    manifest["updated_at"] = utc_now()
    _save_manifest(harness_root, manifest)
    _append(
        workspace,
        "verification_started",
        case_id,
        _provenance(manifest, snapshot_before=snapshot_before["sha256"]),
        "verifier",
    )

    adapter = LocalCommandAdapter()
    environment = dict(manifest["environment"])
    environment["MOSAIC_ROLE"] = "verifier"
    environment["TMPDIR"] = str(verifier_root / "tmp")
    environment["PYTHONPATH"] = str(workspace_root / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    profile = _verifier_profile(manifest)
    budget = normalize_budget(manifest["budget"])

    mutation_denied = False
    mutation_result: dict[str, Any] | None = None
    if mutation_probe:
        mutation_result = adapter.run(
            mutation_probe,
            cwd=workspace_root,
            profile=profile,
            budget=budget,
            env=environment,
        )
        mutation_denied = mutation_result["denial"] is not None

    suite = run_verification_suite(
        adapter=adapter,
        run_root=Path(manifest["candidate_root"]),
        workspace_root=workspace_root,
        environment=environment,
        profile=profile,
        budget=budget,
        extras={
            "zero_workspace": find_zero_workspace(harness_root, case_id, run_id),
        },
    )

    snapshot_after = snapshot_tree(workspace_root)
    candidate_unmodified = snapshot_before["sha256"] == snapshot_after["sha256"]
    present = protected_paths_present(workspace_root)
    floor_passed = (not suite["hard_failed"]) and not present and candidate_unmodified
    peer = find_zero_workspace(harness_root, case_id, run_id)
    verdict = {
        "run_id": run_id,
        "case_id": case_id,
        "kind": manifest["kind"],
        "floor_passed": floor_passed,
        "floors": suite["floors"],
        "floor_definition": suite["floor_definition"],
        "floor_results": suite["details"],
        "measures": verdict_measures(
            kind=str(manifest["kind"]),
            workspace_root=workspace_root,
            peer=peer,
            budget=suite.get("budget") or budget,
            reversible=True,
        ),
        "candidate_unmodified": candidate_unmodified,
        "mutation_denied": mutation_denied if mutation_probe is not None else None,
        "mutation_result": (
            {
                "exit_code": mutation_result["exit_code"],
                "denial": mutation_result["denial"],
            }
            if mutation_result is not None
            else None
        ),
        "snapshot_before": snapshot_before["sha256"],
        "snapshot_after": snapshot_after["sha256"],
        "protected_paths_present": present,
        "isolation": manifest["isolation"],
        "budget": budget,
        "recorded_at": utc_now(),
    }
    save_json(verdict_path(harness_root, run_id), verdict)
    manifest["state"] = "verdict_recorded"
    manifest["updated_at"] = utc_now()
    _save_manifest(harness_root, manifest)
    _append(
        workspace,
        "verdict_recorded",
        case_id,
        _provenance(
            manifest,
            floor_passed=verdict["floor_passed"],
            floor_definition=verdict["floor_definition"],
            candidate_unmodified=candidate_unmodified,
            mutation_denied=verdict["mutation_denied"],
            snapshot_before=verdict["snapshot_before"],
            snapshot_after=verdict["snapshot_after"],
        ),
        "verifier",
    )
    record_experiment(
        workspace,
        case_id,
        experiment_ref(manifest, verdict),
        actor="verifier",
    )
    return verdict


def build_candidate(
    workspace: Path,
    case_id: str,
    run_id: str,
    script_path: Path,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    harness_root, _ = ensure_runtime(workspace)
    manifest = _load_manifest(harness_root, run_id)
    require_writable_pack(CaseStore(harness_root).load(case_id))
    if manifest["case_id"] != case_id:
        raise RunStateError(f"run {run_id} does not belong to case {case_id}")
    if manifest["kind"] == "zero-change":
        raise RunStateError("zero-change candidates do not run a builder command")
    writes = confined_writes(Path(manifest["workspace_root"]), load_instructions(script_path))
    return run_role(
        workspace,
        case_id,
        run_id,
        role="builder",
        command=[sys.executable, "-c", apply_script_source(writes)],
    )


def compare_candidates(
    workspace: Path,
    case_id: str,
    run_ids: Sequence[str],
) -> dict[str, Any]:
    from mosaic_harness.admission import rank_candidates

    if len(run_ids) < 2:
        raise ValueError("compare requires at least two run ids")
    harness_root = _harness_root(workspace)
    verdicts = [load_json(verdict_path(harness_root, run_id)) for run_id in run_ids]
    if any(item.get("case_id") != case_id for item in verdicts):
        raise RunStateError("compared runs must belong to the same case")
    definitions = {item.get("floor_definition") for item in verdicts}
    if len(definitions) != 1:
        raise RunStateError("compared runs did not use the same public floor")
    ranking = rank_candidates(verdicts)
    result = {
        "case_id": case_id,
        "floor_definition": verdicts[0]["floor_definition"],
        "same_floor": True,
        "verdicts": verdicts,
        **ranking,
    }
    if len(verdicts) == 2:
        result["left"] = verdicts[0]
        result["right"] = verdicts[1]
    return result


def dispose_candidate(workspace: Path, case_id: str, run_id: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    harness_root, _ = ensure_runtime(workspace)
    manifest = _load_manifest(harness_root, run_id)
    require_writable_pack(CaseStore(harness_root).load(case_id))
    if manifest["case_id"] != case_id:
        raise RunStateError(f"run {run_id} does not belong to case {case_id}")
    dispose_tree(Path(manifest["workspace_root"]))
    if manifest.get("hidden_evaluator_root"):
        dispose_tree(Path(manifest["hidden_evaluator_root"]))
    manifest["state"] = "disposed"
    manifest["updated_at"] = utc_now()
    _save_manifest(harness_root, manifest)
    _append(workspace, "candidate_disposed", case_id, _provenance(manifest), "mosaic")
    return manifest


def interrupt_run(workspace: Path, case_id: str, run_id: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    harness_root, _ = ensure_runtime(workspace)
    manifest = _load_manifest(harness_root, run_id)
    require_writable_pack(CaseStore(harness_root).load(case_id))
    if manifest["case_id"] != case_id:
        raise RunStateError(f"run {run_id} does not belong to case {case_id}")
    manifest["state"] = "interrupted"
    manifest["updated_at"] = utc_now()
    _save_manifest(harness_root, manifest)
    _append(workspace, "run_interrupted", case_id, _provenance(manifest, reason="explicit"), "mosaic")
    return manifest


def show_run(workspace: Path, case_id: str, run_id: str) -> dict[str, Any]:
    harness_root = _harness_root(workspace)
    manifest = _load_manifest(harness_root, run_id)
    if manifest["case_id"] != case_id:
        raise RunStateError(f"run {run_id} does not belong to case {case_id}")
    result = {"manifest": manifest}
    verdict_file = verdict_path(harness_root, run_id)
    if verdict_file.exists():
        result["verdict"] = load_json(verdict_file)
    return result


__all__ = [
    "CandidateNotFoundError",
    "IsolationUnavailableError",
    "RunStateError",
    "compare_candidates",
    "create_candidate",
    "build_candidate",
    "dispose_candidate",
    "interrupt_run",
    "run_role",
    "show_run",
    "verify_candidate",
]
