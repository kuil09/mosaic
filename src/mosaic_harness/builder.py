"""Scripted Builder adapter. No model provider is included."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class BuilderPathError(ValueError):
    pass


def load_instructions(script_path: Path) -> list[dict[str, Any]]:
    script_path = script_path.resolve()
    if not script_path.is_file():
        raise ValueError(f"builder script does not exist: {script_path}")
    payload = json.loads(script_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("builder script must be a JSON array")
    return payload


def confined_writes(
    workspace_root: Path, instructions: list[dict[str, Any]]
) -> list[tuple[Path, str]]:
    root = workspace_root.resolve()
    writes: list[tuple[Path, str]] = []
    for index, item in enumerate(instructions):
        if not isinstance(item, dict) or item.get("op") != "write":
            raise ValueError(f"builder script[{index}] must be a write operation")
        relative = item.get("path")
        contents = item.get("contents")
        if not isinstance(relative, str) or not isinstance(contents, str):
            raise ValueError(f"builder script[{index}] needs string path and contents")
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise BuilderPathError("script path escapes candidate workspace")
        destination = (root / candidate).resolve()
        if destination != root and root not in destination.parents:
            raise BuilderPathError("script path escapes candidate workspace")
        writes.append((destination, contents))
    return writes


def apply_script_source(writes: list[tuple[Path, str]]) -> str:
    payload = [{"path": str(path), "contents": contents} for path, contents in writes]
    encoded = json.dumps(payload)
    return (
        "import json\n"
        "from pathlib import Path\n"
        f"items = json.loads({encoded!r})\n"
        "for item in items:\n"
        "    path = Path(item['path'])\n"
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    path.write_text(item['contents'], encoding='utf-8')\n"
        "print('builder-script-ok')\n"
    )
