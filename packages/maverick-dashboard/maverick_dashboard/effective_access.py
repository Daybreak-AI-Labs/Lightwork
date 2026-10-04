"""What one person or one specialist can do, and which rule allows it.

Every answer comes from the functions that enforce access, never a copy of
their logic: the role and permission checks in :mod:`auth` (asked with a
request that carries the person as its signed-in identity), the page
admission rule the app applies to every page (:func:`ui_visibility.is_visible`
with the role :func:`app._page_role` resolves), department scope
(:func:`auth.caller_suites`), and the spending-limit totals admission uses
(:func:`maverick.spending_limits.standing`). :mod:`access_explanations` only
names the rule behind a role. Read-only: nothing here changes access.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .identity_format import POWERS


def probe(principal: str, path: str = "/users/access"):
    """A request whose signed-in identity is ``principal``.

    The permission functions read the caller from the request, so asking them
    with this request answers exactly as they would for that person's own
    requests (no API-key scope narrows it)."""
    from starlette.requests import Request

    sub = principal.split(":", 1)[1] if principal.startswith("user:") else principal
    identity = SimpleNamespace(principal=principal, sub=sub, claims={})
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "server": ("localhost", 80),
        "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
        "headers": [], "state": {"principal": identity},
    })


def _explained(principal: str, *, installation: bool) -> dict:
    from . import access_explanations
    try:
        return access_explanations.role(principal, installation=installation)
    except Exception:  # noqa: BLE001 -- an unreadable source is shown, not raised
        return {"role": None, "source": "Access policy could not be read",
                "owner": "Installation administrator"}


def _scope_row(request, principal: str, role: str | None, *, installation: bool) -> dict:
    """One place someone works: their role there, the rule, and each power."""
    from . import auth
    from .identity_format import role_label

    check = auth.has_global_permission if installation else auth.has_permission
    explained = _explained(principal, installation=installation)
    if explained.get("role") == role:
        source = explained["source"]
    elif explained.get("source") == "Deactivated identity":
        source = "Account turned off in the sign-in provider"
    else:
        source = "Access policy could not be explained"
    return {
        "role": role, "label": role_label(role) if role else "No active role",
        "source": source, "owner": explained.get("owner", ""),
        "powers": {atom: bool(check(request, atom)) for atom, _text in POWERS},
    }


def _workspaces(request, principal: str) -> list[dict]:
    """Each workspace where the person has their own role, answered inside
    that workspace by the same role resolution its pages use."""
    from maverick.paths import current_tenant_id, tenant_scope

    from . import auth, rbac
    try:
        from maverick.tenant.registry import list_tenants
        records = [rec for rec in list_tenants() if rec.active]
    except Exception:  # noqa: BLE001 -- no workspace roster: installation role only
        records = []
    current = current_tenant_id() or ""
    rows = []
    for rec in records:
        try:
            member = rbac.get_tenant_role(rec.id, principal)
        except Exception:  # noqa: BLE001 -- unreadable membership denies; shown below
            member = None
        if member is None and rec.id != current:
            continue
        with tenant_scope(tenant=rec.id):
            row = _scope_row(request, principal, auth.role_for_principal(principal), installation=False)
        row.update(id=rec.id, name=getattr(rec, "display_name", "") or rec.id,
                   current=rec.id == current, member=member is not None)
        rows.append(row)
    return rows


def _screens(request) -> dict:
    """The pages the person can open in the current workspace, by area.

    Same rule the app applies to every page request: the page's scope picks
    the installation-wide or the workspace role, then page visibility decides
    (security floor first, then the administrator's visibility settings)."""
    from . import app as app_module
    from . import ui_visibility

    roles = {"workspace": app_module._page_role(request),
             "global": app_module._page_role(request, global_scope=True)}
    try:
        off = app_module._switched_off_pages()
    except Exception:  # noqa: BLE001 -- module state unknown: show pages as they are
        off = frozenset()
    groups: dict[str, dict] = {}
    hidden, closed = [], 0
    chosen = ui_visibility.overrides()
    for page in ui_visibility.PAGES:
        if not ui_visibility.customer_ready(page):
            continue
        scope = "global" if page.get("scope") == "global" else "workspace"
        role = roles[scope]
        if ui_visibility.is_visible(page, role):
            group = groups.setdefault(page["group"], {
                "title": ui_visibility.group_title(page["group"]), "pages": []})
            group["pages"].append({"path": page["path"], "label": page["label"],
                                   "installation": scope == "global",
                                   "off": page["path"] in off})
        elif role and ui_visibility.eligible(page, role):
            hidden.append({"path": page["path"], "label": page["label"],
                           "by_setting": chosen.get(page["path"], {}).get(role) is False})
        else:
            closed += 1
    return {"groups": list(groups.values()), "hidden": hidden, "closed": closed,
            "open": sum(len(g["pages"]) for g in groups.values())}


def _departments(request, principal: str) -> dict:
    try:
        return _department_scope(request, principal)
    except Exception:  # noqa: BLE001 -- dispatch refuses department work then too
        return {"all": False, "names": [], "error": True,
                "source": "Department access could not be read, so department work is refused"}


def _department_scope(request, principal: str) -> dict:
    from maverick.departments import department_title

    from . import auth, scim_groups, suite_grants
    suites = auth.caller_suites(request)
    if auth.is_dashboard_admin(principal):
        source = "Installation administrators are never limited to departments"
    elif suite_grants.get_grant(principal) is not None:
        source = "Their own department setting on the Users page"
    elif scim_groups.suites_for_principal(principal) is not None:
        source = "Departments given to their sign-in group"
    elif suite_grants.default_suites() is not None:
        source = "Standard departments for people without their own setting"
    else:
        source = "Standard setting: every department"
    names = sorted(department_title(key) for key in suites) if suites is not None else []
    return {"all": suites is None, "names": names, "source": source, "error": False}


def _task_ceiling() -> float | None:
    try:
        from maverick.runner import configured_max_dollars
        return configured_max_dollars()
    except Exception:  # noqa: BLE001 -- shown as unknown, never as no limit
        return None


def _spending(principal: str) -> dict:
    from maverick import spending_limits
    try:
        view = spending_limits.standing(principal)
    except Exception:  # noqa: BLE001 -- unreadable limits pause paid work; say so
        return {"error": ("The spending limits or spending records could not be read. Paid "
                          "work stops until they can be read again."),
                "limits": [], "task_ceiling": _task_ceiling()}
    return {**view, "error": "", "task_ceiling": _task_ceiling()}


def _approvals(request, principal: str, workspace_role: dict) -> dict:
    from maverick.safety.dual_control import allow_self_approval

    from . import auth, content_access
    can = auth.has_permission(request, "operate")
    try:
        exceptional = content_access.enabled()
    except Exception:  # noqa: BLE001 -- unknown means the request path is not offered
        exceptional = False
    return {"can_decide": can, "role": workspace_role["label"], "source": workspace_role["source"],
            "settings": bool(auth.has_global_permission(request, "admin")),
            "content_requests": exceptional, "self_approval": allow_self_approval()}


def person(principal: str) -> dict[str, Any]:
    """The effective-access report for one person (read-only)."""
    from . import auth, scim_groups

    request = probe(principal)
    try:
        active = scim_groups.active_for_principal(principal)
    except Exception:  # noqa: BLE001 -- sign-in would also be refused
        active = False
    installation = _scope_row(request, principal, auth.global_role_for_principal(principal),
                              installation=True)
    workspaces = _workspaces(request, principal)
    current = next((row for row in workspaces if row["current"]), None)
    workspace_role = current or _scope_row(request, principal, auth.role_for_principal(principal),
                                           installation=False)
    return {
        "principal": principal,
        "active": active,
        "pinned_admin": auth.is_dashboard_admin(principal),
        "installation": installation,
        "workspaces": workspaces,
        "current": current,
        "powers": POWERS,
        "screens": _screens(request),
        "departments": _departments(request, principal),
        "spending": _spending(principal),
        "approvals": _approvals(request, principal, workspace_role),
    }


# How each independence rung reads, and what it means for an action.
LEVELS = {
    "observe": ("Watch only", "Never takes a consequential action."),
    "suggest": ("Suggest", "Prepares the action; a person carries it out."),
    "request": ("Ask before acting", "Acts after a person approves each action."),
    "auto": ("Act on its own", "Acts without asking, within its allowed actions, limits and refusals."),
}
RISKS = (("low", "Low-risk actions"), ("medium", "Medium-risk actions"), ("high", "High-risk actions"))


def _level_source(name: str, view: dict, risk: str) -> str:
    """Which setting gives a specialist its level for one risk tier."""
    from maverick.departments import department_title
    from maverick.domain import available_domains

    auto = view["autonomy"]
    per_risk = auto.get(risk) is not None
    which = f"its level for {risk}-risk actions" if per_risk else "its usual level"
    if auto.get("config_override"):
        return f"Set for this specialist in the installation's settings file ({which})"
    if "autonomy" in view.get("overridden", ()):
        return f"Set in this specialist's Independence settings ({which})"
    profile = available_domains().get(name)
    if profile is not None and profile.autonomy is not None:
        return f"Built into this specialist ({which})"
    suite = view.get("suite")
    team = department_title(suite) if suite else "specialists without a department"
    return f"Standard for {team} ({which})"


def agent(name: str, view: dict) -> dict[str, Any]:
    """The access report for one specialist (read-only).

    Levels per risk tier come from the resolver the runtime gate calls
    (:func:`maverick.agent_autonomy.resolve` on the effective profile, as
    :func:`maverick.domain_edit.resolved_view` records them); this only adds
    the setting each one comes from."""
    auto = view["autonomy"]
    tiers = []
    for risk, title in RISKS:
        level = auto["effective"][risk]
        label, means = LEVELS.get(level, (level, ""))
        if not auto["levels_enabled"]:
            rule = "Independence levels are off for this installation, so every consequential action waits for a person"
        else:
            rule = _level_source(name, view, risk)
            configured = auto.get(risk) or auto["default"]
            if auto["onboarding"] and configured != level:
                rule += f", lowered one step from {LEVELS.get(configured, (configured,))[0]} while it is supervised"
        tiers.append({"risk": risk, "title": title, "level": level, "label": label,
                      "means": means, "rule": rule})
    workspace, spending_error = None, False
    try:
        from maverick import spending_limits
        workspace = spending_limits.workspace_standing()
    except Exception:  # noqa: BLE001 -- paid work stops too; say so instead of guessing
        spending_error = True
    required = sorted({tool for step in view.get("workflow", ()) for tool in step.get("tools", ())})
    return {
        "levels_enabled": auto["levels_enabled"], "supervised": bool(auto["onboarding"]),
        "tiers": tiers, "readiness": view.get("readiness") or {},
        "required": required, "allowed": list(view.get("allow_tools", ())),
        "blocked": list(view.get("deny_tools", ())), "max_risk": view.get("max_risk") or "",
        "task_ceiling": _task_ceiling(), "workspace_limit": workspace,
        "spending_error": spending_error,
    }


__all__ = ["LEVELS", "agent", "person", "probe"]
