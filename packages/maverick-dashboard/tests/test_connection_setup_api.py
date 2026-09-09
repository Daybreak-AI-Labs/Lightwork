"""HTTP setup uses server-owned callback/state and never returns provider secrets."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from maverick import config, connection_oauth, connections
from maverick.paths import tenant_scope
from maverick_dashboard import connection_setup_api as api
from maverick_dashboard import rbac


@pytest.fixture
def client(tmp_path, monkeypatch):
    import os
    for key in tuple(os.environ):
        if key.startswith(("MAVERICK_", "LIGHTWORK_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_SECURE_DEFAULT", "0")
    rbac.set_role("user:owner", "admin")
    rbac.set_role("user:other", "operator")
    monkeypatch.setattr(connection_oauth, "_post_token", lambda *a: pytest.fail("Unexpected provider request"))
    monkeypatch.setattr(connection_oauth, "_provider_identity", lambda provider, token: {
        "provider": provider, "subject": "fixture-account", "display_name": "Fixture Account",
        "email": "fixture@example.test", "email_verified": True, "hosted_domain": "example.test",
    })
    application = FastAPI()
    @application.middleware("http")
    async def identity(request: Request, call_next):
        name = request.headers.get("X-Test-User", "owner")
        request.state.principal = SimpleNamespace(principal="user:" + name, sub=name)
        with tenant_scope(tenant=request.headers.get("X-Test-Tenant", "tenant-a")):
            return await call_next(request)
    application.include_router(api.router)
    with TestClient(application, base_url="https://lightwork.example", headers={"Origin": "https://lightwork.example"}) as value:
        yield value
    config.reset_config_cache()


def state(client):
    response = client.get("/api/v1/connection-setup")
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    return response.json()


def enable(client):
    before = state(client)
    response = client.post("/api/v1/connection-setup/enable", json={"confirm": True, "context": before["context"]})
    assert response.status_code == 200, response.text
    after = state(client)
    assert after["enabled"] and after["context"] != before["context"]
    return after


def app(client):
    current = enable(client)
    response = client.put("/api/v1/connection-setup/application", json={"confirm": True, "context": current["context"], "provider": "google", "client_id": "synthetic-client", "client_secret": "synthetic-client-secret"})
    assert response.status_code == 200, response.text
    assert "synthetic-client-secret" not in response.text
    return response.json()


def test_actual_http_consent_return_and_named_store(monkeypatch, client):
    current = app(client)
    body = {"confirm": True, "context": current["context"], "profile": "google-drive", "name": "customer-drive"}
    authorized = client.post("/api/v1/connection-setup/authorize", json=body)
    assert authorized.status_code == 200, authorized.text
    assert "verifier" not in authorized.text
    assert "redirect_uri=https%3A%2F%2Flightwork.example%2Fconnections" in authorized.json()["authorize_url"]
    monkeypatch.setattr(connection_oauth, "_post_token", lambda *a: {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "expires_in": 3600})
    response = client.post("/api/v1/connection-setup/exchange", json={"confirm": True, "context": current["context"], "state": authorized.json()["state"], "code": "synthetic-code"})
    assert response.status_code == 200, response.text
    assert response.json()["connection"]["name"] == "customer-drive"
    assert response.json()["live_provider_verified"] is False
    assert all(secret not in response.text for secret in ("synthetic-access", "synthetic-refresh", "synthetic-client-secret"))
    with tenant_scope(tenant="tenant-a"):
        assert connections.get_connection("customer-drive")["token"] == "synthetic-access"


def test_cross_site_context_role_and_unknown_payload_are_denied_before_exchange(client):
    current = app(client)
    payload = {"confirm": True, "context": current["context"], "profile": "google-drive", "name": "customer-drive"}
    assert client.post("/api/v1/connection-setup/authorize", json=payload, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/v1/connection-setup/authorize", json=payload, headers={"X-Test-User": "other"}).status_code == 403
    assert client.post("/api/v1/connection-setup/authorize", json=payload, headers={"X-Test-Tenant": "tenant-b"}).status_code == 409
    assert client.post("/api/v1/connection-setup/authorize", json={**payload, "redirect_uri": "https://attacker.example/connections"}).status_code == 422
    assert client.get("/api/v1/connection-setup", headers={"X-Test-Tenant": "tenant-b", "X-Lightwork-Connection-Context": current["context"]}).status_code == 409


def test_provider_failure_is_redacted_and_cannot_replay_consent(client, monkeypatch):
    current = app(client)
    authorized = client.post("/api/v1/connection-setup/authorize", json={"confirm": True, "context": current["context"], "profile": "google-drive", "name": "customer-drive"}).json()
    calls = []
    def fail(*args):
        calls.append(args)
        raise RuntimeError("synthetic-secret-provider-response")
    monkeypatch.setattr(connection_oauth, "_post_token", fail)
    payload = {"confirm": True, "context": current["context"], "state": authorized["state"], "code": "synthetic-code"}
    response = client.post("/api/v1/connection-setup/exchange", json=payload)
    assert response.status_code == 503 and "synthetic-secret-provider-response" not in response.text
    assert client.post("/api/v1/connection-setup/exchange", json=payload).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize("name", ["MAVERICK_CONNECTIONS", "LIGHTWORK_CONNECTIONS", "MAVERICK_OAUTH_VAULT", "LIGHTWORK_OAUTH_VAULT"])
def test_enable_respects_current_deployment_denial(client, monkeypatch, name):
    monkeypatch.setenv(name, "0")
    current = state(client)
    response = client.post("/api/v1/connection-setup/enable", json={"confirm": True, "context": current["context"]})
    assert response.status_code == 409
    assert not config.dashboard_overrides_path().exists()
