"""Bounded candidate workspace materialization."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mosaic_harness.isolation import PROTECTED_RELATIVE_PATHS
from mosaic_harness.scanner import IGNORED_DIRECTORIES
from mosaic_harness.util import canonical_json, sha256_bytes, sha256_file

PROTECTED_MATERIALIZATION_PATHS = PROTECTED_RELATIVE_PATHS + (".harness/experiments",)


SNAPSHOT_IGNORED_DIRECTORIES = IGNORED_DIRECTORIES | {".tmp", ".mosaic"}


def iter_repository_files(repository: Path) -> list[Path]:
    repository = repository.resolve()
    files: list[Path] = []
    for current_root, directories, filenames in os.walk(repository):
        directories[:] = sorted(
            directory for directory in directories if directory not in IGNORED_DIRECTORIES
        )
        root_path = Path(current_root)
        for filename in sorted(filenames):
            path = root_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            files.append(path)
    return files


def copy_repository(
    repository: Path,
    destination: Path,
    *,
    max_files: int = 500,
    max_file_bytes: int = 2 * 1024 * 1024,
) -> dict[str, Any]:
    repository = repository.resolve()
    destination = destination.resolve()
    if not repository.is_dir():
        raise ValueError(f"repository is not a directory: {repository}")
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped_large = 0
    truncated = False
    for path in iter_repository_files(repository):
        if copied >= max_files:
            truncated = True
            break
        size = path.stat().st_size
        if size > max_file_bytes:
            skipped_large += 1
            continue
        target = destination / path.relative_to(repository)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return {
        "copied_file_count": copied,
        "skipped_large_file_count": skipped_large,
        "truncated": truncated,
    }


def git_head(repository: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--verify", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def strip_protected_paths(root: Path) -> None:
    for relative in PROTECTED_MATERIALIZATION_PATHS:
        target = root / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def add_detached_worktree(repository: Path, destination: Path, commit: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--detach",
            str(destination),
            commit,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git worktree add failed")


def materialize_repository(
    repository: Path,
    destination: Path,
    *,
    max_files: int = 500,
    max_file_bytes: int = 2 * 1024 * 1024,
) -> dict[str, Any]:
    commit = git_head(repository)
    if commit:
        try:
            add_detached_worktree(repository, destination, commit)
            strip_protected_paths(destination)
            return {"method": "git-worktree", "commit": commit}
        except (OSError, RuntimeError):
            if destination.exists():
                shutil.rmtree(destination)
    copied = copy_repository(
        repository,
        destination,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
    )
    if copied["truncated"] or copied["skipped_large_file_count"]:
        if destination.exists():
            shutil.rmtree(destination)
        raise RuntimeError(
            "bounded repository copy was incomplete; use a Git repository or raise explicit limits"
        )
    strip_protected_paths(destination)
    return {"method": "bounded-copy", "commit": commit, **copied}


def copy_tree_if_present(source: Path, destination: Path) -> int:
    if not source.is_dir():
        return 0
    copied = 0
    for path in source.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied


def snapshot_tree(root: Path) -> dict[str, Any]:
    root = root.resolve()
    artifacts: list[dict[str, Any]] = []
    for current_root, directories, filenames in os.walk(root):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory not in SNAPSHOT_IGNORED_DIRECTORIES
        )
        base = Path(current_root)
        for filename in sorted(filenames):
            path = base / filename
            if path.is_symlink() or not path.is_file():
                continue
            artifacts.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    digest = sha256_bytes(canonical_json(artifacts).encode("utf-8"))
    return {
        "sha256": digest,
        "file_count": len(artifacts),
        "artifacts": artifacts,
    }


def protected_paths_present(workspace_root: Path) -> list[str]:
    present: list[str] = []
    for relative in PROTECTED_RELATIVE_PATHS:
        if (workspace_root / relative).exists():
            present.append(relative)
    return present


def scan_for_tokens(haystacks: list[str], tokens: list[str]) -> list[str]:
    return [token for token in tokens if token and any(token in hay for hay in haystacks)]


def hidden_tokens(hidden_root: Path) -> list[str]:
    tokens: list[str] = []
    if not hidden_root.is_dir():
        return tokens
    for path in hidden_root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            tokens.append(text)
    return tokens


def workspace_texts(workspace_root: Path) -> list[str]:
    texts: list[str] = []
    if not workspace_root.is_dir():
        return texts
    for path in workspace_root.rglob("*"):
        if not path.is_file():
            continue
        texts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return texts


def dispose_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
