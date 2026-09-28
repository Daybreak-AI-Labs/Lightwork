"""Download a task's full history, or share it as a private GitHub gist.

``maverick export`` -> ``GET /api/v1/goals/{id}/export.json`` builds the same
bundle through :func:`maverick.goal_bundle.build_goal_bundle` and returns it as
a download. ``maverick share`` -> ``POST /api/v1/goals/{id}/share/gist`` calls
:func:`maverick.run_share.share_run` (secrets are scrubbed there; it never
uploads without a GitHub key and always makes a private gist). Access follows
the task pages: the per-task access check, ``operate`` for the upload, an
explicit confirmation, the same-origin guard and an audit row before anything
leaves this installation.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from .auth import assert_goal_access, caller_principal, require_permission

log = logging.getLogger(__name__)
router = APIRouter(tags=["goal-export"])


class GistShareIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirm: bool = False


def _goal(request: Request, goal_id: int):
    from .app import _world

    world = _world()
    goal = world.get_goal(goal_id)
    if goal is None:
        raise HTTPException(404, "no such goal")
    assert_goal_access(request, goal)
    return world, goal


@router.get("/api/v1/goals/{goal_id}/export.json")
async def export_goal_bundle(request: Request, goal_id: int) -> Response:
    """The task's goal record, events and episode summaries as one file."""
    from maverick.goal_bundle import build_goal_bundle

    require_permission(request, "view")
    world, _ = _goal(request, goal_id)
    bundle = await run_in_threadpool(build_goal_bundle, world, goal_id)
    if bundle is None:
        raise HTTPException(404, "no such goal")
    body = json.dumps(bundle, default=str, indent=2)
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="task-{goal_id}.json"',
        "Cache-Control": "private, no-store",
    })


@router.post("/api/v1/goals/{goal_id}/share/gist")
async def share_goal_gist(request: Request, goal_id: int, body: GistShareIn) -> dict:
    """Upload a scrubbed copy of the task's history as a private gist."""
    from maverick.audit import AuditRefused, audit_event
    from maverick.run_share import share_run

    from .app import _require_same_origin

    require_permission(request, "operate")
    _require_same_origin(request)
    _goal(request, goal_id)
    if body.confirm is not True:
        raise HTTPException(409, "Confirm that you want to upload a copy of this task to GitHub.")
    actor = caller_principal(request) or "local"
    try:
        audit_event("goal_shared_gist", agent="dashboard", goal_id=goal_id, actor=actor, phase="start")
    except AuditRefused as exc:
        raise HTTPException(503, "The activity log couldn't record this, so nothing was uploaded.") from exc
    try:
        url = await run_in_threadpool(share_run, goal_id)
    except RuntimeError:
        return {"shared": False, "message": "GitHub isn't connected on this installation, so nothing was uploaded. "
                                            "An administrator can add a GitHub key in settings."}
    except Exception:
        log.warning("gist share failed for goal %s", goal_id, exc_info=True)
        return {"shared": False, "message": "GitHub didn't accept the upload. Nothing was shared. Try again later."}
    try:
        audit_event("goal_shared_gist", agent="dashboard", goal_id=goal_id, actor=actor, phase="done")
    except AuditRefused:
        log.warning("gist share completion audit row refused for goal %s", goal_id)
    if not isinstance(url, str) or not url.startswith("https://"):
        return {"shared": False, "message": "GitHub didn't return a link. Check your GitHub account's gists."}
    return {"shared": True, "url": url}
