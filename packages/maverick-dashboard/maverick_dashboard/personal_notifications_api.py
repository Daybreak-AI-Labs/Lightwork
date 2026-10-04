"""Personal in-product alerts use current named-account and task authority."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from . import auth

router = APIRouter()


def _app():
    from . import app
    return app


def _identity(request):
    from maverick import config

    from .preferences_api import _principal
    principal = _principal(request)
    if config.config_source_errors(include_tenant=True) or (config.load_config().get("notifications") or {}).get("personal_alerts", False) is not True:
        raise HTTPException(403, "Personal alerts are turned off. An administrator can enable them in All settings.")
    return principal


def _goal_pages(world, actor):
    """Unresolved old work survives, independent of recent completed volume."""
    after, since = 0, time.time() - 7 * 86400
    while True:
        batch = world.personal_alert_goals(actor, since=since, after_id=after, limit=250)
        if not batch:
            return
        yield from batch
        after = batch[-1][0].id


def _projection(request, actor: str, *, world=None) -> tuple[list[dict], list[dict], list[dict], bool]:
    world = world if world is not None else _app()._world()
    if request is None:
        from maverick.paths import current_tenant_id_strict
        from maverick.personal_identity import principal_current

        from . import rbac, suite_grants
        if not principal_current(actor):
            raise PermissionError("Personal alert account no longer has access.")
        tenant = current_tenant_id_strict()
        role = rbac.get_tenant_role(tenant, actor) if tenant else auth.global_role_for_principal(actor)
        operate = "operate" in rbac.permissions_for(role)
        suites = None if auth.is_dashboard_admin(actor) else suite_grants.granted_suites(actor)
    else:
        operate = auth.has_permission(request, "operate")
        suites = auth.caller_suites(request)
    sources, tasks = [], []
    limited = False
    projects = {project["id"]: {"id": project["id"], "name": project["name"]}
                for project in world.list_projects(owner=actor) if project.get("owner") == actor}
    for goal, state_revision, result_revision in _goal_pages(world, actor):
        if goal.owner != actor:
            continue
        if request is not None:
            auth.assert_goal_access(request, goal)
        tasks.append({"id": goal.id, "title": goal.title})
        project_id = getattr(goal, "project_id", None)
        if project_id:
            project = world.get_project(project_id)
            if project is not None and project.get("owner") == actor:
                projects[project_id] = {"id": project_id, "name": project["name"]}
        category = {"blocked": "blocked", "failed": "failed", "done": "completed"}.get(goal.status)
        def add(kind, version, label, *, category=category, href=None, goal=goal, project_id=project_id):
            if not category:
                return
            sources.append({"ref": f"goal:{goal.id}:{kind}", "version": str(version),
                            "category": category, "goal_id": goal.id, "project_id": project_id,
                            "title": goal.title, "label": label, "href": href or f"/chat/goal/{goal.id}",
                            "occurred_at": getattr(goal, "completed_at", None)})
        add("state", f"{goal.status}:{state_revision}", {"blocked": "Needs attention", "failed": "Did not finish", "done": "Finished"}.get(goal.status, ""))
        if goal.status == "done" and goal.result:
            result_digest = hashlib.sha256(str(goal.result).encode()).hexdigest()
            add("result", f"{result_digest}:{result_revision}", "Result changed", category="changed-result")
            sources[-1]["semantic_digest"] = result_digest
            sources[-1]["changed_after_completion"] = result_revision > 0
        for question in world.open_questions(goal.id):
            add("question:" + str(question.id), question.id, "Needs your answer", category="needs-decision")
        if goal.deadline and goal.status not in {"done", "failed", "cancelled"} and goal.deadline <= time.time() + 3600:
            add("deadline", goal.deadline, "Deadline approaching or overdue", category="deadline")
    if operate:
        for approval in world.pending_approvals():
            if approval.claimed_by != actor:
                continue
            if approval.department and suites is not None and approval.department not in suites:
                continue
            state = world.approval_state(approval.id)
            if not state or state["status"] != "pending" or actor in state["approvers"] or approval.requested_by == actor:
                continue
            sources.append({"ref": f"approval:{approval.id}", "version": str(approval.requested_at),
                            "category": "needs-decision", "title": "Assigned approval", "label": "Needs your decision",
                            "href": f"/approvals#approval-{approval.id}", "goal_id": None, "project_id": None})
        from .flow_approval_inbox import pending_for_observer, pending_for_request

        flow_approvals = (pending_for_observer(actor) if request is None
                          else pending_for_request(request))
        limited = flow_approvals["coverage"] != "current"
        for row in flow_approvals["items"]:
            if row["approval_card_status"] != "current":
                continue
            # Keep personal notification history free of the card and run data.
            # The linked run page performs the current approval checks again.
            sources.append({
                "ref": f"flow-approval:{row['run_id']}",
                "version": row["approval_binding_sha256"],
                "category": "needs-decision", "title": "Flow approval waiting",
                "label": "Review and decide", "href": row["href"],
                "goal_id": None, "project_id": None,
            })
    from maverick.budget_guidance import warning_sources
    sources.extend(warning_sources(actor))
    return sources, tasks, list(projects.values()), limited


def _state(request, *, seen_id=None):
    from maverick.authority_effects import authority_effect_fence
    with authority_effect_fence():
        return _state_locked(request, seen_id=seen_id)


def _state_locked(request, *, seen_id=None):
    from maverick import config
    from maverick import experience_preferences as preferences
    from maverick.personal_notification_delivery import status as delivery_status
    from maverick.personal_notification_recipients import enroll, status
    from maverick.personal_notifications import PersonalInbox, validate_preferences

    from .draft_api import context_for
    actor = _identity(request)
    enroll(actor)
    enrollment = status(actor)
    state = preferences.state(actor)
    choice = validate_preferences(state["values"].get("notification_preferences", {}))
    sources, tasks, projects, limited = _projection(request, actor)
    # Check authority again after reading current sources and before publication.
    if actor != _identity(request):
        raise HTTPException(409, "Your account changed. Reload personal alerts.")
    inbox = PersonalInbox(actor).refresh(sources, choice, seen_id=seen_id,
        allow_critical=(config.load_config().get("notifications") or {}).get("personal_critical_bypass", False) is True,
        enrolled_at=enrollment["enrolled_at"])
    return {**inbox, "preferences": choice, "context": context_for(request), "revision": state["revision"],
            "tasks": tasks, "projects": projects, "partial": limited, "recent_history_days": 7,
            "critical_allowed": (config.load_config().get("notifications") or {}).get("personal_critical_bypass", False) is True,
            "background_enabled": (config.load_config().get("notifications") or {}).get("personal_background") is True,
            "background": enrollment, "private_delivery": delivery_status(actor)}


def page_authority(request):
    """The page's own admission; navigation offers the page only when it passes."""
    auth.require_permission(request, "view")
    if not auth.caller_principal(request):
        raise HTTPException(403, "Sign in with your own account to see personal notifications.")


@router.get("/notifications")
def page(request: Request):
    from maverick import config, experience_preferences

    page_authority(request)
    if config.config_source_errors(include_tenant=True):
        unavailable = "Notification settings could not be read. Ask an administrator to check All settings."
    elif not experience_preferences.enabled():
        unavailable = "Personal preferences are turned off. An administrator can enable them in All settings."
    elif (config.load_config().get("notifications") or {}).get("personal_alerts") is not True:
        unavailable = "Personal task alerts are turned off. An administrator can enable them in All settings."
    else:
        unavailable = ""
    return _app().templates.TemplateResponse(request, "personal_notifications.html",
                                             {"personal_alerts_unavailable": unavailable},
                                             headers={"Cache-Control": "private, no-store"})


@router.get("/api/v1/notifications/personal")
def inbox(request: Request, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return _state(request)


@router.get("/api/v1/notifications/personal/summary")
def summary(request: Request, response: Response):
    """A minimal owner-scoped count for the shared navigation bell."""
    response.headers["Cache-Control"] = "private, no-store"
    state = _state(request)
    return {"unseen": sum(item["seen_at"] is None for item in state["items"]),
            "partial": state["partial"], "checked_at": state["checked_at"]}


class PreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context: str = Field(max_length=128)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    preferences: dict[str, Any]
    operation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


def _binding(request, expected):
    from .draft_api import context_for
    _app()._require_same_origin(request)
    if not hmac.compare_digest(expected, context_for(request)):
        raise HTTPException(409, "Your account or workspace changed. Reload personal alerts.")


def validate_current_preferences(request, value):
    """One current authority check shared by direct and reviewed preference edits."""
    from maverick.personal_identity import linked_channels
    from maverick.personal_notifications import validate_preferences
    actor = _identity(request)
    choice = validate_preferences(value)
    if set(choice["external_links"]) - {link["id"] for link in linked_channels(actor)}:
        raise HTTPException(409, "A selected channel identity is no longer verified for your account. Refresh and review your linked identities.")
    world = _app()._world()
    for task_id in choice["muted_tasks"]:
        goal = world.get_goal(int(task_id))
        if goal is None or goal.owner != actor:
            raise HTTPException(409, "A selected task is no longer available. Refresh your preferences.")
        auth.assert_goal_access(request, goal)
    for project_id in choice["projects"]:
        project = world.get_project(project_id)
        if project is None or project.get("owner") != actor:
            raise HTTPException(409, "A selected project is no longer available. Refresh your preferences.")
    return actor, choice


@router.post("/api/v1/notifications/personal/preferences")
def save_preferences(request: Request, response: Response, body: PreferencesIn):
    from maverick import experience_preferences as preferences
    from maverick.authority_effects import authority_effect_fence
    _binding(request, body.context)
    try:
        with authority_effect_fence():
            actor, choice = validate_current_preferences(request, body.preferences)
            preferences.save(actor, scope="personal", changes={"notification_preferences": choice},
                             expected_revision=body.expected_revision, operation_id=body.operation_id)
    except preferences.PreferenceConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    response.headers["Cache-Control"] = "private, no-store"
    return _state(request)


class SeenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context: str = Field(max_length=128)
    item_id: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/api/v1/notifications/personal/seen")
def seen(request: Request, response: Response, body: SeenIn):
    _binding(request, body.context)
    current = _state(request)
    if not any(item["id"] == body.item_id for item in current["items"]):
        raise HTTPException(409, "That alert changed or is no longer available. Refresh the inbox.")
    response.headers["Cache-Control"] = "private, no-store"
    return _state(request, seen_id=body.item_id)
