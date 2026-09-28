"""Workspaces page: one-step client setup (``tenant onboard``) and re-protecting
every workspace key with a new master key (``tenant kms-rotate``)."""
from __future__ import annotations

import html as _html
import re

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}
BASE = "/api/v1/admin/workspace-ops"
_BANNED = re.compile(r"\b(tenant|principal|payload|kek|dek|kms|json|toml|uuid)\b|/api/v1|MAVERICK_", re.IGNORECASE)


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(audit_writer, "_default", None)
    audit_writer._defaults.clear()
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_TENANT", "MAVERICK_CLIENT_ID", "MAVERICK_DASHBOARD_INVITES"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    dash_app._world_cache.clear()
    from maverick.tenant import kms
    kms._clear_cache()
    yield TestClient(dash_app.app, headers=ORIGIN)
    kms._clear_cache()
    config.reset_config_cache()
    dash_app._world_cache.clear()
    audit_writer._defaults.clear()


def _as(monkeypatch, role: str, who: str = "user:someone"):
    from maverick_dashboard import auth, rbac
    rbac.set_role(who, role)
    monkeypatch.setattr(auth, "caller_principal", lambda request: who)


def _audit(monkeypatch):
    from maverick.audit import writer
    seen: list[tuple[str, dict]] = []
    real = writer.audit_event

    def spy(kind, **payload):
        seen.append((str(kind), payload))
        return real(kind, **payload)
    monkeypatch.setattr(writer, "audit_event", spy)
    import maverick.audit as audit_pkg
    monkeypatch.setattr(audit_pkg, "audit_event", spy)
    return seen


def _visible(page: str) -> str:
    main = page.split('<main id="content"', 1)[-1].split("</main>", 1)[0]
    main = re.sub(r"<(script|style)\b.*?</\1>", " ", main, flags=re.DOTALL)
    return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", main)))


ONBOARD = {"display_name": "Acme Finance", "plan": "pro", "max_daily_dollars": 25,
           "admin_email": "Owner@Acme.example", "role": "admin"}


def test_workspace_controls_need_an_installation_administrator(client, monkeypatch):
    from maverick_dashboard import auth
    _as(monkeypatch, "viewer")
    monkeypatch.setattr(auth, "role_for_principal", lambda principal: "admin")  # a workspace admin
    assert client.get(BASE + "/roles").status_code == 403
    assert client.post(BASE + "/onboard", json=ONBOARD).status_code == 403
    keys = {"current_key": "a" * 64, "new_key": "b" * 64, "confirm": True}
    assert client.post(BASE + "/keys/preview", json=keys).status_code == 403
    assert client.post(BASE + "/keys/rotate", json=keys).status_code == 403
    assert client.get(BASE + "/jobs/x").status_code == 403


def test_one_step_setup_needs_invitations_turned_on(client):
    from maverick.tenant.registry import list_tenants
    refused = client.post(BASE + "/onboard", json=ONBOARD)
    assert refused.status_code == 409 and "No workspace was created" in refused.json()["detail"]
    assert list_tenants() == []


def test_one_step_setup_creates_the_workspace_and_its_invitation(client, monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_INVITES", "1")
    token = "workspace-ops-test-token"  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", token)  # invitations on means people sign in
    client.headers["Authorization"] = f"Bearer {token}"
    seen = _audit(monkeypatch)
    assert client.post(BASE + "/onboard", json={**ONBOARD, "role": "wizard"}).status_code == 422
    made = client.post(BASE + "/onboard", json=ONBOARD)
    assert made.status_code == 201, made.text
    data = made.json()
    assert data["workspace"]["name"] == "Acme Finance" and data["workspace"]["plan"] == "pro"
    assert data["invite_email"] == "owner@acme.example"
    assert "/auth/invite/inv_" in data["invite_link"] and "shown only once" in data["summary"]
    from maverick.tenant.registry import get_tenant
    record = get_tenant(data["workspace"]["id"])
    assert record.max_daily_dollars == 25
    from maverick_dashboard import invites
    assert [i.email for i in invites.list_invites()] == ["owner@acme.example"]
    kinds = [kind for kind, _ in seen]
    assert any("tenant_created" in kind for kind in kinds)
    assert any(p.get("verb") == "workspace_onboarded" for _, p in seen)
    assert not _BANNED.search(data["summary"])


def _workspace_with_key(tenant_id: str, key: bytes):
    from maverick.tenant import kms
    from maverick.tenant.registry import create_tenant
    create_tenant(tenant_id, display_name=tenant_id.capitalize())
    kms.tenant_dek(tenant_id, kms=kms.LocalKMS(key))
    kms._clear_cache()


def test_key_preview_then_confirmed_rotation_never_returns_key_material(client, monkeypatch):
    from maverick_dashboard import ops_admin_jobs as ops_jobs
    old, new = bytes(range(32)), bytes(range(1, 33))
    _workspace_with_key("acme", old)
    _workspace_with_key("beta", old)
    body = {"current_key": old.hex(), "new_key": new.hex()}
    same = client.post(BASE + "/keys/preview", json={"current_key": old.hex(), "new_key": old.hex()})
    assert same.status_code == 422
    bad = client.post(BASE + "/keys/preview", json={"current_key": "not a real key at all", "new_key": new.hex()})
    assert bad.status_code == 422 and "not a real key" not in bad.text
    preview = client.post(BASE + "/keys/preview", json=body)
    assert preview.status_code == 200
    assert old.hex() not in preview.text and new.hex() not in preview.text
    data = preview.json()
    assert data["total"] == 2 and "2 workspaces would be re-protected" in data["summary"]
    assert {r["workspace"] for r in data["rows"]} == {"Acme", "Beta"}
    assert client.post(BASE + "/keys/rotate", json=body).status_code == 409  # needs confirming
    seen = _audit(monkeypatch)
    job = client.post(BASE + "/keys/rotate", json={**body, "confirm": True}).json()["job"]
    assert old.hex() not in str(job)
    finished = ops_jobs.wait(job["id"])
    assert finished["state"] == "done" and "Re-protected 2 workspaces" in finished["result"]["summary"]
    status = client.get(BASE + "/jobs/" + job["id"])
    assert old.hex() not in status.text and new.hex() not in status.text
    records = [p for _, p in seen if p.get("verb") == "workspace_keys_reprotect"]
    assert len(records) == 1 and old.hex() not in str(records) and new.hex() not in str(records)
    again = client.post(BASE + "/keys/preview", json=body).json()
    assert "2 already use it" in again["summary"]


def test_workspaces_page_offers_the_new_controls_in_plain_words(client):
    page = client.get("/tenants")
    assert page.status_code == 200
    for marker in ('id="tenant-onboard"', 'id="tenant-keys"', "/static/lightwork-tenant-ops.js",
                   "Create workspace and invite", "Re-protect workspaces"):
        assert marker in page.text
    assert not _BANNED.search(_visible(page.text))
    assert client.get("/static/lightwork-tenant-ops.js").status_code == 200
