"""Ordinary saved-answer feedback with native authority and original readback."""
from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException, Request, Response
from maverick import result_feedback_receipts as store
from maverick.goal_mutations import GoalMutationConflict
from pydantic import BaseModel, ConfigDict, Field

from . import auth, task_signoff_operations
from .draft_api import context_for
from .product_errors import next_action, product_error

router = APIRouter(prefix="/api/v1/goals", tags=["answer-feedback"])


class FeedbackChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rating: str = Field(pattern=r"^(up|down)$")
    note: str = Field(default="", max_length=500)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_context: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")


def public(value, goal):
    return {"operation_id": value["operation_id"], "rating": value["rating"], "note": value["note"],
            "recorded_at": value["recorded_at"], "current_result": value["revision"] == store.version(goal),
            "observation": value["observation"], "replayed": value.get("replayed", False), "legacy": False}


def read(request, goal_id, operation_id=None):
    from . import api
    world, goal = task_signoff_operations.authorized(request, goal_id)
    value = store.receipt(world, goal_id, operation_id, api._supervisor(request)) if operation_id else store.latest(world, goal_id, goal.owner or "")
    _world, goal = task_signoff_operations.authorized(request, goal_id)
    if value is not None and value["owner"] != (goal.owner or ""):
        value = None
    feedback = public(value, goal) if value else None
    if feedback is None and operation_id is None:
        from . import goal_feedback_store
        legacy = goal_feedback_store.latest_for_goal(goal_id, owner=goal.owner or "")
        if (legacy and legacy.get("rating") in {"up", "down"} and isinstance(legacy.get("note", ""), str)
                and type(legacy.get("ts")) in (int, float) and math.isfinite(legacy["ts"]) and legacy["ts"] > 0):
            feedback = {"operation_id": None, "rating": legacy["rating"], "note": legacy.get("note", "")[:500],
                        "recorded_at": legacy["ts"], "current_result": False, "observation": "not_known", "replayed": False,
                        "legacy": True, "message": "Earlier feedback: the answer version was not recorded"}
    _world, current = task_signoff_operations.authorized(request, goal_id)
    if (current.owner or "") != (goal.owner or ""):
        raise HTTPException(404, "This task is unavailable to your account.")
    if value is not None:
        feedback = public(value, current)
    return {"context": context_for(request), "revision": store.version(current), "updated_at": float(current.updated_at),
            "task": current.title, "eligible": current.status in {"done", "blocked", "failed"} and bool(current.result),
            "feedback": feedback,
            "original": operation_id is not None}


def write_authority(request, goal_id):
    from maverick.task_handoffs import closure

    world, goal = task_signoff_operations.authorized(request, goal_id, write=True)
    if (auth.caller_principal(request) or "") != (goal.owner or ""):
        raise HTTPException(403, "Only the current task owner can give feedback on this answer.")
    if closure(world, goal_id):
        raise HTTPException(409, "This original conversation is closed after its handoff. Read its saved feedback instead.")
    return world, goal


def submit(request, goal_id, payload):
    from maverick.authority_effects import authority_effect_fence

    with authority_effect_fence():
        return _submit_reviewed(request, goal_id, payload)


def _submit_reviewed(request, goal_id, payload):
    from . import api
    world, goal = write_authority(request, goal_id)
    if payload.expected_context != context_for(request):
        raise product_error(409, "context_changed", "Your account, workspace or access changed. Reopen the authorized answer before giving feedback.")
    actor = api._supervisor(request)
    try:
        value = store.record(world, goal_id, rating=payload.rating, note=payload.note, revision=payload.revision,
                             owner=goal.owner or "", context=payload.expected_context, actor=actor, operation_id=payload.operation_id)
    except GoalMutationConflict as exc:
        raise product_error(409, "stale_version", "The reviewed answer or feedback changed. Your note is kept; review the current saved answer before giving feedback.",
                            next_action=next_action("Review the saved answer", f"/chat/goal/{goal_id}#feedback")) from exc
    # Only the caller that admitted the receipt may attempt the optional signal.
    # A crash or a lost response never authorizes another learning attempt.
    if not value.get("replayed"):
        store.observed(world, goal_id, payload.operation_id, actor, "started")
        def authorize_signal():
            # No world read here: the native fence rechecks every source field.
            auth.require_permission(request, "operate")
            auth.assert_goal_access(request, goal)
            if context_for(request) != payload.expected_context:
                raise HTTPException(409, "The feedback's account or workspace changed.")
        try:
            recorded = store.observe(world, goal_id, value, authorize=authorize_signal)
        except Exception:
            # Failure of optional learning never changes the accepted feedback.
            recorded = False
        value = store.observed(world, goal_id, payload.operation_id, actor, "recorded" if recorded else "not_recorded")
    _world, current = task_signoff_operations.authorized(request, goal_id)
    return {"context": context_for(request), "feedback": public(value, current) if value["owner"] == (current.owner or "") else None}


@router.get("/{goal_id}/answer-feedback")
def feedback_read(request: Request, response: Response, goal_id: int, operation_id: str | None = None):
    response.headers["Cache-Control"] = "no-store"
    if operation_id is not None and (len(operation_id) != 32 or any(c not in "0123456789abcdef" for c in operation_id)):
        raise HTTPException(422, "The original feedback reference is unavailable. Review current saved feedback instead.")
    return read(request, goal_id, operation_id)


@router.post("/{goal_id}/answer-feedback")
def feedback_submit(request: Request, response: Response, goal_id: int, payload: FeedbackChangeIn):
    response.headers["Cache-Control"] = "no-store"
    return submit(request, goal_id, payload)
