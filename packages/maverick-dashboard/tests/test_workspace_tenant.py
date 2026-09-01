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
from maverick.oidc import VerifiedPrincipal
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})

# The user-reported dead end: a tenant-gated workbench page. Its only gates
# are "view" and the tenant binding, so it isolates exactly this feature.
GATED_PAGE = "/consent/publish-settings"
MEMBER_GATED_PAGE = "/privacy/data-discovery"


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


def _select(tenant_id, *, expected: str = ""):
    headers = {"X-Lightwork-Expected-Tenant": expected} if expected else None
    return client.put(
        "/api/v1/workspace/active-tenant",
        json={"tenant_id": tenant_id},
        headers=headers,
    )


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
    assert r.json() == {"tenant_id": "acme", "source": "local_selection"}

    got = client.get("/api/v1/workspace/active-tenant").json()
    assert got["tenant_id"] == "acme"
    assert got["source"] == "local_selection"

    assert _select(None, expected="acme").json()["tenant_id"] is None
    assert client.get("/api/v1/workspace/active-tenant").json()["tenant_id"] is None


def test_selection_is_anchored_in_global_audit(monkeypatch):
    import maverick.audit as audit

    client.post("/api/v1/admin/tenants", json={"id": "acme"})
    calls = []
    monkeypatch.setattr(
        audit,
        "record_global",
        lambda kind, **payload: calls.append((kind, payload)) or True,
    )

    response = _select("acme")

    assert response.status_code == 200, response.text
    assert calls == [
        (
            audit.EventKind.WORKSPACE_SELECTED,
            {
                "agent": "local",
                "goal_id": None,
                "actor": "local",
                "previous_tenant": None,
                "tenant": "acme",
                "source": "local_selection",
            },
        )
    ]


def test_selection_audit_refusal_restores_previous_workspace(monkeypatch):
    import maverick.audit as audit

    client.post("/api/v1/admin/tenants", json={"id": "acme"})

    def refuse(*_args, **_kwargs):
        raise audit.AuditRefused("custody unavailable")

    monkeypatch.setattr(audit, "record_global", refuse)
    response = _select("acme")

    assert response.status_code == 503
    assert "previous workspace restored" in response.json()["detail"]
    assert client.get("/api/v1/workspace/active-tenant").json()["tenant_id"] is None


def test_selecting_unknown_tenant_is_400_not_silent():
    r = _select("ghost")
    assert r.status_code == 400
    assert "unknown tenant" in r.json()["detail"]


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

    suspended = client.post(
        "/api/v1/admin/tenants/acme/suspend",
        headers={"X-Lightwork-Expected-Tenant": "acme"},
    )
    assert suspended.status_code == 200, suspended.text
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


def test_picker_quick_workspace_creates_selects_and_recovers_original_url():
    blocked = client.get(GATED_PAGE, headers={"Accept": "text/html"})
    assert blocked.status_code == 409
    assert blocked.url.path == GATED_PAGE
    assert "No tenants exist yet" in blocked.text
    assert 'id="tsel-quick"' in blocked.text
    assert "createAndSwitch({ id: 'local', display_name: 'My workspace', plan: 'free' }" in blocked.text

    # Execute the exact create-then-select sequence wired to the quick action.
    created = client.post(
        "/api/v1/admin/tenants",
        json={"id": "local", "display_name": "My workspace", "plan": "free"},
    )
    assert created.status_code == 201, created.text

    selected = _select("local")
    assert selected.status_code == 200, selected.text
    assert selected.json() == {"tenant_id": "local", "source": "local_selection"}
    active = client.get("/api/v1/workspace/active-tenant").json()
    assert active["tenant_id"] == "local"
    assert active["source"] == "local_selection"

    recovered = client.get(blocked.url.path, headers={"Accept": "text/html"})
    assert recovered.status_code == 200, recovered.text
    assert recovered.url.path == GATED_PAGE
    assert "select an active tenant" not in recovered.text


def test_overlay_key_lints_clean():
    """config-lint must know [dashboard] active_tenant -- the wizard/lint
    drift class of bug (unknown-key warnings for keys the app itself wrote)."""
    from maverick.config_lint import lint_config

    findings = lint_config({"dashboard": {"active_tenant": "acme"}})
    problems = [f for f in findings if "active_tenant" in str(f)]
    assert problems == [], problems


def _enable_oidc(monkeypatch) -> None:
    import maverick_dashboard.auth as auth

    monkeypatch.setattr(auth, "oidc_enabled", lambda: True)
    monkeypatch.setattr(
        auth,
        "verify_oidc_token",
        lambda token, **_kw: VerifiedPrincipal(
            sub=token,
            issuer="https://issuer.example",
            audience="lightwork",
            claims={"sub": token},
        ),
    )


def _as(user: str, *, expected: str = "") -> dict[str, str]:
    headers = {"Authorization": f"Bearer {user}"}
    if expected:
        headers["X-Lightwork-Expected-Tenant"] = expected
    return headers


def test_authenticated_selection_is_principal_and_membership_scoped(monkeypatch):
    """One operator's selection must never become another user's authority."""
    from maverick_dashboard import rbac

    client.post("/api/v1/admin/tenants", json={"id": "acme"})
    client.post("/api/v1/admin/tenants", json={"id": "globex"})
    rbac.set_role("user:alice", "viewer")
    rbac.set_tenant_role("acme", "user:alice", "operator")
    rbac.set_role("user:outsider", "operator")
    _enable_oidc(monkeypatch)

    selected = client.put(
        "/api/v1/workspace/active-tenant",
        json={"tenant_id": "acme"},
        headers=_as("alice"),
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["source"] == "principal_selection"
    assert client.get(MEMBER_GATED_PAGE, headers=_as("alice")).status_code == 200

    # No membership: no inherited tenant binding, no roster disclosure, and no
    # ability to select a tenant merely by guessing its id.
    assert client.get(MEMBER_GATED_PAGE, headers=_as("outsider")).status_code == 409
    picker = client.get("/workspace/tenant", headers=_as("outsider"))
    assert picker.status_code == 200
    assert "acme" not in picker.text and "globex" not in picker.text
    denied = client.put(
        "/api/v1/workspace/active-tenant",
        json={"tenant_id": "acme"},
        headers=_as("outsider"),
    )
    assert denied.status_code == 404


def test_stale_tab_mutation_is_rejected_after_principal_switch(monkeypatch):
    from maverick_dashboard import rbac

    client.post("/api/v1/admin/tenants", json={"id": "acme"})
    client.post("/api/v1/admin/tenants", json={"id": "globex"})
    rbac.set_role("user:alice", "viewer")
    rbac.set_tenant_role("acme", "user:alice", "operator")
    rbac.set_tenant_role("globex", "user:alice", "operator")
    _enable_oidc(monkeypatch)

    assert client.put(
        "/api/v1/workspace/active-tenant",
        json={"tenant_id": "acme"},
        headers=_as("alice"),
    ).status_code == 200
    page = client.get(MEMBER_GATED_PAGE, headers=_as("alice"))
    assert 'name="lightwork-workspace-tenant" content="acme"' in page.text

    # A current Acme tab may intentionally switch to Globex.
    switched = client.put(
        "/api/v1/workspace/active-tenant",
        json={"tenant_id": "globex"},
        headers=_as("alice", expected="acme"),
    )
    assert switched.status_code == 200, switched.text

    # Another still-open Acme tab cannot mutate under the now-current Globex
    # binding. A freshly rendered Globex page carries the matching guard.
    stale = client.put(
        "/api/v1/workspace/active-tenant",
        json={"tenant_id": "acme"},
        headers=_as("alice", expected="acme"),
    )
    assert stale.status_code == 409
    assert "workspace changed" in stale.json()["detail"]
    fresh = client.put(
        "/api/v1/workspace/active-tenant",
        json={"tenant_id": "acme"},
        headers=_as("alice", expected="globex"),
    )
    assert fresh.status_code == 200, fresh.text
