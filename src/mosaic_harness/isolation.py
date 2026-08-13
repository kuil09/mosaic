"""Process isolation for Builder and Verifier roles.

V1 process isolation is implemented and tested with macOS
``/usr/bin/sandbox-exec``. Other platforms are not claimed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Sequence


SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
TESTED_PLATFORM = "darwin"
TESTED_MECHANISM = "sandbox-exec"

PROTECTED_RELATIVE_PATHS: tuple[str, ...] = (
    ".harness/constitution",
    ".harness/evaluators/hidden",
    ".harness/historian",
    ".harness/future/scenarios",
)


class IsolationUnavailableError(RuntimeError):
    """Raised when V1 isolation cannot be enforced on this platform."""


def isolation_available() -> bool:
    return sys.platform == "darwin" and SANDBOX_EXEC.is_file()


def describe_isolation() -> dict[str, object]:
    available = isolation_available()
    return {
        "platform": sys.platform,
        "mechanism": TESTED_MECHANISM if available else "unavailable",
        "tested_platform": TESTED_PLATFORM,
        "available": available,
        "detail": (
            "Process isolation is enforced with macOS /usr/bin/sandbox-exec."
            if available
            else "V1 process isolation is implemented and tested only with "
            "macOS /usr/bin/sandbox-exec. This platform is not claimed."
        ),
    }


def require_isolation() -> dict[str, object]:
    description = describe_isolation()
    if not description["available"]:
        raise IsolationUnavailableError(str(description["detail"]))
    return description


def resolve_protected_paths(*roots: Path) -> list[Path]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        base = root.resolve()
        for relative in PROTECTED_RELATIVE_PATHS:
            path = (base / relative).resolve()
            if path not in seen:
                seen.add(path)
                resolved.append(path)
    return resolved


def _subpath_lines(paths: Iterable[Path]) -> str:
    return "\n".join(f'  (subpath "{path.resolve()}")' for path in paths)


def render_builder_profile(*, writable: Path, denied_read: Sequence[Path]) -> str:
    clauses = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        "(allow file-write*",
        f'  (subpath "{writable.resolve()}")',
        '  (subpath "/dev")',
        ")",
    ]
    denied = [path for path in denied_read if path is not None]
    if denied:
        clauses.append("(deny file-read*")
        clauses.append(_subpath_lines(denied))
        clauses.append(")")
    return "\n".join(clauses) + "\n"


def render_verifier_profile(*, writable: Path, candidate: Path) -> str:
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*)\n"
        "(allow file-write*\n"
        f'  (subpath "{writable.resolve()}")\n'
        '  (subpath "/dev")\n'
        ")\n"
        "(deny file-write*\n"
        f'  (subpath "{candidate.resolve()}")\n'
        ")\n"
    )


def wrap_command(command: Sequence[str], profile: str) -> list[str]:
    require_isolation()
    if not command:
        raise ValueError("sandboxed command must not be empty")
    return [str(SANDBOX_EXEC), "-p", profile, "--", *command]


def classify_denial(exit_code: int | None, stdout: str, stderr: str) -> str | None:
    if exit_code == 0:
        return None
    text = f"{stdout}\n{stderr}"
    markers = (
        "Operation not permitted",
        "PermissionError",
        "Permission denied",
        "EPERM",
    )
    if any(marker in text for marker in markers):
        return "access denied by process isolation"
    return None
