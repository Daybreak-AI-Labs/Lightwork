"""Owned task discovery and reviewed launch through native task admission."""
from __future__ import annotations

from fastapi import BackgroundTasks, HTTPException, Response
from maverick.governed_operations import digest
from maverick.runner import (
    configured_goal_ceiling_dollars,
    configured_goal_ceiling_wall_seconds,
    configured_max_dollars,
    configured_max_wall_seconds,
    requested_goal_dollars,
    requested_goal_wall_seconds,
)
from pydantic import Field

from . import api, auth
from .api_schemas import GoalIn
from .draft_api import context_for
from .product_operations import Adapter, Outcome, Preview, Snapshot, StrictInput


class FindInput(StrictInput):
    query: str = Field(min_length=1, max_length=256)


class StartInput(StrictInput):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=12000)
    max_dollars: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_wall_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


def find_snapshot(request, selection):
    auth.require_permission(request, "view")
    owner = auth.goal_owner_filter(request)
    return Snapshot(digest({"context": context_for(request), "owner": owner}),
        "Current task search for this account and workspace",
        "Find saved tasks by words in their title, request, or result. Open a result to review its current controls.",
        fields=[{"name": "query", "label": "Task search", "kind": "text", "required": True,
                 "max_length": 256}])


def find_prepare(request, selection, arguments):
    auth.require_permission(request, "view")
    query = arguments["query"].strip()
    if not query:
        raise HTTPException(422, "Enter words to search your tasks.")
    # Use the same indexed native search and owner scope as /api/v1/goals/search.
    # Recheck each result's current suite/content authority before publishing it.
    world = api._world()
    goals = world.search_goals(query, owner=auth.goal_owner_filter(request), limit=200)
    # The search projection omits the domain column. Resolve each full row so
    # current department grants remain effective for discoverability as well.
    current = [world.get_goal(goal.id) for goal in goals]
    visible = [goal for goal in current if goal is not None and auth.can_access_goal(request, goal)]
    rows = [{"task": {"label": goal.title, "href": f"/chat/goal/{goal.id}"},
             "status": goal.status}
            for goal in visible[:20]]
    auth.require_permission(request, "view")
    summary = (f"Showing the first {len(rows)} matching tasks. Open a task to review its current controls."
               if len(visible) > 20 else
               f"Found {len(rows)} matching task{'s' if len(rows) != 1 else ''} in the first search window."
               if rows else "No accessible tasks matched these words in the first search window.")
    return Preview(summary,
                   native={"query": query, "tasks": rows,
                           "more_in_window": len(visible) > 20,
                           "window_limited": len(goals) == 200,
                           "search_href": "/goals"})


def _policy():
    return {"default_max_dollars": configured_max_dollars(),
            "default_max_wall_seconds": configured_max_wall_seconds(),
            "configured_ceiling_dollars": configured_goal_ceiling_dollars(),
            "configured_ceiling_wall_seconds": configured_goal_ceiling_wall_seconds()}


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
    GoalIn(**{**arguments, "title": title})
    policy = _policy()
    effective = {"max_dollars": requested_goal_dollars(arguments["max_dollars"]),
                 "max_wall_seconds": requested_goal_wall_seconds(arguments["max_wall_seconds"])}
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
