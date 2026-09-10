"""Autonomous Compliance product landing page (Govern -> Autonomous Compliance).

The six compliance suites already ship as separate dashboard pages scattered
across several nav groups. This page presents them as ONE product -- the
compliance platform run by governed agents -- each module carrying an honest
shipped / preview / external status badge, a link to the real module page, and
a packaging/pricing call. Status is read from ``maverick.compliance_catalog``,
the single source of truth, so the product surface can never claim more than
the capability review supports.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .auth import require_permission

PAGE_PATH = "/autonomous-compliance"
#: Packaging/pricing contact. A mailto keeps the call honest -- there is no
#: fabricated self-serve checkout behind the "one product" story.
PRICING_MAILTO = (
    "mailto:chris@daybreakailabs.com"
    "?subject=Autonomous%20Compliance%20pricing"
)


def visible_suites(suites, not_ready) -> list:
    """The catalog with every module held back from customers removed, and any
    suite left empty dropped: the landing must never link a visitor into a
    workbench the sidebar and finder deliberately do not offer."""
    from dataclasses import replace

    out = []
    for suite in suites:
        modules = tuple(m for m in suite.modules if m.path not in not_ready)
        if modules:
            out.append(suite if len(modules) == len(suite.modules) else replace(suite, modules=modules))
    return out


def create_autonomous_compliance_page_router(
    templates: Jinja2Templates,
) -> APIRouter:
    """Create the store-free Autonomous Compliance product landing router."""

    if not isinstance(templates, Jinja2Templates):
        raise TypeError(
            "Autonomous Compliance page requires a Jinja template authority"
        )
    router = APIRouter(tags=["autonomous-compliance"])

    @router.get(PAGE_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def landing(request: Request) -> HTMLResponse:
        require_permission(request, "view")
        if any(
            key not in {"theme", "density", "font", "lang"}
            for key in request.query_params
        ):
            raise HTTPException(
                status_code=404, detail="Autonomous Compliance unavailable"
            )
        from maverick import compliance_catalog as catalog

        from . import ui_visibility

        catalog_suites = visible_suites(catalog.suites(), ui_visibility.not_ready_paths())
        response = templates.TemplateResponse(
            request,
            "autonomous_compliance.html",
            {
                "suites": catalog_suites,
                "module_count": sum(len(s.modules) for s in catalog_suites),
                "status_order": catalog.STATUS_ORDER,
                "status_labels": catalog.STATUS_LABELS,
                "status_meanings": catalog.STATUS_MEANINGS,
                "status_badge_class": catalog.STATUS_BADGE_CLASS,
                "pricing_mailto": PRICING_MAILTO,
            },
        )
        response.headers["Cache-Control"] = "no-store, private, max-age=0"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    return router


__all__ = ["PAGE_PATH", "PRICING_MAILTO", "create_autonomous_compliance_page_router"]
