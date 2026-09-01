"""In-app workspace tenant selection (the dashboard's own tenant binding).

Tenant-scoped workbenches 409 ("select an active tenant ...") until a tenant
is bound. Historically the only bindings were the MAVERICK_TENANT env var or
an enterprise client binding -- server-side steps the dashboard promises not
to need. This suite covers the in-app path end to end: the Settings-overlay
persistence, the /api/v1/workspace/active-tenant endpoints, the middleware
that scopes each request to the selection, and the HTML tenant picker that
replaces the raw-JSON 409 dead end for browser users.

Auth-off TestClient => the local caller is admin (matches the rest of the
API). Registry, workspaces, and the settings overlay are isolated under a
temp HOME/MAVERICK_HOME so nothing touches real data.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})

# The user-reported dead end: a tenant-gated workbench page. Its only gates
# are "view" and the tenant binding, so it isolates exactly this feature.
GATED_PAGE = "/consent/publish-settings"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _select(tenant_id):
    return client.put("/api/v1/workspace/active-tenant", json={"tenant_id": tenant_id})


def test_store_rejects_unknown_and_suspended_tenants():
    from maverick_dashboard import settings_store

    with pytest.raises(ValueError, match="unknown tenant"):
        settings_store.set_active_tenant("ghost")

    client.post("/api/v1/admin/tenants", json={"id": "acme"})
    client.post("/api/v1/admin/tenants/acme/suspend")
    with pytest.raises(ValueError, match="suspended"):
        settings_store.set_active_tenant("acme")

    # Clearing never validates -- it must work even after the tenant is gone.
    settings_store.set_active_tenant(None)
    assert settings_store.active_tenant() is None


def test_selection_roundtrip_via_api():
    client.post("/api/v1/admin/tenants", json={"id": "acme"})

    assert client.get("/api/v1/workspace/active-tenant").json()["tenant_id"] is None
    r = _select("acme")
    assert r.status_code == 200, r.text
    assert r.json() == {"tenant_id": "acme", "source": "selection"}

    got = client.get("/api/v1/workspace/active-tenant").json()
    assert got["tenant_id"] == "acme"
    assert got["source"] == "selection"

    assert _select(None).json()["tenant_id"] is None
    assert client.get("/api/v1/workspace/active-tenant").json()["tenant_id"] is None


def test_selecting_unknown_tenant_is_400_not_silent():
    r = _select("ghost")
    assert r.status_code == 400
    assert "unknown tenant" in r.json()["detail"]


def test_selection_requires_global_admin(monkeypatch):
    import maverick_dashboard.auth as auth

    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:viewer")
    monkeypatch.setattr(auth, "role_for_principal", lambda principal: "viewer")
    monkeypatch.setattr(auth, "global_role_for_principal", lambda principal: "viewer")
    assert _select("anything").status_code == 403
    # Reading which workspace is active stays open to any signed-in user.
    assert client.get("/api/v1/workspace/active-tenant").status_code == 200


def test_stored_selection_unlocks_tenant_gated_workbench():
    """The end-to-end promise: pick a tenant in-app, and a workbench that
    409'd starts working -- no env var, no config file, no restart."""
    assert client.get(GATED_PAGE).status_code == 409

    client.post("/api/v1/admin/tenants", json={"id": "acme"})
    assert _select("acme").status_code == 200

    r = client.get(GATED_PAGE)
    assert r.status_code == 200, r.text

    # And the gated APIs see the same binding (request-scoped, not global).
    assert client.get("/api/v1/workspace/active-tenant").json()["tenant_id"] == "acme"


def test_suspended_selection_is_not_bound():
    """A tenant suspended AFTER being selected must stop being bound: the
    registry stays the authority on who may do work."""
    client.post("/api/v1/admin/tenants", json={"id": "acme"})
    _select("acme")
    assert client.get(GATED_PAGE).status_code == 200

    client.post("/api/v1/admin/tenants/acme/suspend")
    assert client.get(GATED_PAGE).status_code == 409

    client.post("/api/v1/admin/tenants/acme/resume")
    assert client.get(GATED_PAGE).status_code == 200


def test_env_binding_outranks_selection(monkeypatch):
    client.post("/api/v1/admin/tenants", json={"id": "acme"})
    client.post("/api/v1/admin/tenants", json={"id": "globex"})
    _select("acme")

    monkeypatch.setenv("MAVERICK_TENANT", "globex")
    got = client.get("/api/v1/workspace/active-tenant").json()
    assert got["tenant_id"] == "globex"
    assert got["source"] == "env"
    assert got["selected"] == "acme"  # remembered, but outranked


def test_browser_409_renders_the_picker_api_keeps_json():
    """A browser user hitting the tenant gate gets the picker page (same URL,
    so selecting + reloading resumes), not raw JSON; API callers keep the
    machine-readable 409."""
    r = client.get(GATED_PAGE, headers={"Accept": "text/html"})
    assert r.status_code == 409
    assert r.headers["content-type"].startswith("text/html")
    assert "Workspace tenant" in r.text
    assert "select an active tenant" in r.text  # the gate's own message

    r = client.get(
        "/api/v1/consent/publish-settings/status",
        headers={"Accept": "application/json"},
    )
    if r.status_code == 409:  # tenant gate (not module gating) -> stays JSON
        assert r.headers["content-type"].startswith("application/json")


def test_picker_page_lists_tenants_and_marks_selection():
    client.post("/api/v1/admin/tenants", json={"id": "acme", "display_name": "Acme"})
    client.post("/api/v1/admin/tenants", json={"id": "globex"})
    client.post("/api/v1/admin/tenants/globex/suspend")
    _select("acme")

    t = client.get("/workspace/tenant").text
    assert "Acme" in t and "globex" in t
    assert "selected" in t and "suspended" in t
    # Admin controls are wired to the workspace API and tenant provisioning.
    assert "/api/v1/workspace/active-tenant" in t
    assert "/api/v1/admin/tenants" in t


def test_picker_page_empty_state_offers_one_click_workspace():
    t = client.get("/workspace/tenant").text
    assert "No tenants exist yet" in t
    assert 'id="tsel-quick"' in t  # creates + selects a "local" workspace


def test_overlay_key_lints_clean():
    """config-lint must know [dashboard] active_tenant -- the wizard/lint
    drift class of bug (unknown-key warnings for keys the app itself wrote)."""
    from maverick.config_lint import lint_config

    findings = lint_config({"dashboard": {"active_tenant": "acme"}})
    problems = [f for f in findings if "active_tenant" in str(f)]
    assert problems == [], problems
