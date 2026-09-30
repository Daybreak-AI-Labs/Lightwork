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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .auth import assert_goal_access, require_permission

router = APIRouter(tags=["goal-export"])


class GistShareIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirm: bool = False
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


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
    from maverick.goal_bundle import IncompleteGoalBundle, build_goal_bundle

    require_permission(request, "view")
    world, _ = _goal(request, goal_id)
    try:
        bundle = await run_in_threadpool(build_goal_bundle, world, goal_id)
    except IncompleteGoalBundle as exc:
        raise HTTPException(409, str(exc)) from exc
    if bundle is None:
        raise HTTPException(404, "no such goal")
    body = json.dumps(bundle, default=str, indent=2)
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="task-{goal_id}.json"',
        "Cache-Control": "private, no-store",
    })


@router.get("/api/v1/goals/{goal_id}/handoff.html")
async def export_goal_handoff(request: Request, goal_id: int) -> Response:
    """A readable private handoff, without internal event/tool history."""
    import html
    import re

    require_permission(request, "view")
    world, goal = _goal(request, goal_id)
    artifacts = await run_in_threadpool(world.artifacts_for_goal, goal_id)
    questions = await run_in_threadpool(world.open_questions, goal_id=goal_id)
    _, fresh = _goal(request, goal_id)
    if fresh.updated_at != goal.updated_at or fresh.result != goal.result:
        raise HTTPException(409, "The task changed while preparing the handoff. Review the new result and retry.")
    def esc(value):
        return html.escape(str(value or ""))
    sections = ["<!doctype html><html lang='en'><meta charset='utf-8'>",
                "<meta name='viewport' content='width=device-width,initial-scale=1'>",
                "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">",
                "<title>Task handoff</title><style>body{max-width:70rem;margin:2rem auto;padding:0 1rem;font:18px/1.5 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #999;padding-top:1rem}</style><body><main>",
                f"<h1>{esc(goal.title)}</h1>",
                "<p>Private task handoff. Downloading does not grant access, approve work, transfer responsibility or submit anything externally. Review internal and business content before forwarding.</p>",
                f"<p>Status: {esc(goal.status)}. Source task: {goal_id}. Recorded version: {esc(goal.updated_at)}.</p>",
                f"<section><h2>Objective</h2><pre>{esc(goal.description)}</pre></section>",
                f"<section><h2>Current outcome</h2><pre>{esc(goal.result) or 'No saved outcome yet.'}</pre></section>",
                "<section><h2>Deliverables</h2>"]
    for artifact in artifacts[:200]:
        sections.append(f"<h3>{esc(artifact.get('title'))} — version {esc(artifact.get('version'))}</h3><pre>{esc(artifact.get('content'))}</pre>")
    if not artifacts:
        sections.append("<p>No stored artifacts.</p>")
    sections.append("</section><section><h2>Cited sources</h2><ul>")
    sources = sorted(set(re.findall(r"https?://[^\s<>\"')]+", goal.result or "")))
    sections.extend(f"<li>{esc(source)}</li>" for source in sources[:200])
    if not sources:
        sections.append("<li>No source URLs identified in the saved outcome. This is not evidence that sources were verified.</li>")
    sections.append("</ul></section><section><h2>Pending questions</h2><ul>")
    sections.extend(f"<li>{esc(question.question)}</li>" for question in questions[:200])
    if not questions:
        sections.append("<li>No open questions recorded.</li>")
    sections.append("</ul></section><section><h2>Permissions and completeness</h2><p>This copy contains the saved outcome, up to 200 artifact versions, identified outcome URLs and open questions. It excludes private conversation history, tool/event logs, attachment bytes and external-system records. Links do not grant access to their destinations.</p>")
    if len(artifacts) > 200 or len(questions) > 200 or len(sources) > 200:
        sections.append("<p><strong>Partial handoff: at least one section exceeds 200 entries. Review the task for the remaining items.</strong></p>")
    else:
        sections.append("<p>All stored items in the stated handoff scope are included. This is a read-only snapshot, not a resumable task or a verification of external effects.</p>")
    sections.append("</section></main></body></html>")
    return HTMLResponse("".join(sections), headers={"Content-Disposition": f'attachment; filename="task-{goal_id}-handoff.html"',
                                                    "Cache-Control": "private, no-store"})


def _sharing_context(request, goal_id):
    from .app import _require_same_origin
    from .draft_api import context_for

    require_permission(request, "operate")
    _require_same_origin(request)
    _goal(request, goal_id)
    return context_for(request)


async def _sharing_call(function, *args):
    from maverick.replay.export import ReplayEvidenceError
    from maverick.run_share import MissingGitHubToken

    try:
        return await run_in_threadpool(function, *args)
    except MissingGitHubToken as exc:
        raise HTTPException(409, "GitHub sharing is not connected. Ask an administrator to connect it in Settings.") from exc
    except ReplayEvidenceError as exc:
        raise HTTPException(409, "This task's evidence could not be verified. No upload started. Review the task evidence or contact support.") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else "This review is no longer available. Prepare a new copy.") from exc
    except Exception as exc:
        raise HTTPException(503, "The sharing operation could not be confirmed. Check the existing attempt before sending another copy.") from exc


@router.post("/api/v1/goals/{goal_id}/share/gist/preview")
async def preview_goal_gist(request: Request, goal_id: int) -> dict:
    from .goal_share_previews import prepare

    return await _sharing_call(prepare, _sharing_context(request, goal_id), goal_id)


@router.get("/api/v1/goals/{goal_id}/share/gist/attempts")
async def goal_gist_attempts(request: Request, goal_id: int) -> dict:
    from .draft_api import context_for
    from .goal_share_previews import attempts

    require_permission(request, "operate")
    _goal(request, goal_id)
    return await _sharing_call(attempts, context_for(request), goal_id)


@router.post("/api/v1/goals/{goal_id}/share/gist")
async def share_goal_gist(request: Request, goal_id: int, body: GistShareIn) -> dict:
    from .goal_share_previews import send

    context = _sharing_context(request, goal_id)
    if body.confirm is not True:
        raise HTTPException(409, "Review the exact copy and confirm before uploading to GitHub.")
    return await _sharing_call(send, context, goal_id, body.preview_id, body.sha256)


@router.post("/api/v1/goals/{goal_id}/share/gist/{attempt}/reconcile")
async def reconcile_goal_gist(request: Request, goal_id: int, attempt: str) -> dict:
    import re

    from .goal_share_previews import reconcile

    context = _sharing_context(request, goal_id)
    if not re.fullmatch(r"[a-f0-9]{32}", attempt):
        raise HTTPException(404, "No such sharing attempt.")
    return await _sharing_call(reconcile, context, goal_id, attempt)
