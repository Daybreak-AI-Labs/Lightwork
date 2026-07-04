"""The Learning surface: status of the learning systems + the accumulated,
per-tenant moat (Operating Record + grounded outcomes + self-taught skills).

Read-only and owner-scoped; the numbers on /learning and /api/v1/learning match.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})

_TABLE = "| Week | Net |\n| --- | ---: |\n| W1 | 300 |\n"


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import consequence, world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    consequence.reset_shared()
    return world_model.WorldModel(tmp_path / "world.db")


def test_learning_api_reports_components_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MAVERICK_CONSEQUENCE", raising=False)
    _isolate(tmp_path, monkeypatch)
    data = client.get("/api/v1/learning").json()
    keys = {c["key"] for c in data["components"]}
    assert {"consequence", "reflexion", "dreaming", "capture", "data_engine"} <= keys
    assert data["components_total"] == len(data["components"])
    # the kernel is stateless by default: nothing on
    assert all(c["on"] is False for c in data["components"])
    assert data["components_on"] == 0
    assert set(data["accumulated"]) >= {
        "decisions", "human_decisions", "approvals",
        "grounded_outcomes", "learned_capabilities", "departments",
    }


def test_consequence_flag_flips_component_on(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_CONSEQUENCE", "1")
    _isolate(tmp_path, monkeypatch)
    data = client.get("/api/v1/learning").json()
    comp = {c["key"]: c["on"] for c in data["components"]}
    assert comp["consequence"] is True
    assert data["components_on"] >= 1


def test_signoff_shows_up_as_a_grounded_outcome(tmp_path, monkeypatch):
    # End-to-end moat proof: a human sign-off becomes a grounded outcome the
    # Learning view counts -- the loop from human judgement to accumulated asset.
    monkeypatch.setenv("MAVERICK_CONSEQUENCE", "1")
    w = _isolate(tmp_path, monkeypatch)
    gid = w.create_goal("Refresh the cash forecast", "", domain="finance_cash13w")
    w.set_goal_status(gid, "done", result=_TABLE)
    w.start_episode(gid)
    assert client.get("/api/v1/learning").json()["accumulated"]["grounded_outcomes"] == 0
    r = client.post(f"/api/v1/goals/{gid}/signoff", json={"decision": "approved"})
    assert r.status_code == 200
    assert client.get("/api/v1/learning").json()["accumulated"]["grounded_outcomes"] == 1


def test_enable_all_learning_flips_every_component(tmp_path, monkeypatch):
    # env vars unset so the config overlay controls; one POST turns the whole
    # loop on, and it persists + reverses.
    for v in ("MAVERICK_CONSEQUENCE", "MAVERICK_REFLEXION", "MAVERICK_DREAMING",
              "MAVERICK_TRAJECTORY_CAPTURE", "MAVERICK_DATA_ENGINE"):
        monkeypatch.delenv(v, raising=False)
    from maverick.config import reset_config_cache
    self = _isolate(tmp_path, monkeypatch)  # noqa: F841 (world not needed here)
    reset_config_cache()
    total = client.get("/api/v1/learning").json()["components_total"]
    assert client.get("/api/v1/learning").json()["components_on"] == 0
    r = client.post("/api/v1/learning", json={"enabled": True})
    assert r.status_code == 200 and r.json()["components_on"] == total
    assert client.get("/api/v1/learning").json()["components_on"] == total   # persisted
    client.post("/api/v1/learning", json={"enabled": False})
    assert client.get("/api/v1/learning").json()["components_on"] == 0


def test_learning_page_has_enable_button(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    t = client.get("/learning").text
    assert 'id="lrn-enable"' in t and "Turn all learning on" in t


def test_learning_page_renders_the_moat(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    t = client.get("/learning").text
    assert "learning systems are on" in t
    assert "What your workforce has accumulated" in t
    assert "grounded outcomes learned" in t
