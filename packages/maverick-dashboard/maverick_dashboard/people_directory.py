"""People a page may offer as a choice: assignees and @mention targets.

Only named accounts whose current role in this workspace carries the
permission the object needs are offered, so a list never names someone who
could not open what it is about. The same check validates a choice when it is
saved, so a stale page cannot assign or notify someone who lost access.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _role(principal: str, tenant: str | None, roles: dict[str, str]) -> str | None:
    from .auth import global_role_for_principal, is_dashboard_admin

    if is_dashboard_admin(principal):
        return "admin"
    if tenant:
        return roles.get(principal)
    return global_role_for_principal(principal)


def _known(tenant: str | None, roles: dict[str, str]) -> list[str]:
    from maverick.suite_grants import admin_principals

    known = [*roles, *sorted(admin_principals())]
    if not tenant:
        from .admin_pages import _signed_in_principals

        known += _signed_in_principals()
    return known


def may(principal: str, permission: str) -> bool:
    """Whether ``principal`` currently holds ``permission`` in this workspace."""
    from maverick.paths import current_tenant_id_strict

    from . import rbac

    principal = str(principal or "").strip()
    if not principal.startswith("user:"):
        return False
    try:
        tenant = current_tenant_id_strict()
        roles = rbac.list_tenant_roles(tenant) if tenant else rbac.list_users()
        return permission in rbac.permissions_for(_role(principal, tenant, roles))
    except Exception:  # noqa: BLE001 -- an unreadable roster offers nobody
        log.warning("people directory unavailable", exc_info=True)
        return False


def candidates(permission: str, *, limit: int = 200) -> list[dict]:
    """``[{principal, label}]`` for everyone who holds ``permission`` now."""
    from maverick.paths import current_tenant_id_strict

    from . import rbac
    from .identity_format import humanize_principal

    try:
        tenant = current_tenant_id_strict()
        roles = rbac.list_tenant_roles(tenant) if tenant else rbac.list_users()
        known = _known(tenant, roles)
    except Exception:  # noqa: BLE001 -- an unreadable roster offers nobody
        log.warning("people directory unavailable", exc_info=True)
        return []
    out: dict[str, dict] = {}
    for principal in known:
        principal = str(principal or "").strip()
        if not principal.startswith("user:") or principal in out:
            continue
        if permission not in rbac.permissions_for(_role(principal, tenant, roles)):
            continue
        ident = humanize_principal(principal)
        label = f"{ident.name} ({ident.detail})" if ident.detail and ident.detail != "Email" else ident.name
        out[principal] = {"principal": principal, "label": label}
        if len(out) >= limit:
            break
    return sorted(out.values(), key=lambda person: person["label"].lower())
