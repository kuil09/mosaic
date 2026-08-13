"""Versioned run manifests and the local command adapter."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from mosaic_harness.isolation import classify_denial, wrap_command
from mosaic_harness.util import atomic_write_json, utc_now


MANIFEST_VERSION = "1.0.0"
DEFAULT_BUDGET = {"max_seconds": 30, "max_output_bytes": 65536}


class CandidateNotFoundError(FileNotFoundError):
    pass


class RunStateError(ValueError):
    pass


class LocalCommandAdapter:
    """Run one local command under the supplied isolation profile."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        profile: str,
        budget: Mapping[str, int],
        env: Mapping[str, str],
    ) -> dict[str, Any]:
        max_seconds = int(budget.get("max_seconds", DEFAULT_BUDGET["max_seconds"]))
        max_output = int(budget.get("max_output_bytes", DEFAULT_BUDGET["max_output_bytes"]))
        wrapped = wrap_command(command, profile)
        merged = os.environ.copy()
        merged.update(env)
        started = time.monotonic()
        process = subprocess.Popen(
            wrapped,
            cwd=str(cwd),
            env=merged,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=max_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        elapsed = time.monotonic() - started
        stdout = stdout or ""
        stderr = stderr or ""
        exit_code = process.returncode if not timed_out else None
        denial = classify_denial(0 if timed_out else exit_code, stdout, stderr)
        return {
            "command": list(command),
            "wrapped_command": [wrapped[0], "-p", "<isolation-profile>", "--", *list(command)],
            "exit_code": exit_code,
            "stdout": stdout[:max_output],
            "stderr": stderr[:max_output],
            "interrupted": timed_out,
            "timed_out": timed_out,
            "denial": denial,
            "budget": {
                "max_seconds": max_seconds,
                "max_output_bytes": max_output,
                "used_seconds": round(elapsed, 3),
                "used_output_bytes": len(stdout.encode("utf-8")) + len(stderr.encode("utf-8")),
            },
            "finished_at": utc_now(),
        }


def normalize_budget(budget: Mapping[str, int] | None) -> dict[str, int]:
    source = budget or {}
    max_seconds = int(source.get("max_seconds", DEFAULT_BUDGET["max_seconds"]))
    max_output = int(source.get("max_output_bytes", DEFAULT_BUDGET["max_output_bytes"]))
    if max_seconds < 1:
        raise ValueError("max_seconds must be at least 1")
    if max_output < 1:
        raise ValueError("max_output_bytes must be at least 1")
    return {"max_seconds": max_seconds, "max_output_bytes": max_output}


def run_path(harness_root: Path, run_id: str) -> Path:
    return harness_root / "experiments" / "candidates" / run_id


def manifest_path(harness_root: Path, run_id: str) -> Path:
    return run_path(harness_root, run_id) / "run-manifest.json"


def verdict_path(harness_root: Path, run_id: str) -> Path:
    return run_path(harness_root, run_id) / "verdict.json"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CandidateNotFoundError(f"run artifact not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"run artifact is not an object: {path}")
    return value


def save_json(path: Path, value: dict[str, Any]) -> Path:
    atomic_write_json(path, value)
    return path
