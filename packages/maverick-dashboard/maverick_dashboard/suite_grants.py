"""Per-user department (suite) access grants: job-function scoping.

:mod:`maverick_dashboard.rbac` answers "how MUCH may this user do?" (admin /
operator / auditor / viewer — privilege). This store answers the orthogonal
"WHICH part of the workforce may they use?" — job function. A finance analyst
granted ``{"finance", "tax"}`` sees and dispatches Finance and Tax specialists;
Legal's never appear for them. Both gates must pass: a viewer granted
``finance`` still cannot dispatch anything (no ``operate`` permission), and an
operator scoped to finance cannot deploy the Legal department.

Safety invariants (mirror :mod:`.rbac`):
  * Auth off (no principal) -> unrestricted; every gate is a no-op — the local
    operator stays omnipotent, exactly as before.
  * No stored grant -> unrestricted by default, so deployments that never touch
    this feature are unchanged. ``[dashboard] default_suites = [...]`` flips
    that to deny-by-default: an authenticated user with no explicit grant is
    limited to the listed suites.
  * Dashboard admins are never scoped (enforced in ``auth.caller_suites``).
  * The roster is control-plane data: one GLOBAL file, never per-tenant.

Store: ``~/.maverick/dashboard-user-suites.json`` (0600),
``{principal: [suite, ...]}``. An explicit EMPTY list is a valid grant meaning
"no departments at all"; remove the entry to lift scoping.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

# Serializes a grants load-modify-save in-process; cross_process_lock in
# _locked() extends it across processes (multiple dashboard workers edit grants).
_GRANTS_LOCK = threading.Lock()


def _locked(path: Path):
    from contextlib import ExitStack

    from maverick.file_lock import cross_process_lock
    stack = ExitStack()
    stack.enter_context(_GRANTS_LOCK)
    stack.enter_context(cross_process_lock(path))
    return stack


def store_path() -> Path:
    return Path.home() / ".maverick" / "dashboard-user-suites.json"


def known_suites() -> frozenset[str]:
    """Every suite key a grant may name (the department catalog's key space)."""
    from maverick.domain import SUITE_PREFIXES
    return frozenset(SUITE_PREFIXES.values())


def default_suites() -> frozenset[str] | None:
    """Suites for an authenticated user with NO explicit grant.

    ``None`` (the default, and on any config problem) means unrestricted —
    scoping is opt-in per user. Set ``[dashboard] default_suites = ["finance"]``
    for deny-by-default; unknown suite keys in the list are ignored."""
    try:
        from maverick.config import load_config
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
    from maverick.file_lock import atomic_write_text
    atomic_write_text(store_path(), json.dumps(data, indent=2, sort_keys=True))


def list_grants() -> dict[str, list[str]]:
    """All explicitly-scoped ``{principal: [suite, ...]}`` assignments."""
    return _load()


def get_grant(principal: str) -> frozenset[str] | None:
    """The principal's explicit grant, or ``None`` when they have no entry."""
    suites = _load().get((principal or "").strip())
    return None if suites is None else frozenset(suites)


def granted_suites(principal: str) -> frozenset[str] | None:
    """The suites ``principal`` may use, or ``None`` for unrestricted.

    An explicit grant wins; otherwise the configured default
    (:func:`default_suites`), which is itself ``None`` unless an operator
    opted the deployment into deny-by-default."""
    explicit = get_grant(principal)
    if explicit is not None:
        return explicit
    return default_suites()


def set_suites(principal: str, suites: object) -> None:
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
        data[principal] = sorted(cleaned)
        _write(data)


def remove_grant(principal: str) -> None:
    """Lift scoping for ``principal`` (back to the default: unrestricted)."""
    with _locked(store_path()):
        data = _load()
        if data.pop((principal or "").strip(), None) is not None:
            _write(data)


__all__ = [
    "store_path", "known_suites", "default_suites",
    "list_grants", "get_grant", "granted_suites", "set_suites", "remove_grant",
]
