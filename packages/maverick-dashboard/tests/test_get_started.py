"""The /start Get Started page: a live setup checklist that self-completes as
the workspace gets configured (provider -> built -> run/automated)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _route_state(configured: bool) -> dict:
    return {
        "status": "configured" if configured else "incomplete",
        "configured": configured,
        "detail": (
            "Selected routes have offline prerequisites."
            if configured
            else "Selected routes are missing offline prerequisites."
        ),
        "evidence": {
            "kind": "offline_configuration",
            "live_provider_verified": False,
        },
    }


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    import maverick.templates as tpl
    monkeypatch.setattr(tpl, "USER_TEMPLATES", tmp_path / ".maverick" / "templates")
    import maverick.domain_edit as de
    monkeypatch.setattr(de, "list_agents", list)   # no overridden agents in a fresh ws


def test_fresh_workspace_nothing_done(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import onboarding_state

    monkeypatch.setattr(
        onboarding_state,
        "selected_model_route_state",
        lambda: _route_state(False),
    )
    t = _client().get("/start").text
    assert "Connect your AI service" in t and "Build a workflow or agent" in t
    assert "0 of 3 done" in t and 'role="progressbar"' in t
    assert "Installation checks" in t
    assert "maverick preflight --json" in t
    assert "SYNTHETIC DEMO DATA" in t
    assert "Every generated record stays labeled synthetic" in t
    assert "production assurance" in t


def test_checklist_reflects_progress(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import onboarding_state

    monkeypatch.setattr(
        onboarding_state,
        "selected_model_route_state",
        lambda: _route_state(True),
    )
    t = _client().get("/start").text
    assert "1 of 3 done" in t and "gs__step--done" in t


def test_get_started_reachable(monkeypatch, tmp_path):
    # Get started is folded out of the sidebar in the moderate declutter (it's
    # onboarding, linked from the empty states), but still renders at /start.
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import onboarding_state

    monkeypatch.setattr(
        onboarding_state,
        "selected_model_route_state",
        lambda: _route_state(False),
    )
    r = _client().get("/start")
    assert r.status_code == 200
    assert 'page-title">Get started' in r.text


def test_runtime_completion_is_gated_separately_from_assurance(monkeypatch):
    from maverick import operator_preflight
    from maverick_dashboard import onboarding_state

    class Report:
        def __init__(self, ready):
            self.ready = ready

        def to_dict(self):
            return {
                "ready": self.ready,
                "checks": [],
                "next_action": "none",
            }

    class World:
        @staticmethod
        def list_goals(limit=1):
            return [{"id": 1}][:limit]

    monkeypatch.setattr(
        onboarding_state,
        "selected_model_route_state",
        lambda: _route_state(True),
    )
    monkeypatch.setattr(onboarding_state, "_has_user_template", lambda: True)
    monkeypatch.setattr(onboarding_state, "_has_user_agent", lambda: False)
    monkeypatch.setattr(onboarding_state, "_has_automation", lambda: False)
    monkeypatch.setattr(
        operator_preflight,
        "collect",
        lambda profile: Report(profile == "run"),
    )

    state = onboarding_state.build(World())
    assert state["runtime_ready"] is True
    assert state["assurance_ready"] is False
    assert state["runtime_all_set"] is True

    monkeypatch.setattr(
        operator_preflight,
        "collect",
        lambda _profile: Report(False),
    )
    blocked = onboarding_state.build(World())
    assert blocked["done_count"] == blocked["total"]
    assert blocked["runtime_all_set"] is False


def test_unconfigured_root_enters_setup_and_chat_action_is_disabled(
    monkeypatch,
    tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import app as dashboard
    from maverick_dashboard import onboarding_state

    monkeypatch.setattr(onboarding_state, "selected_model_routes_ready", lambda: False)
    dashboard._world_cache.clear()
    client = _client()

    landing = client.get("/", follow_redirects=False)
    assert landing.status_code == 303
    assert landing.headers["location"] == "/start"

    direct_chat = client.get("/chat")
    assert direct_chat.status_code == 200
    assert 'id="model-route-setup"' in direct_chat.text
    assert '<fieldset class="composer__controls" disabled>' in direct_chat.text
    assert 'href="/start">Continue setup</a>' in direct_chat.text


def test_unrelated_codex_login_does_not_complete_anthropic_route(
    monkeypatch,
    tmp_path,
):
    from maverick import config
    from maverick.providers import codex_cli_provider
    from maverick_dashboard import onboarding_state

    _isolate(monkeypatch, tmp_path)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[models]\norchestrator = "anthropic:test-model"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    for name in (*config.PROVIDER_CREDENTIAL_ENV_VARS, *config.PROVIDER_BASE_URL_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(codex_cli_provider, "_auth_file_present", lambda: True)
    config.reset_config_cache()
    try:
        assert config.any_provider_configured() is True
        assert onboarding_state.selected_model_routes_ready() is False
    finally:
        config.reset_config_cache()


def test_viewer_redirected_from_root_can_read_setup_without_mutation_links(
    monkeypatch,
    tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import app as dashboard
    from maverick_dashboard import auth, onboarding_state, rbac

    rbac.set_role("user:first-reader", "viewer")
    monkeypatch.setattr(auth, "caller_principal", lambda _request: "user:first-reader")
    monkeypatch.setattr(onboarding_state, "selected_model_routes_ready", lambda: False)
    monkeypatch.setattr(
        onboarding_state,
        "selected_model_route_state",
        lambda: _route_state(False),
    )
    dashboard._world_cache.clear()
    client = _client()

    landing = client.get("/", follow_redirects=False)
    assert landing.status_code == 303
    assert landing.headers["location"] == "/start"

    setup = client.get("/start")
    assert setup.status_code == 200
    assert "Ask your administrator to connect an AI service." in setup.text
    assert "An operator can build the first workflow or agent." in setup.text
    assert 'href="/settings"' not in setup.text
    assert 'href="/workflow-builder"' not in setup.text
    assert 'href="/chat"' not in setup.text
    assert 'href="/security/assurance"' not in setup.text
    assert client.get("/chat").status_code == 403


def test_readiness_failures_fail_closed_without_breaking_onboarding(monkeypatch):
    from maverick import config, operator_preflight
    from maverick_dashboard import onboarding_state

    def fail_config():
        raise OSError("configuration source unavailable")

    monkeypatch.setattr(config, "load_config", fail_config)
    route = onboarding_state.selected_model_route_state()
    assert route["status"] == "failed"
    assert route["configured"] is False
    assert route["evidence"]["error_type"] == "OSError"
    assert route["evidence"]["live_provider_verified"] is False

    monkeypatch.setattr(
        onboarding_state,
        "selected_model_route_state",
        lambda: _route_state(True),
    )
    monkeypatch.setattr(
        operator_preflight,
        "collect",
        lambda _profile: (_ for _ in ()).throw(RuntimeError("preflight unavailable")),
    )
    monkeypatch.setattr(onboarding_state, "_has_user_template", lambda: False)
    monkeypatch.setattr(onboarding_state, "_has_user_agent", lambda: False)
    monkeypatch.setattr(onboarding_state, "_has_automation", lambda: False)

    class World:
        @staticmethod
        def list_goals(limit=1):
            return []

    state = onboarding_state.build(World())
    assert state["runtime_preflight"]["status"] == "failed"
    assert state["assurance_preflight"]["status"] == "failed"
    assert state["runtime_ready"] is False
    assert state["runtime_all_set"] is False
