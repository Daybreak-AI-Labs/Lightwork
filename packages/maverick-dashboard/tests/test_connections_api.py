"""Dashboard connections API: CRUD over sealed named connections (gated)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONNECTIONS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr("maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p))
    monkeypatch.setattr("maverick.connections.seal_text_for_tenant", lambda t, s: s.encode())
    monkeypatch.setattr("maverick.connections.unseal_text_for_tenant", lambda t, b: b.decode())


def test_gated_off_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_CONNECTIONS", raising=False)
    monkeypatch.setattr("maverick.connections.enabled", lambda: False)
    assert _client().get("/api/v1/connections").status_code == 403


def test_create_list_delete_and_token_never_returned(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    r = c.post("/api/v1/connections", json={
        "name": "Zendesk Prod", "connector": "zendesk",
        "base_url": "https://x.zendesk.com", "token": "sekret"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "zendesk-prod" and body["has_token"] is True
    assert "token" not in body
    listed = c.get("/api/v1/connections").json()["connections"]
    assert listed[0]["name"] == "zendesk-prod" and "token" not in listed[0]
    assert c.delete("/api/v1/connections/zendesk-prod").status_code == 200
    assert c.get("/api/v1/connections").json()["connections"] == []


def test_bad_name_is_400(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().post("/api/v1/connections", json={"name": "!!!", "token": "x"})
    assert r.status_code == 400


def test_test_endpoint_reports_unreachable_without_crashing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    c.post("/api/v1/connections", json={"name": "n", "base_url": "https://127.0.0.1:0", "token": "t"})
    r = c.post("/api/v1/connections/n/test")
    assert r.status_code == 200 and r.json()["ok"] is False
    assert c.post("/api/v1/connections/nope/test").status_code == 404


def test_test_endpoint_blocks_ssrf_to_internal_hosts(monkeypatch, tmp_path):
    # The probe carries the sealed token, so it MUST go through the SSRF-safe
    # path: a base_url pointing at cloud metadata / a private host is refused
    # (BlockedHost) BEFORE any request leaves -- no internal-network oracle, no
    # token forwarded via a redirect.
    _isolate(monkeypatch, tmp_path)
    c = _client()
    for host in ("http://169.254.169.254/latest/meta-data/",
                 "http://127.0.0.1/", "http://10.0.0.5/"):
        c.post("/api/v1/connections", json={"name": "ssrf", "base_url": host, "token": "t"})
        r = c.post("/api/v1/connections/ssrf/test")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is False and "not permitted" in j["detail"], (host, j)


def test_connections_page_renders(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().get("/connections")
    assert r.status_code == 200 and 'id="cx-form"' in r.text
