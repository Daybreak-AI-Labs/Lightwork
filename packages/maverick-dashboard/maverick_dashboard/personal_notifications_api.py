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
    goals = world.list_goals(owner=actor, limit=201, order="desc")
    limited = len(goals) > 200
    # Own-task scope is deliberately explicit; a global administrator's normal
    # ability to inspect other work does not subscribe them to everybody's work.
    goals = [goal for goal in goals[:200] if goal.owner == actor]
    sources, projects = [], {}
    tasks = []
    for goal in goals:
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
                            "title": goal.title, "label": label, "href": href or f"/chat/goal/{goal.id}"})
        add("state", goal.status, {"blocked": "Needs attention", "failed": "Did not finish", "done": "Finished"}.get(goal.status, ""))
        if goal.status == "done" and goal.result:
            add("result", hashlib.sha256(str(goal.result).encode()).hexdigest(), "Result changed", category="changed-result")
        for question in world.open_questions(goal.id)[:20]:
            add("question:" + str(question.id), question.id, "Needs your answer", category="needs-decision")
        if goal.deadline and goal.status not in {"done", "failed", "cancelled"} and goal.deadline <= time.time() + 3600:
            add("deadline", goal.deadline, "Deadline approaching or overdue", category="deadline")
    if operate:
        for approval in world.pending_approvals()[:200]:
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
    from maverick.budget_guidance import warning_sources
    sources.extend(warning_sources(actor))
    return sources, tasks, list(projects.values()), limited or len(sources) > 1000


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
    state = preferences.state(actor)
    choice = validate_preferences(state["values"].get("notification_preferences", {}))
    sources, tasks, projects, limited = _projection(request, actor)
    # Check authority again after reading current sources and before publication.
    if actor != _identity(request):
        raise HTTPException(409, "Your account changed. Reload personal alerts.")
    inbox = PersonalInbox(actor).refresh(sources, choice, seen_id=seen_id,
        allow_critical=(config.load_config().get("notifications") or {}).get("personal_critical_bypass", False) is True)
    return {**inbox, "preferences": choice, "context": context_for(request), "revision": state["revision"],
            "tasks": tasks, "projects": projects, "partial": limited,
            "critical_allowed": (config.load_config().get("notifications") or {}).get("personal_critical_bypass", False) is True,
            "background_enabled": (config.load_config().get("notifications") or {}).get("personal_background") is True,
            "background": status(actor), "private_delivery": delivery_status(actor)}


@router.get("/notifications")
def page(request: Request):
    _identity(request)
    return _app().templates.TemplateResponse(request, "personal_notifications.html", {},
                                             headers={"Cache-Control": "private, no-store"})


@router.get("/api/v1/notifications/personal")
def inbox(request: Request, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return _state(request)


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
    _, tasks, projects, _ = _projection(request, actor)
    if (set(choice["muted_tasks"]) - {str(task["id"]) for task in tasks}
            or set(choice["projects"]) - {project["id"] for project in projects}):
        raise HTTPException(409, "A selected task or project is no longer available. Refresh and review your preferences.")
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
