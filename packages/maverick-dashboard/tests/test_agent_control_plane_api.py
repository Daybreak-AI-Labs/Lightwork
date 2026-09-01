"""HTTP contract of the Agent Control Plane console API (CSRF, roles, the
plane's own gate, and that a verb's outcome is the core module's, unlaundered)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})
BASE = "/api/v1/agent-control-plane"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import audit, config, killswitch, revocation, world_model
    from maverick_dashboard import api

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setenv("MAVERICK_HALT_FILE", str(tmp_path / "HALT"))
    monkeypatch.setenv("MAVERICK_CONTROL_PLANE", "1")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    for name in ("MAVERICK_CONFIG", "MAVERICK_DASHBOARD_TOKEN",
                 "MAVERICK_DASHBOARD_REQUIRE_AUTH", "MAVERICK_OIDC_ENABLED",
                 "MAVERICK_PROXY_AUTH", "MAVERICK_TENANT", "MAVERICK_TENANT_BY_USER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(audit, "record", lambda *a, **k: True)
    config.reset_config_cache()
    revocation.reset_shared()
    api._world_cache.clear()
    yield
    try:
        killswitch.clear()
    except Exception:
        pass
    config.reset_config_cache()
    revocation.reset_shared()
    api._world_cache.clear()


def test_status_and_roster_read_on_an_enabled_plane():
    r = client.get(f"{BASE}/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True and body["halted"] is False
    assert body["schema"] == "lightwork.agent-control-plane-status.v1"
    r = client.get(f"{BASE}/agents")
    assert r.status_code == 200 and r.json() == {"items": [], "count": 0}


def test_suspend_then_resume_round_trips_through_the_revocation_registry():
    from maverick.revocation import is_revoked
    r = client.post(f"{BASE}/agents/user:local/suspend", json={"reason": "runaway"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["verb"] == "suspend"
    assert is_revoked("user:local")
    rows = client.get(f"{BASE}/agents").json()["items"]
    assert rows and rows[0]["principal"] == "user:local" and rows[0]["state"] == "suspended"
    assert "runaway" in rows[0]["revoked_reason"]
    r = client.post(f"{BASE}/agents/user:local/resume")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert not is_revoked("user:local")


def test_a_disabled_plane_answers_403_on_verbs_and_still_reads(monkeypatch):
    from maverick import config
    from maverick.revocation import is_revoked
    monkeypatch.delenv("MAVERICK_CONTROL_PLANE")
    config.reset_config_cache()
    r = client.post(f"{BASE}/agents/user:local/suspend", json={"reason": "x"})
    assert r.status_code == 403 and r.json()["detail"] == "control_plane_disabled"
    assert not is_revoked("user:local")
    assert client.get(f"{BASE}/status").json()["enabled"] is False


def test_bad_input_is_422_before_anything_lands():
    from maverick.revocation import is_revoked
    assert client.post(f"{BASE}/agents/user:local/suspend", json={}).status_code == 422
    assert client.post(f"{BASE}/agents/user:local/suspend",
                       json={"reason": "x", "extra": 1}).status_code == 422
    assert client.post(f"{BASE}/agents/..bad/suspend", json={"reason": "x"}).status_code == 422
    assert client.post(f"{BASE}/agents/user:local/attenuate",
                       json={"max_risk": "extreme"}).status_code == 422
    assert not is_revoked("user:local")


def test_attenuate_maps_core_refusals_to_http_codes():
    r = client.post(f"{BASE}/agents/user:local/attenuate", json={"max_risk": "low"})
    assert r.status_code == 422 and "external" in r.json()["detail"]
    r = client.post(f"{BASE}/agents/agent:ghost/attenuate", json={"max_risk": "low"})
    assert r.status_code == 404


def test_halt_and_clear_are_admin_verbs_over_the_killswitch():
    from maverick import killswitch
    r = client.post(f"{BASE}/halt", json={"reason": "incident"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert killswitch.is_active()
    assert client.get(f"{BASE}/status").json()["halted"] is True
    r = client.post(f"{BASE}/clear-halt")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert not killswitch.is_active()


def test_cross_origin_mutations_are_refused_by_the_csrf_check():
    from maverick.revocation import is_revoked
    foreign = TestClient(app, headers={"Origin": "http://evil.example"})
    r = foreign.post(f"{BASE}/agents/user:local/suspend", json={"reason": "x"})
    assert r.status_code == 403
    assert not is_revoked("user:local")


def test_ledger_reads_the_forensic_timeline():
    r = client.get(f"{BASE}/agents/user:local/ledger?limit=5")
    assert r.status_code == 200, r.text
    assert r.json() == {"principal": "user:local", "items": [], "count": 0}


def test_roles_gate_the_verbs(monkeypatch):
    from maverick.revocation import is_revoked
    from maverick_dashboard import agent_control_plane_api as api_mod

    def _only(allowed):
        def _require(request, permission):
            if permission not in allowed:
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="insufficient role")
        return _require

    monkeypatch.setattr(api_mod, "require_permission", _only({"view", "operate"}))
    assert client.post(f"{BASE}/halt", json={"reason": "x"}).status_code == 403
    assert client.post(f"{BASE}/agents/user:local/contain", json={"reason": "x"}).status_code == 403
    assert client.get(f"{BASE}/agents/user:local/ledger").status_code == 403
    assert client.post(f"{BASE}/agents/user:local/suspend", json={"reason": "x"}).status_code == 200
    assert is_revoked("user:local")
