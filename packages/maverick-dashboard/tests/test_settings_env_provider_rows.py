"""UX-216: Settings lists every provider that takes a key and a server address,
so an OpenAI-compatible (or TGI) server configured on the computer running
Lightwork is shown as set up instead of "0 providers connected / No key"."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ENV_URL = "https://llm.example.test/v1"
_ENV_KEY = "env-only-credential-1234"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path: Path):
    home = tmp_path / "maverick-home"
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setenv("MAVERICK_CONFIG", str(home / "config.toml"))
    for name in (
        "MAVERICK_AUTH_REQUIRED",
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_ENTERPRISE",
        "MAVERICK_TENANT",
        "MAVERICK_MODEL_OVERRIDE",
    ):
        monkeypatch.delenv(name, raising=False)

    import maverick.runtime_overrides as runtime_overrides
    from maverick import config, world_model
    from maverick_dashboard import app as dashboard_app
    from maverick_dashboard import integration_readiness

    for name in config.PROVIDER_KEY_ENV_VARS + config.PROVIDER_BASE_URL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in ("GOOGLE_API_KEY", "GROK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    integration_readiness._clear_for_testing()
    monkeypatch.setattr(world_model, "DEFAULT_DB", home / "world.db")
    monkeypatch.setattr(runtime_overrides, "OVERRIDES_PATH", home / "runtime-overrides.toml")
    dashboard_app._world_cache.clear()
    yield
    integration_readiness._clear_for_testing()
    config.reset_config_cache()


def _env_openai_compatible(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", _ENV_URL)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", _ENV_KEY)
    from maverick import config

    config.reset_config_cache()


def _rows() -> dict[str, dict]:
    from maverick_dashboard import settings_store

    return {row["name"]: row for row in settings_store.state()["providers"]}


def test_environment_openai_compatible_server_is_listed_as_set_up(monkeypatch):
    _env_openai_compatible(monkeypatch)
    row = _rows()["openai_compatible"]
    assert row["configured"] is True
    assert row["base_url"] == _ENV_URL
    assert row["base_url_field"] is True
    assert row["via"] and "environment:" in row["via"]
    assert row["api_key_source"].startswith("environment:")
    # Nothing here reveals the credential itself.
    assert _ENV_KEY not in repr(row)


def test_vllm_row_is_not_confused_with_the_openai_compatible_server(monkeypatch):
    _env_openai_compatible(monkeypatch)
    rows = _rows()
    assert rows["vllm"]["configured"] is False
    assert "OpenAI-compatible" not in rows["vllm"]["label"]
    assert "OPENAI_COMPATIBLE" not in rows["vllm"]["env_hint"]


def test_tgi_server_from_the_environment_is_listed_as_set_up(monkeypatch):
    monkeypatch.setenv("TGI_BASE_URL", "https://tgi.example.test")
    from maverick import config

    config.reset_config_cache()
    row = _rows()["tgi"]
    assert row["configured"] is True
    assert row["base_url"] == "https://tgi.example.test"


def test_settings_page_no_longer_says_nothing_is_connected(monkeypatch):
    _env_openai_compatible(monkeypatch)
    # The routed model, exactly as in the audit's lab: tasks already run on it.
    monkeypatch.setenv("MAVERICK_MODEL_OVERRIDE", "openai_compatible:fake-model")
    client = TestClient(
        __import__("maverick_dashboard.app", fromlist=["app"]).app,
        headers={"Origin": "http://testserver"},
    )
    response = client.get("/settings")
    assert response.status_code == 200
    page = response.text
    assert 'id="provider-openai_compatible"' in page
    assert "OpenAI-compatible server" in page
    assert "Add a provider key" not in page
    # Models tab: one provider has what it needs, not "0 providers with a key".
    assert re.search(
        r'<span class="num">1</span><span class="muted">provider with a key</span>', page
    )
    # The address in use is named, and the credential is described without
    # naming a variable or revealing it.
    row = page.split('id="provider-openai_compatible"', 1)[1].split('id="provider-', 1)[0]
    assert _ENV_URL in row
    assert _ENV_KEY not in page
    assert "OPENAI_COMPATIBLE" not in row
    assert "set on the computer running Lightwork" in row


def test_a_key_saved_here_replaces_the_environment_key_and_clearing_returns(monkeypatch):
    _env_openai_compatible(monkeypatch)
    from maverick import config
    from maverick_dashboard import settings_store

    new_key = "dashboard-rotated-5678"  # pragma: allowlist secret
    settings_store.rotate_provider("openai_compatible", api_key=new_key)
    config.reset_config_cache()
    assert config.get_provider_config("openai_compatible")["api_key"] == new_key
    row = _rows()["openai_compatible"]
    assert row["dashboard_set"] is True
    assert row["key_hint"] == "••••5678"

    settings_store.clear_provider("openai_compatible")
    config.reset_config_cache()
    row = _rows()["openai_compatible"]
    assert row["dashboard_set"] is False
    assert row["configured"] is True  # the server's own key is back in use
    assert row["api_key_source"].startswith("environment:")
