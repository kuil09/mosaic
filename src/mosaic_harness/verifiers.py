"""Independent verifier adapters for public, hidden, mutation, property, and differential floors."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Protocol

from mosaic_harness.executor import LocalCommandAdapter
from mosaic_harness.util import canonical_json, sha256_bytes, sha256_file
from mosaic_harness.workspace import copy_tree_if_present


Runner = Callable[..., dict[str, Any]]


class VerifierAdapter(Protocol):
    name: str

    def input_records(self, run_root: Path, workspace_root: Path) -> list[dict[str, Any]]: ...

    def evaluate(
        self,
        *,
        run_root: Path,
        workspace_root: Path,
        runner: Runner,
        environment: dict[str, str],
        profile: str,
        budget: dict[str, int],
        extras: dict[str, Any],
    ) -> dict[str, Any]: ...


def _scripts(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*.py") if path.is_file())


def select_mutation_targets(workspace_root: Path) -> list[Path]:
    source_root = workspace_root / "src"
    if not source_root.is_dir():
        return []
    files = [
        path
        for path in sorted(source_root.rglob("*.py"))
        if path.is_file() and path.name != "__init__.py"
    ]
    preferred = [
        path
        for path in files
        if "return True" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    remainder = [path for path in files if path not in preferred]
    return preferred + remainder


def select_mutation_target(workspace_root: Path) -> Path | None:
    targets = select_mutation_targets(workspace_root)
    return targets[0] if targets else None


def _hash_tree(root: Path, prefix: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not root.is_dir():
        return records
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        records.append(
            {
                "path": f"{prefix}/{path.relative_to(root).as_posix()}",
                "sha256": sha256_file(path),
            }
        )
    return records


class PublicVerifier:
    name = "public"

    def input_records(self, run_root: Path, workspace_root: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = [{"builtin": "unittest.discover", "start": "tests"}]
        records.extend(_hash_tree(run_root / "evaluators" / "public", "public"))
        return records

    def evaluate(
        self,
        *,
        run_root: Path,
        workspace_root: Path,
        runner: Runner,
        environment: dict[str, str],
        profile: str,
        budget: dict[str, int],
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        commands: list[list[str]] = []
        if (workspace_root / "tests").is_dir():
            commands.append([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        for script in _scripts(run_root / "evaluators" / "public"):
            commands.append([sys.executable, str(script)])
        if not commands:
            return {"name": self.name, "status": None, "results": [], "configured": False}
        results = []
        passed = True
        for command in commands:
            result = runner(
                command,
                cwd=workspace_root,
                profile=profile,
                budget=budget,
                env=environment,
            )
            results.append({"command": command, "exit_code": result["exit_code"]})
            if result["exit_code"] != 0:
                passed = False
        return {"name": self.name, "status": passed, "results": results, "configured": True}


class HiddenVerifier:
    name = "hidden"

    def input_records(self, run_root: Path, workspace_root: Path) -> list[dict[str, Any]]:
        return _hash_tree(run_root / "evaluators" / "hidden", "hidden")

    def evaluate(
        self,
        *,
        run_root: Path,
        workspace_root: Path,
        runner: Runner,
        environment: dict[str, str],
        profile: str,
        budget: dict[str, int],
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        scripts = _scripts(run_root / "evaluators" / "hidden")
        if not scripts:
            return {"name": self.name, "status": None, "results": [], "configured": False}
        hidden_dir = run_root / "verifier" / "hidden"
        hidden_dir.mkdir(parents=True, exist_ok=True)
        results = []
        passed = True
        for script in scripts:
            result = runner(
                [sys.executable, str(script)],
                cwd=workspace_root,
                profile=profile,
                budget=budget,
                env=environment,
            )
            out_path = hidden_dir / f"{script.stem}.out"
            out_path.write_text(
                (result.get("stdout") or "") + (result.get("stderr") or ""),
                encoding="utf-8",
            )
            digest = sha256_file(out_path)
            results.append(
                {
                    "script": script.name,
                    "exit_code": result["exit_code"],
                    "output_sha256": digest,
                }
            )
            if result["exit_code"] != 0:
                passed = False
        return {"name": self.name, "status": passed, "results": results, "configured": True}


class MutationVerifier:
    name = "mutation"

    def input_records(self, run_root: Path, workspace_root: Path) -> list[dict[str, Any]]:
        records = [{"builtin": "mutation-kills", "target": "src/**/*.py"}]
        records.extend(_hash_tree(run_root / "evaluators" / "mutation", "mutation"))
        return records

    def evaluate(
        self,
        *,
        run_root: Path,
        workspace_root: Path,
        runner: Runner,
        environment: dict[str, str],
        profile: str,
        budget: dict[str, int],
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        targets = select_mutation_targets(workspace_root)
        if not targets or not (workspace_root / "tests").is_dir():
            return {"name": self.name, "status": None, "results": [], "configured": False}
        env = dict(environment)
        env["PYTHONPATH"] = str((run_root / "verifier" / "mutation-scratch") / "src")
        env["TMPDIR"] = str(run_root / "verifier" / "tmp")
        attempts: list[dict[str, Any]] = []
        killed = False
        for source in targets:
            scratch = run_root / "verifier" / "mutation-scratch"
            if scratch.exists():
                shutil.rmtree(scratch)
            copy_tree_if_present(workspace_root, scratch)
            relative = source.relative_to(workspace_root)
            mutant = scratch / relative
            if not mutant.is_file():
                continue
            original = mutant.read_text(encoding="utf-8")
            if "return True" in original:
                mutant.write_text(original.replace("return True", "return False", 1), encoding="utf-8")
            else:
                mutant.write_text(original + "\nraise RuntimeError('mosaic-mutation')\n", encoding="utf-8")
            env["PYTHONPATH"] = str(scratch / "src")
            result = runner(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                cwd=scratch,
                profile=profile,
                budget=budget,
                env=env,
            )
            this_killed = result["exit_code"] != 0
            attempts.append(
                {
                    "mutant": relative.as_posix(),
                    "killed": this_killed,
                    "exit_code": result["exit_code"],
                }
            )
            shutil.rmtree(scratch, ignore_errors=True)
            if this_killed:
                killed = True
                break
        return {
            "name": self.name,
            "status": killed,
            "results": attempts,
            "configured": True,
        }


class PropertyVerifier:
    name = "property"

    def input_records(self, run_root: Path, workspace_root: Path) -> list[dict[str, Any]]:
        return _hash_tree(run_root / "evaluators" / "property", "property")

    def evaluate(
        self,
        *,
        run_root: Path,
        workspace_root: Path,
        runner: Runner,
        environment: dict[str, str],
        profile: str,
        budget: dict[str, int],
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        scripts = _scripts(run_root / "evaluators" / "property")
        if not scripts:
            return {"name": self.name, "status": None, "results": [], "configured": False}
        results = []
        passed = True
        for script in scripts:
            result = runner(
                [sys.executable, str(script)],
                cwd=workspace_root,
                profile=profile,
                budget=budget,
                env=environment,
            )
            results.append({"script": script.name, "exit_code": result["exit_code"]})
            if result["exit_code"] != 0:
                passed = False
        return {"name": self.name, "status": passed, "results": results, "configured": True}


class DifferentialVerifier:
    name = "differential"

    def input_records(self, run_root: Path, workspace_root: Path) -> list[dict[str, Any]]:
        return [{"builtin": "differential-public", "peer": "zero-change"}]

    def evaluate(
        self,
        *,
        run_root: Path,
        workspace_root: Path,
        runner: Runner,
        environment: dict[str, str],
        profile: str,
        budget: dict[str, int],
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        peer = extras.get("zero_workspace")
        if not isinstance(peer, Path) or not peer.is_dir() or not (workspace_root / "tests").is_dir():
            return {"name": self.name, "status": None, "results": [], "configured": False}
        command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
        left = runner(command, cwd=workspace_root, profile=profile, budget=budget, env=environment)
        peer_env = dict(environment)
        peer_env["PYTHONPATH"] = str(peer / "src")
        right = runner(command, cwd=peer, profile=profile, budget=budget, env=peer_env)
        changed = (left["exit_code"] != right["exit_code"]) or (left.get("stdout") != right.get("stdout"))
        return {
            "name": self.name,
            "status": None,
            "results": [
                {
                    "changed": changed,
                    "candidate_exit": left["exit_code"],
                    "zero_exit": right["exit_code"],
                }
            ],
            "configured": True,
        }


ADAPTERS: tuple[VerifierAdapter, ...] = (
    PublicVerifier(),
    HiddenVerifier(),
    MutationVerifier(),
    PropertyVerifier(),
    DifferentialVerifier(),
)


HARD_FLOORS = {"public", "hidden", "mutation", "property"}


def floor_definition(run_root: Path, workspace_root: Path) -> str:
    items: list[dict[str, Any]] = []
    for adapter in ADAPTERS:
        items.extend(adapter.input_records(run_root, workspace_root))
    return sha256_bytes(canonical_json(items).encode("utf-8"))


def find_zero_workspace(harness_root: Path, case_id: str, current_run: str) -> Path | None:
    from mosaic_harness.executor import load_json

    root = harness_root / "experiments" / "candidates"
    if not root.is_dir():
        return None
    for manifest_file in sorted(root.glob("*/run-manifest.json")):
        try:
            manifest = load_json(manifest_file)
        except (OSError, ValueError):
            continue
        if (
            manifest.get("case_id") == case_id
            and manifest.get("kind") == "zero-change"
            and manifest.get("run_id") != current_run
        ):
            workspace = Path(manifest["workspace_root"])
            if workspace.is_dir():
                return workspace
    return None


def run_verification_suite(
    *,
    adapter: LocalCommandAdapter,
    run_root: Path,
    workspace_root: Path,
    environment: dict[str, str],
    profile: str,
    budget: dict[str, int],
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extras = extras or {}
    floors: dict[str, Any] = {}
    details: list[dict[str, Any]] = []
    hard_failed = False
    for verifier in ADAPTERS:
        result = verifier.evaluate(
            run_root=run_root,
            workspace_root=workspace_root,
            runner=adapter.run,
            environment=environment,
            profile=profile,
            budget=budget,
            extras=extras,
        )
        details.append(result)
        floors[verifier.name] = result["status"]
        if verifier.name in HARD_FLOORS and result["configured"] and result["status"] is False:
            hard_failed = True
    return {
        "floors": floors,
        "details": details,
        "hard_failed": hard_failed,
        "floor_definition": floor_definition(run_root, workspace_root),
    }


MUTATION_TEMPLATE = '''"""Generated mutation evaluator marker. Mosaic mutates a throwaway copy of src/."""
print("mutation-evaluator-present")
'''

PROPERTY_TEMPLATE = '''"""Generated property evaluator. Exit 0 if the candidate still satisfies a trivial invariant."""
from pathlib import Path
source = Path("src")
assert source.is_dir(), "src directory missing"
print("property-ok")
'''


def emit_challenger_evaluators(harness_root: Path) -> list[str]:
    created: list[str] = []
    mapping = {
        harness_root / "evaluators" / "mutation" / "generated.py": MUTATION_TEMPLATE,
        harness_root / "evaluators" / "property" / "generated.py": PROPERTY_TEMPLATE,
    }
    for path, contents in mapping.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            continue
        path.write_text(contents, encoding="utf-8")
        created.append(str(path))
    hidden = harness_root / "evaluators" / "hidden"
    hidden.mkdir(parents=True, exist_ok=True)
    return created
