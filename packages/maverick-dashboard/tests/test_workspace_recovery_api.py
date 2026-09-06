from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from maverick import client, config, control_plane_lease, crypto_at_rest, world_model
from maverick.paths import data_dir
from maverick_dashboard import recovery_api, recovery_mode
from maverick_dashboard.app import app


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CLIENT_ID", "acme")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_BACKUP_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MAVERICK_ALLOW_MULTIPLE_CONTROL_PLANES", raising=False)
    monkeypatch.setattr(config, "load_config", lambda: {"features": {"workspace_recovery": True}})
    monkeypatch.setattr(config, "config_source_errors", lambda **_: [])
    monkeypatch.setattr(recovery_api.recovery, "application_identity", lambda: "test-build-one")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(crypto_at_rest, "_KEY_PATH", tmp_path / "shared-keys" / "at_rest.key")
    client.reset_client_cache()
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "record.json").write_text('{"value":"before"}')
    lifespan = app.router.lifespan_context
    mode = getattr(app.state, "workspace_recovery_mode", False)
    started = getattr(app.state, "workspace_recovery_started", False)
    yield root
    app.router.lifespan_context = lifespan
    app.state.workspace_recovery_mode = mode
    app.state.workspace_recovery_started = started
    control_plane_lease.release()
    client.reset_client_cache()


def api_client():
    return TestClient(app, headers={"Origin": "http://testserver"})


def context(client):
    response = client.get("/api/v1/recovery/status")
    assert response.status_code == 200, response.text
    data = response.json()
    return {"X-Lightwork-Recovery-Context": data["context"], "X-Lightwork-Expected-Tenant": data["expected_tenant"]}


def setup_snapshot(client):
    headers = context(client)
    response = client.post("/api/v1/recovery/key", json={"confirm": True}, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    result = client.post("/api/v1/recovery/snapshots", json={"confirm": True}, headers=headers)
    assert result.status_code == 201, result.text
    return headers, result.json()["id"]


def test_comparison_routes_are_read_only_private_and_context_bound(workspace):
    client = api_client()
    headers, identifier = setup_snapshot(client)
    (workspace / "record.json").write_text("changed private document")
    endpoint = "/api/v1/recovery/snapshots/" + identifier
    assert client.post(endpoint + "/compare", json={}).status_code == 409
    assert client.post(endpoint + "/compare", headers=headers, json={"waive_guard": True}).status_code == 422
    compared = client.post(endpoint + "/compare", headers=headers, json={})
    assert compared.status_code == 200, compared.text
    assert compared.headers["cache-control"] == "private, no-store"
    report = compared.json()
    assert report["restore_authorized"] is False and report["isolated"] is False
    assert any(row["path"] == "record.json" and row["change"] == "changed" for row in report["items"])
    assert "changed private document" not in compared.text
    page = client.post(endpoint + "/comparison", headers=headers, json={"report_id": report["report_id"], "offset": 0, "limit": 1})
    assert page.status_code == 200 and len(page.json()["items"]) == 1
    assert client.post(endpoint + "/comparison", headers=headers, json={"report_id": report["report_id"], "offset": True}).status_code == 422
    assert client.post(endpoint + "/comparison/download", json={"report_id": report["report_id"]}).status_code == 409
    downloaded = client.post(endpoint + "/comparison/download", headers=headers, json={"report_id": report["report_id"]})
    assert downloaded.status_code == 200 and downloaded.headers["cache-control"] == "private, no-store"
    assert downloaded.headers["content-disposition"].endswith(identifier + '.json"')
    assert downloaded.json()["authentication"]["algorithm"] == "HMAC-SHA256"
    assert downloaded.json()["restore_authorized"] is False
    assert (workspace / "record.json").read_text() == "changed private document"
    assert not (workspace / "backups" / "recovery" / "review.json").exists()


def test_comparison_routes_reject_other_actor_and_admin_revocation(monkeypatch):
    client = api_client()
    headers, identifier = setup_snapshot(client)
    endpoint = "/api/v1/recovery/snapshots/" + identifier
    report = client.post(endpoint + "/compare", headers=headers, json={}).json()
    monkeypatch.setattr(recovery_api, "_authorization_principal", lambda _: "another-admin")
    other_headers = context(client)
    assert other_headers["X-Lightwork-Recovery-Context"] != headers["X-Lightwork-Recovery-Context"]
    assert client.post(endpoint + "/comparison", headers=other_headers, json={"report_id": report["report_id"]}).status_code == 409
    def deny(*_):
        raise HTTPException(403, "global admin required")
    monkeypatch.setattr(recovery_api, "require_global_permission", deny)
    assert client.post(endpoint + "/compare", headers=other_headers, json={}).status_code == 403
    assert client.post(endpoint + "/comparison/download", headers=other_headers, json={"report_id": report["report_id"]}).status_code == 403


def test_drill_report_survives_a_fresh_client_and_requires_current_context(workspace):
    first = api_client()
    headers, identifier = setup_snapshot(first)
    endpoint = "/api/v1/recovery/snapshots/" + identifier
    absent = first.post(endpoint + "/drill/report", headers=headers, json={})
    assert absent.status_code == 200 and absent.json()["state"] == "not_recorded"
    result = first.post(endpoint + "/drill", headers=headers, json={"confirm": True})
    assert result.status_code == 200, result.text
    report_id = result.json()["report_id"]
    (workspace / "record.json").write_text("newer live work")
    second = api_client()
    fresh_headers = context(second)
    loaded = second.post(endpoint + "/drill/report", headers=fresh_headers, json={})
    assert loaded.status_code == 200 and loaded.headers["cache-control"] == "private, no-store"
    assert loaded.json()["report_id"] == report_id and loaded.json()["current_build_matches"] is True
    assert loaded.json()["restore_authorized"] is False
    assert loaded.json()["completed_at"] == result.json()["completed_at"]
    assert second.post(endpoint + "/drill/report/download", json={"report_id": report_id}).status_code == 409
    downloaded = second.post(endpoint + "/drill/report/download", headers=fresh_headers, json={"report_id": report_id})
    assert downloaded.status_code == 200 and downloaded.headers["cache-control"] == "private, no-store"
    assert downloaded.json()["authentication"]["algorithm"] == "HMAC-SHA256"
    assert downloaded.json()["archive_sha256"] == result.json()["archive_sha256"]
    assert (workspace / "record.json").read_text() == "newer live work"


def test_recovery_page_and_assets_render_from_real_routes():
    client = api_client()
    assert client.get("/workspace/recovery").status_code == 200
    for suffix in ("js", "css"):
        response = client.get("/static/lightwork-workspace-recovery." + suffix)
        assert response.status_code == 200
        assert response.content


def test_disabled_feature_refuses_recovery_api(monkeypatch):
    monkeypatch.setattr(config, "get_features", lambda: {"workspace_recovery": False})
    assert api_client().get("/api/v1/recovery/status").status_code == 403


def test_installation_authorization_needs_admin_context_and_strict_whole_home_review(monkeypatch):
    client = api_client()
    headers = context(client)
    calls = []
    def authorize(**kwargs):
        calls.append(kwargs)
        return {"authorization": "a" * 32 + ":" + "b" * 64}
    monkeypatch.setattr(recovery_api.installation_recovery, "authorize", authorize)
    endpoint = "/api/v1/recovery/installation/authorize"
    assert client.post(endpoint, json={"confirm": True, "confirm_all_workspaces": True}).status_code == 409
    assert client.post(endpoint, headers=headers, json={"confirm": True, "confirm_all_workspaces": "true"}).status_code == 422
    response = client.post(endpoint, headers=headers, json={"confirm": True, "confirm_all_workspaces": True})
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    assert calls[0]["isolated"] is False  # the core must reject normal mode
    assert calls[0]["actor"] == headers["X-Lightwork-Recovery-Context"]
    def deny(*_):
        raise HTTPException(403, "global admin required")
    monkeypatch.setattr(recovery_api, "require_global_permission", deny)
    assert client.post(endpoint, headers=headers, json={"confirm": True, "confirm_all_workspaces": True}).status_code == 403
    assert client.get("/api/v1/recovery/installation").status_code == 403
    assert len(calls) == 1


def test_real_installation_core_refuses_normal_dashboard_authorization():
    client = api_client()
    response = client.post("/api/v1/recovery/installation/authorize", headers=context(client),
                           json={"confirm": True, "confirm_all_workspaces": True})
    assert response.status_code == 409
    assert "recovery mode" in response.json()["detail"]


def test_global_admin_gate_applies_before_recovery_reads_and_downloads(monkeypatch):
    def deny(*_):
        raise HTTPException(403, "global admin required")
    monkeypatch.setattr(recovery_api, "require_global_permission", deny)
    client = api_client()
    assert client.get("/workspace/recovery").status_code == 403
    assert client.get("/api/v1/recovery/status").status_code == 403
    assert client.post("/api/v1/recovery/key/download", json={"confirm": True}).status_code == 403


def test_key_creation_needs_same_account_context_and_explicit_true(monkeypatch):
    client = api_client()
    headers = context(client)
    path = "/api/v1/recovery/key"
    assert client.post(path, json={"confirm": True}).status_code == 409
    assert client.post(path, json={"confirm": False}, headers=headers).status_code == 400
    assert client.post(path, json={"confirm": "true"}, headers=headers).status_code == 422
    monkeypatch.setattr(recovery_api, "_authorization_principal", lambda _: "other-account")
    assert client.post(path, json={"confirm": True}, headers=headers).status_code == 409


def test_installation_retirement_requires_global_authority_context_and_explicit_review(monkeypatch):
    client = api_client()
    headers = context(client)
    path = "/api/v1/recovery/installation/" + "a" * 32 + "/retire"
    calls = []
    def retire(identifier, **kwargs):
        calls.append((identifier, kwargs))
        return {"retired": True, "id": identifier}
    monkeypatch.setattr(recovery_api.installation_recovery, "retire", retire)
    body = {"confirm": True, "sha256": "b" * 64}
    assert client.post(path, json=body).status_code == 409
    assert client.post(path, json={**body, "confirm": False}, headers=headers).status_code == 400
    assert client.post(path, json={**body, "confirm": "true"}, headers=headers).status_code == 422
    assert client.post(path, json={**body, "sha256": "changed"}, headers=headers).status_code == 422
    assert not calls
    result = client.post(path, json=body, headers=headers)
    assert result.status_code == 200 and result.json()["retired"]
    assert result.headers["cache-control"] == "private, no-store"
    assert calls == [("a" * 32, {"expected_sha256": "b" * 64, "actor": headers["X-Lightwork-Recovery-Context"], "confirm": True})]
    def deny(*_):
        raise HTTPException(403, "global admin required")
    monkeypatch.setattr(recovery_api, "require_global_permission", deny)
    assert client.post(path, json=body, headers=headers).status_code == 403
    assert len(calls) == 1


def test_installation_retirement_rejects_a_changed_review_without_claiming_success(monkeypatch):
    client = api_client()
    headers = context(client)
    def changed(*_, **kwargs):
        raise recovery_api.installation_recovery.MaintenanceError("The installation recovery point changed.")
    monkeypatch.setattr(recovery_api.installation_recovery, "retire", changed)
    result = client.post("/api/v1/recovery/installation/" + "a" * 32 + "/retire", json={"confirm": True, "sha256": "b" * 64}, headers=headers)
    assert result.status_code == 409
    assert "changed" in result.json()["detail"]


def test_encryption_key_export_requires_shared_key_consent_and_never_logs_keys():
    client = api_client()
    headers, _ = setup_snapshot(client)
    crypto_at_rest._KEY_PATH.parent.mkdir()
    crypto_at_rest._KEY_PATH.write_text(bytes(range(32)).hex())
    path = "/api/v1/recovery/encryption-keys/download"
    assert client.post(path, json={"confirm": True, "confirm_shared_keys": False}, headers=headers).status_code == 400
    assert client.post(path, json={"confirm": True, "confirm_shared_keys": "true"}, headers=headers).status_code == 422
    result = client.post(path, json={"confirm": True, "confirm_shared_keys": True}, headers=headers)
    assert result.status_code == 200 and result.content[:2] == b"PK"
    assert result.headers["cache-control"] == "private, no-store"
    assert bytes(range(32)).hex() not in client.get("/api/v1/recovery/status").text


def test_recovery_upload_preserves_same_origin_csrf_gate():
    client = api_client()
    headers = context(client)
    result = client.post("/api/v1/recovery/import", content=b"untrusted", headers={**headers, "Origin": "https://other.example.test", "Content-Type": "application/octet-stream"})
    assert result.status_code == 403


def test_snapshot_drill_and_download_never_enable_live_restore(workspace):
    client = api_client()
    headers, identifier = setup_snapshot(client)
    base = "/api/v1/recovery/snapshots/" + identifier
    assert client.post(base + "/inspect", headers=headers).json()["authenticated"]
    response = client.post(base + "/download", headers=headers)
    assert response.status_code == 200 and response.content[:2] == b"\x1f\x8b"
    result = client.post(base + "/drill", json={"confirm": True}, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["live_workspace_changed"] is False
    assert client.post(base + "/prepare", json={"confirm": True}, headers=headers).status_code == 409
    assert (workspace / "record.json").read_text() == '{"value":"before"}'


def test_import_size_and_paths_are_bounded(monkeypatch):
    client = api_client()
    headers = context(client)
    monkeypatch.setattr(recovery_api.recovery, "MAX_UPLOAD_BYTES", 8)
    response = client.post("/api/v1/recovery/import", content=b"more than eight bytes", headers={**headers, "Content-Type": "application/octet-stream"})
    assert response.status_code == 413
    assert client.post("/api/v1/recovery/snapshots/not-a-server-id/inspect", headers=headers).status_code == 409


def test_isolated_startup_skips_all_composed_normal_lifespans_and_blocks_work(workspace):
    async def forbidden(*_):
        raise AssertionError("A normal background lifespan must never start in recovery mode")
    app.router.lifespan_context = forbidden
    recovery_mode.configure(app, host="127.0.0.1")
    with api_client() as client:
        assert recovery_mode.isolated(app)
        assert client.get("/livez").status_code == 200
        assert client.get("/get-started").status_code == 503
        assert client.post("/api/v1/goals", json={}).status_code == 503
        assert client.get("/workspace/recovery").status_code == 200
        headers, identifier = setup_snapshot(client)
        (workspace / "record.json").write_text("after")
        response = client.post("/api/v1/recovery/snapshots/" + identifier + "/prepare", json={"confirm": True}, headers=headers)
        assert response.status_code == 200, response.text
        review = response.json()
        result = client.post("/api/v1/recovery/restore", json={"confirm": True, "review": review["review"], "target": review["target"]}, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["restored"] and result.json()["post_restore_verified"]
        assert (workspace / "record.json").read_text() == '{"value":"before"}'
    assert control_plane_lease.current() is None


def test_recovery_cannot_bind_an_external_network_interface():
    with pytest.raises(RuntimeError, match="loopback"):
        recovery_mode.configure(app, host="0.0.0.0")


def test_no_lease_means_no_restore_even_when_mode_flag_is_set():
    app.state.workspace_recovery_mode = True
    app.state.workspace_recovery_started = True
    control_plane_lease.release()
    assert recovery_mode.isolated(app) is False


@pytest.mark.parametrize("other_root", [False, True])
def test_existing_control_plane_lease_is_not_recovery_authority(tmp_path, other_root):
    control_plane_lease.acquire(tmp_path / "other" if other_root else None, role="control-plane")
    app.state.workspace_recovery_mode = True
    app.state.workspace_recovery_started = True
    assert recovery_mode.isolated(app) is False
    recovery_mode.configure(app, host="127.0.0.1")
    with pytest.raises(RuntimeError, match="existing control plane"):
        with api_client():
            pass
    assert control_plane_lease.current() is not None
