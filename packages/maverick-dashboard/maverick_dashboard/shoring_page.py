"""Authenticated operator page for Autonomous Shoring (Observe -> Autonomous
Shoring).

Ties the engagement together on one surface: the Shoring P&L (money saved vs
the human baseline, from :mod:`maverick.savings`), the Book of Work with each
function's lifecycle status and per-SLA met/at-risk/breached indicators
(:mod:`maverick.shoring`), an operator form to add or update a function, and
the exception desk -- the pending human sign-offs for each shored function's
department, drawn from the same approvals queue behind ``/approvals``.

Read-only ``view`` floor; the add/update form posts to the operate-gated
shoring API.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ._shared import _world
from .auth import has_global_permission, require_permission

PAGE_PATH = "/shoring"
API_BASE = "/api/v1/shoring"


def create_shoring_page_router(templates: Jinja2Templates) -> APIRouter:
    if not isinstance(templates, Jinja2Templates):
        raise TypeError("shoring page requires a Jinja template authority")
    router = APIRouter(tags=["shoring-console"])

    @router.get(PAGE_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def shoring_console(request: Request) -> HTMLResponse:
        require_permission(request, "view")
        from maverick import savings as savings_mod
        from maverick import shoring

        if not shoring.enabled():
            raise HTTPException(status_code=404, detail="autonomous shoring is disabled")

        world = _world()
        report = savings_mod.compute(world)
        functions = shoring.book_status(world)
        return templates.TemplateResponse(
            request,
            "shoring.html",
            {
                "api_base": API_BASE,
                "report": savings_mod.to_dict(report),
                "functions": functions,
                "lifecycle": shoring.LIFECYCLE,
                "metrics": [
                    {"key": key, "unit": spec["unit"], "direction": spec["direction"]}
                    for key, spec in shoring.METRICS.items()
                ],
                "can_manage": has_global_permission(request, "operate"),
                "approvals_path": "/approvals",
            },
        )

    return router


__all__ = ["API_BASE", "PAGE_PATH", "create_shoring_page_router"]
