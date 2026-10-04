"""Settings search finds controls by the words people use, within page access."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick_dashboard import app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    config.reset_config_cache()
    app._world_cache.clear()
    yield TestClient(app.app, headers={"Origin": "http://testserver"})
    config.reset_config_cache()


def _items(client, query):
    response = client.get("/api/v1/settings/search", params={"q": query})
    assert response.status_code == 200
    return response.json()["items"]


def _signed_in(monkeypatch, role):
    from maverick_dashboard import auth, rbac

    rbac.set_role("user:alice", role)
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:alice")


@pytest.mark.parametrize("query", ["undone", "can't be undone", "destructive", "risky", "approval"])
def test_plain_words_find_the_setting_for_actions_that_cannot_be_undone(client, query):
    titles = [item["title"] for item in _items(client, query)]
    assert "Actions that can't be undone" in titles


def test_closest_titles_lead_and_dedicated_controls_come_before_all_settings(client):
    assert _items(client, "undone")[0]["title"] == "Actions that can't be undone"
    assert _items(client, "destructive")[0]["title"] == "Actions that can't be undone"
    assert _items(client, "theme")[0]["href"] == "/settings?focus=settings-theme"
    assert _items(client, "budget")[0]["href"] == "/settings?focus=settings-task-budget"


@pytest.mark.parametrize("query", ["ai service", "ai provider", "api key", "model key"])
def test_provider_key_words_lead_to_the_provider_keys_section(client, query):
    first = _items(client, query)[0]
    assert (first["title"], first["href"]) == ("AI provider keys", "/settings#settings-integrations")


def test_people_who_cannot_open_settings_get_no_organization_settings(client, monkeypatch):
    _signed_in(monkeypatch, "operator")
    assert _items(client, "undone") == []
    assert all(item["scope"] == "Personal" or item["href"].startswith("/workspace/")
               for item in _items(client, "a"))


def test_signed_in_people_find_their_notification_settings(client, monkeypatch):
    _signed_in(monkeypatch, "viewer")
    for query in ("notification", "notifications", "digest"):
        assert any(item["href"] == "/notifications#notification-preferences" for item in _items(client, query))


def test_one_letter_searches_skip_all_settings_but_keep_dedicated_controls(client):
    one = _items(client, "t")
    assert one and not any(item["href"].startswith("/settings/all") for item in one)
    assert any(item["href"].startswith("/settings/all") for item in _items(client, "th"))
