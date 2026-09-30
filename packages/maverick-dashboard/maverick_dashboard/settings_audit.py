"""One activity-record row for a deployment-wide setting changed outside
Settings > All settings (the Trust page switch, Store catalog addresses,
turning a module on). Same row shape as All settings: who made the change,
which setting, the value before and after, and whether a loosening was
confirmed. Callers pass values that are never secret."""
from __future__ import annotations

from fastapi import Request


def record_setting_change(request: Request, *, section: str, key: str, old: object,
                          new: object, confirmed: bool = False) -> None:
    """Write the row; :class:`maverick.audit.AuditRefused` propagates."""
    from maverick.audit import EventKind, audit_event

    from . import auth

    audit_event(
        EventKind.SETTING_CHANGED,
        agent="dashboard",
        _global=True,
        actor=auth.caller_principal(request) or "local administrator",
        section=section,
        key=key,
        old=old,
        new=new,
        confirmed=bool(confirmed),
    )


__all__ = ["record_setting_change"]
