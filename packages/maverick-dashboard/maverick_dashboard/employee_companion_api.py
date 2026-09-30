"""An owner-bound, read-only conversation beside the existing task runtime.

This coordinator never starts a model, agent, connector, or tool execution.
Closed supervision intents read the caller's current tasks; existing durable
chat/runner admission is unchanged. Companion text never becomes a goal or an
ordinary audit payload, where legacy administrator content access still exists.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, Response
from maverick import config
from maverick.authority_effects import authority_effect_fence
from maverick.conversational_supervisor import DEFAULT_GRAMMAR, parse_utterance
from maverick.domain import suite_for
from maverick.employee_companion import (
    CompanionConflictError,
    CompanionError,
    CompanionPolicyDenied,
    CompanionValidationError,
    EmployeeCompanionStore,
)
from maverick.paths import current_tenant_id_strict
from maverick.suite_grants import granted_suites
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import auth, rbac, scim_groups
from .identity_format import humanize_principal

router = APIRouter(tags=["employee-companion"])
BASE = "/api/v1/employee-companion"
_HEADER = "X-Lightwork-Companion-Context"
_GRAMMAR = [(intent, pattern) for intent, pattern in DEFAULT_GRAMMAR if intent in {"status", "failures"}]
_GRAMMAR += [("failures", "what needs attention"), ("status", "show my tasks")]
_HELP = ("I can show the status of your own tasks. Try ‘what is running?’ or "
         "‘what needs attention?’. This preview cannot start, stop, or change work.")
_STATUSES = ("active", "pending", "blocked", "failed", "error", "draft")
_STATUS_LABELS = {"active": "running", "pending": "queued", "blocked": "needs attention",
                  "failed": "failed", "error": "needs attention", "draft": "awaiting submission"}


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(min_length=1, max_length=4000)
    expected_revision: int = Field(ge=0)


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    owner: str = Field(min_length=6, max_length=320)
    expected_revision: int = Field(ge=0)
    enabled: bool
    allowed_sources: list[str] = Field(max_length=1)
    allowed_tools: list[str] = Field(max_length=1)
    retention_days: int


def _enabled() -> bool:
    return config.get_features().get("employee_companions") is True


def _authority(request: Request, *, admin: bool = False, write: bool = False):
    """Named, current workspace membership is mandatory, including for admins."""
    auth.require_permission(request, "admin" if admin else "view")
    actor = auth.caller_principal(request)
    tenant = current_tenant_id_strict()
    if not actor or not actor.startswith("user:") or not tenant:
        raise HTTPException(403, "Sign in with a named account and select a workspace to use a companion.")
    membership = rbac.get_tenant_role(tenant, actor)
    if "view" not in rbac.permissions_for(membership) or scim_groups.active_for_principal(actor) is False:
        raise HTTPException(403, "Current workspace membership is required to use a companion.")
    context = hashlib.sha256(json.dumps(["employee-companion-v1", tenant, actor]).encode()).hexdigest()
    supplied = request.headers.get(_HEADER)
    if write:
        from .app import _require_same_origin

        _require_same_origin(request)
    if (write and supplied is None) or (supplied is not None and not hmac.compare_digest(supplied, context)):
        raise HTTPException(409, {"code": "companion_context_changed",
                                  "message": "Your account or workspace changed. Reload the companion before continuing."})
    return actor, tenant, context


async def _call(request: Request, response: Response, operation: Callable, *, admin=False, write=False):
    response.headers["Cache-Control"] = "private, no-store"

    def guarded():
        # App-owned roster/suite changes use this same short local fence.
        with authority_effect_fence():
            actor, tenant, context = _authority(request, admin=admin, write=write)
            return operation(actor, tenant, context, EmployeeCompanionStore())

    try:
        return await run_in_threadpool(guarded)
    except HTTPException:
        raise
    except CompanionConflictError as exc:
        raise HTTPException(409, "Companion policy changed. Refresh and review your request again.") from exc
    except CompanionPolicyDenied as exc:
        raise HTTPException(403, "Your administrator has not allowed this companion operation.") from exc
    except CompanionValidationError as exc:
        raise HTTPException(422, "Choose the supported read-only companion options.") from exc
    except (CompanionError, OSError, ValueError) as exc:
        raise HTTPException(503, "Companion state is unavailable. Refresh before retrying; no task was changed.") from exc
    except Exception as exc:
        # Role/group or source-store outages must not fall back to broader
        # defaults, disclose internal paths, or show an unaudited answer.
        raise HTTPException(503, "Companion access is unavailable. Refresh before retrying; no task was changed.") from exc


def _available(policy):
    return (_enabled() and policy["enabled"] and "own_tasks" in policy["allowed_sources"]
            and "task_status" in policy["allowed_tools"])


def _explanation(policy):
    if not _enabled():
        return "Employee companions are turned off for this installation. An administrator can enable them in Settings."
    if not policy["enabled"]:
        return "Your administrator has not enabled your companion in this workspace."
    if not _available(policy):
        return "Your companion policy does not currently allow task status. Saved conversation can still be cleared."
    return "Read-only task status from your current workspace. Connected sources, actions, and learned memory are unavailable."


def _tasks(actor, *, attention=False):
    from .app import _world

    world = _world()
    # Deliberately do not use goal_owner_filter/caller_suites: their bootstrap
    # administrator bypass is not authority over another person's companion.
    suites = granted_suites(actor)
    statuses = ("blocked", "failed", "error", "draft") if attention else _STATUSES
    rows = {}
    checked_at = time.time()
    for status in statuses:
        for goal in world.list_goals(owner=actor, status=status, limit=50, order="desc"):
            if goal.owner != actor:
                continue
            suite = suite_for(goal.domain)
            if suites is not None and suite not in suites:
                continue
            if goal.project_id is not None:
                project = world.get_project(goal.project_id)
                if not project or project.get("owner") != actor:
                    continue
            # Running work may change status between the bounded queries.
            # Keep one source per task rather than duplicate its receipt.
            rows[goal.id] = {"goal_id": goal.id, "title": goal.title, "status": goal.status,
                             "href": f"/chat/goal/{goal.id}", "checked_at": checked_at}
    return sorted(rows.values(), key=lambda item: item["goal_id"], reverse=True)[:50]


def _history(store, actor):
    # Historic source IDs remain opaque provenance; do not reconstruct links
    # or task titles without a fresh source authorization check.
    turns = []
    for item in store.history(actor):
        turns.extend([{"role": "user", "content": item["message"]},
                      {"role": "assistant", "content": item["response"], "sources": []}])
    return turns


@router.get(BASE)
async def current(request: Request, response: Response):
    def operation(actor, _tenant, context, store):
        policy = store.policy(actor)
        available = _available(policy)
        return {"context": context, "policy": policy, "available": available,
                "explanation": _explanation(policy), "history": _history(store, actor),
                "tasks": _tasks(actor) if available else []}

    return await _call(request, response, operation)


@router.post(BASE + "/messages")
async def message(request: Request, response: Response, body: MessageIn):
    def operation(actor, _tenant, context, store):
        policy = store.policy(actor)
        if policy["revision"] != body.expected_revision:
            raise CompanionConflictError("policy changed")
        if not _available(policy):
            raise CompanionPolicyDenied("operation disabled")
        text = body.message.strip()
        if not text:
            raise HTTPException(422, "Write a message before sending.")
        parsed = parse_utterance(text, _GRAMMAR)
        tasks = _tasks(actor, attention=parsed[0] == "failures") if parsed else []
        if parsed:
            counts = {status: sum(item["status"] == status for item in tasks) for status in _STATUSES}
            summary = ", ".join(f"{count} {_STATUS_LABELS[status]}" for status, count in counts.items() if count)
            reply = (f"Your current task view shows {summary}. Open a source below to review its work."
                     if tasks else "No matching tasks are visible in your current task view.")
            reply += " This view includes at most 50 tasks and reflects your current access."
        else:
            reply = _HELP
        result = store.record_exchange(actor, message=text, response=reply,
                                       source_refs=[f"task:{item['goal_id']}" for item in tasks],
                                       expected_revision=body.expected_revision)
        return {"context": context, "policy": store.policy(actor), "history": _history(store, actor),
                "message": {"role": "assistant", "content": reply, "sources": tasks, "trace": result["trace"]}}

    return await _call(request, response, operation, write=True)


@router.delete(BASE + "/history")
async def clear_history(request: Request, response: Response):
    def operation(actor, _tenant, context, store):
        trace = store.clear_history(actor, actor=actor)
        return {"context": context, "history": [], "trace": trace}

    return await _call(request, response, operation, write=True)


def _employees(tenant):
    people = []
    for principal, role in rbac.list_tenant_roles(tenant).items():
        if (principal.startswith("user:") and "view" in rbac.permissions_for(role)
                and scim_groups.active_for_principal(principal) is not False):
            identity = humanize_principal(principal)
            label = identity.name + (f" ({identity.detail})" if identity.detail and identity.detail != "Email" else "")
            people.append({"principal": principal, "label": label})
    return sorted(people, key=lambda item: (item["label"].casefold(), item["principal"]))


@router.get(BASE + "/admin")
async def administration(request: Request, response: Response):
    def operation(_actor, tenant, context, store):
        return {"context": context, "available": _enabled(),
                "explanation": ("Policies apply only to this read-only companion. Administrators cannot read companion conversation text here."
                                if _enabled() else "Turn on Employee companions in Settings. You can prepare employee policies here first."),
                "policies": store.policies(), "employees": _employees(tenant), "events": store.events()}

    return await _call(request, response, operation, admin=True)


@router.put(BASE + "/admin/policy")
async def update_policy(request: Request, response: Response, body: PolicyIn):
    def operation(actor, tenant, context, store):
        if body.owner not in {item["principal"] for item in _employees(tenant)}:
            raise HTTPException(404, "Choose a current member of this workspace.")
        policy = store.update_policy(body.owner, actor=actor, **body.model_dump(exclude={"owner"}))
        return {"context": context, "policy": policy}

    return await _call(request, response, operation, admin=True, write=True)


def _page(request, *, admin=False):
    from .app import templates

    with authority_effect_fence():
        _actor, _tenant, context = _authority(request, admin=admin)
    name = "employee_companion_admin.html" if admin else "employee_companion.html"
    return templates.TemplateResponse(request, name, {"companion_context": context},
                                      headers={"Cache-Control": "private, no-store"})


@router.get("/companion")
def companion_page(request: Request):
    return _page(request)


@router.get("/companion/admin")
def companion_admin_page(request: Request):
    return _page(request, admin=True)
