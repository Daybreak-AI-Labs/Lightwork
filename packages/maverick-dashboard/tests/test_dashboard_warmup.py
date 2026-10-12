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


def test_a_stopped_dashboard_does_not_build_the_schema(monkeypatch):
    """Shutdown ends the background warm-up: a stopped server (or a test's
    TestClient) must not keep spending seconds of CPU on its schema."""
    import threading

    from maverick_dashboard import request_activity

    built = []
    monkeypatch.setattr(dashboard.app, "openapi", lambda: built.append(1))
    stop = threading.Event()
    stop.set()
    assert dashboard._build_openapi_when_idle(quiet=0.0, sleep=lambda _s: None, stop=stop) is False
    assert built == []

    stop = threading.Event()
    request_activity.started()
    try:
        assert dashboard._build_openapi_when_idle(quiet=0.0, sleep=lambda _s: stop.set(), stop=stop) is False
    finally:
        request_activity.finished()
    assert built == [], "a stop that arrives while waiting for quiet also ends it"


def test_leaving_the_lifespan_stops_its_warm_up_thread(monkeypatch, tmp_path):
    """Every startup used to leave a warm-up thread behind that later built the
    OpenAPI schema in whatever process it was in (dozens per test worker)."""
    import threading
    import time

    from fastapi.testclient import TestClient
    from maverick import world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    dashboard._world_cache.clear()
    built = []
    monkeypatch.setattr(dashboard.app, "openapi", lambda: built.append(1))
    monkeypatch.setattr(dashboard, "_warm_pack_catalogue", lambda: None)

    def mine():
        return {t for t in threading.enumerate() if t.name == "lightwork-warm-hooks"}

    before = mine()
    with TestClient(dashboard.app, headers={"Origin": "http://testserver"}):
        started = mine() - before
    assert started, "startup runs the warm-up in the background"
    deadline = time.monotonic() + 120
    for thread in started:
        thread.join(max(0.0, deadline - time.monotonic()))
        assert not thread.is_alive(), "the warm-up thread outlived its dashboard"
    assert built == [], "a stopped dashboard never builds its schema"
    dashboard._world_cache.clear()


def test_a_garbage_round_unfreezes_collects_then_freezes(monkeypatch):
    """A full collection walked every warmed object (about 700,000), pausing
    whichever request triggered it for 350-450 ms. Each round reclaims what
    was frozen and has become garbage since, then freezes the survivors."""
    order = []
    monkeypatch.setattr(dashboard.gc, "unfreeze", lambda: order.append("unfreeze"))
    monkeypatch.setattr(dashboard.gc, "collect", lambda: order.append("collect") or 0)
    monkeypatch.setattr(dashboard.gc, "freeze", lambda: order.append("freeze"))
    dashboard._freeze_long_lived_objects()
    assert order == ["unfreeze", "collect", "freeze"]


def test_garbage_rounds_wait_for_a_quiet_moment_and_stop_with_the_dashboard(monkeypatch):
    import threading

    from maverick_dashboard import request_activity

    rounds, waits, sleeps = [], [], []
    monkeypatch.setattr(request_activity, "_in_flight", 1)  # restored after the test
    monkeypatch.setattr(request_activity, "_last_start", 0.0)
    monkeypatch.setattr(dashboard, "_freeze_long_lived_objects", lambda: rounds.append("round"))
    monkeypatch.setattr(dashboard.gc, "unfreeze", lambda: rounds.append("unfreeze"))

    def sleep(_seconds):
        sleeps.append(1)
        if len(sleeps) == 3:
            monkeypatch.setattr(request_activity, "_in_flight", 0)  # the request ends

    def wait(seconds):
        waits.append(seconds)
        return len(waits) == 2  # the dashboard stops during the second pause

    dashboard._collect_when_idle(threading.Event(), quiet=0.0, sleep=sleep, wait=wait, every=60.0)
    assert len(sleeps) == 3, "no round while a request is in flight"
    assert rounds == ["round", "round"] and waits == [60.0, 60.0]


def test_traffic_that_never_quietens_unfreezes_instead_of_keeping_garbage(monkeypatch):
    """A round that finds no quiet moment returns the frozen objects to
    ordinary collection, so nothing frozen is kept indefinitely."""
    import threading

    from maverick_dashboard import request_activity

    calls = []
    monkeypatch.setattr(request_activity, "_in_flight", 1)
    monkeypatch.setattr(dashboard, "_freeze_long_lived_objects", lambda: calls.append("round"))
    monkeypatch.setattr(dashboard.gc, "unfreeze", lambda: calls.append("unfreeze"))
    dashboard._collect_when_idle(threading.Event(), quiet=0.0, sleep=lambda _s: None,
                                 wait=lambda _s: True, limit=2.0)
    assert calls == ["unfreeze"]
    stop = threading.Event()
    stop.set()
    dashboard._collect_when_idle(stop, quiet=0.0, sleep=lambda _s: None, wait=lambda _s: False)
    assert calls == ["unfreeze"], "a stopped dashboard does nothing"


def test_the_warm_up_collects_only_after_building_the_schema(monkeypatch):
    import threading

    steps, targets = [], []

    class _Thread:
        def __init__(self, target, **_kwargs):
            targets.append(target)

        def start(self):
            pass

    async def _hooks():
        steps.append("hooks")

    monkeypatch.setattr(dashboard, "_warm_pack_catalogue", lambda: steps.append("packs"))
    monkeypatch.setattr("maverick.hooks.ensure_loaded", _hooks)
    monkeypatch.setattr(dashboard, "_warm_dashboard_caches", lambda stop=None: steps.append("caches") or {})
    monkeypatch.setattr(dashboard, "_build_openapi_when_idle", lambda stop=None: steps.append("schema") or True)
    monkeypatch.setattr(dashboard, "_collect_when_idle", lambda stop: steps.append(("gc", stop.is_set())))
    stop = dashboard._warm_goal_runtime(thread_factory=_Thread)
    assert isinstance(stop, threading.Event) and len(targets) == 1
    targets[0]()
    assert steps == ["packs", "hooks", "caches", "schema", ("gc", False)]
