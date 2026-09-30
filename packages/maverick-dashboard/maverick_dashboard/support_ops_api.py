"""Help page: report a problem with a task that stopped (``maverick report-issue``).

The person picks one of their own stopped tasks; the kernel's
``issue_report.build_report`` gathers that task's error messages, removes
passwords and keys, and builds a link to Lightwork's public bug-report form
with the details filled in. Nothing is sent: the person opens the link and
decides whether to submit it.

Anyone who can view the dashboard may use it for tasks they can see; the list
and the link follow the same task-access rules as the Tasks page.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .auth import assert_goal_access, goal_owner_filter, require_permission

router = APIRouter(prefix="/api/v1/support/issue-report")
asset_router = APIRouter()
_STATIC = Path(__file__).parent / "static"
_STOPPED = ("blocked", "failed")


@asset_router.get("/static/lightwork-support-ops.js", include_in_schema=False)
async def support_ops_script() -> FileResponse:
    return FileResponse(_STATIC / "lightwork-support-ops.js", media_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=300"})


def _world():
    from ._shared import _world as shared
    return shared()


@router.get("/tasks")
async def stopped_tasks(request: Request) -> dict:
    require_permission(request, "view")
    owner = goal_owner_filter(request)

    def read() -> list[dict]:
        world = _world()
        rows = []
        for status in _STOPPED:
            for goal in world.list_goals(status=status, owner=owner, limit=25, order="desc"):
                rows.append({"id": goal.id, "title": (goal.title or "Untitled task")[:160],
                             "when": goal.updated_at})
        rows.sort(key=lambda row: row["when"] or 0, reverse=True)
        return rows[:25]
    return {"tasks": await run_in_threadpool(read)}


class IssueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: int = Field(ge=1, le=2**53)


@router.post("")
async def issue_link(body: IssueIn, request: Request) -> dict:
    require_permission(request, "view")
    from maverick.issue_report import DEFAULT_REPO, build_report
    world = _world()
    goal = await run_in_threadpool(world.get_goal, body.task)
    if goal is None:
        raise HTTPException(404, "That task no longer exists. Refresh the list.")
    assert_goal_access(request, goal)

    def build() -> str:
        errors = [e for e in world.goal_events(body.task, limit=10_000) if e.kind == "error"]
        return build_report(goal, errors, repo=DEFAULT_REPO)
    url = await run_in_threadpool(build)
    return {"url": url,
            "summary": "The report is ready. It opens Lightwork's public bug-report page with the task's name and "
                       "error messages filled in; passwords and keys have been removed. Nothing is sent until you "
                       "submit it there."}
