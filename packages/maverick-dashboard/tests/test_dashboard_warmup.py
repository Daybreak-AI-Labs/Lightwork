"""Start-up warm-up of the dashboard's first-hit caches (templates, OpenAPI,
packs, shield) and the Jinja environment's production settings."""
from __future__ import annotations

import os

from maverick_dashboard import app as dashboard


def test_warm_dashboard_caches_compiles_templates_and_openapi():
    env = dashboard.templates.env
    env.cache.clear()
    dashboard.app.openapi_schema = None

    timings = dashboard._warm_dashboard_caches()

    assert set(timings) == {"templates", "openapi", "domains", "shield"}
    cached = {getattr(t, "name", None) for t in env.cache.values()}
    for name in dashboard._WARM_TEMPLATES:
        assert name in cached, name
    assert dashboard.app.openapi_schema is not None


def test_warm_dashboard_caches_is_fail_open(monkeypatch):
    def _boom():
        raise RuntimeError("no schema today")

    monkeypatch.setattr(dashboard.app, "openapi", _boom)
    timings = dashboard._warm_dashboard_caches()
    assert "openapi" in timings  # the failed step is timed and skipped, not raised


def test_templates_do_not_auto_reload_outside_development(monkeypatch):
    monkeypatch.delenv("MAVERICK_DEBUG", raising=False)
    monkeypatch.delenv("MAVERICK_TEMPLATE_AUTO_RELOAD", raising=False)
    assert dashboard._templates_auto_reload() is False
    monkeypatch.setenv("MAVERICK_TEMPLATE_AUTO_RELOAD", "true")
    assert dashboard._templates_auto_reload() is True
    monkeypatch.delenv("MAVERICK_TEMPLATE_AUTO_RELOAD")
    monkeypatch.setenv("MAVERICK_DEBUG", "1")
    assert dashboard._templates_auto_reload() is True


def test_template_bytecode_cache_lives_under_maverick_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    cache = dashboard._template_bytecode_cache()
    assert cache is not None
    assert os.path.isdir(tmp_path / "cache" / "templates")
    assert dashboard.templates.env.bytecode_cache is not None
