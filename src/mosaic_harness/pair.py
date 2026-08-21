"""V2 experiment pairs with frozen external verification contracts."""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any, Mapping

from mosaic_harness.compat import require_writable_pack
from mosaic_harness.executor import (
    MANIFEST_VERSION,
    CandidateNotFoundError,
    load_json,
    manifest_path,
    normalize_budget,
    run_path,
    save_json,
)
from mosaic_harness.historian import EventLedger
from mosaic_harness.isolation import describe_isolation, resolve_protected_paths
from mosaic_harness.storage import CaseStore
from mosaic_harness.util import git_commit, utc_now, validate_case_id
from mosaic_harness.verification_contract import (
    freeze_verification_contract,
    load_verification_contract,
)
from mosaic_harness.workflow import ensure_runtime
from mosaic_harness.workspace import (
    materialize_repository,
    protected_paths_present,
    snapshot_tree,
    strip_protected_paths,
)


PAIR_VERSION = "2.0.0"


class PairNotFoundError(FileNotFoundError):
    """Raised when an experiment pair artifact is missing."""


class PairStateError(ValueError):
    """Raised when a pair or attempt is used in an invalid state."""


def _new_pair_id() -> str:
    return f"P-{secrets.token_hex(6)}"


def _new_run_id() -> str:
    return f"R-{secrets.token_hex(6)}"


def pair_path(harness_root: Path, pair_id: str) -> Path:
    return harness_root / "experiments" / "pairs" / pair_id


def pair_manifest_path(harness_root: Path, pair_id: str) -> Path:
    return pair_path(harness_root, pair_id) / "pair-manifest.json"


def pair_verdict_path(harness_root: Path, pair_id: str, code_run_id: str) -> Path:
    return pair_path(harness_root, pair_id) / "verdicts" / f"{code_run_id}.json"


def load_pair(harness_root: Path, pair_id: str) -> dict[str, Any]:
    path = pair_manifest_path(harness_root, pair_id)
    if not path.is_file():
        raise PairNotFoundError(f"pair not found: {pair_id}")
    return load_json(path)


def save_pair(harness_root: Path, pair: dict[str, Any]) -> None:
    save_json(pair_manifest_path(harness_root, pair["pair_id"]), pair)


def _append(
    harness_root: Path,
    event_type: str,
    case_id: str,
    payload: dict[str, Any],
    actor: str = "mosaic",
) -> dict[str, Any]:
    return EventLedger(harness_root).append(event_type, case_id, payload, actor=actor)


def _candidate_environment(case_id: str, run_id: str, workspace_root: Path) -> dict[str, str]:
    return {
        "MOSAIC_ROLE": "builder",
        "MOSAIC_CASE_ID": case_id,
        "MOSAIC_RUN_ID": run_id,
        "MOSAIC_CANDIDATE_ROOT": str(workspace_root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(workspace_root / ".tmp"),
    }


def _create_pair_run(
    *,
    harness_root: Path,
    workspace: Path,
    pair: dict[str, Any],
    pack: dict[str, Any],
    repository: Path,
    kind: str,
    budget: Mapping[str, int],
    max_files: int,
    parent_run_id: str | None = None,
    copy_from: Path | None = None,
    inherited_budget_used: Mapping[str, float | int] | None = None,
) -> dict[str, Any]:
    run_id = _new_run_id()
    run_root = run_path(harness_root, run_id)
    workspace_root = run_root / "workspace"
    verifier_root = run_root / "verifier"
    verifier_root.mkdir(parents=True, exist_ok=True)
    try:
        if copy_from is None:
            materialization = materialize_repository(
                repository,
                workspace_root,
                max_files=max_files,
            )
        else:
            workspace_root.mkdir(parents=True, exist_ok=True)
            for current_root, directories, filenames in os.walk(copy_from):
                directories[:] = sorted(
                    item for item in directories if item not in {".git", ".tmp", "__pycache__"}
                )
                source_root = Path(current_root)
                for filename in sorted(filenames):
                    source = source_root / filename
                    if source.is_symlink() or not source.is_file() or source.name == ".git":
                        continue
                    target = workspace_root / source.relative_to(copy_from)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            strip_protected_paths(workspace_root)
            materialization = {"method": "retry-snapshot", "commit": pair.get("base_commit")}
        baseline = snapshot_tree(workspace_root)
        if baseline["sha256"] != pair["source_snapshot"] and copy_from is None:
            raise PairStateError(
                "candidate materialization does not match the investigated repository tree"
            )
    except Exception:
        shutil.rmtree(run_root, ignore_errors=True)
        raise
    (workspace_root / ".tmp").mkdir(parents=True, exist_ok=True)
    mosaic_dir = workspace_root / ".mosaic"
    mosaic_dir.mkdir(exist_ok=True)
    mosaic_dir.joinpath("decision-pack.json").write_text(
        CaseStore(harness_root).path_for(pair["case_id"]).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    leaked = protected_paths_present(workspace_root)
    if leaked:
        raise RuntimeError(f"protected paths materialized in candidate: {', '.join(leaked)}")
    pair_root = pair_path(harness_root, pair["pair_id"])
    denied = resolve_protected_paths(workspace, repository, run_root)
    denied.append(pair_root.resolve())
    denied_paths = sorted({str(path.resolve()) for path in denied})
    baseline = snapshot_tree(workspace_root)
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_VERSION,
        "run_id": run_id,
        "pair_id": pair["pair_id"],
        "parent_run_id": parent_run_id,
        "case_id": pair["case_id"],
        "kind": kind,
        "role": "builder",
        "revision": pack["scope"]["base_revision"],
        "source_repository": str(repository),
        "candidate_root": str(run_root),
        "workspace_root": str(workspace_root),
        "verifier_root": str(verifier_root),
        "accessible_paths": [str(workspace_root)],
        "denied_paths": denied_paths,
        "command": None,
        "budget": normalize_budget(budget),
        "budget_used": {
            "seconds": float((inherited_budget_used or {}).get("seconds", 0.0)),
            "output_bytes": int((inherited_budget_used or {}).get("output_bytes", 0)),
        },
        "environment": _candidate_environment(pair["case_id"], run_id, workspace_root),
        "expected_artifacts": ["run-manifest.json", "verdict.json"],
        "isolation": describe_isolation(),
        "materialization": materialization["method"],
        "baseline_snapshot": pair.get("baseline_snapshot", baseline["sha256"]),
        "attempt_start_snapshot": baseline["sha256"],
        "frozen_snapshot": baseline["sha256"] if kind == "zero-change" else None,
        "floor_definition_sha256": pair["floor_definition_sha256"],
        "state": "frozen" if kind == "zero-change" else "created",
        "created_at": utc_now(),
        "rollback_plan": {
            "trigger": "Builder or Verifier violation, timeout, or user interrupt",
            "action": "Keep the source repository unchanged and dispose the candidate workspace",
        },
    }
    save_json(manifest_path(harness_root, run_id), manifest)
    _append(
        harness_root,
        "candidate_created",
        pair["case_id"],
        {
            "pair_id": pair["pair_id"],
            "run_id": run_id,
            "kind": kind,
            "revision": manifest["revision"],
            "baseline_snapshot": baseline["sha256"],
            "floor_definition_sha256": pair["floor_definition_sha256"],
            "parent_run_id": parent_run_id,
        },
    )
    return manifest


def prepare_pair(
    workspace: Path,
    case_id: str,
    repository: Path,
    verification_contract: Path,
    *,
    max_files: int = 500,
    budget: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    validate_case_id(case_id)
    workspace = workspace.resolve()
    repository = repository.resolve()
    harness_root, _ = ensure_runtime(workspace)
    pack = CaseStore(harness_root).load(case_id)
    require_writable_pack(pack)
    if str(repository) != str(Path(pack["scope"]["repository"]).resolve()):
        raise PairStateError("pair repository must match the investigated repository")
    base_commit = pack["scope"].get("base_commit")
    if base_commit is not None and git_commit(repository) != base_commit:
        raise PairStateError("repository HEAD no longer matches the investigated base commit")
    contract = load_verification_contract(verification_contract)
    pair_id = _new_pair_id()
    pair_root = pair_path(harness_root, pair_id)
    pair_root.mkdir(parents=True, exist_ok=False)
    try:
        frozen = freeze_verification_contract(
            contract,
            repository=repository,
            harness_root=harness_root,
            pair_root=pair_root,
        )
    except Exception:
        shutil.rmtree(pair_root, ignore_errors=True)
        raise
    save_json(pair_root / "floor" / "internal-contract.json", frozen["definition"])
    active_budget = normalize_budget(budget or contract["budgets"])
    pair: dict[str, Any] = {
        "schema_version": PAIR_VERSION,
        "pair_id": pair_id,
        "case_id": case_id,
        "repository": str(repository),
        "base_commit": base_commit,
        "base_revision": pack["scope"]["base_revision"],
        "source_snapshot": snapshot_tree(repository)["sha256"],
        "contract_id": contract["contract_id"],
        "floor_definition_sha256": frozen["floor_definition_sha256"],
        "verification_contract": frozen["public_view"],
        "public_root": frozen["public_root"],
        "budget": active_budget,
        "zero_run_id": None,
        "code_run_ids": [],
        "state": "preparing",
        "created_at": utc_now(),
    }
    save_pair(harness_root, pair)
    zero: dict[str, Any] | None = None
    try:
        zero = _create_pair_run(
            harness_root=harness_root,
            workspace=workspace,
            pair=pair,
            pack=pack,
            repository=repository,
            kind="zero-change",
            budget=active_budget,
            max_files=max_files,
        )
        code = _create_pair_run(
            harness_root=harness_root,
            workspace=workspace,
            pair=pair,
            pack=pack,
            repository=repository,
            kind="code",
            budget=active_budget,
            max_files=max_files,
        )
    except Exception as error:
        pair["zero_run_id"] = zero.get("run_id") if zero else None
        pair["state"] = "failed"
        pair["failure_kind"] = type(error).__name__
        pair["updated_at"] = utc_now()
        save_pair(harness_root, pair)
        _append(
            harness_root,
            "experiment_pair_failed",
            case_id,
            {
                "pair_id": pair_id,
                "zero_run_id": pair["zero_run_id"],
                "failure_kind": pair["failure_kind"],
            },
        )
        raise
    assert zero is not None
    if zero["baseline_snapshot"] != code["baseline_snapshot"]:
        raise PairStateError("zero-change and code candidates do not share the same baseline")
    pair["zero_run_id"] = zero["run_id"]
    pair["code_run_ids"] = [code["run_id"]]
    pair["baseline_snapshot"] = zero["baseline_snapshot"]
    pair["state"] = "ready"
    pair["updated_at"] = utc_now()
    save_pair(harness_root, pair)
    _append(
        harness_root,
        "experiment_pair_created",
        case_id,
        {
            "pair_id": pair_id,
            "zero_run_id": zero["run_id"],
            "code_run_id": code["run_id"],
            "base_revision": pair["base_revision"],
            "baseline_snapshot": pair["baseline_snapshot"],
            "floor_definition_sha256": pair["floor_definition_sha256"],
        },
    )
    return {
        "pair_id": pair_id,
        "zero_run_id": zero["run_id"],
        "code_run_id": code["run_id"],
        "base_revision": pair["base_revision"],
        "baseline_snapshot": pair["baseline_snapshot"],
        "floor_definition_sha256": pair["floor_definition_sha256"],
    }


def freeze_code_run(workspace: Path, case_id: str, run_id: str) -> dict[str, Any]:
    harness_root, _ = ensure_runtime(workspace.resolve())
    require_writable_pack(CaseStore(harness_root).load(case_id))
    manifest = load_json(manifest_path(harness_root, run_id))
    if manifest.get("case_id") != case_id or manifest.get("kind") != "code":
        raise PairStateError("run is not a code candidate for this case")
    if manifest.get("state") == "disposed":
        raise PairStateError("disposed candidates cannot be frozen")
    if manifest.get("frozen_snapshot") is None:
        snapshot = snapshot_tree(Path(manifest["workspace_root"]))
        manifest["frozen_snapshot"] = snapshot["sha256"]
        manifest["state"] = "frozen"
        manifest["updated_at"] = utc_now()
        save_json(manifest_path(harness_root, run_id), manifest)
        _append(
            harness_root,
            "candidate_frozen",
            case_id,
            {
                "pair_id": manifest.get("pair_id"),
                "run_id": run_id,
                "snapshot": snapshot["sha256"],
            },
            actor="builder",
        )
    return manifest


def retry_code(
    workspace: Path,
    case_id: str,
    pair_id: str,
    from_run_id: str,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    harness_root, _ = ensure_runtime(workspace)
    pack = CaseStore(harness_root).load(case_id)
    require_writable_pack(pack)
    pair = load_pair(harness_root, pair_id)
    if pair["case_id"] != case_id:
        raise PairStateError("pair does not belong to case")
    parent = load_json(manifest_path(harness_root, from_run_id))
    if parent.get("pair_id") != pair_id or parent.get("kind") != "code":
        raise PairStateError("retry source must be a code attempt in the same pair")
    if parent.get("frozen_snapshot") is None:
        raise PairStateError("retry source must be frozen")
    current_parent = snapshot_tree(Path(parent["workspace_root"]))["sha256"]
    if current_parent != parent["frozen_snapshot"]:
        raise PairStateError("retry source changed after it was frozen")
    child = _create_pair_run(
        harness_root=harness_root,
        workspace=workspace,
        pair=pair,
        pack=pack,
        repository=Path(pair["repository"]),
        kind="code",
        budget=pair["budget"],
        max_files=1,
        parent_run_id=from_run_id,
        copy_from=Path(parent["workspace_root"]),
        inherited_budget_used=parent.get("budget_used"),
    )
    pair["code_run_ids"].append(child["run_id"])
    pair["state"] = "ready"
    pair["updated_at"] = utc_now()
    save_pair(harness_root, pair)
    _append(
        harness_root,
        "code_attempt_retried",
        case_id,
        {
            "pair_id": pair_id,
            "parent_run_id": from_run_id,
            "run_id": child["run_id"],
            "parent_snapshot": parent["frozen_snapshot"],
        },
    )
    return child


def show_pair(workspace: Path, case_id: str, pair_id: str) -> dict[str, Any]:
    harness_root = workspace.resolve() / ".harness"
    require_writable_pack(CaseStore(harness_root).load(case_id))
    pair = load_pair(harness_root, pair_id)
    if pair["case_id"] != case_id:
        raise PairStateError("pair does not belong to case")
    verdicts: list[dict[str, Any]] = []
    for run_id in pair.get("code_run_ids", []):
        path = pair_verdict_path(harness_root, pair_id, run_id)
        if path.is_file():
            verdicts.append(load_json(path))
    return {"pair": pair, "verdicts": verdicts}


def require_pair_run(harness_root: Path, pair_id: str, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    pair = load_pair(harness_root, pair_id)
    try:
        manifest = load_json(manifest_path(harness_root, run_id))
    except CandidateNotFoundError as error:
        raise PairStateError(str(error)) from error
    if manifest.get("pair_id") != pair_id:
        raise PairStateError("run does not belong to pair")
    return pair, manifest
