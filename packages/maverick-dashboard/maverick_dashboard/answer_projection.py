"""Revalidated view projection shared by initial and live conversation answers."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from .auth import assert_goal_access, caller_principal, has_permission, require_permission
from .draft_api import context_for
from .product_errors import product_error


def version_for(goal, contract, signoff, artifacts):
    source = {"goal": asdict(goal), "contract": contract, "signoff": signoff, "artifacts": artifacts}
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def _snapshot(world, goal):
    from .app import _goal_artifacts, _goal_deliverable

    contract, rendered = _goal_deliverable(goal, strict=True)
    signoff = world.signoff_for(goal.id) if contract and contract.get("gate") else None
    artifacts = _goal_artifacts(world, goal.id, strict=True)
    from maverick.task_handoffs import closure
    closed = closure(world, goal.id)
    version = version_for(goal, contract, signoff, artifacts)
    version = hashlib.sha256((version + json.dumps(closed, sort_keys=True)).encode()).hexdigest()
    return {"goal": goal, "deliverable": contract, "rendered": rendered,
            "signoff": signoff, "artifacts": artifacts, "answer_version": version, "handoff_closed": closed}


def projection(request, world, goal_id: int) -> dict:
    from .app import _chat_answer_download_state, _chat_answer_text, _voice_only_mode

    require_permission(request, "view")
    goal = world.get_goal(goal_id)
    if goal is None:
        raise product_error(404, "task_unavailable", "This task is no longer available.")
    assert_goal_access(request, goal)
    try:
        context = _snapshot(world, goal)
        fresh = world.get_goal(goal_id)
        if fresh is None:
            raise product_error(404, "task_unavailable", "This task is no longer available.")
        assert_goal_access(request, fresh)
        latest = _snapshot(world, fresh)
        if context["answer_version"] != latest["answer_version"]:
            raise product_error(409, "result_changing", "The saved result is still changing. Refresh it again in a moment.", retryable=True)
    except (LookupError, ValueError, TypeError) as exc:
        raise product_error(503, "result_unavailable", "The saved result and its release requirements could not be loaded. Retry to confirm the current version.", retryable=True) from exc
    can_download, download_note = _chat_answer_download_state(world, fresh)
    # Exceptional content grants never become permission to mutate the task.
    context.update(can_operate=not context["handoff_closed"] and has_permission(request, "operate") and
                   (caller_principal(request) or "") == (fresh.owner or ""),
                   conversation_context=context_for(request), voice_only=_voice_only_mode(),
                   can_download_chat_answer=can_download, chat_answer_download_note=download_note,
                   chat_answer_text=_chat_answer_text(fresh))
    return context
