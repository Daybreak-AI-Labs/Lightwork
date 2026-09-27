"""Learning buttons on the Learned and Learning pages: global-admin only,
kernel functions underneath, plain answers."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick_dashboard import app as dash_app
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_TENANT"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    dash_app._world_cache.clear()
    yield TestClient(dash_app.app, headers=ORIGIN)
    config.reset_config_cache()
    dash_app._world_cache.clear()


def _operator(monkeypatch):
    from maverick_dashboard import auth, rbac
    rbac.set_role("user:op1", "operator")
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:op1")


def test_every_learning_action_needs_a_global_admin(client, monkeypatch):
    _operator(monkeypatch)
    for method, path, body in (
        ("post", "/api/v1/learned/guidance/remove", {"model_id": "m", "line": "x"}),
        ("post", "/api/v1/learned/guidance/retire", {"older_than_days": 90}),
        ("post", "/api/v1/learned/guidance/probation-review", {"model_id": "m"}),
        ("get", "/api/v1/learning/snapshots", None),
        ("post", "/api/v1/learning/dream", {"preview": True}),
        ("post", "/api/v1/learning/rollback", {"snapshot": "latest"}),
        ("post", "/api/v1/learning/hindsight", {}),
        ("post", "/api/v1/learning/prove", {"baseline": [1], "treatment": [1]}),
    ):
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(path, **kwargs).status_code == 403, path


def test_remove_one_lesson(client, monkeypatch, tmp_path):
    from maverick import self_harness as sh
    store = tmp_path / "addenda.json"
    monkeypatch.setattr(sh, "_store_path", lambda: store)
    sh._write_addenda({"claude-x": "Operating guidance learned for this model:\n"
                                   "- keep this\n- drop this"}, store)
    r = client.post("/api/v1/learned/guidance/remove", json={"model_id": "claude-x", "line": "drop this"})
    assert r.json() == {"removed": True}
    assert sh.list_learned(store)["claude-x"] == ["keep this"]
    again = client.post("/api/v1/learned/guidance/remove", json={"model_id": "claude-x", "line": "drop this"})
    assert again.status_code == 404 and "already removed" in again.json()["detail"]


def test_retire_takes_only_the_offered_choices(client, monkeypatch):
    from maverick import self_harness as sh
    seen = []
    monkeypatch.setattr(sh, "retire_stale", lambda **kw: seen.append(kw) or 2)
    assert client.post("/api/v1/learned/guidance/retire", json={"older_than_days": 7}).status_code == 422
    assert client.post("/api/v1/learned/guidance/retire", json={"older_than_days": 90}).json() == {"retired": 2}
    assert seen == [{"older_than_days": 90.0}]


def test_snapshots_rollback_and_hindsight(client, monkeypatch):
    from maverick import dreaming
    from maverick_dashboard.learning_actions_api import snapshot_label
    assert snapshot_label("20260927T101500Z") == "27 Sep 2026, 10:15 UTC"
    monkeypatch.setattr(dreaming, "list_snapshots", list)
    assert client.get("/api/v1/learning/snapshots").json() == {"snapshots": []}
    r = client.post("/api/v1/learning/hindsight", json={})
    assert r.status_code == 409 and "saved point" in r.json()["detail"]
    r = client.post("/api/v1/learning/rollback", json={"snapshot": "20260101T000000Z"})
    assert r.status_code == 404
    assert client.post("/api/v1/learning/rollback", json={"snapshot": "../etc"}).status_code == 422
    monkeypatch.setattr(dreaming, "list_snapshots", lambda: ["20260101T000000Z"])
    monkeypatch.setattr(dreaming, "rollback_learning_state", lambda name: ["skills", "insights"])
    assert client.post("/api/v1/learning/rollback", json={"snapshot": "20260101T000000Z"}).json() == {"restored": 2}


def test_dream_preview_and_run_respect_the_switch(client, monkeypatch):
    from maverick import dreaming
    report = dreaming.DreamReport(goals_replayed=4, insights_written=1, skills_distilled=2)
    monkeypatch.setattr(dreaming, "dream_cycle_dry", lambda world: report)
    out = client.post("/api/v1/learning/dream", json={"preview": True}).json()
    assert out["preview"] is True and out["tasks_reviewed"] == 4 and out["skills_learned"] == 2
    monkeypatch.setattr(dreaming, "enabled", lambda: False)
    r = client.post("/api/v1/learning/dream", json={"preview": False})
    assert r.status_code == 409 and "Turn on Dreaming" in r.json()["detail"]


def test_prove_learning_verdict(client):
    r = client.post("/api/v1/learning/prove", json={"baseline": [0, 0, 0, 0, 0, 0],
                                                    "treatment": [1, 1, 1, 1, 1, 1]})
    assert r.json()["verdict"] == "improved" and r.json()["better"] == 6
    assert client.post("/api/v1/learning/prove", json={"baseline": [0, 1], "treatment": [1]}).status_code == 422
    assert client.post("/api/v1/learning/prove", json={"baseline": [2], "treatment": [1]}).status_code == 422


def test_learning_page_offers_run_now_to_admins(client):
    from test_support.plain_language import developer_terms, visible_text
    page = client.get("/learning").text
    assert developer_terms(page) == []
    text = visible_text(page)
    for label in ("Run learning now", "Preview what would change", "Learn now",
                  "Go back to this point", "Check now", "Check the results"):
        assert label in text, label
    assert "HTTP" not in page and "flip it" not in page
