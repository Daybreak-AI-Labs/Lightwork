"""Lazy workbench page router for cloud-agent governance."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .auth import has_permission, require_permission


def _tenant_id() -> str:
    from maverick.paths import current_tenant_id_strict

    tenant = current_tenant_id_strict()
    return "" if tenant is None else tenant


def create_ai_cloud_agent_governance_workbench_router(
    templates: Jinja2Templates,
    *,
    enabled_probe: Callable[[str], bool] | None = None,
) -> APIRouter:
    """Create a page router without constructing provider/store dependencies."""

    if not isinstance(templates, Jinja2Templates):
        raise TypeError("cloud-agent workbench requires a Jinja template authority")
    if enabled_probe is not None and not callable(enabled_probe):
        raise TypeError("cloud-agent workbench enabled probe must be callable")
    router = APIRouter(tags=["ai-cloud-agent-governance-workbench"])

    @router.get("/assurance/cloud-agents", response_class=HTMLResponse)
    async def cloud_agent_governance_workbench(request: Request) -> HTMLResponse:
        require_permission(request, "view")
        tenant = _tenant_id()
        selected = getattr(
            request.app.state,
            "ai_cloud_agent_governance_enabled_probe",
            None,
        )
        probe = selected if callable(selected) else enabled_probe
        runtime_factory = getattr(
            request.app.state,
            "ai_cloud_agent_governance_runtime_factory",
            None,
        )
        available = False
        # Why the page cannot be used, so the copy can say the true thing:
        # "workspace" (none selected), "not_connected" (nothing on this
        # installation supplies the service) or "unreachable" (it failed).
        unavailable_reason = ""
        if not callable(probe) or not callable(runtime_factory):
            unavailable_reason = "not_connected"
        elif tenant == "":
            unavailable_reason = "workspace"
        else:
            try:
                result = probe(tenant)
                if type(result) is bool:
                    available = result
                else:
                    unavailable_reason = "unreachable"
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                unavailable_reason = "unreachable"
        configuration_available = unavailable_reason == ""
        response = templates.TemplateResponse(
            request,
            "ai_cloud_agent_governance.html",
            {
                "ai_cloud_agent_governance_enabled": available,
                "ai_cloud_agent_governance_configuration_available": (configuration_available),
                "cag_unavailable_reason": unavailable_reason,
                # Only hides buttons a role cannot use; the API still enforces each.
                "cag_can_operate": has_permission(request, "operate"),
                "cag_can_admin": has_permission(request, "admin"),
                "cag_can_audit": has_permission(request, "audit"),
            },
        )
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    return router


__all__ = ["create_ai_cloud_agent_governance_workbench_router"]
