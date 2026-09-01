"""Workspace tenant picker: choose the tenant every workbench runs as.

Dozens of tenant-scoped workbenches refuse to run unbound (their APIs 409
with "select an active tenant ..."). Historically the only bindings were the
``MAVERICK_TENANT`` environment variable or an enterprise client binding --
both config-file/environment steps. This page is the in-app path: it lists
the registry's tenants, lets an admin pick (or create) one, and the
``bind_active_tenant`` middleware scopes every later request to it.

Rendering performs registry reads only -- selection and creation go through
the admin-gated APIs, so a viewer sees the state but cannot change it.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

PAGE_PATH = "/workspace/tenant"


def _page_context(request: Request, detail: str = "") -> dict:
    import os

    from maverick.tenant.registry import list_tenants

    from .auth import has_global_permission
    from maverick_dashboard import settings_store

    try:
        tenants = [
            {
                "id": rec.id,
                "name": getattr(rec, "display_name", "") or rec.id,
                "plan": getattr(rec, "plan", ""),
                "active": bool(rec.active),
            }
            for rec in list_tenants()
        ]
    except Exception:
        # A corrupt roster must not turn the picker into a 500: the page is
        # how an operator digs out of exactly this kind of state.
        tenants = []
    return {
        "detail": detail,
        "tenants": tenants,
        "selected": settings_store.active_tenant(),
        "env_tenant": os.environ.get("MAVERICK_TENANT", "").strip(),
        "is_admin": has_global_permission(request, "admin"),
    }


def tenant_select_response(
    request: Request,
    templates: Jinja2Templates,
    *,
    detail: str = "",
) -> HTMLResponse:
    """The picker rendered in place of a tenant-gate 409 (keeps the URL, so
    selecting a tenant and reloading lands on the workbench that refused)."""
    return templates.TemplateResponse(
        request,
        "tenant_select.html",
        _page_context(request, detail),
        status_code=409,
    )


def create_tenant_select_page_router(templates: Jinja2Templates) -> APIRouter:
    if not isinstance(templates, Jinja2Templates):
        raise TypeError("tenant picker requires Jinja templates")
    router = APIRouter(tags=["workspace-tenant"])

    @router.get(PAGE_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def workspace_tenant(request: Request) -> HTMLResponse:
        from .auth import require_permission

        require_permission(request, "view")
        return templates.TemplateResponse(
            request,
            "tenant_select.html",
            _page_context(request),
        )

    return router
