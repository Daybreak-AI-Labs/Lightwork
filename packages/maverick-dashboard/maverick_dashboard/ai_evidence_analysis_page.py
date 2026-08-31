"""Authenticated, store-free operator workbench for AI evidence analysis."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .auth import require_permission

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Content-Security-Policy": (
        "default-src 'none'; base-uri 'none'; connect-src 'self'; "
        "font-src 'none'; form-action 'none'; frame-ancestors 'none'; "
        "img-src 'none'; manifest-src 'none'; media-src 'none'; "
        "object-src 'none'; script-src 'self'; style-src 'self'; "
        "worker-src 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "serial=(), bluetooth=(), clipboard-read=(), clipboard-write=()"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}


def _tenant_selected() -> None:
    from maverick.paths import current_tenant_id_strict

    if current_tenant_id_strict() is None:
        raise HTTPException(
            status_code=409,
            detail="select an active tenant before using AI evidence analysis",
        )


def create_ai_evidence_analysis_page_router() -> APIRouter:
    """Create the standalone page router; rendering performs no store access."""

    router = APIRouter(tags=["ai-evidence-analysis-workbench"])

    @router.get(
        "/ai-evidence-analysis",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def workbench(request: Request) -> HTMLResponse:
        require_permission(request, "view")
        _tenant_selected()
        if request.query_params:
            raise HTTPException(status_code=404, detail="workbench unavailable")
        response = _TEMPLATES.TemplateResponse(
            request,
            "ai_evidence_analysis.html",
            {"api_base": "/api/v1/ai-evidence-analysis"},
        )
        for name, value in _HEADERS.items():
            response.headers[name] = value
        return response

    return router


router = create_ai_evidence_analysis_page_router()

__all__ = ["create_ai_evidence_analysis_page_router", "router"]
