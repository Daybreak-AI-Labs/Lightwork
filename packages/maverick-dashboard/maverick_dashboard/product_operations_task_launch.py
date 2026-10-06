"""Owned task discovery and reviewed launch through native task admission."""
from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException, Response
from maverick.governed_operations import digest
from maverick.runner import (
    configured_goal_ceiling_dollars,
    configured_goal_ceiling_wall_seconds,
)
from pydantic import Field

from . import api, auth, task_search
from .api_schemas import GoalIn
from .draft_api import context_for
from .product_operations import Adapter, Outcome, Preview, Snapshot, StrictInput


class FindInput(StrictInput):
    query: str = Field(min_length=1, max_length=256)
    where: str = Field(default="newest", max_length=1536)


class StartInput(StrictInput):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=12000)
    max_dollars: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_wall_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


def find_snapshot(request, selection):
    current = task_search.context(request)
    filters = selection.get("filters", {})
    query = filters.get("q", "") if selection.get("kind") == "task_filter" and isinstance(filters, dict) else ""
    choices = [{"value": "newest", "label": "Newest work"}]
    selected = selection.get("task_search", {})
    if (isinstance(selected, dict) and set(selected) == {"query", "continuation"}
            and isinstance(selected["query"], str) and isinstance(selected["continuation"], str)):
        try:
            task_search.validate_cursor(request, selected["query"], selected["continuation"])
            choices.append({"value": selected["continuation"], "label": "Older than this search part"})
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
    return Snapshot(digest({"context": current, "choices": choices}),
        "Current task search for this account and workspace",
        task_search.NOTICE,
        fields=[{"name": "query", "label": "Task search", "kind": "text", "required": True,
                 "max_length": 256, "value": query if isinstance(query, str) else ""},
                {"name": "where", "label": "Where to look", "kind": "select", "value": "newest", "choices": choices}])


def find_prepare(request, selection, arguments):
    auth.require_permission(request, "view")
    query = arguments["query"].strip()
    if not query:
        raise HTTPException(422, "Enter words to search your tasks.")
    where = arguments.get("where", "newest")
    if where != "newest":
        selected = selection.get("task_search", {})
        if not isinstance(selected, dict) or selected.get("continuation") != where:
            raise HTTPException(409, "Choose older work from the current task search, or search the newest work.")
    value = task_search.page(request, query, cursor="" if where == "newest" else where, limit=20)
    rows = [{"task": {"label": goal.title, "href": f"/chat/goal/{goal.id}"},
             "status": goal.status}
            for goal in value["goals"]]
    summary = f"Found {len(rows)} matching task{'s' if len(rows) != 1 else ''} in this part. " + (
        "Reached the end of the retained list; later edits need a fresh search." if value["complete"] else
        "Older work remains unchecked. Continue on Tasks to keep looking.")
    details = {"query": query, "tasks": rows, "search_complete": value["complete"],
               "search": {"label": "Open Tasks search", "href": task_search.link(query)}}
    if value["continuation"]:
        details["next_part"] = {"label": "Search older work", "href": task_search.link(query, value["continuation"])}
    return Preview(summary, native=details)


def _policy():
    defaults = api._rest_goal_limits(GoalIn(title="New task"))
    return {"default_max_dollars": defaults["max_dollars"],
            "default_max_wall_seconds": defaults["max_wall_seconds"],
            "configured_ceiling_dollars": configured_goal_ceiling_dollars(),
            "configured_ceiling_wall_seconds": configured_goal_ceiling_wall_seconds(),
            "api_ceiling_dollars": api._REST_GOAL_MAX_DOLLARS,
            "api_ceiling_wall_seconds": api._REST_GOAL_MAX_WALL_SECONDS}


def start_snapshot(request, selection):
    auth.require_permission(request, "operate")
    policy = _policy()
    blocked = ""
    try:
        api.require_provider_or_400()
    except HTTPException as exc:
        if exc.status_code not in {400, 503}:
            raise
        blocked = "Set up an available AI provider before starting a task."
    return Snapshot(digest({"context": context_for(request), "policy": policy,
                            "provider_ready": not blocked}),
        "Current native task limits and AI-provider readiness",
        "Start one new task under current workspace limits. Acceptance starts work; it does not mean the task is complete.",
        {"policy": policy, "provider_ready": not blocked, "setup_href": "/start"},
        [{"name": "title", "label": "Task", "kind": "text", "required": True, "max_length": 200},
         {"name": "description", "label": "Instructions (optional)", "kind": "textarea", "max_length": 12000},
         {"name": "max_dollars", "label": "Maximum dollars (blank uses workspace limit)", "kind": "number"},
         {"name": "max_wall_seconds", "label": "Maximum seconds (blank uses workspace limit)", "kind": "number"}],
        blocked)


def start_prepare(request, selection, arguments):
    title = arguments["title"].strip()
    if not title:
        raise HTTPException(422, "Enter a task before reviewing its start.")
    # Validate against the native launch schema before sealing the review.
    payload = GoalIn(**{**arguments, "title": title})
    policy = _policy()
    # This reviewed Copilot action enters the same native goal endpoint. Show
    # the limits that endpoint will actually dispatch, including REST safety
    # ceilings on a legacy installation whose general task defaults are off.
    effective = api._rest_goal_limits(payload)
    return Preview("Start one new task. Its accepted receipt confirms admission, not completion or external delivery.",
        [{"label": "Task", "after": title},
         {"label": "Instructions", "after": arguments["description"]},
         {"label": "Maximum dollars", "after": effective["max_dollars"]},
         {"label": "Maximum seconds", "after": effective["max_wall_seconds"]},
         {"label": "External effects", "after": "Work may use authorized tools; review task progress and any approvals."}],
        {"policy": policy, "effective": effective})


def _receipt_outcome(goal, *, replayed, background=None):
    return Outcome("replayed" if replayed else "queued",
        "The task was already admitted; no second task was started." if replayed else
        "Task admission was recorded. Open progress to see whether execution has started or needs your input.",
        {"goal_id": goal.id, "status": goal.status,
         "task": {"label": "Open task progress", "href": f"/chat/goal/{goal.id}"}},
        background=background)


def start_recover(request, selection, arguments, prepared, operation_id):
    from .goal_submission import lookup_receipt

    auth.require_permission(request, "operate")
    try:
        _receipt, goal = lookup_receipt(api._world(), key=operation_id,
            owner=auth.caller_principal(request) or "", kind="api")
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise
    auth.assert_goal_access(request, goal)
    if goal.status == "draft":
        return None  # Native same-key replay may finish the one pending dispatch claim.
    return _receipt_outcome(goal, replayed=True)


def start_execute(request, selection, arguments, prepared, operation_id):
    if _policy() != prepared["policy"]:
        raise HTTPException(409, "Task limits changed. Review the current limits before starting.")
    background, response = BackgroundTasks(), Response()
    goal = api._create_goal(request, GoalIn(**{**arguments, "title": arguments["title"].strip()}),
                            background, response, idempotency_key=operation_id)
    return _receipt_outcome(goal, replayed=response.headers.get("Idempotency-Replayed") == "true",
                            background=background)


def entries():
    pages = ("/chat", "/goals", "/today", "/chat/goal/*", "/goals/*")
    return (
        Adapter("tasks.find", "Find one of my tasks", "tasks", "/goals", pages,
                FindInput, find_snapshot, find_prepare,
                aliases=("find a task", "search my tasks", "find my task")),
        Adapter("tasks.start", "Start a new task", "tasks", "/chat", ("/chat", "/goals", "/today"),
                StartInput, start_snapshot, start_prepare, start_execute, start_recover,
                aliases=("start a task", "create a task", "new task")),
    )
