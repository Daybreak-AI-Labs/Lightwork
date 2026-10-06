"""Start-up warm-up of the dashboard's first-hit caches (templates, OpenAPI,
packs, shield) and the Jinja environment's production settings."""
from __future__ import annotations

import os

from maverick_dashboard import app as dashboard


def test_warm_dashboard_caches_compiles_templates_and_routes_but_not_the_schema(monkeypatch):
    """Routing needs every included route's context (FastAPI builds them on
    the first request otherwise, about 2.5 s); the OpenAPI schema (about
    4.3 s) is only for /openapi.json and is built when the server is idle."""
    from maverick_dashboard import route_identity

    env = dashboard.templates.env
    env.cache.clear()
    dashboard.app.openapi_schema = None
    walked = []
    real = route_identity.iter_compatible_route_contexts
    monkeypatch.setattr(route_identity, "iter_compatible_route_contexts",
                        lambda routes: walked.append(1) or real(routes))

    timings = dashboard._warm_dashboard_caches()

    assert set(timings) == {"templates", "routes", "domains", "agents", "shield"}
    assert list(timings)[:3] == ["domains", "agents", "routes"], "the pack catalogue and routes warm first"
    assert walked, "every route context is materialised"
    cached = {getattr(t, "name", None) for t in env.cache.values()}
    for name in dashboard._WARM_TEMPLATES:
        assert name in cached, name
    assert dashboard.app.openapi_schema is None, "the schema is not built with the request-path caches"


def test_warm_dashboard_caches_is_fail_open(monkeypatch):
    from maverick_dashboard import route_identity

    def _boom(_routes):
        raise RuntimeError("no routes today")

    monkeypatch.setattr(route_identity, "iter_compatible_route_contexts", _boom)
    timings = dashboard._warm_dashboard_caches()
    assert "routes" in timings  # the failed step is timed and skipped, not raised


def test_the_schema_waits_for_a_quiet_moment(monkeypatch):
    """The OpenAPI build holds the CPU for seconds, so it starts only when no
    request is in flight and none has started for a moment."""
    from maverick_dashboard import request_activity

    built = []
    monkeypatch.setattr(dashboard.app, "openapi", lambda: built.append(1))
    request_activity.started()
    waits = []

    def fake_sleep(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            request_activity.finished()

    assert dashboard._build_openapi_when_idle(quiet=0.0, sleep=fake_sleep) is True
    assert built == [1] and len(waits) >= 2, "it waited while a request was in flight"


def test_request_activity_counts_requests_in_flight():
    import asyncio

    from maverick_dashboard import request_activity

    seen = []

    async def inner(scope, receive, send):
        seen.append(request_activity.in_flight())

    middleware = request_activity.RequestActivityMiddleware(inner)
    before = request_activity.in_flight()
    asyncio.run(middleware({"type": "http"}, None, None))
    assert seen == [before + 1] and request_activity.in_flight() == before


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


def test_the_pack_catalogue_warms_before_the_slower_steps(monkeypatch):
    """Ctrl+K's first agent lookup needs the parsed pack catalogue and the
    tool inventory; both are person-independent and warm before hooks and
    the OpenAPI build, so the first search after start answers in time."""
    from maverick_dashboard import route_identity

    order = []
    monkeypatch.setattr("maverick.domain_edit.list_agents", lambda: order.append("agents") or [])
    monkeypatch.setattr(route_identity, "iter_compatible_route_contexts", lambda _routes: order.append("routes") or [])
    dashboard._warm_dashboard_caches()
    assert order.index("agents") < order.index("routes")
