"""Authenticated operator surface for governed computer-use sessions."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .auth import has_global_permission, has_permission, require_permission

PAGE_PATH = "/computer-use"
API_BASE = "/api/v1/computer-use"
ASSET_PATH = "/static/lightwork-computer-use.js"

_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Content-Security-Policy": (
        "default-src 'none'; base-uri 'none'; connect-src 'self'; "
        "font-src 'self'; form-action 'none'; frame-ancestors 'none'; "
        "img-src 'self' data:; manifest-src 'none'; media-src 'none'; "
        "object-src 'none'; script-src 'self'; style-src 'self'; worker-src 'none'"
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


def create_computer_use_page_router(templates: Jinja2Templates) -> APIRouter:
    """Create the page router against the app's shared template authority."""

    if not isinstance(templates, Jinja2Templates):
        raise TypeError("computer-use page requires a Jinja template authority")
    router = APIRouter(tags=["computer-use-console"])

    @router.get(PAGE_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def console(request: Request) -> HTMLResponse:
        require_permission(request, "view")
        if any(key not in {"theme", "density", "font", "lang"} for key in request.query_params):
            raise HTTPException(status_code=404, detail="computer-use console unavailable")
        nonce = secrets.token_urlsafe(18)
        response = templates.TemplateResponse(
            request,
            "computer_use.html",
            {
                "api_base": API_BASE,
                "page_path": PAGE_PATH,
                "csp_style_nonce": nonce,
                "computer_can_operate": has_permission(request, "operate"),
                "computer_can_audit": has_permission(request, "audit"),
                "computer_can_admin": has_global_permission(request, "admin"),
            },
        )
        for name, value in _HEADERS.items():
            if name == "Content-Security-Policy":
                value = value.replace(
                    "style-src 'self'",
                    f"style-src 'self' 'nonce-{nonce}'",
                )
            response.headers[name] = value
        return response

    return router


__all__ = ["API_BASE", "ASSET_PATH", "PAGE_PATH", "create_computer_use_page_router"]
