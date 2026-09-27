"""Skill Store: plain wording, find/sort, and admin-managed catalog addresses."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from maverick import config, world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr("maverick.catalog.load_catalog", lambda kind, **_k: [])
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _client() -> TestClient:
    from maverick_dashboard.app import app

    return TestClient(app, headers={"Origin": "http://testserver"})


def _visible(html: str) -> str:
    main = html.split('<main id="content"', 1)[-1]
    main = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", main)
    return re.sub(r"(?s)<[^>]+>", " ", main)


def test_store_empty_state_offers_next_steps_in_plain_words():
    html = _client().get("/store").text
    text = _visible(html)
    assert "No skills to show" in text
    assert 'href="/skills"' in html
    assert "Built-in catalog" in text
    for word in ("deployment", "HTTP", "catalogs]", "config.toml", "sha256", "v0"):
        assert word not in text, word


def test_admin_adds_removes_and_resets_catalogs():
    from maverick.catalog import _configured_indexes

    c = _client()
    r = c.post("/store/catalogs", data={"action": "add", "url": "https://skills.example.com/catalog/"},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/store?saved=add"
    assert _configured_indexes() == ["https://skills.example.com/catalog"]
    html = c.get("/store?saved=add").text
    assert "https://skills.example.com/catalog" in html
    assert "Use the built-in catalog" in html

    r = c.post("/store/catalogs", data={"action": "add", "url": "http://insecure.example.com"},
               follow_redirects=False)
    assert r.headers["location"] == "/store?saved=bad-address"
    assert _configured_indexes() == ["https://skills.example.com/catalog"]

    r = c.post("/store/catalogs", data={"action": "reset"}, follow_redirects=False)
    assert r.status_code == 303
    from maverick.catalog import DEFAULT_INDEXES

    assert _configured_indexes() == [u.rstrip("/") for u in DEFAULT_INDEXES]


def test_catalog_changes_need_same_origin():
    from maverick_dashboard.app import app

    r = TestClient(app, headers={"Origin": "https://evil.example"}).post(
        "/store/catalogs", data={"action": "reset"}, follow_redirects=False)
    assert r.status_code == 403


def test_add_button_is_disabled_without_the_install_opt_in(monkeypatch):
    from types import SimpleNamespace

    entry = SimpleNamespace(name="weekly-report", to_dict=lambda: {
        "name": "weekly-report", "version": "1.0.0", "summary": "Writes the weekly report.",
        "author": "Acme", "verified": True, "install_count": 3})
    monkeypatch.setattr("maverick.catalog.load_catalog", lambda kind, **_k: [entry])
    monkeypatch.delenv("MAVERICK_ALLOW_SKILL_INSTALL", raising=False)
    html = _client().get("/store").text
    assert "Weekly report" in html and "Checked author" in html
    assert re.search(r'aria-label="Add Weekly report"\s+disabled', html)
    assert "switched off on this server" in html
