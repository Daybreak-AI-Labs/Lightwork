"""Local actual-route connection acceptance with real identity and sealed stores.

Only remote OAuth/Jira responses are doubled. No live provider or installed
desktop qualification is implied by these ASGI workflow tests.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from maverick import config, connections, world_model
from maverick import connection_oauth as oauth
from maverick.paths import tenant_scope

ORIGIN = "http://127.0.0.1:8765"
OWNER = "user:connection-owner@example.test"
SETUP = "/api/v1/connection-setup"
CASES = "/api/v1/vendor-onboarding/cases"
CLOUD = "12345678-1234-1234-1234-123456789abc"
SITE = "https://synthetic.atlassian.net"


@pytest.fixture
def customer(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(("MAVERICK_", "LIGHTWORK_")) or key.endswith("_API_KEY"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("MAVERICK_SECURE_DEFAULT", "0")
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setenv("MAVERICK_VOICE_AUTO_FETCH", "0")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    config.reset_config_cache()
    calls = []

    def token(provider, payload):
        calls.append((provider, dict(payload)))
        profile = "jira-cloud" if provider == "atlassian" else "google-drive"
        return {"access_token": "synthetic-access-" + str(len(calls)),
                "refresh_token": "synthetic-refresh-" + str(len(calls)),
                "expires_in": 3600, "scope": " ".join(oauth.PROFILES[profile]["scopes"])}

    monkeypatch.setattr(oauth, "_post_token", token)
    monkeypatch.setattr(oauth, "_jira_sites", lambda *a: [{
        "id": CLOUD, "url": SITE, "scopes": oauth.PROFILES["jira-cloud"]["scopes"],
    }])
    monkeypatch.setattr(oauth, "_provider_identity", lambda provider, token: {
        "provider": provider,
        "subject": "fixture-account",
        "display_name": "Fixture Account",
        "email": "fixture@example.test",
        **(
            {"email_verified": True, "hosted_domain": "example.test"}
            if provider == "google"
            else {"account_status": "active"}
        ),
    })
    from maverick.tools import _ssrf
    monkeypatch.setattr(_ssrf, "safe_client", lambda *a, **k: pytest.fail("Unexpected provider request"))
    from maverick_dashboard.app import app
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53210),
                    headers={"Origin": ORIGIN}, follow_redirects=False) as client:
        enrolled = client.post("/auth/local-owner", data={
            "email": OWNER.removeprefix("user:"),
            "password": "Disposable owner " + secrets.token_hex(16), "confirm": "yes",  # pragma: allowlist secret
        })
        assert enrolled.status_code == 303, enrolled.text
        for tenant in ("workspace-a", "workspace-b"):
            created = client.post("/api/v1/admin/tenants", json={
                "id": tenant, "display_name": tenant, "plan": "free",
            })
            assert created.status_code == 201, created.text
            member = client.put(f"/api/v1/admin/tenants/{tenant}/roles/{OWNER}", json={"role": "admin"})
            assert member.status_code == 204, member.text
        _select(client, "workspace-a")
        before = _overview(client)
        enabled = client.post(SETUP + "/enable", json=_reviewed(before))
        assert enabled.status_code == 200, enabled.text
        yield client, calls
    config.reset_config_cache()


def _select(client, tenant):
    selected = client.put("/api/v1/workspace/active-tenant", json={"tenant_id": tenant})
    assert selected.status_code == 200, selected.text
    client.headers["X-Lightwork-Expected-Tenant"] = tenant
    assert client.get("/api/v1/workspace/active-tenant").json()["tenant_id"] == tenant


def _overview(client):
    response = client.get(SETUP)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


def _reviewed(row):
    return {"context": row["context"], "confirm": True}


def _register(client, profile):
    response = client.put(SETUP + "/application", json={
        **_reviewed(_overview(client)), "provider": oauth.PROFILES[profile]["provider"],
        "client_id": "synthetic-client", "client_secret": "synthetic-client-secret",
    })
    assert response.status_code == 200, response.text


def _begin(client, profile, revision=""):
    context = _overview(client)["context"]
    response = client.post(SETUP + "/authorize", json={
        "context": context, "confirm": True, "profile": profile, "name": "reviewed-account",
        "expected_revision": revision, "site_url": SITE if profile == "jira-cloud" else "",
    })
    assert response.status_code == 200, response.text
    return {"context": context, "state": response.json()["state"],
            "confirm": True, "code": "synthetic-callback-code"}


def _exchange(client, payload):
    response = client.post(SETUP + "/exchange", json=payload)
    for secret in ("synthetic-callback-code", "synthetic-client-secret", "synthetic-access-", "synthetic-refresh-"):
        assert secret not in response.text
    return response


@pytest.mark.parametrize("profile", ["google-drive", "jira-cloud"])
@pytest.mark.parametrize("copied_context", [True, False], ids=["original-context", "current-context"])
def test_two_real_workspaces_refuse_copied_and_replayed_callbacks(customer, profile, copied_context):
    client, calls = customer
    _register(client, profile)
    first = _begin(client, profile)
    _select(client, "workspace-b")
    _register(client, profile)
    second = _begin(client, profile)
    copied = dict(first)
    if not copied_context:
        copied["context"] = second["context"]
    assert _exchange(client, copied).status_code == 409
    assert calls == []
    for tenant in ("workspace-a", "workspace-b"):
        with tenant_scope(tenant=tenant):
            assert connections.get_connection("reviewed-account") is None
    accepted = _exchange(client, second)
    assert accepted.status_code == 200, accepted.text
    second_record = accepted.json()["connection"]
    assert second_record["owner"] == OWNER and second_record["has_token"]
    assert _exchange(client, second).status_code == 409
    assert len(calls) == 1
    _select(client, "workspace-a")
    # The untouched original transaction belongs to this workspace again.
    accepted = _exchange(client, first)
    assert accepted.status_code == 200, accepted.text
    assert _exchange(client, first).status_code == 409
    assert len(calls) == 2
    with tenant_scope(tenant="workspace-a"):
        first_record = connections.get_connection("reviewed-account")
    with tenant_scope(tenant="workspace-b"):
        other_record = connections.get_connection("reviewed-account")
    assert first_record["revision"] != other_record["revision"] == second_record["revision"]
    assert first_record["token"] != other_record["token"]


@pytest.mark.parametrize("review", ["missing", "stale"])
def test_authenticated_disconnect_requires_current_reviewed_revision(customer, review):
    client, _ = customer
    _register(client, "jira-cloud")
    old = _exchange(client, _begin(client, "jira-cloud")).json()["connection"]
    current = _exchange(client, _begin(client, "jira-cloud", old["revision"])).json()["connection"]
    path = "/api/v1/connections/reviewed-account"
    denied = client.delete(path, params={} if review == "missing" else {"expected_revision": old["revision"]})
    assert denied.status_code == (422 if review == "missing" else 409), denied.text
    with tenant_scope(tenant="workspace-a"):
        assert connections.get_connection("reviewed-account")["revision"] == current["revision"]
    deleted = client.delete(path, params={"expected_revision": current["revision"]})
    assert deleted.status_code == 200, deleted.text
    with tenant_scope(tenant="workspace-a"):
        assert connections.get_connection("reviewed-account") is None


def _step(client, row, name, **extra):
    response = client.post(f"{CASES}/{row['id']}/{name}", json={"expected_revision": row["revision"], **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _approve_vendor(client, credential):
    status = client.get("/api/v1/vendor-setup").json()
    status = client.post("/api/v1/vendor-setup/enable", json={
        **_reviewed(status), "configuration": status["configuration"],
    }).json()
    saved = client.put("/api/v1/vendor-setup", json={
        **_reviewed(status), "configuration": status["configuration"],
        "expected_revision": status["revision"], "ticket": credential["name"],
        "ticket_revision": credential["revision"], "resource_owner": OWNER,
    })
    assert saved.status_code == 200, saved.text
    response = client.post(CASES, json={"vendor_name": "Synthetic review vendor", "project": "VEND", "purpose": "Local credential generation acceptance"})
    assert response.status_code == 201, response.text
    row = response.json()
    response = client.post(f"{CASES}/{row['id']}/upload", params={
        "expected_revision": row["revision"], "name": "policy.txt", "document_date": time.time() - 100,
    }, headers={"Content-Type": "text/plain"}, content=b"Models never train on customer data. Data is encrypted at rest and in transit.")
    assert response.status_code == 200, response.text
    row = _step(client, response.json(), "collect-evidence")
    row = _step(client, row, "assess")
    row = _step(client, row, "review", decision="approved", rationale="Restricted local fixture accepted")
    row = _step(client, row, "prepare-action")
    row = _step(
        client,
        row,
        "approve-action",
        confirm=True,
        approval_binding_sha256=row["operation"]["approval_card"]["binding_sha256"],
    )
    assert row["operation"]["state"] == "approved"
    assert row["operation"]["preconditions"]["ticket_credential_version"] == credential["revision"]
    return row


def test_actual_vendor_routes_reject_stale_workspace_context_before_composition(customer, monkeypatch):
    client, _ = customer
    _register(client, "jira-cloud")
    credential = _exchange(client, _begin(client, "jira-cloud")).json()["connection"]
    row = _approve_vendor(client, credential)
    _select(client, "workspace-b")
    foreign_page = client.get("/third-party/vendor-onboarding")
    stale = re.search(r'data-workspace-context="([a-f0-9]{64})"', foreign_page.text).group(1)
    _select(client, "workspace-a")
    current_page = client.get("/third-party/vendor-onboarding")
    current = re.search(r'data-workspace-context="([a-f0-9]{64})"', current_page.text).group(1)
    assert stale != current
    factory = client.app.state.vendor_onboarding_binding_factory
    invocations = []

    def observed(request):
        invocations.append(True)
        return factory(request)

    monkeypatch.setattr(client.app.state, "vendor_onboarding_binding_factory", observed)
    body = {"vendor_name": "Context reviewed vendor", "project": "VEND", "purpose": "Current workspace"}
    for response in (
        client.get(f"{CASES}/{row['id']}", headers={"X-Lightwork-Vendor-Context": stale}),
        client.post(CASES, json=body, headers={"X-Lightwork-Vendor-Context": stale}),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "workspace_context_changed"
        assert response.json()["detail"]["next_action"]
    assert invocations == []
    read = client.get(f"{CASES}/{row['id']}", headers={"X-Lightwork-Vendor-Context": current})
    assert read.status_code == 200, read.text
    assert read.json()["id"] == row["id"]
    created = client.post(CASES, json=body, headers={"X-Lightwork-Vendor-Context": current})
    assert created.status_code == 201, created.text
    assert len(invocations) == 2


@pytest.mark.parametrize("change", ["unchanged", "reconnect", "revoke"])
def test_real_approved_vendor_action_rechecks_reconnected_or_revoked_account(customer, monkeypatch, change):
    client, calls = customer
    _register(client, "jira-cloud")
    credential = _exchange(client, _begin(client, "jira-cloud")).json()["connection"]
    row = _approve_vendor(client, credential)
    requests = []
    issue = {}

    def remote(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-access-1"
        assert request.url.host == "api.atlassian.com"
        if request.method == "POST":
            assert request.url.path == f"/ex/jira/{CLOUD}/rest/api/3/issue"
            issue.update(json.loads(request.content))
            issue["fields"]["updated"] = "2026-09-08T01:00:00+00:00"
            return httpx.Response(201, json={"key": "VEND-1"})
        assert request.method == "GET"
        assert request.url.path == f"/ex/jira/{CLOUD}/rest/api/3/issue/VEND-1"
        return httpx.Response(200, json={"key": "VEND-1", **issue})

    from maverick.tools import _ssrf
    monkeypatch.setattr(_ssrf, "safe_client", lambda *a, **k: httpx.Client(transport=httpx.MockTransport(remote)))
    if change == "reconnect":
        replacement = _exchange(client, _begin(client, "jira-cloud", credential["revision"]))
        assert replacement.status_code == 200, replacement.text
        assert replacement.json()["connection"]["revision"] != credential["revision"]
    elif change == "revoke":
        revoked = client.delete("/api/v1/connections/reviewed-account", params={"expected_revision": credential["revision"]})
        assert revoked.status_code == 200, revoked.text
    response = client.post(f"{CASES}/{row['id']}/execute", json={"confirm": True, "expected_revision": row["revision"]})
    if change == "unchanged":
        assert response.status_code == 200, response.text
        assert response.json()["operation"]["state"] == "verified"
        assert [request.method for request in requests] == ["POST", "GET"]
        assert len(calls) == 1
        return
    assert response.status_code == (409 if change == "reconnect" else 503), response.text
    if change == "reconnect":
        assert response.json()["detail"]["next_action"]
    else:
        assert "Enroll the Jira connection" in response.json()["detail"]
    assert requests == []
    assert len(calls) == (2 if change == "reconnect" else 1)
    if change == "revoke":
        # Missing credentials block composition before touching the historical
        # case. Reenrollment must never resurrect that historical approval.
        restored = _exchange(client, _begin(client, "jira-cloud"))
        assert restored.status_code == 200, restored.text
        denied = client.post(f"{CASES}/{row['id']}/execute", json={"confirm": True, "expected_revision": row["revision"]})
        assert denied.status_code == 409, denied.text
        assert denied.json()["detail"]["code"] == "approval_preconditions_changed"
    current = client.get(f"{CASES}/{row['id']}")
    assert current.status_code == 200, current.text
    assert current.json()["operation"] is None
    assert current.json()["operation_history"][-1]["state"] == "invalidated"
    assert requests == []
