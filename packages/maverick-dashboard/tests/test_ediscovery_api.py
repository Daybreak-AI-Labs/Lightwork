from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from maverick import audit, ediscovery
from maverick import paths as paths_module
from maverick.world_model import WorldModel
from maverick_dashboard import (
    compliance_controls_api,
    personal_data_requests,
    rbac,
    system_checks_api,
)
from maverick_dashboard import ediscovery_api as api

ALICE = "user:cli:alice"
BASE = "/api/v1/ediscovery/matters"


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from maverick import crypto_at_rest
    from maverick.paths import tenant_scope

    state = {"enabled": True, "tenant": "ws-a", "role": "auditor"}
    ledger = tmp_path / "ledger.sqlite3"
    world = WorldModel(tmp_path / "world.db")
    # The companion and inbox stores live under a private home and the active workspace.
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPT_PER_TENANT", "0")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "27" * 32)
    monkeypatch.setattr(crypto_at_rest, "_KEY_PATH", tmp_path / "unused.key")

    recorded: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "audit_event",
                        lambda kind, **payload: recorded.append((payload.get("verb", kind), payload)) or True)
    monkeypatch.setattr(ediscovery, "enabled", lambda: state["enabled"])
    monkeypatch.setattr(ediscovery, "store_path", lambda tenant=None: ledger)
    monkeypatch.setattr(paths_module, "current_tenant_id", lambda: state["tenant"])
    monkeypatch.setattr(paths_module, "current_tenant_id_strict", lambda: state["tenant"])

    def permitted(request: Request, permission: str) -> None:
        if permission not in rbac.permissions_for(str(request.headers.get("X-Role", "viewer"))):
            raise HTTPException(status_code=403, detail="denied")

    def has(request: Request, permission: str) -> bool:
        try:
            permitted(request, permission)
        except HTTPException:
            return False
        return True

    monkeypatch.setattr(api, "require_global_permission", permitted)
    monkeypatch.setattr(api, "has_global_permission", has)
    monkeypatch.setattr("maverick_dashboard._shared._world", lambda: world)

    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    client = TestClient(app, headers={"Origin": "http://testserver", "X-Role": "auditor"})
    with tenant_scope(tenant="ws-a"):
        yield SimpleNamespace(client=client, state=state, world=world, recorded=recorded, ledger=ledger)
    world.close()


def _seed(world: WorldModel) -> dict:
    alice_goal = world.create_goal("Alice task", owner=ALICE)
    conv = world.get_or_create_conversation("cli", "alice").id
    world.append_turn(conv, "user", "alice remark", goal_id=alice_goal)
    bob_conv = world.get_or_create_conversation("cli", "bob").id
    world.append_turn(bob_conv, "user", "bob remark")
    return {"conv": conv, "bob_conv": bob_conv, "goal": alice_goal}


def _body(**overrides) -> dict:
    body = {"name": "Acme dispute", "description": "", "custodians": [ALICE],
            "record_kinds": ["goals", "chats", "attachments", "approvals"]}
    body.update(overrides)
    return body


def test_every_route_is_absent_while_the_feature_is_off(env):
    env.state["enabled"] = False
    for role in ("auditor", "admin", "viewer"):
        headers = {"X-Role": role}
        assert env.client.get(BASE, headers=headers).status_code == 404
        assert env.client.post(BASE, json=_body(), headers=headers).status_code == 404
    assert env.client.get(f"{BASE}/mtr-0000000000000000/export").status_code == 404


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_matter_details_are_refused_without_the_audit_permission(env, role):
    headers = {"X-Role": role}
    assert env.client.get(BASE, headers=headers).status_code == 403
    assert env.client.post(BASE, json=_body(), headers=headers).status_code == 403
    assert ediscovery.MatterStore(env.ledger).list("ws-a") == []


@pytest.mark.parametrize("role", ["auditor", "admin"])
def test_auditors_and_admins_manage_matters_and_export_them(env, role):
    headers = {"X-Role": role}
    created = env.client.post(BASE, json=_body(), headers=headers)
    assert created.status_code == 201, created.text
    matter = created.json()
    assert matter["revision"] == 1 and matter["status"] == "active"
    assert "tenant_id" not in matter and "schema" not in matter

    edited = env.client.patch(f"{BASE}/{matter['id']}", headers=headers,
                              json={"expected_revision": 1, "date_from": "2001-01-01", "date_to": "2001-12-31"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 2 and edited.json()["custodians"] == [ALICE]
    stale = env.client.patch(f"{BASE}/{matter['id']}", headers=headers,
                             json={"expected_revision": 1, "name": "Old"})
    assert stale.status_code == 409

    cleared = env.client.patch(f"{BASE}/{matter['id']}", headers=headers,
                               json={"expected_revision": 2, "date_to": None})
    assert cleared.json()["date_to"] is None and cleared.json()["date_from"] == "2001-01-01"

    exported = env.client.get(f"{BASE}/{matter['id']}/export", headers=headers)
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"
    assert exported.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert "manifest.json" in archive.namelist()

    released = env.client.post(f"{BASE}/{matter['id']}/release", headers=headers, json={"expected_revision": 3})
    assert released.status_code == 200 and released.json()["status"] == "released"
    after = env.client.patch(f"{BASE}/{matter['id']}", headers=headers,
                             json={"expected_revision": 4, "name": "Later"})
    assert after.status_code == 409
    assert [verb for verb, _ in env.recorded] == [
        "ediscovery_matter_created", "ediscovery_matter_edited", "ediscovery_matter_edited",
        "ediscovery_matter_exported", "ediscovery_matter_released"]


def test_invalid_input_and_unknown_fields_are_refused(env):
    assert env.client.post(BASE, json=_body(extra="x")).status_code == 422
    assert env.client.post(BASE, json=_body(record_kinds=["email"])).status_code == 422
    assert env.client.post(BASE, json=_body(custodians=[])).status_code == 422
    assert env.client.post(BASE, json=_body(date_from="2026-13-01")).status_code == 422
    assert env.client.get(f"{BASE}/not-a-matter").status_code == 404


def test_matters_are_invisible_to_another_workspace(env):
    created = env.client.post(BASE, json=_body()).json()
    env.state["tenant"] = "ws-b"
    assert env.client.get(BASE).json()["matters"] == []
    assert env.client.get(f"{BASE}/{created['id']}").status_code == 404
    assert env.client.post(f"{BASE}/{created['id']}/release", json={"expected_revision": 1}).status_code == 404


def test_page_section_is_offered_only_to_audit_access(env):
    from maverick_dashboard.ediscovery_api import page_context

    auditor = page_context(SimpleNamespace(headers={"X-Role": "auditor"}))
    operator = page_context(SimpleNamespace(headers={"X-Role": "operator"}))
    assert auditor == {"ediscovery_enabled": True, "ediscovery_can_audit": True}
    assert operator == {"ediscovery_enabled": True, "ediscovery_can_audit": False}
    env.state["enabled"] = False
    assert page_context(SimpleNamespace(headers={"X-Role": "auditor"}))["ediscovery_can_audit"] is False


def test_self_service_deletion_reports_a_held_closure_and_keeps_it(env, monkeypatch):
    seeded = _seed(env.world)
    created = env.client.post(BASE, json=_body()).json()
    assert created["status"] == "active"
    scope = {"conversation_ids": [seeded["conv"]], "goal_ids": [seeded["goal"]], "episode_ids": []}
    outcome = personal_data_requests._erase_conversations(env.world, {}, {"scope": scope})
    assert outcome["state"] == "held"
    assert outcome["message"] == ediscovery.LEGAL_HOLD_MESSAGE
    assert "Alice" not in outcome["message"] and "Acme" not in outcome["message"]
    assert env.world.conn.execute("SELECT COUNT(*) FROM turns WHERE conversation_id=?",
                                  (seeded["conv"],)).fetchone()[0] == 1


def test_admin_erase_of_a_person_is_refused_with_the_plain_message(env, monkeypatch):
    seeded = _seed(env.world)
    env.client.post(BASE, json=_body())
    monkeypatch.setattr(compliance_controls_api, "_world_for_request", lambda: env.world)
    result = compliance_controls_api._run_erase("cli", "alice", "admin-user")
    assert result["erased"] is False
    assert result["message"] == ediscovery.LEGAL_HOLD_MESSAGE
    assert env.world.conn.execute("SELECT COUNT(*) FROM turns WHERE conversation_id=?",
                                  (seeded["conv"],)).fetchone()[0] == 1


def test_tidy_up_of_old_records_is_paused_by_an_active_matter(env):
    env.client.post(BASE, json=_body())
    with pytest.raises(HTTPException) as refused:
        system_checks_api._cleanup_run(30, 30)
    assert refused.value.status_code == 409
    assert ediscovery.LEGAL_HOLD_MESSAGE not in str(refused.value.detail)
    env.state["tenant"] = "ws-b"
    ediscovery.assert_no_active_matters()  # another workspace's matter does not hold this one


def test_compliance_page_shows_legal_holds_only_when_enabled_and_nonced(monkeypatch):
    import re

    from fastapi.testclient import TestClient
    from maverick_dashboard.app import app

    monkeypatch.setattr(ediscovery, "enabled", lambda: True)
    client = TestClient(app, headers={"Origin": "http://testserver"})
    page = client.get("/compliance")
    assert page.status_code == 200
    assert 'id="edh-title"' in page.text
    nonce = re.search(r'<script nonce="([^"]+)"[^>]*>\s*\(function \(\) \{\s*"use strict";\s*var API = "/api/v1/ediscovery', page.text)
    assert nonce is not None
    assert not re.search(r"<[^>]+\son[a-z]+=", page.text.split('id="edh-title"', 1)[1].split("</section>", 1)[0])

    monkeypatch.setattr(ediscovery, "enabled", lambda: False)
    assert 'id="edh-title"' not in client.get("/compliance").text


def test_switch_is_a_privacy_setting_off_by_default_that_the_settings_page_turns_on(tmp_path, monkeypatch):
    from maverick import config
    from maverick_dashboard import settings_catalog as catalog
    from maverick_dashboard.app import app

    from test_support.settings_pages import save_catalog

    row = catalog.BY_ID["ediscovery.enable"]
    assert row.default is False and row.group == "Privacy" and row.reader == "maverick.ediscovery"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    config.reset_config_cache()
    client = TestClient(app, headers={"Origin": "http://testserver"})
    assert ediscovery.enabled() is False
    assert client.get(BASE).status_code == 404
    saved = save_catalog(client, "/api/v1/settings/catalog",
                         json={"changes": [{"id": "ediscovery.enable", "value": True}]})
    assert saved.status_code == 200, saved.text
    config.reset_config_cache()
    assert ediscovery.enabled() is True
    assert client.get(BASE).status_code != 404


def test_retention_review_shows_a_workspace_blocked_while_a_matter_is_active(env, monkeypatch):
    from maverick.tenant import registry
    from maverick_dashboard import retention_maintenance
    from maverick_dashboard import retention_runs as native

    monkeypatch.setattr(registry, "get_tenant", lambda tid: SimpleNamespace(id=tid, display_name="Acme"))
    monkeypatch.setattr(native, "_policy", lambda: {})
    monkeypatch.setattr(retention_maintenance, "authorize", lambda request, expected_context=None: ("admin", {}))
    monkeypatch.setattr(retention_maintenance, "context_for", lambda request: {})
    request = SimpleNamespace(headers={})
    assert retention_maintenance.policy(request)["workspaces"][0]["blocked"] is False
    created = env.client.post(BASE, json=_body())
    assert created.status_code == 201
    assert retention_maintenance.policy(request)["workspaces"][0]["blocked"] is True
    env.client.post(f"{BASE}/{created.json()['id']}/release", json={"expected_revision": 1})
    assert retention_maintenance.policy(request)["workspaces"][0]["blocked"] is False
