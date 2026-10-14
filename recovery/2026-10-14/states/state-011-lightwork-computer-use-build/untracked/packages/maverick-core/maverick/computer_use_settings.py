"""Canonical computer-use settings with environment precedence."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _env(name: str, fallback: Any) -> Any:
    value = os.environ.get(name)
    return value if value is not None and value.strip() else fallback


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    return default


def _integer(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(parsed, high))


@dataclass(frozen=True)
class ComputerUseSettings:
    mode: str
    require_sandbox: bool
    allow_host: bool
    execution_cell_url: str
    execution_cell_id: str
    execution_cell_token: str
    evidence_sealing_key: str
    monitor: int | None
    device: str
    consent_mode: str
    max_sessions: int
    session_timeout: int
    heartbeat_timeout: int
    require_evidence: bool
    evidence_retention_days: int

    @property
    def execution_cell_enrolled(self) -> bool:
        return bool(
            self.execution_cell_url
            and self.execution_cell_id
            and self.execution_cell_token
        )

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("execution_cell_token", None)
        result.pop("evidence_sealing_key", None)
        result["execution_cell_enrolled"] = self.execution_cell_enrolled
        result["evidence_sealing_key_enrolled"] = bool(self.evidence_sealing_key)
        return result


def load_computer_use_settings() -> ComputerUseSettings:
    """Read unsealed config, then overlay explicit process environment."""
    from .config import load_config

    raw = (load_config().get("computer_use") or {})
    cfg = dict(raw) if isinstance(raw, dict) else {}
    mode = str(_env("MAVERICK_COMPUTER_BACKEND", cfg.get("mode", "remote"))).strip().lower()
    if mode not in {"remote", "host", "disabled"}:
        mode = "invalid"
    monitor_raw = _env("MAVERICK_COMPUTER_MONITOR", cfg.get("monitor"))
    try:
        monitor = int(monitor_raw) if monitor_raw not in {None, ""} else None
    except (TypeError, ValueError):
        monitor = None
    if monitor is not None and monitor < 1:
        monitor = None
    return ComputerUseSettings(
        mode=mode,
        require_sandbox=_bool(
            _env("MAVERICK_COMPUTER_REQUIRE_SANDBOX", cfg.get("require_sandbox", True)),
            True,
        ),
        allow_host=_bool(
            _env("MAVERICK_COMPUTER_ALLOW_HOST", cfg.get("allow_host", False)),
            False,
        ),
        execution_cell_url=str(
            _env("MAVERICK_COMPUTER_CELL_URL", cfg.get("execution_cell_url", ""))
        ).strip(),
        execution_cell_id=str(
            _env("MAVERICK_COMPUTER_EXECUTION_CELL_ID", cfg.get("execution_cell_id", ""))
        ).strip(),
        execution_cell_token=str(
            _env("MAVERICK_COMPUTER_CELL_KEY", cfg.get("execution_cell_token", ""))
        ).strip(),
        evidence_sealing_key=str(
            _env(
                "MAVERICK_COMPUTER_EVIDENCE_SEALING_KEY",
                cfg.get("evidence_sealing_key", ""),
            )
        ).strip(),
        monitor=monitor,
        device=str(_env("MAVERICK_COMPUTER_DEVICE", cfg.get("device", ""))).strip(),
        consent_mode=str(
            _env("MAVERICK_CONSENT_MODE", cfg.get("consent_mode", "ask"))
        ).strip().lower(),
        max_sessions=_integer(
            _env("MAVERICK_COMPUTER_MAX_SESSIONS", cfg.get("max_sessions", 8)),
            8, 1, 1_000,
        ),
        session_timeout=_integer(
            _env("MAVERICK_COMPUTER_SESSION_TIMEOUT", cfg.get("session_timeout", 3_600)),
            3_600, 30, 86_400,
        ),
        heartbeat_timeout=_integer(
            _env("MAVERICK_COMPUTER_HEARTBEAT_TIMEOUT", cfg.get("heartbeat_timeout", 60)),
            60, 5, 3_600,
        ),
        require_evidence=_bool(
            _env("MAVERICK_COMPUTER_REQUIRE_EVIDENCE", cfg.get("require_evidence", False)),
            False,
        ),
        evidence_retention_days=_integer(
            _env(
                "MAVERICK_COMPUTER_EVIDENCE_RETENTION_DAYS",
                cfg.get("evidence_retention_days", 30),
            ),
            30, 1, 3_650,
        ),
    )


def public_computer_use_settings() -> dict[str, Any]:
    return load_computer_use_settings().public_dict()


__all__ = [
    "ComputerUseSettings", "load_computer_use_settings",
    "public_computer_use_settings",
]
