"""Bounded, read-only repository inventory for investigation provenance."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from mosaic_harness.util import canonical_json, sha256_bytes, sha256_file


IGNORED_DIRECTORIES = {
    ".git",
    ".harness",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}

DOCUMENT_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc"}
TEST_MARKERS = {"test", "tests", "spec", "specs", "__tests__"}
CONFIG_NAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "Cargo.toml",
    "Dockerfile",
    "go.mod",
    "package.json",
    "pyproject.toml",
}


def _category(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    name = path.name
    if any(marker in parts or name.lower().startswith(f"{marker}_") for marker in TEST_MARKERS):
        return "test"
    if path.suffix.lower() in DOCUMENT_SUFFIXES:
        return "document"
    if name in CONFIG_NAMES or path.suffix.lower() in {".yaml", ".yml", ".toml", ".json"}:
        return "configuration"
    if path.suffix.lower() in {".log", ".trace"}:
        return "log"
    return "source"


def _walk(repository: Path) -> Iterable[Path]:
    for current_root, directories, files in os.walk(repository):
        directories[:] = sorted(
            directory for directory in directories if directory not in IGNORED_DIRECTORIES
        )
        root_path = Path(current_root)
        for filename in sorted(files):
            path = root_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            yield path


def scan_repository(
    repository: Path,
    *,
    max_files: int = 500,
    max_file_bytes: int = 2 * 1024 * 1024,
) -> dict[str, Any]:
    repository = repository.resolve()
    if not repository.is_dir():
        raise ValueError(f"repository is not a directory: {repository}")
    artifacts: list[dict[str, Any]] = []
    skipped_large = 0
    truncated = False
    for path in _walk(repository):
        if len(artifacts) >= max_files:
            truncated = True
            break
        stat = path.stat()
        if stat.st_size > max_file_bytes:
            skipped_large += 1
            continue
        artifacts.append(
            {
                "path": path.relative_to(repository).as_posix(),
                "category": _category(path.relative_to(repository)),
                "size_bytes": stat.st_size,
                "sha256": sha256_file(path),
            }
        )
    counts: dict[str, int] = {}
    for artifact in artifacts:
        category = artifact["category"]
        counts[category] = counts.get(category, 0) + 1
    fingerprint = sha256_bytes(canonical_json(artifacts).encode("utf-8"))
    return {
        "artifacts": artifacts,
        "fingerprint_sha256": fingerprint,
        "counts_by_category": counts,
        "scanned_file_count": len(artifacts),
        "skipped_large_file_count": skipped_large,
        "truncated": truncated,
        "limits": {"max_files": max_files, "max_file_bytes": max_file_bytes},
    }
