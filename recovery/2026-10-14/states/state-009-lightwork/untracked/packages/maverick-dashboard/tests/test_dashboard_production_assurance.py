"""Dashboard production assurance is gated, redacted, and readiness-authoritative."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException, Request
from maverick import production_observability as assurance
from maverick_dashboard import health_routes


def _request(*, authorization: str = "") -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": headers,
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    )


def _report(*, healthy: bool) -> assurance.AssuranceReport:
    signal = assurance.AssuranceSignal(
        assurance.KMS_KEY_CUSTODY,
        healthy,
        1 if healthy else 0,
        1,
        "verified" if healthy else "verification unavailable or failed",
    )
    return assurance.AssuranceReport(True, healthy, (signal,))


def test_production_metrics_requires_service_credential(monkeypatch):
    monkeypatch.setattr(assurance, "production_assurance_required", lambda: True)
    monkeypatch.setenv("MAVERICK_METRICS_TOKEN", "m" * 32)

    with pytest.raises(HTTPException) as denied:
        asyncio.run(
            health_routes.metrics_response(
                _request(),
                world_provider=lambda: pytest.fail("world must not be queried"),
                owner_filter=lambda _request: None,
            )
        )
    assert denied.value.status_code == 401


def test_production_metrics_omit_principal_series(monkeypatch):
    from maverick import quotas
    class _World:
        def goal_status_counts(self):
            return {}

        def total_spend(self):
            return {"dollars": 0, "input_tokens": 0, "output_tokens": 0, "runs": 0}

    monkeypatch.setattr(assurance, "production_assurance_required", lambda: True)
    monkeypatch.setattr(assurance, "collect_assurance", lambda _world: _report(healthy=True))
    monkeypatch.setenv("MAVERICK_METRICS_TOKEN", "m" * 32)
    monkeypatch.setattr(
        quotas.UsageLedger,
        "spend_by_principal",
        lambda _self: pytest.fail("production must not read per-principal spend"),
    )

    response = asyncio.run(
        health_routes.metrics_response(
            _request(authorization=f"Bearer {'m' * 32}"),
            world_provider=_World,
            owner_filter=lambda _request: None,
        )
    )
    text = response.body.decode()

    assert "maverick_user_spend_dollars_today" not in text
    assert (
        'maverick_production_assurance_state{signal="kms_key_custody"} 1'
        in text
    )


def test_failed_assurance_refuses_readiness_but_not_health(monkeypatch):
    from maverick import agent_trust, shield_policy
    from maverick import client as client_module

    class _World:
        def ping(self):
            return True

    monkeypatch.setattr(assurance, "production_assurance_required", lambda: True)
    monkeypatch.setattr(
        assurance,
        "collect_assurance",
        lambda _world: _report(healthy=False),
    )
    monkeypatch.setattr(client_module, "client_binding_enforced", lambda: False)
    monkeypatch.setattr(shield_policy, "shield_required", lambda: False)
    monkeypatch.setattr(agent_trust, "load_trust_state", lambda: (False, {}))
    monkeypatch.setattr(health_routes, "health_should_redact", lambda: False)

    health = asyncio.run(
        health_routes.health_response(
            world_provider=_World,
            provider_key_set=lambda: True,
        )
    )
    ready = asyncio.run(
        health_routes.readiness_response(
            world_provider=_World,
            provider_key_set=lambda: True,
        )
    )

    assert health.status_code == 200
    assert ready.status_code == 503
    body = json.loads(ready.body)
    assert body["checks"]["production_kms_key_custody"].startswith("fail")


def test_assurance_collector_exception_fails_readiness_without_leaking(monkeypatch):
    from maverick import agent_trust, shield_policy
    from maverick import client as client_module

    monkeypatch.setattr(assurance, "production_assurance_required", lambda: True)
    monkeypatch.setattr(
        assurance,
        "collect_assurance",
        lambda _world: (_ for _ in ()).throw(RuntimeError("postgres-secret")),
    )
    monkeypatch.setattr(client_module, "client_binding_enforced", lambda: False)
    monkeypatch.setattr(shield_policy, "shield_required", lambda: False)
    monkeypatch.setattr(agent_trust, "load_trust_state", lambda: (False, {}))

    ok, checks = health_routes.readiness_deep_checks(lambda: object())

    assert ok is False
    assert checks["production_assurance"] == "fail: verification unavailable"
    assert "postgres-secret" not in str(checks)


def test_production_probe_details_are_always_redacted(monkeypatch):
    monkeypatch.setattr(assurance, "production_assurance_required", lambda: True)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)

    assert health_routes.health_should_redact() is True
