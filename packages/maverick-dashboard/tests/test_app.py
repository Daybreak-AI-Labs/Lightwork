"""Dashboard smoke tests.

No network -- just verify the FastAPI app constructs, routes are
registered, and templates render with empty data.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app)


def test_livez():
    """Cheap liveness probe always 200s."""
    resp = client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_returns_check_breakdown(monkeypatch):
    """Deep healthz returns a per-check map, may be 200 or 503."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    resp = client.get("/healthz")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert "checks" in body


def test_readyz_deep_check_client_binding(tmp_path, monkeypatch):
    """/readyz fails (503 not_ready) when client binding is enforced but unset —
    a pod that is up yet refuses all work. A plain /healthz wouldn't catch it."""
    from maverick import client, world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("MAVERICK_CLIENT_ENFORCE", "1")
    monkeypatch.delenv("MAVERICK_CLIENT_ID", raising=False)
    client.reset_client_cache()
    try:
        resp = client_get_readyz()
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert "enforced" in body["checks"]["client_binding"]
    finally:
        client.reset_client_cache()


def test_readyz_ok_when_bound(tmp_path, monkeypatch):
    from maverick import client, world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("MAVERICK_CLIENT_ENFORCE", "1")
    monkeypatch.setenv("MAVERICK_CLIENT_ID", "acme")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    client.reset_client_cache()
    try:
        resp = client_get_readyz()
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
    finally:
        client.reset_client_cache()


def client_get_readyz():
    return client.get("/readyz")


@pytest.mark.parametrize(
    ("module_name", "attribute", "check_name"),
    [
        ("client", "client_binding_enforced", "client_binding"),
        ("shield_policy", "shield_required", "shield"),
        ("agent_trust", "load_trust_state", "agent_trust"),
    ],
)
def test_readiness_deep_check_errors_fail_closed(
    monkeypatch, module_name, attribute, check_name
):
    """An unreadable trust posture is not evidence that a pod is ready."""
    from maverick import agent_trust, shield_policy
    from maverick import client as client_module
    from maverick_dashboard import app as dash_app

    modules = {
        "client": client_module,
        "shield_policy": shield_policy,
        "agent_trust": agent_trust,
    }
    monkeypatch.setattr(client_module, "client_binding_enforced", lambda: False)
    monkeypatch.setattr(shield_policy, "shield_required", lambda: False)
    monkeypatch.setattr(agent_trust, "load_trust_state", lambda: (False, {}))

    def _raise_probe_error():
        raise RuntimeError("probe unavailable")

    monkeypatch.setattr(modules[module_name], attribute, _raise_probe_error)
    ok, checks = dash_app._readiness_deep_checks()

    assert ok is False
    assert checks[check_name] == "unknown: RuntimeError"


def test_overview_renders(tmp_path, monkeypatch):
    # Point the WorldModel at a fresh tmp DB so we don't depend on
    # ~/.maverick/world.db existing on the runner.
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    resp = client.get("/overview")
    assert resp.status_code == 200
    assert "Lightwork" in resp.text
    assert "overview" in resp.text or "goals" in resp.text


def test_goals_page_renders(tmp_path, monkeypatch):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    resp = client.get("/goals")
    assert resp.status_code == 200


def test_skills_page_renders(tmp_path, monkeypatch):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    resp = client.get("/skills")
    assert resp.status_code == 200


def test_compartments_page_renders(tmp_path, monkeypatch):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    resp = client.get("/compartments")
    assert resp.status_code == 200
    assert "compartments" in resp.text.lower()
    # a pack that declares an output contract surfaces its deliverable
    assert "Delivers:" in resp.text
    # the built-in finance packs are findable through the search box
    found = client.get("/compartments", params={"q": "finance"})
    assert found.status_code == 200
    assert "finance" in found.text
    assert "matching agents and specialists" in found.text
    # the deliverable of a specific pack is on its search result
    cash = client.get("/compartments", params={"q": "finance_cash13w"})
    assert "13-week cash forecast" in cash.text


def test_compartments_page_is_searched_and_paginated(tmp_path, monkeypatch):
    """W3-30: unscoped, the roster is 2,000+ packs and rendered every
    capability envelope into one multi-megabyte page. It now renders one
    bounded slice, like /tools."""
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    resp = client.get("/compartments", params={"per_page": 5})
    assert resp.status_code == 200
    assert len(resp.content) < 300_000, len(resp.content)
    assert resp.text.count('<tr>') <= 6          # header row + 5 packs
    assert "Showing 1–5 of" in resp.text
    assert "Page 1 of" in resp.text and "Next" in resp.text
    second = client.get("/compartments", params={"per_page": 5, "page": 2})
    assert second.status_code == 200
    assert "Showing 6–10 of" in second.text and "Previous" in second.text
    # Beyond the last page is a clean 400, not an empty table.
    assert client.get("/compartments", params={"per_page": 100, "page": 100_000}).status_code == 400
    # A search narrows the slice and is reflected in the pager links.
    found = client.get("/compartments", params={"q": "finance", "per_page": 5})
    assert found.status_code == 200
    assert "finance" in found.text
    assert "q=finance" in found.text or "Page 1 of 1" in found.text


def test_facts_page_renders(tmp_path, monkeypatch):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    resp = client.get("/facts")
    assert resp.status_code == 200


def test_shell_assets_are_fingerprinted_and_long_cached(tmp_path, monkeypatch):
    """W3-77: the shell CSS/JS links carry a content hash and the versioned
    URL is cached for a year; unversioned or stale requests revalidate
    (``no-cache`` plus a content ETag) so an upgrade can never pin an old
    bundle."""
    import re

    from maverick import world_model
    from maverick_dashboard.app import STATIC_IMMUTABLE_CACHE, static_version

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    page = client.get("/overview").text
    links = re.findall(r'/static/(lightwork[\w.-]+)\?v=([0-9a-f]{8})', page)
    assert links, "no fingerprinted shell asset links rendered"
    assert ("lightwork.css", static_version("lightwork.css")) in links
    assert ("lightwork-ui.js", static_version("lightwork-ui.js")) in links
    for name, version in links:
        assert version == static_version(name)

    versioned = client.get(f"/static/lightwork.css?v={static_version('lightwork.css')}")
    assert versioned.status_code == 200
    assert versioned.headers["cache-control"] == STATIC_IMMUTABLE_CACHE
    etag = f'W/"{static_version("lightwork.css")}"'
    plain = client.get("/static/lightwork.css")
    assert plain.headers["cache-control"] == "public, no-cache"
    assert plain.headers["etag"] == etag
    stale = client.get("/static/lightwork.css?v=00000000")
    assert stale.headers["cache-control"] == "public, no-cache"
    revalidated = client.get("/static/lightwork.css", headers={"If-None-Match": etag})
    assert revalidated.status_code == 304
    assert static_version("does-not-exist.js") == ""


def test_responses_are_gzipped_when_the_client_accepts_it(tmp_path, monkeypatch):
    from maverick import world_model
    from maverick_dashboard import app as app_module

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    r = client.get("/static/lightwork.css", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert "lightwork" in r.text                      # transparently decoded
    # The knob: env wins, then [dashboard] compress, default on.
    monkeypatch.setenv("MAVERICK_DASHBOARD_COMPRESS", "0")
    assert app_module._compress_enabled() is False
    monkeypatch.setenv("MAVERICK_DASHBOARD_COMPRESS", "1")
    assert app_module._compress_enabled() is True
    monkeypatch.delenv("MAVERICK_DASHBOARD_COMPRESS")
    import maverick.config as cfg
    monkeypatch.setattr(cfg, "load_config", lambda: {"dashboard": {"compress": False}})
    assert app_module._compress_enabled() is False
    monkeypatch.setattr(cfg, "load_config", lambda: {"dashboard": {}})
    assert app_module._compress_enabled() is True
