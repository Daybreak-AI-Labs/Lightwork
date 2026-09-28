"""Dashboard controls for operations that used to be command-line only.

One include point for the small routers that put ``maverick ...`` operations
in the app (task file history, task export and sharing, schedules, community
templates, business agent setup, Ekko recording and flow teaching), plus the
page scripts they use. Each router keeps the permission, access and audit
checks of its closest existing sibling and calls the same kernel function the
command does.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from .business_agent_ops_api import router as _business_agent_router
from .ekko_ops_api import router as _ekko_router
from .flow_teach_ops_api import router as _flow_teach_router
from .goal_export_ops_api import router as _goal_export_router
from .plan_reflect_ops_api import router as _plan_reflect_router
from .schedule_ops_api import router as _schedule_router
from .task_journal_ops_api import router as _journal_router
from .template_ops_api import router as _template_router

router = APIRouter()
for _child in (_journal_router, _goal_export_router, _schedule_router, _template_router,
               _business_agent_router, _ekko_router, _flow_teach_router, _plan_reflect_router):
    router.include_router(_child)

_STATIC = Path(__file__).parent / "static"
ASSETS = (
    "lightwork-task-journal.js",
    "lightwork-goal-export.js",
    "lightwork-schedule-picker.js",
    "lightwork-community-templates.js",
    "lightwork-business-agent.js",
    "lightwork-ekko-recording.js",
    "lightwork-flow-teach.js",
    "lightwork-planning-steps.js",
)


def _asset(name: str):
    async def serve() -> FileResponse:
        return FileResponse(_STATIC / name, media_type="application/javascript; charset=utf-8",
                            headers={"Cache-Control": "public, max-age=300"})
    serve.__name__ = "asset_" + name.replace("-", "_").replace(".", "_")
    return serve


for _name in ASSETS:
    router.add_api_route("/static/" + _name, _asset(_name), methods=["GET"], include_in_schema=False)
