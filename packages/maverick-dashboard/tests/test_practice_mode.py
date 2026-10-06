"""Practice mode in the dashboard: a marker on every screen, practice work kept
apart, live changes refused, and one action back to live (ONB-4)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick_dashboard import app as dash_app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    dash_app._world_cache.clear()
    yield TestClient(dash_app.app, headers={"Origin": "http://testserver"}), tmp_path
    config.reset_config_cache()
    dash_app._world_cache.clear()


def _start(app):
    started = app.post("/api/v1/practice", json={"on": True})
    assert started.status_code == 200, started.text
    assert started.json() == {"on": True, "available": True, "reason": ""}
    assert app.cookies.get("lw_practice") == "1"


def test_the_switch_marks_every_screen_and_back_to_live_removes_it(client):
    app, _tmp = client
    assert app.get("/api/v1/practice").json()["on"] is False
    start_page = app.get("/start").text
    assert "Start practice mode" in start_page and 'id="practice-banner"' not in start_page
    _start(app)
    for path in ("/", "/start", "/chat", "/approvals", "/settings"):
        page = app.get(path)
        assert page.status_code == 200, (path, page.status_code)
        assert 'id="practice-banner"' in page.text and "Back to live" in page.text, path
        assert 'class="is-practice ' in page.text or 'chat-shell is-practice ' in page.text, path
    assert "Start practice mode" not in app.get("/start").text
    assert app.get("/api/v1/practice").json() == {"on": True, "available": True, "reason": "", "outbox": []}
    left = app.post("/api/v1/practice", json={"on": False})
    assert left.status_code == 200 and left.json()["on"] is False
    assert 'id="practice-banner"' not in app.get("/").text


def test_practice_work_never_appears_in_live_work(client):
    app, _tmp = client
    from maverick import practice
    from maverick_dashboard._shared import _world

    live_goal = _world().create_goal("Live: pay the March invoices", "real work")
    with practice.scope():
        practice_goal = _world().create_goal("Practice: pay the March invoices", "practice")
    _start(app)
    practised = {g["title"] for g in app.get("/api/v1/goals").json()}
    app.post("/api/v1/practice", json={"on": False})
    live = {g["title"] for g in app.get("/api/v1/goals").json()}
    assert "Practice: pay the March invoices" in practised and "Live: pay the March invoices" not in practised
    assert "Live: pay the March invoices" in live and "Practice: pay the March invoices" not in live
    assert live_goal and practice_goal


def test_live_changes_are_refused_until_back_to_live(client):
    app, _tmp = client
    _start(app)
    refused = app.post("/api/v1/agents/proc_po_chaser/validate", json={"persona": "Changed"})
    assert refused.status_code == 409 and refused.headers["X-Lightwork-Reason"] == "practice"
    assert "Back to live" in refused.json()["detail"]
    assert app.post("/api/v1/practice", json={"on": False}).status_code == 200
    assert app.post("/api/v1/agents/proc_po_chaser/validate", json={"persona": "Changed"}).status_code != 409


def test_what_practice_held_back_is_listed_for_the_person(client):
    app, _tmp = client
    from maverick import notifications, practice

    with practice.scope():
        notifications.send_email("pat@example.com", "Payment sent", "Body")
    _start(app)
    held = app.get("/api/v1/practice").json()["outbox"]
    assert held and held[0]["kind"] == "email" and "Payment sent" in held[0]["detail"]


def test_practice_cannot_start_when_it_is_turned_off(client):
    app, tmp = client
    from maverick import config

    (tmp / "config.toml").write_text("[practice]\nenable = false\n", encoding="utf-8")
    config.reset_config_cache()
    refused = app.post("/api/v1/practice", json={"on": True})
    assert refused.status_code == 409 and "turned off" in refused.json()["detail"]
    # A stale switch from before is ignored: the person is live.
    app.cookies.set("lw_practice", "1")
    assert 'id="practice-banner"' not in app.get("/").text
