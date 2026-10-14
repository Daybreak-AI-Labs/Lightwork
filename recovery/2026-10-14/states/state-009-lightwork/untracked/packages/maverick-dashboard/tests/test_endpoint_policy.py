"""Production endpoint inventory, identity, role, and tenant admission."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from maverick.oidc import VerifiedPrincipal
from maverick_dashboard import auth, invites, rbac
from maverick_dashboard.app import app
from maverick_dashboard.endpoint_policy import (
    EXPECTED_PRODUCTION_POLICY_COUNT,
    EXPECTED_PRODUCTION_POLICY_SHA256,
    EXPECTED_ROUTE_COUNT,
    EXPECTED_ROUTE_INVENTORY_SHA256,
    EndpointIdentity,
    ProductionEndpointPolicyError,
    endpoint_policy_report,
    policy_for_endpoint,
    production_endpoint_policy_registry,
    validate_production_endpoint_policy,
)
from starlette.requests import Request

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_MANIFEST = REPO_ROOT / "release" / "production-surface-manifest.json"
client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import config, world_model
    from maverick.paths import current_tenant_id
    from maverick_dashboard import api

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    for key in (
        "MAVERICK_REQUIRE_PRODUCTION_SURFACE",
        "MAVERICK_PRODUCTION_MANIFEST",
        "MAVERICK_PRODUCTION_MANIFEST_SHA256",
        "MAVERICK_TENANT_BY_USER",
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_INVITES",
        "MAVERICK_DASHBOARD_ADMINS",
        "MAVERICK_PRODUCTION_IDENTITY_PROVIDER",
        "MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256",
    ):
        monkeypatch.delenv(key, raising=False)
    config.reset_config_cache()
    world_model._tenant_worlds.clear()
    api._world_cache.clear()
    yield
    assert current_tenant_id() is None
    config.reset_config_cache()
    world_model._tenant_worlds.clear()
    api._world_cache.clear()


def _production_oidc(monkeypatch) -> None:
    import maverick.oidc as oidc
    import maverick.production_identity as production_identity
    import maverick.proxy_auth as proxy_auth

    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(PRODUCTION_MANIFEST))
    monkeypatch.setenv("MAVERICK_TENANT_BY_USER", "1")
    monkeypatch.setenv("MAVERICK_METRICS_TOKEN", "metrics-service-token-32-bytes-minimum")
    monkeypatch.setenv("MAVERICK_PRODUCTION_IDENTITY_PROVIDER", "oidc")
    monkeypatch.setenv("MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256", "d" * 64)
    monkeypatch.setattr(oidc, "oidc_enabled", lambda: True)
    monkeypatch.setattr(proxy_auth, "proxy_auth_enabled", lambda: False)
    monkeypatch.setattr(auth, "_saml_policy_configured", lambda: False)
    monkeypatch.setattr(invites, "invites_enabled", lambda: False)
    monkeypatch.setattr(
        production_identity,
        "validate_runtime_identity_posture",
        lambda: SimpleNamespace(provider="oidc"),
    )


def _production_proxy(monkeypatch) -> None:
    import maverick.oidc as oidc
    import maverick.production_identity as production_identity
    import maverick.proxy_auth as proxy_auth

    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(PRODUCTION_MANIFEST))
    monkeypatch.setenv("MAVERICK_TENANT_BY_USER", "1")
    monkeypatch.setenv("MAVERICK_METRICS_TOKEN", "metrics-service-token-32-bytes-minimum")
    monkeypatch.setenv(
        "MAVERICK_PRODUCTION_IDENTITY_PROVIDER",
        "trusted_proxy_sso",
    )
    monkeypatch.setenv("MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256", "d" * 64)
    monkeypatch.setattr(oidc, "oidc_enabled", lambda: False)
    monkeypatch.setattr(proxy_auth, "proxy_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "oidc_enabled", lambda: False)
    monkeypatch.setattr(auth, "proxy_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "proxy_trusts", lambda _host: True)
    monkeypatch.setattr(auth, "proxy_header_name", lambda: "X-Forwarded-User")
    monkeypatch.setattr(auth, "_saml_policy_configured", lambda: False)
    monkeypatch.setattr(invites, "invites_enabled", lambda: False)
    monkeypatch.setattr(
        production_identity,
        "validate_runtime_identity_posture",
        lambda: SimpleNamespace(provider="trusted_proxy_sso"),
    )


def _request(*, authorization: str = "") -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/facts",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_constructed_app_has_complete_machine_readable_policy():
    report = endpoint_policy_report(app)

    assert report["schema"] == "lightwork.dashboard-endpoint-policy.v3"
    assert report["covered"] is True
    assert report["actual_route_count"] == EXPECTED_ROUTE_COUNT == 606
    assert report["actual_inventory_sha256"] == EXPECTED_ROUTE_INVENTORY_SHA256
    assert report["production_admitted_count"] == EXPECTED_PRODUCTION_POLICY_COUNT
    assert report["production_policy_sha256"] == EXPECTED_PRODUCTION_POLICY_SHA256
    assert report["uncovered"] == []
    assert len(report["routes"]) == 606
    json.dumps(report)  # declaration is directly consumable by release tooling

    public_probes = {
        row["path"] for row in report["routes"]
        if row["policy"]["policy_class"] == "public_probe"
    }
    assert public_probes == {"/healthz", "/livez", "/readyz"}
    callbacks = {
        row["path"] for row in report["routes"]
        if row["policy"]["policy_class"] == "oidc_bootstrap"
    }
    assert callbacks == {"/auth/login", "/auth/callback", "/auth/logout", "/auth/error"}


def test_public_routes_are_exact_and_static_routes_are_inventoried():
    future_auth_route = EndpointIdentity(
        "http", "GET", "/auth/callback/extra", "future_callback",
    )
    future_policy = policy_for_endpoint(future_auth_route)
    assert future_policy.policy_class == "production_excluded"
    assert future_policy.production_allowed is False

    report = endpoint_policy_report(app)
    static = [row for row in report["routes"] if row["path"].startswith("/static/")]
    assert len(static) == 6
    assert {
        row["path"] for row in static
        if row["policy"]["policy_class"] == "public_asset"
    } == {"/static/daybreak-logo.jpg"}
    shell_assets = {
        row["path"] for row in static
        if row["policy"]["policy_class"] == "dashboard_shell_asset"
    }
    assert shell_assets == {"/static/lightwork.css", "/static/lightwork-ui.js"}
    assert all(
        row["policy"]["policy_class"] == "production_excluded"
        for row in static
        if row["path"] not in shell_assets | {"/static/daybreak-logo.jpg"}
    )
    assert not [row for row in report["routes"] if row["kind"] in {"mount", "unknown"}]


def test_opaque_mount_and_route_drift_are_default_denied(monkeypatch):
    from fastapi import FastAPI

    class OpaqueAsgi:
        async def __call__(self, scope, receive, send):  # pragma: no cover
            return None

    drifted = FastAPI()
    drifted.mount("/opaque", OpaqueAsgi(), name="opaque")
    report = endpoint_policy_report(drifted)
    assert report["covered"] is False
    assert any(row["path"] == "/opaque" for row in report["uncovered"])

    _production_oidc(monkeypatch)
    with pytest.raises(ProductionEndpointPolicyError, match="route count drifted"):
        validate_production_endpoint_policy(drifted)


def test_reviewed_production_app_passes_startup_admission(monkeypatch):
    _production_oidc(monkeypatch)
    report = validate_production_endpoint_policy(app)
    assert report["covered"] is True
    assert app.state.endpoint_policy_report["actual_route_count"] == 606
    assert app.state.production_endpoint_policy_registry["count"] == (
        EXPECTED_PRODUCTION_POLICY_COUNT
    )


def test_exact_production_registry_has_concrete_guard_metadata():
    from maverick_dashboard.resource_authority import SUPPORTED_OWNERSHIP_GUARDS

    registry = production_endpoint_policy_registry(app)

    assert len(registry) == EXPECTED_PRODUCTION_POLICY_COUNT
    assert all(policy.production_allowed for policy in registry.values())
    assert all(
        policy.resource != "none"
        and policy.ownership != "none"
        and policy.entitlement != "none"
        for policy in registry.values()
    )
    assert not any(
        "route_handler" in policy.authorization for policy in registry.values()
    )
    assert not any(
        identity.path == "/api/v1/facts" for identity in registry
    )

    goal_answer = next(
        policy for identity, policy in registry.items()
        if identity.path == "/api/v1/goals/{goal_id}/answer"
        and identity.method == "POST"
    )
    assert goal_answer.authorization == "operate"
    assert goal_answer.resource == "goal"
    assert goal_answer.ownership == "goal_owner_check"
    goal_signoff = next(
        policy for identity, policy in registry.items()
        if identity.path == "/api/v1/goals/{goal_id}/signoff"
        and identity.method == "POST"
    )
    assert goal_signoff.policy_class == "goal_mutation"
    assert goal_signoff.authorization == "operate"
    assert goal_signoff.resource == "goal"
    assert goal_signoff.ownership == "goal_owner_check"
    assert not any(
        identity.path == "/flow/approve/{token}"
        for identity in registry
    )
    assert not any(
        identity.path == "/api/v1/goals/{goal_id}/cancel"
        and identity.method == "POST"
        for identity in registry
    )

    tenant_list = next(
        policy for identity, policy in registry.items()
        if identity.path == "/api/v1/admin/tenants"
        and identity.method == "GET"
    )
    assert tenant_list.authorization == "admin"
    assert tenant_list.resource == "tenant_registry"
    assert not any(
        identity.path == "/api/v1/admin/tenants" and identity.method == "POST"
        for identity in registry
    )
    assert {policy.ownership for policy in registry.values()} <= (
        SUPPORTED_OWNERSHIP_GUARDS
    )


def test_resource_authority_denies_cross_owner_goal_flow_and_run(monkeypatch):
    from maverick_dashboard import resource_authority

    principal = VerifiedPrincipal(
        sub="alice",
        issuer="https://issuer.example",
        audience="dashboard",
        claims={},
    )

    def connection(**path_params):
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/resource",
                "path_params": path_params,
                "headers": [],
                "state": {"principal": principal},
                "client": ("127.0.0.1", 1234),
                "scheme": "http",
                "server": ("testserver", 80),
            }
        )

    world = SimpleNamespace(
        get_goal=lambda _goal_id: SimpleNamespace(owner="user:bob"),
    )
    monkeypatch.setattr("maverick_dashboard._shared._world", lambda: world)
    with pytest.raises(HTTPException) as exc:
        resource_authority.enforce_ownership_guard(
            connection(goal_id="17"), "goal_owner_check",
        )
    assert exc.value.status_code == 404

    from maverick.flow import store

    monkeypatch.setattr(
        store, "load_flow", lambda _flow_id: SimpleNamespace(owner="user:bob"),
    )
    with pytest.raises(HTTPException) as exc:
        resource_authority.enforce_ownership_guard(
            connection(flow_id="other-flow"), "flow_owner_check",
        )
    assert exc.value.status_code == 404

    monkeypatch.setattr(
        store, "load_run", lambda _run_id: SimpleNamespace(owner="user:bob"),
    )
    with pytest.raises(HTTPException) as exc:
        resource_authority.enforce_ownership_guard(
            connection(run_id="other-run"), "run_owner_check",
        )
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        resource_authority.enforce_ownership_guard(connection(), "future_guard")
    assert exc.value.status_code == 503


def test_endpoint_entitlements_fail_closed_when_manifest_surface_is_absent():
    from maverick_dashboard.endpoint_policy import _production_entitlement_errors

    class MissingManifest:
        @staticmethod
        def includes(_surface: str) -> bool:
            return False

    errors = _production_entitlement_errors(app, MissingManifest())
    assert errors
    assert any("core_goals" in error for error in errors)
    assert any("feature:governed-flows" in error for error in errors)


def test_production_startup_rejects_static_operator_token(monkeypatch):
    _production_oidc(monkeypatch)
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", "shared-operator")
    with pytest.raises(ProductionEndpointPolicyError, match="static dashboard"):
        validate_production_endpoint_policy(app)


def test_lifespan_blocks_before_control_plane_start_on_policy_failure(monkeypatch):
    import maverick.control_plane_lease as lease

    _production_oidc(monkeypatch)
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", "shared-operator")
    monkeypatch.setattr(
        lease,
        "acquire",
        lambda **_kwargs: pytest.fail("writer lease acquired before endpoint policy"),
    )

    with pytest.raises(ProductionEndpointPolicyError, match="static dashboard"):
        with TestClient(app):
            pass


def test_production_startup_rejects_local_invites_and_saml(monkeypatch):
    _production_oidc(monkeypatch)
    monkeypatch.setattr(invites, "invites_enabled", lambda: True)
    monkeypatch.setattr(auth, "_saml_policy_configured", lambda: True)
    with pytest.raises(ProductionEndpointPolicyError) as exc:
        validate_production_endpoint_policy(app)
    assert "local invite" in str(exc.value)
    assert "SAML" in str(exc.value)


def test_production_startup_requires_tenancy_and_approved_identity(monkeypatch):
    import maverick.production_identity as production_identity

    _production_oidc(monkeypatch)
    monkeypatch.setenv("MAVERICK_TENANT_BY_USER", "0")
    monkeypatch.setattr(
        production_identity,
        "validate_runtime_identity_posture",
        lambda: (_ for _ in ()).throw(
            production_identity.ProductionIdentityError(
                "production requires exactly one configured provider"
            )
        ),
    )
    with pytest.raises(ProductionEndpointPolicyError) as exc:
        validate_production_endpoint_policy(app)
    assert "principal-isolated tenancy" in str(exc.value)
    assert "exactly one configured provider" in str(exc.value)


def test_production_rejects_static_bearer_even_when_proxy_is_valid(monkeypatch):
    _production_proxy(monkeypatch)
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", "shared-operator")
    request = _request(authorization="Bearer shared-operator")
    with pytest.raises(HTTPException) as exc:
        auth.require_principal(request)
    assert exc.value.status_code == 401


def test_production_rejects_local_and_legacy_browser_sessions(monkeypatch):
    _production_oidc(monkeypatch)
    invite = VerifiedPrincipal(
        sub="invitee@example.com",
        issuer="invite-session",
        audience="",
        claims={"via": "invite"},
    )
    monkeypatch.setattr(auth, "proxy_auth_enabled", lambda: False)
    monkeypatch.setattr(auth, "oidc_enabled", lambda: False)
    monkeypatch.setattr(auth, "_session_principal", lambda _request: None)
    monkeypatch.setattr(auth, "_invite_session_principal", lambda _request: invite)
    with pytest.raises(HTTPException) as exc:
        auth.require_principal(_request())
    assert exc.value.status_code == 401

    legacy = VerifiedPrincipal(
        sub="legacy-user",
        issuer="legacy_browser_session-session",
        audience="",
        claims={"via": "session", "auth_provider": "legacy_browser_session"},
    )
    with pytest.raises(HTTPException) as exc:
        auth._require_production_identity(auth._browser_session_provider(legacy))
    assert exc.value.status_code == 401


def test_production_request_revalidates_posture_between_authn_and_authz(
    monkeypatch,
):
    """Same-provider config drift cannot inherit an already-selected provider."""
    import maverick.production_identity as production_identity

    _production_proxy(monkeypatch)
    calls = 0

    def _current_posture():
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(provider="trusted_proxy_sso")
        raise production_identity.ProductionIdentityError(
            "trusted-proxy/RBAC configuration drifted after authentication"
        )

    monkeypatch.setattr(
        production_identity,
        "validate_runtime_identity_posture",
        _current_posture,
    )

    response = client.get(
        "/api/v1/goals",
        headers={"X-Forwarded-User": "alice"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "service unavailable"
    assert calls == 2


def test_production_self_authenticated_route_rejects_posture_drift(monkeypatch):
    """Public/bootstrap route classes still cross the global posture boundary."""
    import maverick.production_identity as production_identity

    _production_oidc(monkeypatch)
    monkeypatch.setattr(
        production_identity,
        "validate_runtime_identity_posture",
        lambda: (_ for _ in ()).throw(
            production_identity.ProductionIdentityError(
                "OIDC/RBAC configuration drifted after startup"
            )
        ),
    )

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["detail"] == "service unavailable"


def test_production_role_classes_deny_viewer_mutation_and_operator_admin(monkeypatch):
    _production_proxy(monkeypatch)
    headers = {"X-Forwarded-User": "viewer"}
    rbac.set_role("user:viewer", "viewer")

    assert client.get("/api/v1/goals", headers=headers).status_code == 200
    denied = client.post(
        "/api/v1/goals",
        headers=headers,
        json={"title": "viewer must not create this"},
    )
    assert denied.status_code == 403

    rbac.set_role("user:operator", "operator")
    admin_denied = client.get(
        "/api/v1/admin/tenants",
        headers={"X-Forwarded-User": "operator"},
    )
    assert admin_denied.status_code == 403


def test_production_policy_rejects_cross_tenant_context(monkeypatch):
    from maverick.paths import set_tenant

    _production_proxy(monkeypatch)
    rbac.set_role("user:alice", "viewer")

    def wrong_tenant(request, principal):
        request.state.tenant_pin_token = set_tenant("api:user:bob")

    monkeypatch.setattr(auth, "_pin_tenant_from_principal", wrong_tenant)
    response = client.get(
        "/api/v1/goals", headers={"X-Forwarded-User": "alice"},
    )
    assert response.status_code == 403
    # Production callers receive a stable public error, not policy internals.
    assert response.json()["detail"] == "forbidden"


def test_production_websocket_uses_principal_owner_policy(monkeypatch):
    from maverick.world_model import world_for_tenant

    _production_proxy(monkeypatch)
    rbac.set_role("user:alice", "viewer")
    world = world_for_tenant("api:user:alice")
    goal_id = world.create_goal(
        "Policy websocket", "owner scoped", owner="user:alice",
    )
    world.append_event(goal_id, "system", "finding", "VISIBLE_TO_ALICE")
    world.set_goal_status(goal_id, "done", result="ok")

    with client.websocket_connect(
        f"/ws/v1/runs/{goal_id}/events",
        headers={
            "Origin": "http://testserver",
            "X-Forwarded-User": "alice",
        },
    ) as websocket:
        assert websocket.receive_json()["content"] == "VISIBLE_TO_ALICE"
        assert websocket.receive_json()["content"] == "done"


def test_public_and_self_authenticated_policy_classes_do_not_gain_authority(
    monkeypatch,
):
    _production_proxy(monkeypatch)
    assert client.get("/livez").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/auth/invite/not-a-token").status_code == 404

    import maverick.webhooks as webhooks

    monkeypatch.setattr(webhooks, "inbound_secret", lambda: "configured-secret")
    unsigned = client.post("/webhook/start", json={"title": "unsigned"})
    assert unsigned.status_code == 404


def test_oidc_and_saml_cookies_carry_distinct_provenance(monkeypatch):
    from maverick.web_session import verify_session
    from maverick_dashboard.saml import mint_session_cookie

    raw = mint_session_cookie("saml-user", secret="a" * 32)
    payload = verify_session(raw, "a" * 32)
    assert payload["auth_provider"] == "saml"

    oidc = VerifiedPrincipal(
        sub="oidc-user",
        issuer="oidc-session",
        audience="",
        claims={"via": "session", "auth_provider": "oidc"},
    )
    assert auth._browser_session_provider(oidc) == "oidc"
    assert auth._browser_session_provider(SimpleNamespace(claims={})) == (
        "legacy_browser_session"
    )
