"""Adverse-path proof for the production dashboard/API trust boundary."""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request


def _request(
    path: str = "/metrics",
    *,
    method: str = "GET",
    authorization: str = "",
    query: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: bytes | None = None,
) -> Request:
    raw_headers = list(headers or [])
    if authorization:
        raw_headers.append((b"authorization", authorization.encode()))
    scope = {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "headers": raw_headers,
            "query_string": query,
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    if body is None:
        return Request(scope)
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return Request(scope, receive=receive)


@pytest.fixture(autouse=True)
def _clean_service_env(monkeypatch):
    for name in (
        "MAVERICK_METRICS_TOKEN",
        "MAVERICK_METRICS_TOKEN_PREVIOUS",
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_API_MAX_CONCURRENCY",
        "MAVERICK_API_RATE_LIMIT_PER_MINUTE",
    ):
        monkeypatch.delenv(name, raising=False)
    from maverick_dashboard import api_boundary

    api_boundary.reset_api_admission_state_for_tests()


def test_metrics_service_credential_supports_bounded_rotation(monkeypatch):
    from maverick_dashboard.service_credentials import (
        require_metrics_service_credential,
    )

    current = "current-metrics-service-token-0001"
    previous = "previous-metrics-service-token-001"
    monkeypatch.setenv("MAVERICK_METRICS_TOKEN", current)
    monkeypatch.setenv("MAVERICK_METRICS_TOKEN_PREVIOUS", previous)

    for token in (current, previous):
        request = _request(authorization=f"Bearer {token}")
        require_metrics_service_credential(request)
        assert request.state.metrics_service_authenticated is True


def test_metrics_service_credential_rejects_missing_wrong_and_query_tokens(monkeypatch):
    from maverick_dashboard.service_credentials import (
        require_metrics_service_credential,
    )

    current = "current-metrics-service-token-0001"
    monkeypatch.setenv("MAVERICK_METRICS_TOKEN", current)
    requests = (
        _request(),
        _request(authorization="Bearer wrong-metrics-service-token-0001"),
        _request(query=f"token={current}".encode()),
    )
    for request in requests:
        with pytest.raises(HTTPException) as denied:
            require_metrics_service_credential(request)
        assert denied.value.status_code == 401

    monkeypatch.delenv("MAVERICK_METRICS_TOKEN")
    with pytest.raises(HTTPException) as missing:
        require_metrics_service_credential(_request())
    assert missing.value.status_code == 503

    from maverick_dashboard import auth_metrics

    assert auth_metrics.auth_failure_counts()["metrics_bad_token"] >= 3
    assert auth_metrics.auth_failure_counts()["metrics_credential_unavailable"] >= 1


def test_metrics_service_credential_is_exact_path_scoped(monkeypatch):
    from maverick_dashboard.service_credentials import (
        require_metrics_service_credential,
    )

    monkeypatch.setenv("MAVERICK_METRICS_TOKEN", "m" * 32)
    with pytest.raises(HTTPException) as denied:
        require_metrics_service_credential(
            _request("/api/v1/facts", authorization=f"Bearer {'m' * 32}")
        )
    assert denied.value.status_code == 403


def test_production_metrics_configuration_rejects_weak_reused_or_previous_only(
    monkeypatch,
):
    from maverick import production_scope
    from maverick_dashboard.service_credentials import (
        ServiceCredentialConfigurationError,
        validate_metrics_service_configuration,
    )

    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    for setup in (
        {"MAVERICK_METRICS_TOKEN": "short"},
        {
            "MAVERICK_METRICS_TOKEN": "x" * 32,
            "MAVERICK_DASHBOARD_TOKEN": "x" * 32,
        },
        {
            "MAVERICK_METRICS_TOKEN": "x" * 32,
            "MAVERICK_METRICS_TOKEN_PREVIOUS": "y" * 32,
            "MAVERICK_DASHBOARD_TOKEN": "y" * 32,
        },
        {"MAVERICK_METRICS_TOKEN": ("x" * 31) + "\n"},
        {"MAVERICK_METRICS_TOKEN_PREVIOUS": "y" * 32},
    ):
        monkeypatch.delenv("MAVERICK_METRICS_TOKEN", raising=False)
        monkeypatch.delenv("MAVERICK_METRICS_TOKEN_PREVIOUS", raising=False)
        monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
        for name, value in setup.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(ServiceCredentialConfigurationError):
            validate_metrics_service_configuration()


def test_global_principal_boundary_authenticates_metrics_service(monkeypatch):
    from maverick import production_scope
    from maverick_dashboard import auth

    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    monkeypatch.setenv("MAVERICK_METRICS_TOKEN", "m" * 32)
    request = _request(authorization=f"Bearer {'m' * 32}")

    assert auth.require_principal(request) is None
    assert request.state.metrics_service_authenticated is True


def test_scheme_mismatch_is_not_same_origin_and_trusted_tls_shape_matches():
    from maverick_dashboard.api_boundary import same_origin

    plain = _request("/api/v1/facts", method="POST")
    assert same_origin(plain, "http://testserver") is True
    assert same_origin(plain, "https://testserver") is False

    forwarded = _request(
        "/api/v1/facts",
        method="POST",
        headers=[(b"x-forwarded-proto", b"https")],
    )
    assert same_origin(forwarded, "https://testserver") is True
    assert same_origin(forwarded, "http://testserver") is False


def test_api_query_boundary_rejects_credentials_and_unbounded_pagination(monkeypatch):
    from maverick_dashboard import api_boundary

    monkeypatch.setattr(api_boundary, "production_api_boundary_required", lambda: True)

    async def accepted(_request):
        pytest.fail("rejected query must not reach the application")

    for query in (b"token=secret", b"limit=1001", b"offset=1000001"):
        response = asyncio.run(
            api_boundary.enforce_production_api_boundary(
                _request("/api/v1/facts", query=query), accepted
            )
        )
        assert response.status_code == 400


def test_api_body_boundary_rejects_declared_oversize_before_reading(monkeypatch):
    from maverick_dashboard import api_boundary

    monkeypatch.setattr(api_boundary, "production_api_boundary_required", lambda: True)
    request = _request(
        "/api/v1/facts",
        method="POST",
        headers=[(b"content-length", str(2 * 1024 * 1024).encode())],
    )

    async def accepted(_request):
        pytest.fail("oversized body must not reach the application")

    response = asyncio.run(
        api_boundary.enforce_production_api_boundary(request, accepted)
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    ("path", "content_type", "declared"),
    [
        ("/chat/send", "multipart/form-data; boundary=x", 3 * 1024 * 1024),
        (
            "/api/v1/security/assurance/gateway/deliver",
            "application/json",
            2 * 1024 * 1024,
        ),
        (
            "/api/v1/goals/7/attachments",
            "multipart/form-data; boundary=x",
            3 * 1024 * 1024,
        ),
    ],
)
def test_excluded_large_uploads_get_no_pre_routing_body_exception(
    monkeypatch,
    path,
    content_type,
    declared,
):
    from maverick_dashboard import api_boundary

    monkeypatch.setattr(api_boundary, "production_api_boundary_required", lambda: True)
    request = _request(
        path,
        method="POST",
        headers=[
            (b"content-length", str(declared).encode()),
            (b"content-type", content_type.encode()),
        ],
    )

    async def accepted(_request):
        pytest.fail("oversized excluded request must not reach route admission")

    response = asyncio.run(
        api_boundary.enforce_production_api_boundary(request, accepted)
    )
    assert response.status_code == 413


def test_api_body_boundary_replays_the_bounded_body(monkeypatch):
    from maverick_dashboard import api_boundary
    from starlette.responses import JSONResponse

    monkeypatch.setattr(api_boundary, "production_api_boundary_required", lambda: True)
    payload = b'{"key":"value"}'
    request = _request(
        "/api/v1/facts",
        method="POST",
        headers=[
            (b"content-length", str(len(payload)).encode()),
            (b"content-type", b"application/json"),
        ],
        body=payload,
    )

    async def accepted(bounded_request):
        assert await bounded_request.body() == payload
        return JSONResponse({"ok": True})

    response = asyncio.run(
        api_boundary.enforce_production_api_boundary(request, accepted)
    )
    assert response.status_code == 200


def test_api_rate_and_concurrency_admission_are_fail_fast(monkeypatch):
    from maverick_dashboard import api_boundary

    monkeypatch.setenv("MAVERICK_API_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("MAVERICK_API_MAX_CONCURRENCY", "2")
    first, _ = api_boundary._admit_api_request(100.0)
    second, reason = api_boundary._admit_api_request(100.1)
    assert first is True
    assert second is False and reason == "rate"
    api_boundary._release_api_request()

    api_boundary.reset_api_admission_state_for_tests()
    monkeypatch.setenv("MAVERICK_API_RATE_LIMIT_PER_MINUTE", "100")
    monkeypatch.setenv("MAVERICK_API_MAX_CONCURRENCY", "1")
    first, _ = api_boundary._admit_api_request(200.0)
    second, reason = api_boundary._admit_api_request(200.1)
    assert first is True
    assert second is False and reason == "concurrency"
    api_boundary._release_api_request()


def test_production_errors_remove_internal_details(monkeypatch):
    from maverick_dashboard import api_boundary

    monkeypatch.setattr(api_boundary, "production_api_boundary_required", lambda: True)
    assert api_boundary.public_error_detail(
        500, "sqlite /private/tenant/world.db password=secret"
    ) == "internal server error"
    errors = api_boundary.public_validation_errors([
        {
            "loc": ["body", "secret"],
            "type": "string_too_long",
            "input": "plaintext-secret",
            "ctx": {"error": "private parser state"},
        }
    ])
    assert errors == [{"loc": ["body", "secret"], "type": "string_too_long"}]
    assert "plaintext" not in str(errors)


def test_consequential_operations_require_strong_idempotency_in_production(monkeypatch):
    from maverick_dashboard import api_boundary

    monkeypatch.setattr(api_boundary, "production_api_boundary_required", lambda: True)
    with pytest.raises(HTTPException) as missing:
        api_boundary.require_production_idempotency_key(None)
    assert missing.value.status_code == 428
    with pytest.raises(HTTPException) as weak:
        api_boundary.require_production_idempotency_key("short")
    assert weak.value.status_code == 400
    key = "request-2026-08-03-000001"
    assert api_boundary.require_production_idempotency_key(key) == key

    for ambiguous in (
        " request-2026-08-03-000001",
        "request-2026-08-03-000001 ",
        "request-2026-08-03\t000001",
    ):
        with pytest.raises(HTTPException) as whitespace:
            api_boundary.require_production_idempotency_key(ambiguous)
        assert whitespace.value.status_code == 400


def test_missing_suite_grant_is_empty_in_production(monkeypatch):
    from maverick import production_scope
    from maverick_dashboard import auth, suite_grants

    monkeypatch.setattr(auth, "_authorization_principal", lambda _request: "user:alice")
    monkeypatch.setattr(auth, "is_dashboard_admin", lambda _principal: False)
    monkeypatch.setattr(suite_grants, "granted_suites", lambda _principal: None)
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)

    assert auth.caller_suites(_request("/api/v1/departments")) == frozenset()


def test_unknown_production_tenant_gets_no_paid_department_entitlement(monkeypatch):
    from maverick import production_scope
    from maverick.tenant import registry as tenant_registry
    from maverick_dashboard import api

    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    monkeypatch.setattr(
        api, "_department_request_tenant", lambda: "api:user:unprovisioned"
    )
    monkeypatch.setattr(tenant_registry, "get_tenant", lambda _tenant: None)

    assert api._department_entitled_for_request("finance") is False


def test_production_oidc_replay_guard_denies_shared_store_failure(monkeypatch):
    import maverick.world_model_backends as backends
    from maverick import production_scope
    from maverick_dashboard import _shared, oidc_login

    monkeypatch.setattr(backends, "is_postgres_configured", lambda: True)
    monkeypatch.setattr(
        _shared,
        "_world",
        lambda: (_ for _ in ()).throw(RuntimeError("postgres unavailable")),
    )
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)

    assert oidc_login._shared_consume_tx("transaction-id") is False


def test_production_oidc_replay_guard_requires_shared_store(monkeypatch):
    import maverick.world_model_backends as backends
    from maverick import production_scope
    from maverick_dashboard import oidc_login

    monkeypatch.setattr(backends, "is_postgres_configured", lambda: False)
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)

    assert oidc_login._shared_consume_tx("transaction-id") is False


def test_versioned_openapi_artifact_matches_and_detects_drift(monkeypatch):
    from maverick import production_scope
    from maverick_dashboard.app import app
    from maverick_dashboard.openapi_contract import (
        ProductionOpenApiContractError,
        validate_production_openapi_contract,
    )

    # Capture the generated application schema before production feature gates
    # are patched on; the contract builder applies its own fixed, versioned
    # production filter to this raw schema.
    generated_schema = copy.deepcopy(app.openapi())
    production_app = SimpleNamespace(
        openapi=lambda: copy.deepcopy(generated_schema),
        state=SimpleNamespace(),
    )
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    summary = validate_production_openapi_contract(production_app)
    assert summary["contract_version"] == "5.0.0"
    assert summary["operation_count"] == 69
    assert summary["lifecycle_status"] == "unpublished-release-candidate"
    assert summary["migration_guide"] == (
        "docs/api.md#api-compatibility-deprecation-and-migration"
    )
    assert summary["deprecation_policy"] == {
        "clock_starts": "first-supported-release-containing-the-deprecation-notice",
        "minimum_calendar_days": 90,
        "minimum_supported_minor_releases": 2,
        "removal_requires": [
            "reviewed-contract-version-bump",
            "migration-guide",
            "release-notes",
            "claim-evidence-ledger-refresh",
        ],
    }

    drifted_schema = copy.deepcopy(generated_schema)
    drifted_schema["info"]["version"] = "breaking-unreviewed"
    drifted = SimpleNamespace(
        openapi=lambda: drifted_schema,
        state=SimpleNamespace(),
    )
    with pytest.raises(ProductionOpenApiContractError, match="drifted"):
        validate_production_openapi_contract(drifted)


def test_endpoint_policy_marks_metrics_service_and_excludes_signed_link_routes():
    from maverick_dashboard.app import app
    from maverick_dashboard.endpoint_policy import endpoint_policy_report

    report = endpoint_policy_report(app)
    metrics = [row for row in report["routes"] if row["path"] == "/metrics"]
    assert len(metrics) == 1
    assert metrics[0]["policy"]["policy_class"] == "metrics_service"
    approval = [row for row in report["routes"] if "/flow/approve" in row["path"]]
    assert {row["path"] for row in approval} == {"/flow/approve/{token}"}
    assert {row["method"] for row in approval} == {"GET", "POST"}
    assert all(
        row["policy"]["policy_class"] == "production_excluded"
        and row["policy"]["production_allowed"] is False
        for row in approval
    )
