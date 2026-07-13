"""IdP group -> dashboard access mapping (SCIM Groups to roles/departments).

SCIM (``scim.py``) stores the groups an IdP pushes; this module turns that
membership into access, so joining the "Finance Team" in Okta/Entra is what
grants the finance department — no per-user hand assignment:

    [dashboard.group_roles]
    "Finance Team" = "operator"

    [dashboard.group_suites]
    "Finance Team" = ["finance", "tax"]

Group names and role values are matched case-insensitively (an IdP may rename a
group's case, or an operator may capitalize a role). A ``group_suites`` value
may be a single string or a list; unknown suite keys are dropped, so a typo
never silently widens access.

Resolution order (explicit always wins over derived):
  * role:   stored assignment -> group-derived -> ``default_role``
    (wired in ``auth.global_role_for_principal``);
  * suites: explicit grant -> group-derived -> ``[dashboard] default_suites``
    (wired as a kernel grant resolver — see
    :func:`maverick.suite_grants.register_grant_resolver`).

Enforcement reach: the resolver is armed by importing this module, which the
dashboard does at startup (``auth`` imports it). Explicit grants and the
configured default therefore resolve identically in every process, but
group-*derived* grants are only visible where the dashboard resolver is armed.
A pure ``maverick-core`` process (e.g. a worker that never imports the
dashboard) falls back to explicit-grant + default; run department scoping in
**deny-by-default** mode (``[dashboard] default_suites``) if you need a
non-dashboard surface to fail closed for a group-only principal rather than
fall through to unrestricted.

Identity matching is deliberately strict. A dashboard principal is
``user:<sub>`` (the OIDC subject). We match it to a SCIM user only through a
*non-forgeable* binding: the IdP's own ``externalId`` (the usual Okta sub /
Entra immutable object id) or our internal record ``id``. For pairwise-``sub``
IdPs (Entra), where the login sub differs from ``externalId``, we bridge
through the subject directory — but ONLY on those same non-forgeable
identifiers. We never match (directly or via the directory) on
``userName``/``email``: those are contact/display attributes, and the directory
records login ``email``/``upn`` claims WITHOUT an ``email_verified`` check, so
keying a grant on them would let a forged email claim (or a second identity
source) resolve another user's SCIM record and inherit its group-mapped role
and departments. ``subs_for`` stays broad for *revocation* (over-revoking is
safe); *granting* must not. Deactivated (``active=false``) users confer
nothing. Derived access follows the IdP automatically: mappings are read at
resolution time, so removing a user from the group (or the group itself)
revokes on the next request.

Note on trust: with a ``group_roles``/``group_suites`` mapping configured, the
static ``MAVERICK_SCIM_TOKEN`` becomes an authorization-granting credential
(pushing a user into a mapped group confers that group's role/departments).
Treat it with the sensitivity of an admin credential and rotate/scope it
accordingly.
"""
from __future__ import annotations

from maverick.suite_grants import known_suites, register_grant_resolver

from .rbac import ROLES  # ordered most-privileged first; group mapping honors it


def _config_table(key: str) -> dict:
    try:
        from maverick.config import load_config
        raw = (load_config().get("dashboard", {}) or {}).get(key)
    except Exception:  # pragma: no cover - config read must never gate access
        return {}
    return raw if isinstance(raw, dict) else {}


def _ci_mapping(key: str) -> dict:
    """The ``[dashboard.<key>]`` table re-keyed by case-folded group name, so
    lookups survive an IdP renaming a group's case."""
    return {str(k).strip().casefold(): v for k, v in _config_table(key).items()}


def _sub_of(principal: str) -> str:
    p = (principal or "").strip()
    return p.removeprefix("user:")


def _active(rec: dict) -> bool:
    """Whether a stored SCIM user record is active. Stored ``active`` is a bool,
    but coerce defensively so a hand-edited ``"false"`` string can't read truthy
    and confer group access to a deprovisioned user."""
    v = rec.get("active", True)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() == "true"


def group_names_for_principal(principal: str) -> frozenset[str]:
    """The displayNames of every SCIM group the (active) principal belongs to."""
    sub = _sub_of(principal)
    if not sub:
        return frozenset()
    from . import subject_directory
    from .scim import _load, _load_groups
    users = _load()
    groups = _load_groups()
    if not users or not groups:
        return frozenset()

    directory = subject_directory.load_index()  # one read for the whole scan
    matched: set[str] = set()
    for uid, rec in users.items():
        if not _active(rec):
            continue  # deprovisioned users confer no group-derived access
        # Authoritative sub bindings only (see module docstring): the IdP's
        # externalId (its immutable object id) or our internal id.
        ext_id = str(rec.get("externalId") or "")
        int_id = str(rec.get("id") or "")
        if sub in (ext_id, int_id):
            matched.add(uid)
            continue
        # Pairwise-sub IdPs (Entra): the login sub differs from externalId, so
        # bridge through the subject directory — but ONLY on the non-forgeable
        # identifiers (externalId/oid, internal id). We deliberately do NOT
        # bridge via userName/email: those enter the directory as UNVERIFIED
        # login claims (no email_verified check at record time), so keying a
        # grant on them would let a forged email claim resolve another user's
        # SCIM record and inherit its group-mapped role/departments.
        if sub in subject_directory.subs_in(directory, [ext_id, int_id]):
            matched.add(uid)
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
    mapping = _ci_mapping("group_roles")
    if not mapping:
        return None
    names = group_names_for_principal(principal)
    mapped = {str(mapping[n.casefold()]).strip().lower()
              for n in names if n.casefold() in mapping}
    for role in ROLES:  # ROLES is ordered most-privileged first
        if role in mapped:
            return role
    return None


def suites_for_principal(principal: str) -> frozenset[str] | None:
    """The union of department suites mapped to the principal's groups, or
    ``None`` when no group carries a suites mapping (no opinion).

    A matched group with an empty/typo'd value yields a (possibly empty) grant,
    NOT unrestricted: once any of the caller's groups matches a ``group_suites``
    row, the result is the intersection with the real suite catalog — deny is
    the safe direction for a misconfiguration, never a silent widening."""
    mapping = _ci_mapping("group_suites")
    if not mapping:
        return None
    names = group_names_for_principal(principal)
    hit = False
    out: set[str] = set()
    for n in names:
        key = n.casefold()
        if key not in mapping:
            continue
        hit = True
        raw = mapping[key]
        if isinstance(raw, str):
            out.add(raw)  # a lone string is one suite, never "unrestricted"
        elif isinstance(raw, (list, tuple)):
            out.update(str(s) for s in raw)
    if not hit:
        return None
    return frozenset(out) & known_suites()


# Plug group-derived grants into the KERNEL resolution chain, so the
# deploy/dispatch gates in maverick.departments / maverick.fleet honor group
# membership in any process that has imported the dashboard. Importing this
# module (auth.py does, at module import) is what arms the resolver.
register_grant_resolver(suites_for_principal)

__all__ = [
    "group_names_for_principal", "role_for_principal", "suites_for_principal",
]
