"""Executable production policy for every constructed dashboard endpoint.

FastAPI's global dependencies are necessary but not sufficient evidence that
the whole HTTP surface is governed: built-in documentation routes do not use
them, included routers can add hundreds of routes, and optional features may
mount routes only under particular configuration.  This module inventories
the *constructed* application, emits a JSON-serializable policy declaration,
and pins the reviewed route fingerprint.  Production startup refuses route
drift, an uncovered route kind, an unsupported identity mechanism, or missing
principal-isolated tenancy.  Developer/local mode remains unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from fastapi import HTTPException, Request, WebSocket
from fastapi.routing import APIRoute
from starlette.routing import Mount, Route, WebSocketRoute

POLICY_SCHEMA = "lightwork.dashboard-endpoint-policy.v3"
EXPECTED_ROUTE_COUNT = 606
EXPECTED_ROUTE_INVENTORY_SHA256 = (
    "3afe9de842b20eae4b20e9236fcdcbe8859301b5202c513041e0b120751b3c2e"
)
EXPECTED_PRODUCTION_POLICY_COUNT = 78
EXPECTED_PRODUCTION_POLICY_SHA256 = (
    "390bd3d17ce304414ed8e78bfe46fc66117503a43d594dc10b6ebf4a6641d9a5"
)


class ProductionEndpointPolicyError(RuntimeError):
    """The constructed production dashboard violates its reviewed policy."""


@dataclass(frozen=True, order=True)
class EndpointIdentity:
    kind: str
    method: str
    path: str
    name: str


@dataclass(frozen=True)
class EndpointPolicy:
    policy_class: str
    authentication: str
    authorization: str
    tenant: str
    resource: str = "none"
    ownership: str = "none"
    entitlement: str = "none"
    production_allowed: bool = True


# Every semantic entitlement identifier resolves to release-manifest surfaces
# that must all be included.  The endpoint policy therefore cannot advertise
# a governed feature that the immutable production profile omitted.
_ENTITLEMENT_SURFACES: dict[str, tuple[str, ...]] = {
    "core_approvals": (
        "package:maverick-dashboard",
        "interface:dashboard-rest",
        "feature:governed-flows",
    ),
    "core_audit": ("package:maverick-dashboard", "interface:dashboard-rest"),
    "core_dashboard": (
        "package:maverick-dashboard",
        "interface:dashboard-rest",
        "client:dashboard-browser",
    ),
    "core_flows": (
        "package:maverick-dashboard",
        "interface:dashboard-rest",
        "feature:governed-flows",
    ),
    "core_goals": (
        "package:maverick-agent",
        "package:maverick-dashboard",
        "interface:dashboard-rest",
    ),
    "core_probes": ("interface:dashboard-rest",),
    "fleet_halt": (
        "interface:dashboard-rest",
        "runtime:kubernetes-control-plane",
    ),
    "identity_bootstrap": ("interface:dashboard-rest",),
    "production_api_contract": ("interface:dashboard-rest",),
    "production_observability": ("interface:dashboard-rest",),
    "rbac_administration": ("interface:dashboard-rest",),
    "tenant_administration": ("interface:dashboard-rest",),
}


def _identity(kind: str, method: str, path: str, name: str) -> EndpointIdentity:
    return EndpointIdentity(kind, method, path, name)


# Public and authentication-bootstrap endpoints are exact declarations.  Never
# classify a prefix such as /auth/* or /health* as public: a future route must
# change the reviewed inventory and be deliberately added here.
_PUBLIC_PROBES = frozenset(
    {
        _identity("http", "GET", "/healthz", "healthz"),
        _identity("http", "GET", "/livez", "livez"),
        _identity("http", "GET", "/readyz", "readyz"),
    }
)
_PUBLIC_DOCS = frozenset(
    _identity("builtin", method, path, name)
    for path, name in (
        ("/openapi.json", "openapi"),
        ("/docs", "swagger_ui_html"),
        ("/docs/oauth2-redirect", "swagger_ui_redirect"),
        ("/redoc", "redoc_html"),
    )
    for method in ("GET", "HEAD")
)
_OIDC_BOOTSTRAP = frozenset(
    {
        _identity("http", "GET", "/auth/login", "auth_login"),
        _identity("http", "GET", "/auth/callback", "auth_callback"),
        _identity("http", "GET", "/auth/logout", "auth_logout"),
        _identity("http", "GET", "/auth/error", "auth_error"),
    }
)
_PUBLIC_ASSETS = frozenset(
    {_identity("http", "GET", "/static/daybreak-logo.jpg", "brand_logo")}
)
_METRICS_SERVICE = frozenset(
    {_identity("http", "GET", "/metrics", "metrics")}
)
_SELF_AUTH_WEBHOOKS = frozenset(
    {
        _identity("http", "POST", "/webhook/start", "webhook_start"),
        _identity("http", "POST", "/webhook/run", "webhook_run"),
        _identity("http", "POST", "/webhook/linear", "webhook_linear"),
        _identity("http", "POST", "/webhook/jira", "webhook_jira"),
        _identity("http", "POST", "/webhook/github", "webhook_github"),
        _identity("http", "POST", "/webhook/gitlab", "webhook_gitlab"),
    }
)
_SELF_AUTH_TOKENS = frozenset(
    {
        _identity("http", "GET", "/share/{token}", "shared_goal"),
        _identity("http", "POST", "/form/{token}", "form_submit"),
    }
)
_UNSUPPORTED_PRODUCTION_IDENTITY_ROUTES = frozenset(
    {
        _identity("http", "GET", "/auth/invite/{token}", "invite_confirm"),
        _identity("http", "POST", "/auth/invite/{token}", "invite_accept"),
        _identity("http", "GET", "/saml/metadata", "saml_metadata"),
        _identity("http", "GET", "/saml/login", "saml_login"),
        _identity("http", "POST", "/saml/acs", "saml_acs"),
    }
)
_WEBSOCKET_ROUTES = frozenset(
    {
        _identity(
            "websocket",
            "WEBSOCKET",
            "/ws/v1/runs/{goal_id}/events",
            "run_events_firehose",
        )
    }
)


def _walk_routes(routes: Iterable[Any], *, prefix: str = ""):
    """Yield leaf routes, including FastAPI's deferred included routers."""
    for route in routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods or ()):
                yield _identity(
                    "http", method, prefix + route.path, route.name or "",
                )
        elif isinstance(route, WebSocketRoute):
            yield _identity(
                "websocket",
                "WEBSOCKET",
                prefix + route.path,
                route.name or "",
            )
        elif isinstance(route, Mount):
            mounted_prefix = prefix + route.path
            mounted_routes = getattr(route.app, "routes", None)
            if mounted_routes is None:
                yield _identity("mount", "MOUNT", mounted_prefix, route.name or "")
            else:
                yield from _walk_routes(mounted_routes, prefix=mounted_prefix)
        elif isinstance(route, Route):
            for method in sorted(route.methods or ()):
                yield _identity(
                    "builtin", method, prefix + route.path, route.name or "",
                )
        elif hasattr(route, "original_router"):
            # FastAPI 0.116+ defers include_router expansion in
            # ``_IncludedRouter``. The original router is the complete source
            # of leaf declarations; route.path already contains include prefixes.
            yield from _walk_routes(route.original_router.routes, prefix=prefix)
        else:
            yield _identity(
                "unknown",
                "UNKNOWN",
                prefix + str(getattr(route, "path", "")),
                str(getattr(route, "name", "") or ""),
            )


def endpoint_inventory(app: Any) -> tuple[EndpointIdentity, ...]:
    """The stable, sorted method/path/name inventory of the constructed app."""
    return tuple(sorted(_walk_routes(app.routes)))


def inventory_sha256(inventory: Iterable[EndpointIdentity]) -> str:
    rows = [asdict(item) for item in sorted(inventory)]
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _principal_policy(
    policy_class: str,
    permission: str,
    resource: str,
    ownership: str,
    entitlement: str,
) -> EndpointPolicy:
    return EndpointPolicy(
        policy_class,
        "oidc_or_trusted_proxy",
        permission,
        "principal_isolated",
        resource,
        ownership,
        entitlement,
    )


def _flow_operation_policy(identity: EndpointIdentity) -> EndpointPolicy:
    path = identity.path
    mutation = identity.method not in {"GET", "HEAD", "OPTIONS"}
    if path == "/api/v1/flows/runs":
        resource = "flow_run_collection"
        ownership = "owner_filtered"
    elif "/runs/" in path:
        resource = "flow_run"
        ownership = "run_owner_check"
    elif path == "/api/v1/flows/tools":
        resource = "production_tool_catalog"
        ownership = "principal_tool_acl"
    elif path == "/api/v1/flows":
        resource = "flow_collection"
        ownership = "principal_author" if mutation else "owner_filtered"
    elif path in {
        "/api/v1/flows/draft",
        "/api/v1/flows/dry-run",
        "/api/v1/flows/chat",
        "/flows/designer",
    }:
        resource = "flow_authoring"
        ownership = "principal_author"
    else:
        resource = "flow_definition"
        ownership = "flow_owner_check"
    return _principal_policy(
        "flow_mutation" if mutation else "flow_read",
        "operate",
        resource,
        ownership,
        "core_flows",
    )


def _is_goal_operation_path(path: str) -> bool:
    return (
        path == "/chat"
        or path == "/chat/send"
        or path.startswith("/chat/goal/")
        or path == "/goal-builder"
        or path == "/goals"
        or path.startswith("/goals/")
        or path.startswith("/api/goal/")
        or path.startswith("/api/v1/goals")
    )


def _goal_operation_policy(identity: EndpointIdentity) -> EndpointPolicy:
    path = identity.path
    mutation = identity.method not in {"GET", "HEAD", "OPTIONS"}
    permission = "operate" if mutation or path == "/goal-builder" else "view"
    if path in {"/chat", "/goals", "/api/v1/goals", "/api/v1/goals/search"}:
        resource = "goal_collection"
        ownership = "owner_filtered" if not mutation else "owner_assigned"
    elif path == "/chat/send" or path == "/api/v1/goals/compose":
        resource = "goal_authoring"
        ownership = "owner_assigned"
    else:
        resource = "goal"
        ownership = "goal_owner_check"
    return _principal_policy(
        "goal_mutation" if mutation else "goal_read",
        permission,
        resource,
        ownership,
        "core_goals",
    )


def _reviewed_production_policy(
    identity: EndpointIdentity,
) -> EndpointPolicy | None:
    """Concrete guard metadata for one exact admitted operation.

    Admission is checked first against the method-aware production operation
    contract.  The family checks below therefore classify only already-reviewed
    identities; they cannot admit a future route merely because its prefix or
    HTTP method resembles an existing resource.
    """
    from .production_visibility import production_operation_allowed

    if not production_operation_allowed(identity.path, identity.method):
        return None
    if identity in _PUBLIC_PROBES:
        return EndpointPolicy(
            "public_probe", "none", "none", "none",
            "process_health", "read_only", "core_probes",
        )
    if identity in _PUBLIC_DOCS:
        return EndpointPolicy(
            "public_api_docs", "none", "none", "none",
            "production_openapi", "read_only", "production_api_contract",
        )
    if identity in _OIDC_BOOTSTRAP:
        return EndpointPolicy(
            "oidc_bootstrap", "oidc_flow", "none", "none",
            "browser_identity", "oidc_transaction", "identity_bootstrap",
        )
    if identity in _PUBLIC_ASSETS:
        return EndpointPolicy(
            "public_asset", "none", "none", "none",
            "brand_asset", "read_only", "core_dashboard",
        )
    if identity in _METRICS_SERVICE:
        return EndpointPolicy(
            "metrics_service",
            "metrics_service_bearer",
            "none",
            "global_operational",
            "metrics",
            "service_credential",
            "production_observability",
        )
    if identity in _WEBSOCKET_ROUTES:
        return _principal_policy(
            "goal_event_websocket",
            "view",
            "goal_events",
            "goal_owner_check",
            "core_goals",
        )

    path = identity.path
    method = identity.method
    mutation = method not in {"GET", "HEAD", "OPTIONS"}

    if path in {"/static/lightwork.css", "/static/lightwork-ui.js"}:
        return _principal_policy(
            "dashboard_shell_asset",
            "view",
            "dashboard_shell",
            "read_only",
            "core_dashboard",
        )
    if path == "/":
        return _principal_policy(
            "dashboard_home", "view", "dashboard_shell", "principal", "core_dashboard"
        )
    if path.startswith("/api/v1/admin/tenants") or path in {
        "/api/v1/tenants/overview",
        "/tenants",
        "/tenants/overview",
    }:
        return _principal_policy(
            "tenant_administration",
            "admin",
            "tenant_registry",
            "global_admin",
            "tenant_administration",
        )
    if path == "/users" or path.startswith("/users/"):
        return _principal_policy(
            "rbac_administration",
            "admin",
            "dashboard_principal_roles",
            "global_admin",
            "rbac_administration",
        )
    if path == "/audit" or path.startswith("/audit/") or path.startswith(
        "/api/v1/audit/"
    ):
        return _principal_policy(
            "audit_read",
            "audit",
            "audit_evidence",
            "deployment_auditor",
            "core_audit",
        )
    if path == "/approvals" or path.startswith("/api/v1/approvals"):
        return _principal_policy(
            "approval_queue_mutation" if mutation else "approval_queue_read",
            "operate",
            "approval_queue",
            "global_operator_queue",
            "core_approvals",
        )
    if path == "/api/v1/halt":
        return _principal_policy(
            "fleet_halt_mutation" if mutation else "fleet_halt_read",
            "admin" if mutation else "view",
            "fleet_halt_authority",
            "global_control_plane",
            "fleet_halt",
        )
    if path.startswith("/api/v1/flows") or path.startswith("/flows/"):
        return _flow_operation_policy(identity)
    if _is_goal_operation_path(path):
        return _goal_operation_policy(identity)
    # An operation may be added to the visibility allowlist only together with
    # concrete permission/resource/ownership/entitlement metadata here.
    return None


def policy_for_endpoint(identity: EndpointIdentity) -> EndpointPolicy | None:
    """Resolve an exact production operation; everything else is excluded."""
    reviewed = _reviewed_production_policy(identity)
    if reviewed is not None:
        return reviewed
    if identity.kind in {"builtin", "http", "websocket"}:
        return EndpointPolicy(
            "production_excluded",
            "not_admitted",
            "none",
            "none",
            "unreviewed_route",
            "none",
            "none",
            production_allowed=False,
        )
    # New Starlette routes, opaque mounts, and unknown route kinds are not
    # silently classified. Production startup reports and rejects them.
    return None


def production_endpoint_policy_registry(
    app: Any,
) -> dict[EndpointIdentity, EndpointPolicy]:
    """Exact constructed EndpointIdentity -> concrete production guard map."""
    registry: dict[EndpointIdentity, EndpointPolicy] = {}
    for identity in endpoint_inventory(app):
        policy = _reviewed_production_policy(identity)
        if policy is not None:
            registry[identity] = policy
    return registry


def policy_registry_sha256(
    registry: Mapping[EndpointIdentity, EndpointPolicy],
) -> str:
    rows = [
        {"identity": asdict(identity), "policy": asdict(policy)}
        for identity, policy in sorted(registry.items())
    ]
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def endpoint_policy_report(app: Any) -> dict[str, Any]:
    """Return the complete machine-readable declaration and coverage result."""
    inventory = endpoint_inventory(app)
    digest = inventory_sha256(inventory)
    rows: list[dict[str, Any]] = []
    uncovered: list[dict[str, str]] = []
    for identity in inventory:
        policy = policy_for_endpoint(identity)
        row: dict[str, Any] = asdict(identity)
        if policy is None:
            row["policy"] = None
            uncovered.append(asdict(identity))
        else:
            row["policy"] = asdict(policy)
        rows.append(row)
    errors: list[str] = []
    if len(inventory) != EXPECTED_ROUTE_COUNT:
        errors.append(
            f"route count drifted: expected {EXPECTED_ROUTE_COUNT}, got {len(inventory)}"
        )
    if digest != EXPECTED_ROUTE_INVENTORY_SHA256:
        errors.append(
            "route inventory digest drifted: "
            f"expected {EXPECTED_ROUTE_INVENTORY_SHA256}, got {digest}"
        )
    if uncovered:
        errors.append(f"{len(uncovered)} route(s) have no explicit endpoint policy")
    production_registry = production_endpoint_policy_registry(app)
    production_digest = policy_registry_sha256(production_registry)
    if len(production_registry) != EXPECTED_PRODUCTION_POLICY_COUNT:
        errors.append(
            "production policy count drifted: expected "
            f"{EXPECTED_PRODUCTION_POLICY_COUNT}, got {len(production_registry)}"
        )
    if production_digest != EXPECTED_PRODUCTION_POLICY_SHA256:
        errors.append(
            "production policy digest drifted: expected "
            f"{EXPECTED_PRODUCTION_POLICY_SHA256}, got {production_digest}"
        )
    return {
        "schema": POLICY_SCHEMA,
        "expected_route_count": EXPECTED_ROUTE_COUNT,
        "actual_route_count": len(inventory),
        "expected_inventory_sha256": EXPECTED_ROUTE_INVENTORY_SHA256,
        "actual_inventory_sha256": digest,
        "covered": not errors,
        "errors": errors,
        "uncovered": uncovered,
        "expected_production_admitted_count": EXPECTED_PRODUCTION_POLICY_COUNT,
        "production_admitted_count": len(production_registry),
        "expected_production_policy_sha256": EXPECTED_PRODUCTION_POLICY_SHA256,
        "production_policy_sha256": production_digest,
        "routes": rows,
    }


@lru_cache(maxsize=8)
def _load_production_manifest(
    configured_path: str, expected_digest: str,
):
    # Both values are cache-key inputs even though load_manifest reads them from
    # the environment. Production manifests are immutable and digest-pinned;
    # this avoids file IO and SHA-256 work on every authenticated request.
    from maverick.production_scope import load_manifest

    return load_manifest()


def _production_manifest():
    from maverick.production_scope import (
        EXPECTED_DIGEST_ENV,
        MANIFEST_ENV,
        production_surface_required,
    )

    if not production_surface_required():
        return None
    return _load_production_manifest(
        os.environ.get(MANIFEST_ENV, ""),
        os.environ.get(EXPECTED_DIGEST_ENV, ""),
    )


def _enforce_endpoint_entitlement(policy: EndpointPolicy, manifest) -> None:
    surfaces = _ENTITLEMENT_SURFACES.get(policy.entitlement)
    if surfaces is None or any(not manifest.includes(surface) for surface in surfaces):
        raise HTTPException(status_code=503, detail="endpoint entitlement is unavailable")


def _require_current_production_identity_posture():
    """Recompute and verify the complete identity/RBAC posture now.

    Production authentication and authorization read configuration at request
    time.  Checking only the selected-provider environment pin would therefore
    let a same-provider issuer, proxy-trust, or static-RBAC change take effect
    after startup without matching the out-of-band posture digest.  Keep this
    helper uncached: every request boundary must compare the inputs it is about
    to consume with the trusted digest.
    """
    try:
        from maverick.production_identity import validate_runtime_identity_posture

        return validate_runtime_identity_posture()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="production identity/RBAC posture is unavailable",
        ) from exc


def require_production_identity(provider: str) -> None:
    """Reject an identity outside the compatible and selected provider set."""
    try:
        manifest = _production_manifest()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="production identity policy unavailable",
        ) from exc
    if manifest is None:
        return
    allowed = frozenset(manifest.constraints.get("identity_providers") or ())
    if provider not in allowed:
        raise HTTPException(
            status_code=401,
            detail="OIDC or trusted proxy identity required in production",
        )
    # Recompute the whole posture rather than trusting only the provider pin.
    # OIDC/proxy and RBAC configuration are live inputs; same-provider drift is
    # just as authority-changing as switching from OIDC to proxy SSO.
    selected = _require_current_production_identity_posture().provider
    if provider != selected:
        raise HTTPException(
            status_code=401,
            detail="configured production identity provider required",
        )


def _scope_endpoint_identity(connection: Request | WebSocket) -> EndpointIdentity:
    route = connection.scope.get("route")
    original = getattr(route, "original_route", route)
    if connection.scope.get("type") == "websocket":
        kind = "websocket"
    elif isinstance(original, Route) and not isinstance(original, APIRoute):
        kind = "builtin"
    else:
        kind = "http"
    method = "WEBSOCKET" if kind == "websocket" else connection.method.upper()
    path = str(
        getattr(route, "path_format", "")
        or getattr(route, "path", "")
        or getattr(original, "path", "")
    )
    name = str(getattr(route, "name", "") or getattr(original, "name", "") or "")
    return _identity(kind, method, path, name)


def _enforce_principal_tenant(connection: Request | WebSocket) -> None:
    principal = getattr(connection.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="authenticated identity required")
    from maverick.paths import current_tenant_id

    expected = f"api:{getattr(principal, 'principal', '')}"
    if not expected.removeprefix("api:") or current_tenant_id() != expected:
        raise HTTPException(status_code=403, detail="request tenant context mismatch")


async def enforce_endpoint_request_policy(
    request: Request = None,  # type: ignore[assignment]
    websocket: WebSocket = None,  # type: ignore[assignment]
) -> None:
    """Executable production baseline layered before route-specific checks."""
    from maverick.production_scope import production_surface_required

    if not production_surface_required():
        return
    # Authentication validates the posture for principal-bearing requests.
    # Validate it again at the global authorization boundary so self-authenticated
    # routes are covered and drift between authentication and authorization
    # cannot retain authority for the rest of the request.
    _require_current_production_identity_posture()
    connection = request if request is not None else websocket
    identity = _scope_endpoint_identity(connection)
    policy = _reviewed_production_policy(identity)
    if policy is None:
        from .production_visibility import production_operation_allowed

        if production_operation_allowed(identity.path, identity.method):
            raise HTTPException(status_code=503, detail="endpoint policy is unavailable")
        raise HTTPException(status_code=404, detail="endpoint is unavailable in production")
    try:
        manifest = _production_manifest()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="endpoint entitlement is unavailable",
        ) from exc
    if manifest is None:  # pragma: no cover - predicate above is authoritative
        raise HTTPException(status_code=503, detail="endpoint entitlement is unavailable")
    _enforce_endpoint_entitlement(policy, manifest)

    if policy.tenant == "principal_isolated":
        _enforce_principal_tenant(connection)
        from .auth import require_permission

        if policy.authorization not in {"admin", "audit", "operate", "view"}:
            raise HTTPException(status_code=503, detail="endpoint policy is unavailable")
        require_permission(connection, policy.authorization)

    from .resource_authority import enforce_ownership_guard

    enforce_ownership_guard(connection, policy.ownership)


def _production_tenancy_errors(manifest) -> list[str]:
    errors: list[str] = []
    if manifest.constraints.get("tenancy_mode") != "principal_isolated":
        errors.append("production dashboard tenancy_mode must be principal_isolated")
    try:
        from maverick.paths import tenant_by_user_enabled

        if not tenant_by_user_enabled():
            errors.append("production dashboard requires principal-isolated tenancy")
    except Exception as exc:
        errors.append(f"production tenant policy is unavailable: {exc}")
    return errors


def _production_identity_errors(manifest) -> list[str]:
    errors: list[str] = []
    allowed = frozenset(manifest.constraints.get("identity_providers") or ())
    supported = frozenset({"oidc", "trusted_proxy_sso"})
    if not allowed or not allowed <= supported:
        errors.append(
            "production dashboard identity providers must be a non-empty subset "
            "of oidc and trusted_proxy_sso"
        )
    if os.environ.get("MAVERICK_DASHBOARD_TOKEN"):
        errors.append("static dashboard operator tokens are unsupported in production")
    try:
        from .invites import invites_enabled

        if invites_enabled():
            errors.append("local invite sessions are unsupported in production")
    except Exception as exc:
        errors.append(f"local invite policy is unavailable: {exc}")
    try:
        from .auth import _saml_policy_configured

        if _saml_policy_configured():
            errors.append("SAML browser sessions are unsupported in production")
    except Exception as exc:
        errors.append(f"SAML identity policy is unavailable: {exc}")

    try:
        from maverick.production_identity import validate_runtime_identity_posture

        posture = validate_runtime_identity_posture()
        if posture.provider not in allowed:
            errors.append(
                "selected identity provider is absent from the production manifest: "
                + posture.provider
            )
    except Exception as exc:
        errors.append(f"production identity/RBAC posture is unavailable: {exc}")
    try:
        from .service_credentials import validate_metrics_service_configuration

        validate_metrics_service_configuration()
    except Exception as exc:
        errors.append(f"metrics service credential is unavailable: {exc}")
    return errors


def _global_dependency_errors(app: Any) -> list[str]:
    errors: list[str] = []
    dependency_names = {
        getattr(getattr(dep, "dependency", None), "__name__", "")
        for dep in getattr(app.router, "dependencies", ())
    }
    for required in (
        "require_principal_in_request_context",
        "enforce_endpoint_request_policy",
    ):
        if required not in dependency_names:
            errors.append(f"dashboard global dependency is missing: {required}")
    return errors


def _production_registry_errors(app: Any) -> list[str]:
    from .production_visibility import production_operation_allowed

    inventory = endpoint_inventory(app)
    expected = {
        identity
        for identity in inventory
        if production_operation_allowed(identity.path, identity.method)
    }
    registry = production_endpoint_policy_registry(app)
    actual = set(registry)
    errors: list[str] = []
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        errors.append(
            "reviewed production operations lack concrete endpoint policy: "
            + ", ".join(
                f"{identity.method} {identity.path} ({identity.name})"
                for identity in missing
            )
        )
    if unexpected:
        errors.append(
            "endpoint policy admits operations outside the production contract: "
            + ", ".join(
                f"{identity.method} {identity.path} ({identity.name})"
                for identity in unexpected
            )
        )
    from .resource_authority import SUPPORTED_OWNERSHIP_GUARDS

    for identity, policy in registry.items():
        fields = (
            policy.authentication,
            policy.authorization,
            policy.tenant,
            policy.resource,
            policy.ownership,
            policy.entitlement,
        )
        if not all(str(value).strip() for value in fields):
            errors.append(
                f"endpoint policy metadata is incomplete: {identity.method} "
                f"{identity.path} ({identity.name})"
            )
        if "route_handler" in fields or not policy.production_allowed:
            errors.append(
                f"endpoint policy metadata is not concrete: {identity.method} "
                f"{identity.path} ({identity.name})"
            )
        if policy.entitlement not in _ENTITLEMENT_SURFACES:
            errors.append(
                f"endpoint entitlement has no manifest resolver: {identity.method} "
                f"{identity.path} ({policy.entitlement})"
            )
        if policy.ownership not in SUPPORTED_OWNERSHIP_GUARDS:
            errors.append(
                f"endpoint ownership has no executable guard: {identity.method} "
                f"{identity.path} ({policy.ownership})"
            )
    app.state.production_endpoint_policy_registry = {
        "count": len(registry),
        "sha256": policy_registry_sha256(registry),
        "routes": [
            {"identity": asdict(identity), "policy": asdict(policy)}
            for identity, policy in sorted(registry.items())
        ],
    }
    return errors


def _production_entitlement_errors(app: Any, manifest) -> list[str]:
    errors: list[str] = []
    registry = production_endpoint_policy_registry(app)
    for entitlement in sorted({policy.entitlement for policy in registry.values()}):
        surfaces = _ENTITLEMENT_SURFACES.get(entitlement)
        if surfaces is None:
            continue
        missing = [surface for surface in surfaces if not manifest.includes(surface)]
        if missing:
            errors.append(
                f"endpoint entitlement {entitlement} is absent from the production "
                f"manifest: {', '.join(missing)}"
            )
    return errors


def validate_production_endpoint_policy(app: Any) -> dict[str, Any]:
    """Fail production startup unless routes, identity, and tenancy are governed."""
    report = endpoint_policy_report(app)
    app.state.endpoint_policy_report = report
    from maverick.production_scope import production_surface_required

    if not production_surface_required():
        return report
    errors = list(report["errors"])
    try:
        manifest = _production_manifest()
    except Exception as exc:
        raise ProductionEndpointPolicyError(
            f"production endpoint manifest is unavailable: {exc}"
        ) from exc
    if manifest is None:  # pragma: no cover - guarded by predicate above
        raise ProductionEndpointPolicyError("production endpoint manifest is unavailable")
    if not manifest.includes("interface:dashboard-rest"):
        errors.append("production manifest does not include interface:dashboard-rest")
    errors.extend(_production_registry_errors(app))
    errors.extend(_production_entitlement_errors(app, manifest))
    errors.extend(_production_tenancy_errors(manifest))
    errors.extend(_production_identity_errors(manifest))
    errors.extend(_global_dependency_errors(app))

    if errors:
        raise ProductionEndpointPolicyError(
            "production dashboard endpoint policy failed:\n- " + "\n- ".join(errors)
        )
    return report


__all__ = [
    "EXPECTED_ROUTE_COUNT",
    "EXPECTED_ROUTE_INVENTORY_SHA256",
    "EXPECTED_PRODUCTION_POLICY_COUNT",
    "EXPECTED_PRODUCTION_POLICY_SHA256",
    "EndpointIdentity",
    "EndpointPolicy",
    "POLICY_SCHEMA",
    "ProductionEndpointPolicyError",
    "endpoint_inventory",
    "endpoint_policy_report",
    "enforce_endpoint_request_policy",
    "inventory_sha256",
    "policy_registry_sha256",
    "policy_for_endpoint",
    "production_endpoint_policy_registry",
    "require_production_identity",
    "validate_production_endpoint_policy",
]
