"""IdP group -> dashboard access mapping (SCIM Groups to roles/departments).

SCIM (``scim.py``) stores the groups an IdP pushes; this module turns that
membership into access, so joining the "Finance Team" in Okta/Entra is what
grants the finance department — no per-user hand assignment:

    [dashboard.group_roles]
    "Finance Team" = "operator"

    [dashboard.group_suites]
    "Finance Team" = ["finance", "tax"]

Resolution order (explicit always wins over derived):
  * role:   stored assignment -> group-derived -> ``default_role``
    (wired in ``auth.global_role_for_principal``);
  * suites: explicit grant -> group-derived -> ``[dashboard] default_suites``
    (wired as a kernel grant resolver — see
    :func:`maverick.suite_grants.register_grant_resolver` — so the dashboard
    HTTP gates AND the kernel deploy/dispatch gates resolve identically).

Matching a dashboard principal (``user:<sub>``) to a SCIM user is IdP-specific:
``externalId`` usually carries the OIDC ``sub`` (Okta), but pairwise-``sub``
IdPs (Entra) need the subject directory, which recorded the login ``sub``
against the user's stable identifiers. We match on either, mirroring the
deprovisioning logic in ``scim._revoke_user_sessions``.

Derived access follows the IdP automatically: mappings are read at resolution
time, so removing a user from the group (or the group itself) revokes on the
next request. A group with NO mapping confers nothing and blocks nothing.
"""
from __future__ import annotations

from maverick.suite_grants import known_suites, register_grant_resolver

# Most-privileged wins when several mapped groups name different roles.
_ROLE_ORDER = ("admin", "operator", "auditor", "viewer")


def _config_table(key: str) -> dict:
    try:
        from maverick.config import load_config
        raw = (load_config().get("dashboard", {}) or {}).get(key)
    except Exception:  # pragma: no cover - config read must never gate access
        return {}
    return raw if isinstance(raw, dict) else {}


def _sub_of(principal: str) -> str:
    p = (principal or "").strip()
    return p.removeprefix("user:")


def group_names_for_principal(principal: str) -> frozenset[str]:
    """The displayNames of every SCIM group the principal belongs to."""
    sub = _sub_of(principal)
    if not sub:
        return frozenset()
    from .scim import _load, _load_groups
    users = _load()
    groups = _load_groups()
    if not users or not groups:
        return frozenset()

    from .subject_directory import subs_for
    matched: set[str] = set()
    lowered = sub.casefold()
    for uid, rec in users.items():
        idents = [str(rec.get(k) or "") for k in
                  ("externalId", "userName", "email", "id")]
        if (sub in (idents[0], idents[3])
                or lowered in (idents[1].casefold(), idents[2].casefold())):
            matched.add(uid)
            continue
        # Pairwise-sub IdPs: the login sub was recorded against these stable
        # identifiers by the subject directory at sign-in.
        try:
            if sub in subs_for(i for i in idents if i):
                matched.add(uid)
        except Exception:  # pragma: no cover - directory read must fail open
            pass
    if not matched:
        return frozenset()
    return frozenset(
        str(g.get("displayName") or "")
        for g in groups.values()
        if matched & set(g.get("members", []))
    ) - {""}


def role_for_principal(principal: str) -> str | None:
    """The most-privileged role mapped to any of the principal's groups, or
    ``None`` when no group carries a role mapping."""
    mapping = _config_table("group_roles")
    if not mapping:
        return None
    names = group_names_for_principal(principal)
    mapped = {str(mapping[n]).strip() for n in names if n in mapping}
    for role in _ROLE_ORDER:
        if role in mapped:
            return role
    return None


def suites_for_principal(principal: str) -> frozenset[str] | None:
    """The union of department suites mapped to the principal's groups, or
    ``None`` when no group carries a suites mapping (no opinion)."""
    mapping = _config_table("group_suites")
    if not mapping:
        return None
    names = group_names_for_principal(principal)
    hit = False
    out: set[str] = set()
    for n in names:
        raw = mapping.get(n)
        if isinstance(raw, (list, tuple)):
            hit = True
            out.update(str(s) for s in raw)
    if not hit:
        return None
    return frozenset(out) & known_suites()


# Plug group-derived grants into the KERNEL resolution chain, so the
# deploy/dispatch gates in maverick.departments / maverick.fleet honor group
# membership exactly like the dashboard HTTP gates do. Importing this module
# (auth.py does, at module import) is what arms the resolver.
register_grant_resolver(suites_for_principal)

__all__ = [
    "group_names_for_principal", "role_for_principal", "suites_for_principal",
]
