"""Principal-scoped dashboard workspace selections.

The selected business tenant is navigation state, not authorization.  Store it
per authenticated principal and re-check active membership every time it is
bound.  Auth-off/static-token local mode keeps using the operator-wide
``[dashboard] active_tenant`` setting for backward compatibility.
"""
from __future__ import annotations

import json
import threading
from contextlib import ExitStack
from pathlib import Path

_LOCK = threading.Lock()


class WorkspaceTenantStoreError(RuntimeError):
    """The principal selection store is present but cannot be trusted."""


def store_path() -> Path:
    from maverick.paths import maverick_home

    return maverick_home() / "dashboard-workspace-tenants.json"


def _locked(path: Path) -> ExitStack:
    from maverick.file_lock import cross_process_lock, ensure_private_directory

    ensure_private_directory(path.parent)
    stack = ExitStack()
    stack.enter_context(_LOCK)
    stack.enter_context(cross_process_lock(path))
    return stack


def _load() -> dict[str, str]:
    from maverick.file_lock import atomic_read_text, ensure_private_file

    path = store_path()
    if not path.exists():
        return {}
    try:
        ensure_private_file(path)
        raw = json.loads(atomic_read_text(path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise WorkspaceTenantStoreError(
            "workspace tenant selections are unreadable"
        ) from exc
    if not isinstance(raw, dict):
        raise WorkspaceTenantStoreError(
            "workspace tenant selections must be an object"
        )
    clean: dict[str, str] = {}
    for principal, tenant_id in raw.items():
        if (
            not isinstance(principal, str)
            or not principal.strip()
            or principal != principal.strip()
            or not isinstance(tenant_id, str)
            or not tenant_id.strip()
            or tenant_id != tenant_id.strip()
        ):
            raise WorkspaceTenantStoreError(
                "workspace tenant selections contain an invalid entry"
            )
        clean[principal] = tenant_id
    return clean


def get_selection(principal: str) -> str | None:
    name = str(principal or "").strip()
    if not name:
        return None
    return _load().get(name)


def set_selection(principal: str, tenant_id: str | None) -> None:
    """Set or clear one principal's preference without granting membership."""
    from maverick.file_lock import atomic_write_text

    name = str(principal or "").strip()
    if not name:
        raise ValueError("authenticated principal required")
    selected = None if tenant_id is None else str(tenant_id).strip()
    if tenant_id is not None and not selected:
        raise ValueError("tenant id cannot be empty")
    path = store_path()
    with _locked(path):
        data = _load()
        if selected is None:
            data.pop(name, None)
        else:
            data[name] = selected
        atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True))


def principal_can_select(principal: str, tenant_id: str) -> bool:
    """Membership (or global admin authority) required to select a tenant."""
    from . import rbac
    from .auth import global_role_for_principal

    role = global_role_for_principal(principal)
    if "admin" in rbac.permissions_for(role):
        return True
    return rbac.get_tenant_role(tenant_id, principal) is not None


def accessible_tenant_ids(principal: str) -> frozenset[str] | None:
    """Return visible memberships, or ``None`` for global tenant admins."""
    from . import rbac
    from .auth import global_role_for_principal

    role = global_role_for_principal(principal)
    if "admin" in rbac.permissions_for(role):
        return None
    try:
        # Tenant ids are registry identifiers, so iterating the active registry
        # avoids exposing names from tenants the caller cannot access.
        from maverick.tenant.registry import list_tenants

        return frozenset(
            record.id
            for record in list_tenants()
            if rbac.get_tenant_role(record.id, principal) is not None
        )
    except Exception as exc:
        raise WorkspaceTenantStoreError(
            "workspace tenant memberships are unavailable"
        ) from exc


__all__ = [
    "WorkspaceTenantStoreError",
    "accessible_tenant_ids",
    "get_selection",
    "principal_can_select",
    "set_selection",
    "store_path",
]
