"""One reviewed task restart service for native controls and typed Copilot."""
from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException
from maverick import goal_resume
from maverick.attachment_submissions import SubmissionConflict
from maverick.authority_effects import authority_effect_fence
from maverick.goal_mutations import GoalMutationConflict
from maverick.task_execution_guard import TaskBusy
from maverick.task_handoffs import require_open

from . import auth
from .draft_api import context_for
from .product_errors import next_action, product_error


def current(request, goal_id):
    from .api import _world, assert_goal_access, require_permission
    require_permission(request, "operate")
    world = _world()
    goal = world.get_goal(goal_id)
    if goal is None:
        raise HTTPException(404, "This task is unavailable.")
    assert_goal_access(request, goal)
    if (auth.caller_principal(request) or "") != (goal.owner or ""):
        raise HTTPException(403, "Only the current task owner can restart this work.")
    return world, goal, auth.caller_principal(request) or "local administrator"


def limits(request, goal_id, max_dollars):
    from .api import configured_max_dollars
    _world, _goal, _actor = current(request, goal_id)
    ceiling = configured_max_dollars()
    if max_dollars is not None and ceiling is not None and max_dollars > ceiling:
        raise product_error(400, "budget_ceiling", "The requested spending limit is above the workspace limit. Choose a lower amount.",
                            field_errors={"max_dollars": f"Choose an amount no greater than ${ceiling:,.2f}."},
                            next_action=next_action("Review the task spending limit", f"/chat/goal/{goal_id}#halt-resume-form"))
    return ceiling


def status(request, goal_id):
    world, goal, _actor = current(request, goal_id)
    closed = ""
    try:
        require_open(world, goal_id)
    except GoalMutationConflict as exc:
        closed = str(exc)
    return {"goal_id": goal_id, "title": goal.title, "status": goal.status, "context": context_for(request),
            "expected_updated_at": float(goal.updated_at), "expected_owner": goal.owner or "",
            "workspace_ceiling": limits(request, goal_id, None), "blocked": closed or
            ("Only stopped, failed or blocked tasks can be resumed." if goal.status not in {"blocked", "cancelled", "failed"} else ""),
            "unanswered_questions": len(world.open_questions(goal_id=goal_id)),
            "consequence": "Starts a new billable attempt under current policy. Earlier external actions are not undone; inspect uncertain effects before retrying."}


def recover(request, goal_id, payload):
    with authority_effect_fence():
        world, _goal, actor = current(request, goal_id)
        if payload.expected_context is not None and payload.expected_context != context_for(request):
            raise HTTPException(409, "Your account or workspace changed. Review this task again.")
        if not payload.operation_id or payload.expected_updated_at is None or payload.expected_owner is None:
            return None
        try:
            return goal_resume.receipt(world, goal_id, actor=actor, operation_id=payload.operation_id,
                expected_updated_at=payload.expected_updated_at, expected_owner=payload.expected_owner,
                max_dollars=payload.max_dollars)
        except GoalMutationConflict as exc:
            raise HTTPException(409, str(exc)) from exc


def apply(request, goal_id, background, payload):
    from .api import _dispatch_requeued_goal, _require_goal_restart_admission
    with authority_effect_fence():
        world, goal, actor = current(request, goal_id)
        prior = recover(request, goal_id, payload)
        if prior is not None:
            return prior
        limits(request, goal_id, payload.max_dollars)
        try:
            result = goal_resume.reserve(world, goal_id, actor=actor,
                operation_id=payload.operation_id or uuid4().hex,
                expected_updated_at=payload.expected_updated_at if payload.expected_updated_at is not None else float(goal.updated_at),
                expected_owner=payload.expected_owner if payload.expected_owner is not None else goal.owner or "",
                max_dollars=payload.max_dollars, require_authority=lambda: current(request, goal_id),
                require_admission=lambda: _require_goal_restart_admission(goal_id))
        except (GoalMutationConflict, SubmissionConflict, TaskBusy) as exc:
            raise product_error(409, "restart_changed", str(exc),
                next_action=next_action("Review current task progress", f"/chat/goal/{goal_id}")) from exc
        if not result["replayed"]:
            _dispatch_requeued_goal(request, background, goal_id, conversation_id=result["conversation_id"],
                                    max_dollars=payload.max_dollars)
        return result
