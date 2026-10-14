"""Fail-closed backend for profiles that admit no arbitrary code execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .local import ExecResult


@dataclass
class DisabledBackend:
    """Refuse every command without touching a host/container runtime."""

    workdir: Path
    timeout: float = 60.0
    host_visible_fs: bool = False

    def exec(self, cmd: str, timeout: float | None = None) -> ExecResult:
        del cmd, timeout
        return ExecResult(
            stdout="",
            stderr=(
                "arbitrary command execution is disabled by the admitted "
                "production surface"
            ),
            exit_code=126,
        )


__all__ = ["DisabledBackend"]
