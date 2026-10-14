"""Default-deny admission for the first production dashboard profile.

Runtime gates remain the authority for direct Python callers.  This module
closes the HTTP discovery and invocation layer: only route shapes reviewed for
the first production profile are admitted.  Every other page, API, websocket,
and future unclassified route returns 404 and is removed from generated
OpenAPI.  Development retains the complete source-tree surface.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request, WebSocket
from maverick.production_scope import production_surface_required

_PATH_PREFIX_FEATURES: tuple[tuple[str, str], ...] = (
    ("/learning/dgm", "code_dgm"),
    ("/learning/flow-autonomy", "flow_evolution"),
    ("/learning", "self_learning"),
    ("/learned", "self_learning"),
    ("/plugins", "plugins"),
    ("/mcp", "mcp"),
    ("/channels", "channels"),
    ("/connections", "connectors"),
    ("/marketplace/connectors", "connectors"),
    ("/event-triggers", "connectors"),
    ("/import", "connectors"),
    ("/a2a", "a2a"),
)

_FLOW_EVOLUTION_PATHS = (
    re.compile(r"^/flows/insights/?$"),
    re.compile(r"^/flows/[^/]+/proposals/?$"),
    re.compile(r"^/flows/[^/]+/apply/?$"),
    re.compile(r"^/flows/[^/]+/nodes/[^/]+/impact/?$"),
)

_ATTACHMENT_PATHS = (
    re.compile(r"^/goals/[^/]+/attachments/?$"),
    re.compile(r"^/goals/[^/]+/attachments/from-source/?$"),
    re.compile(r"^/goals/[^/]+/attachments/[^/]+/download/?$"),
)

# These endpoints are useful in local development but have no durable task or
# reconnect authority: live caption sources are registered in process memory,
# and the GitHub issue-to-PR handler acknowledges an untracked BackgroundTask.
# Keep them out of the reviewed production contract until they gain a durable
# task id, cancellation state, and restart recovery.
_PROCESS_LOCAL_PATHS = frozenset({"/voice/captions", "/webhook/github"})

def _methods(*values: str) -> frozenset[str]:
    return frozenset(value.upper() for value in values)


# This is deliberately an allowlist of complete route templates *and methods*,
# not denied families.  A broad prefix such as ``/api/v1/goals`` would silently
# admit every future goal-adjacent endpoint.  Path-only checks are presentation
# helpers; request authority and OpenAPI both consult this exact operation map.
_REVIEWED_PRODUCTION_OPERATIONS: dict[str, frozenset[str]] = {
    # Probes, production observability, and API documentation.
    "/healthz": _methods("GET"),
    "/livez": _methods("GET"),
    "/readyz": _methods("GET"),
    "/metrics": _methods("GET"),
    "/docs": _methods("GET", "HEAD"),
    "/docs/oauth2-redirect": _methods("GET", "HEAD"),
    "/openapi.json": _methods("GET", "HEAD"),
    "/redoc": _methods("GET", "HEAD"),
    # OIDC browser bootstrap. Local invites, SAML, and SCIM are absent.
    "/auth/callback": _methods("GET"),
    "/auth/error": _methods("GET"),
    "/auth/login": _methods("GET"),
    "/auth/logout": _methods("GET"),
    # Assets loaded by the retained shell. Voice and embed/analytics assets are
    # intentionally absent from the first profile.
    "/static/daybreak-logo.jpg": _methods("GET"),
    "/static/lightwork.css": _methods("GET"),
    "/static/lightwork-ui.js": _methods("GET"),
    # Retained dashboard pages and canonical entrypoints.
    "/": _methods("GET"),
    "/approvals": _methods("GET"),
    "/audit": _methods("GET"),
    "/audit/binder": _methods("GET"),
    "/chat": _methods("GET"),
    "/chat/send": _methods("POST"),
    "/chat/goal/{goal_id}": _methods("GET"),
    "/flows/designer": _methods("GET"),
    "/flows/designer/{flow_id}": _methods("GET"),
    "/flows/{flow_id}/runs/{run_id}": _methods("GET"),
    "/goal-builder": _methods("GET"),
    "/goals": _methods("GET"),
    "/goals/{goal_id}/errors": _methods("GET"),
    "/goals/{goal_id}/plan": _methods("GET"),
    "/goals/{goal_id}/trajectory": _methods("GET"),
    "/tenants": _methods("GET"),
    "/tenants/overview": _methods("GET"),
    "/users": _methods("GET"),
    # Goal detail polling used by the retained chat page.
    "/api/goal/{goal_id}": _methods("GET"),
    "/api/goal/{goal_id}/events": _methods("GET"),
    "/api/goal/{goal_id}/events/stream": _methods("GET"),
    # Core goal lifecycle. Attachments, share links, learning views, and
    # walkthrough adjuncts are intentionally absent.
    "/api/v1/goals": _methods("GET", "POST"),
    "/api/v1/goals/compose": _methods("POST"),
    "/api/v1/goals/search": _methods("GET"),
    "/api/v1/goals/{goal_id}": _methods("GET"),
    "/api/v1/goals/{goal_id}/answer": _methods("POST"),
    "/api/v1/goals/{goal_id}/artifacts": _methods("GET"),
    "/api/v1/goals/{goal_id}/artifacts/history": _methods("GET"),
    "/api/v1/goals/{goal_id}/children": _methods("POST"),
    "/api/v1/goals/{goal_id}/cost-breakdown": _methods("GET"),
    "/api/v1/goals/{goal_id}/cost-preview": _methods("GET"),
    "/api/v1/goals/{goal_id}/deliverable.csv": _methods("GET"),
    "/api/v1/goals/{goal_id}/events": _methods("GET"),
    "/api/v1/goals/{goal_id}/events/stream": _methods("GET"),
    "/api/v1/goals/{goal_id}/open_questions": _methods("GET"),
    "/api/v1/goals/{goal_id}/signoff": _methods("GET", "POST"),
    # Governed flow definitions, execution, and durable run recovery. Flow
    # evolution proposals/signals, analytics, gallery, and triggers are absent.
    "/api/v1/flows": _methods("GET"),
    "/api/v1/flows/runs": _methods("GET"),
    "/api/v1/flows/runs/{run_id}": _methods("GET"),
    "/api/v1/flows/runs/{run_id}/events": _methods("GET"),
    "/api/v1/flows/tools": _methods("GET"),
    "/api/v1/flows/{flow_id}": _methods("GET"),
    "/api/v1/flows/{flow_id}/schema": _methods("GET"),
    "/api/v1/flows/{flow_id}/versions": _methods("GET"),
    # Authenticated approval-queue operations. Capability-bearing signed flow
    # links stay entirely outside the production browser/API contract: putting
    # their bearer token in a URL would expose it to browser and proxy history.
    "/api/v1/approvals": _methods("GET"),
    "/api/v1/approvals/{approval_id}/approve": _methods("POST"),
    "/api/v1/approvals/{approval_id}/deny": _methods("POST"),
    "/api/v1/approvals/{approval_id}/state": _methods("GET"),
    # Immutable audit review and the shared halt control plane.
    "/api/v1/audit/binder": _methods("GET"),
    "/api/v1/audit/grep": _methods("GET"),
    "/api/v1/audit/tail": _methods("GET"),
    "/api/v1/halt": _methods("GET"),
    # Tenant and RBAC inventory is read-only in the first production profile.
    # Authority-expanding provisioning, resume, quota/plan, role, and delete
    # mutations are unsupported or direct-gated in this profile. The remaining
    # safety-reducing tenant suspension and capability revocation paths are
    # audited operator-CLI controls, not HTTP operations.
    "/api/v1/admin/tenants": _methods("GET"),
    "/api/v1/admin/tenants/{tenant_id}": _methods("GET"),
    "/api/v1/admin/tenants/{tenant_id}/roles": _methods("GET"),
    "/api/v1/tenants/overview": _methods("GET"),
    # Principal-owned run events websocket.
    "/ws/v1/runs/{goal_id}/events": _methods("WEBSOCKET"),
}


def _template_path_pattern(path: str) -> re.Pattern[str]:
    escaped = re.escape(path)
    dynamic = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", escaped)
    return re.compile(f"^{dynamic}$")


_REVIEWED_PRODUCTION_DYNAMIC_PATHS = tuple(
    _template_path_pattern(path)
    for path in _REVIEWED_PRODUCTION_OPERATIONS
    if "{" in path
)


def _normalized_path(path: str) -> str:
    value = str(path or "").split("?", 1)[0] or "/"
    if not value.startswith("/"):
        value = f"/{value}"
    return value.rstrip("/") or "/"


def _relative_path(path: str) -> str:
    value = _normalized_path(path)
    if value.startswith("/api/v1"):
        value = value[len("/api/v1"):] or "/"
    return value


def _reviewed_production_path(path: str) -> bool:
    normalized = _normalized_path(path)
    if normalized in _REVIEWED_PRODUCTION_OPERATIONS:
        return True
    return any(
        pattern.fullmatch(normalized)
        for pattern in _REVIEWED_PRODUCTION_DYNAMIC_PATHS
    )


def feature_for_request_path(path: str) -> str | None:
    """Return the excluded-feature label governing a dashboard path."""
    relative = _relative_path(path)
    for pattern in _ATTACHMENT_PATHS:
        if pattern.fullmatch(relative):
            return "attachments"
    for pattern in _FLOW_EVOLUTION_PATHS:
        if pattern.fullmatch(relative):
            return "flow_evolution"
    for prefix, feature in _PATH_PREFIX_FEATURES:
        if relative == prefix or relative.startswith(prefix + "/"):
            return feature
    return None


def production_page_allowed(path: str) -> bool:
    """Path-only presentation check for navigation and conditional assets.

    This helper deliberately grants no request authority because a concrete
    resource path cannot always distinguish ``/{id}`` from a new static route.
    HTTP/websocket admission uses the resolved route template plus method via
    :func:`production_operation_allowed`.
    """
    if not production_surface_required():
        return True
    return _reviewed_production_path(path)


def production_operation_allowed(path: str, method: str) -> bool:
    """Whether one exact route-template and method pair was reviewed."""
    allowed = _REVIEWED_PRODUCTION_OPERATIONS.get(_normalized_path(path))
    return allowed is not None and str(method or "").upper() in allowed


def production_operation_available(path: str, method: str) -> bool:
    """Presentation helper: all source routes in dev, reviewed methods in prod."""
    if not production_surface_required():
        return True
    return production_operation_allowed(path, method)


def production_contract_path_allowed(path: str) -> bool:
    """Path set pinned by the first versioned production API contract.

    A future profile may deliberately admit another feature, but that expands
    the public API and therefore requires a reviewed contract-version change.
    """
    return _normalized_path(path) in _REVIEWED_PRODUCTION_OPERATIONS


async def enforce_production_feature_request_policy(
    request: Request = None,  # type: ignore[assignment]
    websocket: WebSocket = None,  # type: ignore[assignment]
) -> None:
    """Global FastAPI dependency that makes excluded surfaces undiscoverable."""
    connection = request if request is not None else websocket
    if not production_surface_required():
        return
    route = connection.scope.get("route")
    route_path = getattr(route, "path", None) or connection.url.path
    method = request.method if request is not None else "WEBSOCKET"
    if not production_operation_allowed(route_path, method):
        raise HTTPException(status_code=404, detail="Not Found")


def _schema_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        ref = value.get("$ref")
        prefix = "#/components/schemas/"
        if isinstance(ref, str) and ref.startswith(prefix):
            refs.add(ref[len(prefix):])
        for child in value.values():
            refs.update(_schema_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_schema_refs(child))
    return refs


def _prune_unreferenced_schemas(schema: dict[str, Any]) -> None:
    components = schema.get("components")
    if not isinstance(components, dict):
        return
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        return

    roots = _schema_refs(schema.get("paths", {}))
    roots.update(
        _schema_refs({key: value for key, value in components.items() if key != "schemas"})
    )
    pending = list(roots)
    retained: set[str] = set()
    while pending:
        name = pending.pop()
        if name in retained or name not in schemas:
            continue
        retained.add(name)
        pending.extend(_schema_refs(schemas[name]) - retained)
    components["schemas"] = {
        name: value for name, value in schemas.items() if name in retained
    }


_OPENAPI_OPERATION_METHODS = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)


def _prune_unreviewed_openapi_operations(schema: dict[str, Any]) -> None:
    """Remove unreviewed methods, then paths with no admitted operation."""
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        schema["paths"] = {}
        return
    retained_paths: dict[str, Any] = {}
    for path, value in paths.items():
        if not isinstance(value, Mapping):
            continue
        retained_item: dict[str, Any] = {}
        operation_retained = False
        for key, operation in value.items():
            method = str(key).lower()
            if method in _OPENAPI_OPERATION_METHODS:
                if production_operation_allowed(path, method):
                    retained_item[key] = operation
                    operation_retained = True
            else:
                # Preserve path-level parameters/summary only when at least one
                # reviewed operation survives on the path.
                retained_item[key] = operation
        if operation_retained:
            retained_paths[path] = retained_item
    schema["paths"] = retained_paths


def install_production_openapi_filter(app: Any) -> None:
    """Install an idempotent production-only OpenAPI surface filter."""
    if getattr(app, "_maverick_production_openapi_filter", False):
        return
    original = app.openapi

    def production_openapi() -> dict[str, Any]:
        raw = original()
        if not production_surface_required():
            return raw
        schema = copy.deepcopy(raw)
        _prune_unreviewed_openapi_operations(schema)
        _prune_unreferenced_schemas(schema)
        return schema

    app.openapi = production_openapi
    app._maverick_production_openapi_filter = True


__all__ = [
    "enforce_production_feature_request_policy",
    "feature_for_request_path",
    "install_production_openapi_filter",
    "production_operation_allowed",
    "production_operation_available",
    "production_page_allowed",
    "production_contract_path_allowed",
]
