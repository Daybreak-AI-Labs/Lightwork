"""In-app setup changes real configuration and workspace-private bindings."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from maverick import config, connections
from maverick import vendor_onboarding_setup as setup
from maverick.paths import tenant_scope
from maverick_dashboard import rbac
from maverick_dashboard import vendor_setup_api as api


@pytest.fixture
def client(tmp_path, monkeypatch):
    import os
    for key in tuple(os.environ):
        if key.startswith(("MAVERICK_", "LIGHTWORK_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_SECURE_DEFAULT", "0")
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    rbac.set_role("user:owner", "admin")
    rbac.set_role("user:other", "operator")
    application = FastAPI()
    @application.middleware("http")
    async def identity(request: Request, call_next):
        name = request.headers.get("X-Test-User", "owner")
        request.state.principal = SimpleNamespace(principal="user:" + name, sub=name)
        with tenant_scope(tenant=request.headers.get("X-Test-Tenant", "tenant-a")):
            return await call_next(request)
    application.include_router(api.router)
    from maverick_dashboard.vendor_onboarding_api import router as workflow_router
    from maverick_dashboard.vendor_onboarding_composition import (
        attach_vendor_onboarding_composition,
    )
    application.include_router(workflow_router, prefix="/api/v1")
    attach_vendor_onboarding_composition(application)
    with TestClient(application, headers={"Origin": "http://testserver"}) as value:
        yield value
    config.reset_config_cache()


def reviewed(row):
    return {"confirm": True, "context": row["context"], "configuration": row["configuration"]}


def activate(client):
    before = client.get("/api/v1/vendor-setup")
    assert before.status_code == 200, before.text
    response = client.post("/api/v1/vendor-setup/enable", json=reviewed(before.json()))
    assert response.status_code == 200, response.text
    return response.json()


def credential(tenant="tenant-a", *, owner="user:owner", name="jira-customer"):
    with tenant_scope(tenant=tenant):
        return connections.set_connection(name, connector="jira", base_url="https://example.atlassian.net", token="synthetic-private-token", owner=owner)


def bind(client, record):
    status = client.get("/api/v1/vendor-setup").json()
    return client.put("/api/v1/vendor-setup", json={**reviewed(status), "expected_revision": status["revision"], "ticket": record["name"], "ticket_revision": record["revision"]})


def test_activation_and_upload_only_binding_are_real_and_tenant_private(client):
    state = activate(client)
    assert state["modules_enabled"] and state["connections_enabled"]
    assert state["live_provider_verified"] is False
    actual = config.load_global_config()
    assert all(actual[key]["enable"] for key in setup.REQUIRED_MODULES)
    record = credential()
    saved = bind(client, record)
    assert saved.status_code == 200, saved.text
    assert saved.json()["binding"]["resource_owner"] == "user:owner"
    assert "synthetic-private-token" not in saved.text
    with tenant_scope(tenant="tenant-a"):
        effective = setup.settings(config.load_config())
        assert effective.enable and effective.ticket_connection == record["name"]
        assert effective.document_connection == "unconfigured-drive"
    foreign = client.get("/api/v1/vendor-setup", headers={"X-Test-Tenant": "tenant-b"})
    assert foreign.status_code == 200 and foreign.json()["revision"] == 0
    assert record["name"] not in foreign.text
    created = client.post("/api/v1/vendor-onboarding/cases", json={"vendor_name": "Synthetic upload-only vendor", "project": "VEND", "purpose": "Review synthetic evidence"})
    assert created.status_code == 201, created.text
    assert created.json()["owner"] == "user:owner"
    assert client.get("/api/v1/vendor-onboarding/cases").json()["cases"][0]["id"] == created.json()["id"]


def test_stale_credentials_context_or_role_cannot_bind(client):
    activate(client)
    old = credential()
    new = credential()
    denied = bind(client, old)
    assert denied.status_code == 409
    with tenant_scope(tenant="tenant-a"):
        assert setup.current() is None
    status = client.get("/api/v1/vendor-setup").json()
    payload = {**reviewed(status), "expected_revision": 0, "ticket": new["name"], "ticket_revision": new["revision"]}
    assert client.put("/api/v1/vendor-setup", json=payload, headers={"X-Test-Tenant": "tenant-b"}).status_code == 409
    assert client.get("/api/v1/vendor-setup", headers={"X-Test-User": "other"}).status_code == 403
    assert client.get("/api/v1/vendor-setup", headers={"X-Test-Tenant": "tenant-b", "X-Lightwork-Setup-Context": status["context"]}).status_code == 409
    assert bind(client, credential(name="foreign-jira", owner="user:foreign")).status_code == 409


def test_authority_generation_compare_and_swap_and_stable_resource_owner(client):
    activate(client)
    credential_a = credential()
    saved = bind(client, credential_a)
    assert saved.status_code == 200, saved.text
    old = saved.json()
    credential_b = credential(name="jira-second")
    changed = bind(client, credential_b)
    assert changed.status_code == 200
    assert changed.json()["binding"]["resource_owner"] == old["binding"]["resource_owner"]
    stale = client.put("/api/v1/vendor-setup", json={**reviewed(old), "expected_revision": old["revision"], "ticket": credential_a["name"], "ticket_revision": credential_a["revision"]})
    assert stale.status_code == 409


def test_false_deployment_connection_override_and_unconfirmed_change_are_not_waived(client, monkeypatch):
    monkeypatch.setenv("LIGHTWORK_CONNECTIONS", "0")
    state = client.get("/api/v1/vendor-setup").json()
    assert client.post("/api/v1/vendor-setup/enable", json=reviewed(state), headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/v1/vendor-setup/enable", json={**reviewed(state), "confirm": False}).status_code == 422
    assert client.post("/api/v1/vendor-setup/enable", json=reviewed(state)).status_code == 409
    assert not config.dashboard_overrides_path().exists()
