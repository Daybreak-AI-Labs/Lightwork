"""A signed-in owner sees finished work through the same alert source as the inbox."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient


@pytest.fixture
def client_and_actor(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick_dashboard import app as dashboard
    from maverick_dashboard import auth

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    (tmp_path / "config.toml").write_text("[notifications]\npersonal_alerts = true\n", encoding="utf-8")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    config.reset_config_cache()
    dashboard._world_cache.clear()
    actor = {"principal": "user:alice"}

    async def bind(request: Request):
        request.state.principal = SimpleNamespace(principal=actor["principal"])
        return request.state.principal

    dashboard.app.dependency_overrides[auth.require_principal_in_request_context] = bind
    monkeypatch.setattr(auth, "role_for_principal", lambda _principal: "operator")
    try:
        with TestClient(dashboard.app, headers={"Origin": "http://testserver"}) as client:
            yield client, actor, dashboard._world(), tmp_path
    finally:
        dashboard.app.dependency_overrides.clear()
        dashboard._world_cache.clear()
        config.reset_config_cache()


@pytest.mark.parametrize("path", ["/goals", "/chat"])
def test_both_shells_serve_the_advertised_notification_badge(client_and_actor, path):
    client, _actor, _world, _home = client_and_actor
    page = client.get(path)
    assert page.status_code == 200
    scripts = re.findall(
        r'src="(/static/lightwork-notification-badge\.js(?:\?[^"]*)?)"', page.text,
    )
    assert len(scripts) == 1
    assert "?v=" in scripts[0]

    response = client.get(scripts[0])
    assert response.status_code == 200
    assert response.headers["content-type"].split(";", 1)[0] == "application/javascript"
    source = (Path(__file__).resolve().parents[1]
              / "maverick_dashboard/static/lightwork-notification-badge.js")
    assert response.content == source.read_bytes()


def test_finished_task_bell_to_owner_inbox_and_seen_state(client_and_actor):
    client, actor, world, _tmp_path = client_and_actor
    page = client.get("/chat")
    assert page.status_code == 200
    assert 'id="personal-notification-link"' in page.text
    assert 'data-enabled="true"' in page.text
    assert client.get("/api/v1/notifications/personal/summary").json()["unseen"] == 0

    own = world.create_goal("Owner's private task", owner="user:alice")
    other = world.create_goal("Other person's private task", owner="user:bob")
    world.set_goal_status(own, "done", result="Owner's private result")
    world.set_goal_status(other, "done", result="Other person's private result")
    summary = client.get("/api/v1/notifications/personal/summary")
    assert summary.status_code == 200
    assert summary.headers["Cache-Control"] == "private, no-store"
    assert summary.json()["unseen"] == 1
    assert "private task" not in summary.text and "private result" not in summary.text

    inbox = client.get("/api/v1/notifications/personal").json()
    assert [(row["goal_id"], row["title"]) for row in inbox["items"]] == [
        (own, "Owner's private task")]
    marked = client.post("/api/v1/notifications/personal/seen", json={
        "context": inbox["context"], "item_id": inbox["items"][0]["id"],
    })
    assert marked.status_code == 200
    assert marked.json()["items"][0]["seen_at"] is not None, marked.json()["items"]
    assert client.get("/api/v1/notifications/personal/summary").json()["unseen"] == 0

    actor["principal"] = "user:bob"
    assert client.get("/api/v1/notifications/personal/summary").json()["unseen"] == 0
    assert "Owner's private task" not in client.get("/notifications").text


def test_disabled_alerts_have_a_working_explanation_page(client_and_actor):
    from maverick import config

    client, _actor, _world, home = client_and_actor
    (home / "config.toml").write_text("[notifications]\npersonal_alerts = false\n", encoding="utf-8")
    config.reset_config_cache()
    page = client.get("/notifications")
    assert page.status_code == 200
    assert "Personal task alerts are turned off" in page.text
    assert 'id="notification-preferences"' not in page.text
    assert client.get("/api/v1/notifications/personal/summary").status_code == 403
