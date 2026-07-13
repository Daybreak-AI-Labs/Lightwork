"""Per-user department (suite) access grants: job-function scoping (kernel).

The dashboard's privilege RBAC (``maverick_dashboard.rbac``) answers "how MUCH
may this user do?"; this store answers the orthogonal "WHICH part of the
workforce may they use?" — a finance analyst granted ``{"finance", "tax"}``
works with Finance and Tax specialists, never Legal's.

This module is the KERNEL home of the store and its enforcement so that every
caller — the dashboard, the CLI, and any future networked surface — shares one
source of truth (:func:`ensure_suite_allowed` at the deploy/dispatch
chokepoints in :mod:`maverick.departments` and :mod:`maverick.fleet`).
``maverick_dashboard.suite_grants`` re-exports this module for backward
compatibility; the dashboard additionally layers SCIM-group-derived grants on
top at HTTP resolution time (see ``maverick_dashboard.auth.caller_suites``).

Safety invariants:
  * No/empty acting principal (host operator via the CLI, auth-off dashboard)
    -> unrestricted; every gate is a no-op — kernel rule 1, fail open.
  * A configured dashboard admin (``MAVERICK_DASHBOARD_ADMINS`` /
    ``[dashboard] admins``) is never scoped.
  * No stored grant -> unrestricted by default (opt-in scoping);
    ``[dashboard] default_suites`` flips that to deny-by-default.
  * The store is control-plane data: one GLOBAL file, never per-tenant.

Store: ``~/.maverick/dashboard-user-suites.json`` (0600),
``{principal: [suite, ...]}``. An explicit EMPTY list is a valid grant meaning
"no departments at all"; remove the entry to lift scoping.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class DepartmentAccessError(PermissionError):
    """The acting principal's department grant does not include this suite."""

    def __init__(self, principal: str, suite: str) -> None:
        self.principal = principal
        self.suite = suite
        super().__init__(
            f"principal {principal!r} is not permitted to use the "
            f"{suite!r} department"
        )


# Serializes a grants load-modify-save in-process; cross_process_lock in
# _locked() extends it across processes (multiple dashboard workers edit grants).
_GRANTS_LOCK = threading.Lock()


def _locked(path: Path):
    from contextlib import ExitStack

    from .file_lock import cross_process_lock
    stack = ExitStack()
    stack.enter_context(_GRANTS_LOCK)
    stack.enter_context(cross_process_lock(path))
    return stack


def store_path() -> Path:
    return Path.home() / ".maverick" / "dashboard-user-suites.json"


def known_suites() -> frozenset[str]:
    """Every suite key a grant may name (the department catalog's key space)."""
    from .domain import SUITE_PREFIXES
    return frozenset(SUITE_PREFIXES.values())


def admin_principals() -> frozenset[str]:
    """The configured dashboard admins (never scoped by department grants).

    Mirrors ``maverick_dashboard.auth.is_dashboard_admin`` exactly — the
    ``MAVERICK_DASHBOARD_ADMINS`` env (comma-separated) overrides the
    ``[dashboard] admins`` config list — so the kernel gate and the dashboard
    gate can never disagree about who bypasses scoping."""
    env = (os.environ.get("MAVERICK_DASHBOARD_ADMINS") or "").strip()
    if env:
        return frozenset(a.strip() for a in env.split(",") if a.strip())
    try:
        from .config import load_config
        raw = (load_config().get("dashboard", {}) or {}).get("admins", []) or []
    except Exception:  # pragma: no cover - config read must never gate access
        raw = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    return frozenset(str(a).strip() for a in raw if str(a).strip())


def is_admin_principal(principal: str) -> bool:
    return bool(principal) and principal in admin_principals()


def default_suites() -> frozenset[str] | None:
    """Suites for an authenticated user with NO explicit grant.

    ``None`` (the default, and on any config problem) means unrestricted —
    scoping is opt-in per user. Set ``[dashboard] default_suites = ["finance"]``
    for deny-by-default; unknown suite keys in the list are ignored."""
    try:
        from .config import load_config
        raw = (load_config().get("dashboard", {}) or {}).get("default_suites")
    except Exception:  # pragma: no cover - config read must never gate auth
        return None
    if not isinstance(raw, (list, tuple)):
        return None
    return frozenset(str(s) for s in raw) & known_suites()


def _load() -> dict[str, list[str]]:
    p = store_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Re-validate on read so a hand-edited / corrupt store can't inject an
    # unknown suite or a non-string principal.
    known = known_suites()
    out: dict[str, list[str]] = {}
    for k, v in data.items():
        if not (isinstance(k, str) and k.strip() and isinstance(v, list)):
            continue
        out[str(k)] = sorted({str(s) for s in v if isinstance(s, str)} & known)
    return out


def _write(data: dict[str, list[str]]) -> None:
    from .file_lock import atomic_write_text
    atomic_write_text(store_path(), json.dumps(data, indent=2, sort_keys=True))


def list_grants() -> dict[str, list[str]]:
    """All explicitly-scoped ``{principal: [suite, ...]}`` assignments."""
    return _load()


def get_grant(principal: str) -> frozenset[str] | None:
    """The principal's explicit grant, or ``None`` when they have no entry."""
    suites = _load().get((principal or "").strip())
    return None if suites is None else frozenset(suites)


# Additional grant sources (e.g. the dashboard's SCIM-group-derived grants)
# consulted AFTER the explicit store and BEFORE the configured default. Kept in
# the kernel so the deploy/dispatch gates and the dashboard HTTP gates resolve
# through the SAME chain and can never disagree.
_EXTRA_RESOLVERS: list = []


def register_grant_resolver(fn) -> None:
    """Register an extra grant source: ``fn(principal) -> iterable | None``.

    ``None`` means "no opinion" (fall through); an iterable of suite keys is a
    grant (validated against :func:`known_suites`). First non-None resolver
    wins, in registration order. Idempotent per function object; a resolver
    error is swallowed (fail open, kernel rule 1) rather than blocking work."""
    if fn not in _EXTRA_RESOLVERS:
        _EXTRA_RESOLVERS.append(fn)


def granted_suites(principal: str) -> frozenset[str] | None:
    """The suites ``principal`` may use, or ``None`` for unrestricted.

    Resolution order: explicit stored grant, then any registered extra source
    (SCIM groups), then the configured default (:func:`default_suites`) —
    which is itself ``None`` unless an operator opted the deployment into
    deny-by-default."""
    explicit = get_grant(principal)
    if explicit is not None:
        return explicit
    for fn in _EXTRA_RESOLVERS:
        try:
            derived = fn(principal)
        except Exception:  # pragma: no cover - a grant source must fail open
            continue
        if derived is not None:
            return frozenset(str(s) for s in derived) & known_suites()
    return default_suites()


def _audit_grant_change(actor: str, principal: str, old, new) -> None:
    """One tamper-evident audit row per grant change, so who-granted-whom-what
    is provable rather than a silent JSON edit. Fail-soft: an audit error never
    blocks the mutation (mirrors the tenant registry's billing audit)."""
    try:
        from .audit import EventKind, record
        record(EventKind.ACCESS_GRANT_CHANGED, agent=actor or "local",
               actor=actor or "local", principal=principal, field="suites",
               tenant="", old=old, new=new)
    except Exception:  # pragma: no cover -- audit must never break the store
        pass


def set_suites(principal: str, suites: object, *, actor: str = "") -> None:
    """Scope ``principal`` to exactly ``suites`` (an iterable of suite keys)."""
    principal = (principal or "").strip()
    if not principal:
        raise ValueError("empty principal")
    if isinstance(suites, str) or not hasattr(suites, "__iter__"):
        raise ValueError("suites must be a list of suite keys")
    cleaned = {str(s).strip() for s in suites if str(s).strip()}
    unknown = cleaned - known_suites()
    if unknown:
        raise ValueError(f"unknown suite(s): {sorted(unknown)}")
    with _locked(store_path()):
        data = _load()
        old = data.get(principal)
        data[principal] = sorted(cleaned)
        _write(data)
    if old != sorted(cleaned):
        _audit_grant_change(actor, principal, old, sorted(cleaned))


def remove_grant(principal: str, *, actor: str = "") -> None:
    """Lift scoping for ``principal`` (back to the default: unrestricted)."""
    principal = (principal or "").strip()
    removed = None
    with _locked(store_path()):
        data = _load()
        removed = data.pop(principal, None)
        if removed is not None:
            _write(data)
    if removed is not None:
        _audit_grant_change(actor, principal, removed, None)


def suite_allowed_for(principal: str | None, suite: str | None) -> bool:
    """Whether ``principal`` may use department ``suite`` (kernel gate).

    ``principal`` None/empty — the host operator (CLI) or an auth-off
    dashboard — and configured admins are never scoped. ``suite`` None means
    a generic/legacy pack outside every department, never scoped."""
    if suite is None or not (principal or "").strip():
        return True
    principal = str(principal).strip()
    if is_admin_principal(principal):
        return True
    allowed = granted_suites(principal)
    return allowed is None or suite in allowed


def ensure_suite_allowed(principal: str | None, suite: str | None) -> None:
    """Raise :class:`DepartmentAccessError` unless the grant permits ``suite``."""
    if not suite_allowed_for(principal, suite):
        raise DepartmentAccessError(str(principal), str(suite))


__all__ = [
    "DepartmentAccessError", "store_path", "known_suites", "default_suites",
    "admin_principals", "is_admin_principal", "register_grant_resolver",
    "list_grants", "get_grant", "granted_suites", "set_suites", "remove_grant",
    "suite_allowed_for", "ensure_suite_allowed",
]
