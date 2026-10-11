"""People a page may offer as a choice: assignees and @mention targets.

Only named accounts whose current role in this workspace carries the
permission the object needs are offered, so a list never names someone who
could not open what it is about. The same check validates a choice when it is
saved, so a stale page cannot assign or notify someone who lost access.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _role(principal: str, tenant: str | None, roles: dict[str, str], *, strict: bool = False) -> str | None:
    from .auth import global_role_for_principal, is_dashboard_admin

    if is_dashboard_admin(principal):
        return "admin"
    if tenant:
        return roles.get(principal)
    return global_role_for_principal(principal, strict=True) if strict else global_role_for_principal(principal)


def _holds(role: str | None, permission: str, *, strict: bool = False) -> bool:
    from . import rbac

    return permission in (rbac.permissions_for(role, strict=True) if strict else rbac.permissions_for(role))


def _known(tenant: str | None, roles: dict[str, str]) -> list[str]:
    from maverick.suite_grants import admin_principals

    known = [*roles, *sorted(admin_principals())]
    if not tenant:
        from .admin_pages import _signed_in_principals

        known += _signed_in_principals()
    return known


def _active_check():
    """``check(principal) -> bool``: not turned off in the company directory
    (SCIM), where such an account can no longer sign in. Unknown to the
    directory counts as active, as at sign-in. The directory is read once;
    an unreadable one raises, so nobody is offered."""
    from .scim_groups import active_check

    check = active_check()
    return lambda principal: check(principal) is not False


def _roster():
    from maverick.paths import current_tenant_id_strict

    from . import rbac

    tenant = current_tenant_id_strict()
    return tenant, (rbac.list_tenant_roles(tenant) if tenant else rbac.list_users())


def signed_in(principals) -> set[str]:
    """Those of ``principals`` the company directory has not turned off (see
    :func:`_active_check`). Not read for no principals; raises when it cannot
    be read."""
    principals = set(principals)
    if not principals:
        return set()
    active = _active_check()
    return {principal for principal in principals if active(principal)}


def allowed(principals, permission: str, *, strict: bool = False) -> set[str]:
    """Those of ``principals`` who currently hold ``permission`` in this
    workspace (see :func:`may`), reading the roster and directory once (not
    at all for nobody). ``strict``: a roster, role, permission or directory
    source that cannot be read raises instead of allowing nobody."""
    principals = {principal for principal in (str(p or "").strip() for p in principals)
                  if principal.startswith("user:")}
    if not principals:
        return set()
    try:
        tenant, roles = _roster()
        active = _active_check()
        return {principal for principal in principals
                if _holds(_role(principal, tenant, roles, strict=strict), permission, strict=strict)
                and active(principal)}
    except Exception:  # noqa: BLE001 -- an unreadable roster offers nobody
        if strict:
            raise
        log.warning("people directory unavailable", exc_info=True)
        return set()


def may(principal: str, permission: str) -> bool:
    """Whether ``principal`` currently holds ``permission`` in this workspace."""
    principal = str(principal or "").strip()
    return principal.startswith("user:") and principal in allowed({principal}, permission)


def candidates(permission: str, *, limit: int = 200) -> list[dict]:
    """``[{principal, label}]`` for everyone who holds ``permission`` now."""
    from . import rbac
    from .identity_format import humanize_principal

    try:
        tenant, roles = _roster()
        known = _known(tenant, roles)
        active = _active_check()
    except Exception:  # noqa: BLE001 -- an unreadable roster offers nobody
        log.warning("people directory unavailable", exc_info=True)
        return []
    out: dict[str, dict] = {}
    for principal in known:
        principal = str(principal or "").strip()
        if not principal.startswith("user:") or principal in out:
            continue
        if permission not in rbac.permissions_for(_role(principal, tenant, roles)) or not active(principal):
            continue
        ident = humanize_principal(principal)
        label = f"{ident.name} ({ident.detail})" if ident.detail and ident.detail != "Email" else ident.name
        out[principal] = {"principal": principal, "label": label}
        if len(out) >= limit:
            break
    return sorted(out.values(), key=lambda person: person["label"].lower())
