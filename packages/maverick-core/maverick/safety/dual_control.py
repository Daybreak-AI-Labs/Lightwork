"""N-of-M dual control (segregation of duties) for the approvals queue.

A high-/critical-risk action can require **N distinct approvers** before it is
granted, and by default the requester cannot approve their own request. This is
the "two-person rule" auditors test for under SOX / SOC 2 CC / HIPAA. Off by
default (``required = 1`` -> the legacy single approver); opt-in per risk band::

    [security]
    approvals_required = 2          # applies to every parked approval
    allow_self_approval = false     # default: the requester can't self-approve

    # ...or require more approvers only for the riskiest actions:
    [security.approvals_required]
    high = 2
    critical = 3
    default = 1

    # ...or keep one number for everything and raise it for some risk levels
    # (the dashboard's per-level table; it can only ADD approvers):
    [security.approvals_required_by_risk]
    critical = 3

The enforcement lives in ``WorldModel.decide_approval`` (distinctness via the
``approval_signoffs`` PK + the self-approval bar); this module only resolves the
configured quorum at park time.
"""
from __future__ import annotations

import os
from typing import Any

_RISK_ORDER = ("low", "medium", "high", "critical")


def _security_cfg() -> dict[str, Any]:
    try:
        from ..config import load_config
        sec = (load_config() or {}).get("security") or {}
    except Exception:  # pragma: no cover -- config never blocks a request
        return {}
    return sec if isinstance(sec, dict) else {}


def _coerce_int(value: object, default: int = 1) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return n if n >= 1 else default


def required_approvals(risk: str | None = None) -> int:
    """Quorum (N distinct approvers) required for an action at ``risk``.

    Resolution: ``MAVERICK_APPROVALS_REQUIRED`` env (global) wins; else
    ``[security] approvals_required`` -- an int (applies to all) or a per-risk
    table (``high``/``critical``/... with an optional ``default``). Always >= 1;
    1 means the legacy single-approver behaviour (no dual control).

    ``[security] approvals_required_by_risk`` (a per-risk table) then raises
    the number for the levels it names: it never lowers it, so a stale row can
    only ask for more people, never fewer."""
    env = os.environ.get("MAVERICK_APPROVALS_REQUIRED")
    if env is not None and env.strip() != "":
        return _coerce_int(env, 1)
    sec = _security_cfg()
    base = _base_approvals(sec.get("approvals_required"), risk)
    by_risk = sec.get("approvals_required_by_risk")
    r = str(risk or "").strip().lower()
    if isinstance(by_risk, dict) and r in by_risk:
        return max(base, _coerce_int(by_risk[r], 1))
    return base


def _base_approvals(val: object, risk: str | None) -> int:
    if isinstance(val, dict):
        r = str(risk or "").strip().lower()
        if r in val:
            return _coerce_int(val[r], 1)
        if "default" in val:
            return _coerce_int(val["default"], 1)
        return 1
    if val is None:
        return 1
    return _coerce_int(val, 1)


def allow_self_approval() -> bool:
    """Whether the requester may approve their own request. Default **False**
    (segregation of duties). ``MAVERICK_ALLOW_SELF_APPROVAL`` env wins over
    ``[security] allow_self_approval``."""
    env = os.environ.get("MAVERICK_ALLOW_SELF_APPROVAL")
    if env is not None and env.strip() != "":
        return env.strip().lower() in {"1", "true", "yes", "on"}
    v = _security_cfg().get("allow_self_approval")
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on"}
    return bool(v)


__all__ = ["required_approvals", "allow_self_approval"]
