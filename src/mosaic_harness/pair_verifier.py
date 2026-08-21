"""Verification of frozen V2 experiment pairs."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from mosaic_harness.executor import LocalCommandAdapter, load_json, manifest_path, save_json, verdict_path
from mosaic_harness.compat import require_writable_pack
from mosaic_harness.historian import EventLedger
from mosaic_harness.isolation import render_verifier_profile, require_isolation
from mosaic_harness.pair import (
    PairStateError,
    freeze_code_run,
    load_pair,
    pair_path,
    pair_verdict_path,
    save_pair,
)
from mosaic_harness.storage import CaseStore
from mosaic_harness.util import utc_now
from mosaic_harness.verification_contract import verify_frozen_floor
from mosaic_harness.verifiers import select_mutation_targets
from mosaic_harness.workflow import ensure_runtime, record_experiment
from mosaic_harness.workspace import snapshot_tree


CHECK_STATUSES = {"PASS", "FAIL", "ABSENT", "ERROR"}


def _classify(result: dict[str, Any]) -> str:
    if result.get("timed_out") or result.get("denial") or result.get("exit_code") is None:
        return "ERROR"
    if result["exit_code"] == 0:
        return "PASS"
    if result["exit_code"] == 1:
        return "FAIL"
    return "ERROR"


def _expanded_argv(argv: list[str], *, workspace: Path, frozen_public: Path) -> list[str]:
    return [
        token.replace("{workspace}", str(workspace)).replace(
            "{frozen_public}", str(frozen_public)
        )
        for token in argv
    ]


def _environment(manifest: dict[str, Any], *, workspace_root: Path, verifier_root: Path, public_root: Path) -> dict[str, str]:
    environment = dict(manifest["environment"])
    environment.update(
        {
            "MOSAIC_ROLE": "verifier",
            "MOSAIC_WORKSPACE": str(workspace_root),
            "MOSAIC_FROZEN_PUBLIC": str(public_root),
            "PYTHONPATH": str(workspace_root / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(verifier_root / "tmp"),
        }
    )
    (verifier_root / "tmp").mkdir(parents=True, exist_ok=True)
    return environment


def _run_check(
    *,
    adapter: LocalCommandAdapter,
    harness_root: Path,
    pair: dict[str, Any],
    manifest: dict[str, Any],
    check: dict[str, Any],
    workspace_root: Path | None = None,
    output_suffix: str = "",
) -> dict[str, Any]:
    candidate_root = Path(manifest["workspace_root"])
    active_root = workspace_root or candidate_root
    verifier_root = Path(manifest["verifier_root"])
    public_root = Path(pair["public_root"])
    environment = _environment(
        manifest,
        workspace_root=active_root,
        verifier_root=verifier_root,
        public_root=public_root,
    )
    profile = render_verifier_profile(writable=verifier_root, candidate=candidate_root)
    check_id = check["id"]
    safe_name = check_id.replace("/", "_") + output_suffix
    if check["visibility"] == "public":
        command = _expanded_argv(check["argv"], workspace=active_root, frozen_public=public_root)
        result = adapter.run(
            command,
            cwd=active_root,
            profile=profile,
            budget=pair["budget"],
            env=environment,
            output_dir=verifier_root / "outputs" / "public",
            output_name=safe_name,
        )
        return {
            "id": check_id,
            "role": check["role"],
            "visibility": "public",
            "status": _classify(result),
            "command": command,
            "exit_code": result["exit_code"],
            "error_kind": (
                "timeout"
                if result["timed_out"]
                else "denial"
                if result["denial"]
                else "runner"
                if result["exit_code"] not in {0, 1}
                else None
            ),
            "stdout_ref": result["stdout_ref"],
            "stderr_ref": result["stderr_ref"],
            "stdout_sha256": result["stdout_sha256"],
            "stderr_sha256": result["stderr_sha256"],
            "budget": result["budget"],
        }
    evaluator = (
        pair_path(harness_root, pair["pair_id"])
        / "floor"
        / "hidden"
        / check["evaluator_id"]
    )
    if not evaluator.is_file():
        return {
            "id": check_id,
            "role": check["role"],
            "visibility": "hidden",
            "evaluator_id": check["evaluator_id"],
            "status": "ABSENT",
            "output_sha256": None,
            "error_kind": "missing_evaluator",
        }
    result = adapter.run(
        [sys.executable, str(evaluator)],
        cwd=active_root,
        profile=profile,
        budget=pair["budget"],
        env=environment,
        output_dir=verifier_root / "outputs" / "hidden",
        output_name=safe_name,
    )
    return {
        "id": check_id,
        "role": check["role"],
        "visibility": "hidden",
        "evaluator_id": check["evaluator_id"],
        "status": _classify(result),
        "exit_code": result["exit_code"],
        "output_sha256": result["stdout_sha256"],
        "error_kind": (
            "timeout"
            if result["timed_out"]
            else "denial"
            if result["denial"]
            else "runner"
            if result["exit_code"] not in {0, 1}
            else None
        ),
        "budget": result["budget"],
    }


def _copy_candidate(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        symlinks=False,
        ignore=shutil.ignore_patterns(".git", ".tmp", "__pycache__"),
    )


def _mutation_result(
    *,
    adapter: LocalCommandAdapter,
    harness_root: Path,
    pair: dict[str, Any],
    manifest: dict[str, Any],
    public_checks: list[dict[str, Any]],
) -> dict[str, Any]:
    workspace_root = Path(manifest["workspace_root"])
    targets = select_mutation_targets(workspace_root)
    if not targets or not public_checks:
        return {"name": "mutation", "status": "ABSENT", "mutants": []}
    attempts: list[dict[str, Any]] = []
    verifier_root = Path(manifest["verifier_root"])
    for index, source in enumerate(targets, start=1):
        scratch = verifier_root / "mutation" / f"mutant-{index:04d}"
        _copy_candidate(workspace_root, scratch)
        relative = source.relative_to(workspace_root)
        mutant = scratch / relative
        original = mutant.read_text(encoding="utf-8")
        if "return True" in original:
            changed = original.replace("return True", "return False", 1)
        elif "return False" in original:
            changed = original.replace("return False", "return True", 1)
        else:
            changed = original + "\nraise RuntimeError('mosaic-mutation')\n"
        try:
            compile(changed, str(mutant), "exec")
        except SyntaxError:
            attempts.append(
                {"mutant": relative.as_posix(), "status": "ERROR", "error_kind": "invalid_mutant"}
            )
            continue
        mutant.write_text(changed, encoding="utf-8")
        check_results = [
            _run_check(
                adapter=adapter,
                harness_root=harness_root,
                pair=pair,
                manifest=manifest,
                check=check,
                workspace_root=scratch,
                output_suffix=f"-mutant-{index:04d}",
            )
            for check in public_checks
        ]
        statuses = {item["status"] for item in check_results}
        status = (
            "ERROR"
            if "ERROR" in statuses or "ABSENT" in statuses
            else "KILLED"
            if "FAIL" in statuses
            else "SURVIVED"
        )
        attempts.append(
            {
                "mutant": relative.as_posix(),
                "status": status,
                "check_statuses": {item["id"]: item["status"] for item in check_results},
                "check_results": check_results,
            }
        )
        shutil.rmtree(scratch, ignore_errors=True)
    adapter_status = (
        "ERROR"
        if any(item["status"] == "ERROR" for item in attempts)
        else "FAIL"
        if any(item["status"] == "SURVIVED" for item in attempts)
        else "PASS"
        if attempts and all(item["status"] == "KILLED" for item in attempts)
        else "ABSENT"
    )
    return {
        "name": "mutation",
        "status": adapter_status,
        "mutants": attempts,
        "budget": {
            "used_seconds": sum(
                float(result.get("budget", {}).get("used_seconds", 0.0))
                for attempt in attempts
                for result in attempt.get("check_results", [])
            ),
            "used_output_bytes": sum(
                int(result.get("budget", {}).get("used_output_bytes", 0))
                for attempt in attempts
                for result in attempt.get("check_results", [])
            ),
        },
    }


def _property_result(
    *,
    adapter: LocalCommandAdapter,
    harness_root: Path,
    pair: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    root = pair_path(harness_root, pair["pair_id"]) / "floor" / "adapters" / "property"
    scripts = sorted(path for path in root.glob("*.py") if path.is_file()) if root.is_dir() else []
    if not scripts:
        return {"name": "property", "status": "ABSENT", "results": []}
    candidate = Path(manifest["workspace_root"])
    verifier_root = Path(manifest["verifier_root"])
    environment = _environment(
        manifest,
        workspace_root=candidate,
        verifier_root=verifier_root,
        public_root=Path(pair["public_root"]),
    )
    profile = render_verifier_profile(writable=verifier_root, candidate=candidate)
    results: list[dict[str, Any]] = []
    for index, script in enumerate(scripts, start=1):
        result = adapter.run(
            [sys.executable, str(script)],
            cwd=candidate,
            profile=profile,
            budget=pair["budget"],
            env=environment,
            output_dir=verifier_root / "outputs" / "property",
            output_name=f"property-{index:04d}",
        )
        results.append(
            {
                "evaluator_id": script.name,
                "status": _classify(result),
                "output_sha256": result["stdout_sha256"],
                "budget": result["budget"],
            }
        )
    statuses = {item["status"] for item in results}
    status = "ERROR" if "ERROR" in statuses else "FAIL" if "FAIL" in statuses else "PASS"
    return {
        "name": "property",
        "status": status,
        "results": results,
        "budget": {
            "used_seconds": sum(
                float(item.get("budget", {}).get("used_seconds", 0.0))
                for item in results
            ),
            "used_output_bytes": sum(
                int(item.get("budget", {}).get("used_output_bytes", 0))
                for item in results
            ),
        },
    }


def _change_surface(zero_root: Path, code_root: Path) -> dict[str, Any]:
    zero = {item["path"]: item for item in snapshot_tree(zero_root)["artifacts"]}
    code = {item["path"]: item for item in snapshot_tree(code_root)["artifacts"]}
    added = sorted(set(code) - set(zero))
    deleted = sorted(set(zero) - set(code))
    modified = sorted(
        path for path in set(zero) & set(code) if zero[path]["sha256"] != code[path]["sha256"]
    )
    return {
        "files": len(added) + len(modified) + len(deleted),
        "added": added,
        "modified": modified,
        "deleted": deleted,
    }


def _relation_status(zero: dict[str, Any], code: dict[str, Any]) -> dict[str, Any]:
    if zero["status"] in {"ABSENT", "ERROR"} or code["status"] in {"ABSENT", "ERROR"}:
        return {"status": "ERROR", "satisfied": False}
    if zero["role"] == "target":
        satisfied = zero["status"] == "FAIL" and code["status"] == "PASS"
    else:
        satisfied = zero["status"] == "PASS" and code["status"] == "PASS"
    return {"status": "PASS" if satisfied else "FAIL", "satisfied": satisfied}


def verify_pair(
    workspace: Path,
    case_id: str,
    pair_id: str,
    code_run_id: str,
    *,
    adapter: LocalCommandAdapter | None = None,
    require_real_isolation: bool = True,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    harness_root, _ = ensure_runtime(workspace)
    require_writable_pack(CaseStore(harness_root).load(case_id))
    if require_real_isolation:
        require_isolation()
    elif adapter is None:
        raise ValueError("a test adapter is required when real isolation is disabled")
    pair = load_pair(harness_root, pair_id)
    if pair["case_id"] != case_id or code_run_id not in pair["code_run_ids"]:
        raise PairStateError("code run does not belong to pair and case")
    if any(
        event.get("case_id") == case_id
        and event.get("type") == "work_contract_registered"
        for event in EventLedger(harness_root).read()
    ):
        from mosaic_harness.work import rebuild_work_state

        work_state = rebuild_work_state(workspace, case_id)
        if (
            work_state.get("active_pair_id") != pair_id
            or work_state.get("active_code_run_id") != code_run_id
        ):
            raise PairStateError("work can verify only its active pair and code attempt")
        if work_state["state"] != "IMPLEMENTED":
            raise PairStateError("work must be IMPLEMENTED before pair verification")
    zero = load_json(manifest_path(harness_root, pair["zero_run_id"]))
    code = freeze_code_run(workspace, case_id, code_run_id)
    if zero["revision"] != code["revision"] or code["revision"] != pair["base_revision"]:
        raise PairStateError("pair runs are not bound to the same base revision")
    if zero["baseline_snapshot"] != code["baseline_snapshot"]:
        raise PairStateError("pair runs do not share the same baseline snapshot")
    internal_contract = load_json(pair_path(harness_root, pair_id) / "floor" / "internal-contract.json")
    floor_integrity = verify_frozen_floor(
        internal_contract,
        public_root=Path(pair["public_root"]),
        pair_root=pair_path(harness_root, pair_id),
    )
    if floor_integrity["definition_sha256"] != pair["floor_definition_sha256"]:
        raise PairStateError("verification contract definition changed after pair creation")
    if not floor_integrity["valid"]:
        raise PairStateError(
            "frozen verification inputs changed: " + "; ".join(floor_integrity["problems"])
        )
    pair["state"] = "verifying"
    pair["updated_at"] = utc_now()
    save_pair(harness_root, pair)
    EventLedger(harness_root).append(
        "pair_verification_started",
        case_id,
        {"pair_id": pair_id, "zero_run_id": zero["run_id"], "code_run_id": code_run_id},
        actor="verifier",
    )
    adapter = adapter or LocalCommandAdapter()
    zero_before = snapshot_tree(Path(zero["workspace_root"]))["sha256"]
    code_before = snapshot_tree(Path(code["workspace_root"]))["sha256"]
    zero_checks = [
        _run_check(
            adapter=adapter,
            harness_root=harness_root,
            pair=pair,
            manifest=zero,
            check=check,
        )
        for check in internal_contract["checks"]
    ]
    code_checks = [
        _run_check(
            adapter=adapter,
            harness_root=harness_root,
            pair=pair,
            manifest=code,
            check=check,
        )
        for check in internal_contract["checks"]
    ]
    zero_by_id = {item["id"]: item for item in zero_checks}
    code_by_id = {item["id"]: item for item in code_checks}
    relations = []
    for check in internal_contract["checks"]:
        relation = _relation_status(zero_by_id[check["id"]], code_by_id[check["id"]])
        relations.append(
            {
                "id": check["id"],
                "role": check["role"],
                "zero_status": zero_by_id[check["id"]]["status"],
                "code_status": code_by_id[check["id"]]["status"],
                **relation,
            }
        )
    adapters: list[dict[str, Any]] = []
    required = set(internal_contract.get("required_adapters", []))
    if "hidden" in required:
        hidden_relations = [
            item
            for item, check in zip(relations, internal_contract["checks"])
            if check["visibility"] == "hidden"
        ]
        hidden_status = (
            "ABSENT"
            if not hidden_relations
            else "ERROR"
            if any(item["status"] == "ERROR" for item in hidden_relations)
            else "PASS"
            if all(item["satisfied"] for item in hidden_relations)
            else "FAIL"
        )
        adapters.append({"name": "hidden", "status": hidden_status})
    if "mutation" in required:
        adapters.append(
            _mutation_result(
                adapter=adapter,
                harness_root=harness_root,
                pair=pair,
                manifest=code,
                public_checks=[
                    check for check in internal_contract["checks"] if check["visibility"] == "public"
                ],
            )
        )
    if "property" in required:
        adapters.append(
            _property_result(
                adapter=adapter,
                harness_root=harness_root,
                pair=pair,
                manifest=code,
            )
        )
    zero_after = snapshot_tree(Path(zero["workspace_root"]))["sha256"]
    code_after = snapshot_tree(Path(code["workspace_root"]))["sha256"]
    candidate_unmodified = zero_before == zero_after and code_before == code_after
    snapshot_bound = code_after == code["frozen_snapshot"]
    eligible = (
        all(item["satisfied"] for item in relations)
        and all(item["status"] == "PASS" for item in adapters)
        and candidate_unmodified
        and snapshot_bound
    )
    used_seconds = sum(
        float(item.get("budget", {}).get("used_seconds", 0.0))
        for item in zero_checks + code_checks
    ) + sum(
        float(item.get("budget", {}).get("used_seconds", 0.0))
        for item in adapters
    ) + float(code.get("budget_used", {}).get("seconds", 0.0))
    used_output = sum(
        int(item.get("budget", {}).get("used_output_bytes", 0))
        for item in zero_checks + code_checks
    ) + sum(
        int(item.get("budget", {}).get("used_output_bytes", 0))
        for item in adapters
    ) + int(code.get("budget_used", {}).get("output_bytes", 0))
    change_surface = _change_surface(Path(zero["workspace_root"]), Path(code["workspace_root"]))
    verdict = {
        "schema_version": "2.0.0",
        "pair_id": pair_id,
        "case_id": case_id,
        "zero_run_id": zero["run_id"],
        "code_run_id": code_run_id,
        "base_revision": pair["base_revision"],
        "baseline_snapshot": pair["baseline_snapshot"],
        "code_snapshot": code["frozen_snapshot"],
        "floor_definition_sha256": pair["floor_definition_sha256"],
        "floor_integrity": floor_integrity,
        "checks": relations,
        "zero_results": zero_checks,
        "code_results": code_checks,
        "adapters": adapters,
        "candidate_unmodified": candidate_unmodified,
        "snapshot_bound": snapshot_bound,
        "eligible": eligible,
        "measures": {
            "change_surface_files": change_surface["files"],
            "change_surface": change_surface,
            "budget_seconds_used": round(used_seconds, 3),
            "budget_output_bytes_used": used_output,
            "reversible": True,
            "human_intervention": False,
        },
        "recorded_at": utc_now(),
    }
    save_json(pair_verdict_path(harness_root, pair_id, code_run_id), verdict)
    save_json(
        verdict_path(harness_root, code_run_id),
        {
            "schema_version": "2.0.0",
            "pair_id": pair_id,
            "run_id": code_run_id,
            "eligible": eligible,
            "pair_verdict_ref": str(pair_verdict_path(harness_root, pair_id, code_run_id)),
            "code_snapshot": code["frozen_snapshot"],
            "floor_definition_sha256": pair["floor_definition_sha256"],
        },
    )
    pair["state"] = "verified" if eligible else "refused"
    pair["updated_at"] = utc_now()
    save_pair(harness_root, pair)
    EventLedger(harness_root).append(
        "pair_verdict_recorded",
        case_id,
        {
            "pair_id": pair_id,
            "zero_run_id": zero["run_id"],
            "code_run_id": code_run_id,
            "eligible": eligible,
            "base_revision": pair["base_revision"],
            "code_snapshot": code["frozen_snapshot"],
            "floor_definition_sha256": pair["floor_definition_sha256"],
        },
        actor="verifier",
    )
    record_experiment(
        workspace,
        case_id,
        {
            "pair_id": pair_id,
            "run_id": code_run_id,
            "kind": "code",
            "revision": pair["base_revision"],
            "snapshot": code["frozen_snapshot"],
            "verdict_ref": str(pair_verdict_path(harness_root, pair_id, code_run_id)),
            "eligible": eligible,
            "floor_definition_sha256": pair["floor_definition_sha256"],
            "isolation": code["isolation"],
        },
        actor="verifier",
    )
    return verdict


def _vector(verdict: dict[str, Any]) -> tuple[float, float, int, int]:
    measures = verdict["measures"]
    return (
        -float(measures["change_surface_files"]),
        -float(measures["budget_seconds_used"]),
        1 if measures.get("reversible", True) else 0,
        0 if measures.get("human_intervention", False) else 1,
    )


def _dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_vector = _vector(left)
    right_vector = _vector(right)
    return all(a >= b for a, b in zip(left_vector, right_vector)) and any(
        a > b for a, b in zip(left_vector, right_vector)
    )


def compare_pair(workspace: Path, case_id: str, pair_id: str) -> dict[str, Any]:
    harness_root = workspace.resolve() / ".harness"
    require_writable_pack(CaseStore(harness_root).load(case_id))
    pair = load_pair(harness_root, pair_id)
    if pair["case_id"] != case_id:
        raise PairStateError("pair does not belong to case")
    verdicts = [
        load_json(path)
        for run_id in pair.get("code_run_ids", [])
        if (path := pair_verdict_path(harness_root, pair_id, run_id)).is_file()
    ]
    eligible = [item for item in verdicts if item.get("eligible")]
    frontier = [
        item["code_run_id"]
        for item in eligible
        if not any(
            other["code_run_id"] != item["code_run_id"] and _dominates(other, item)
            for other in eligible
        )
    ]
    return {
        "pair_id": pair_id,
        "case_id": case_id,
        "eligible_run_ids": [item["code_run_id"] for item in eligible],
        "frontier_run_ids": frontier,
        "verdicts": verdicts,
    }


def latest_eligible_pair(workspace: Path, case_id: str) -> dict[str, Any] | None:
    harness_root = workspace.resolve() / ".harness"
    root = harness_root / "experiments" / "pairs"
    if not root.is_dir():
        return None
    candidates: list[dict[str, Any]] = []
    for manifest_path_value in sorted(root.glob("*/pair-manifest.json")):
        pair = load_json(manifest_path_value)
        if pair.get("case_id") != case_id:
            continue
        comparison = compare_pair(workspace, case_id, pair["pair_id"])
        for run_id in comparison["frontier_run_ids"]:
            verdict = next(
                item for item in comparison["verdicts"] if item["code_run_id"] == run_id
            )
            candidates.append(verdict)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item["recorded_at"])[-1]
