"""QA-023: top-level integration inventories expose closed readiness states."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path: Path):
    home = tmp_path / "maverick-home"
    config_path = home / "config.toml"
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    for name in (
        "MAVERICK_AUTH_REQUIRED",
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_ENTERPRISE",
        "MAVERICK_TENANT",
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
    monkeypatch.setattr(
        runtime_overrides,
        "OVERRIDES_PATH",
        home / "runtime-overrides.toml",
    )
    dashboard_app._world_cache.clear()
    yield config_path
    integration_readiness._clear_for_testing()
    config.reset_config_cache()


def _client(*, auth_token: str = "") -> TestClient:
    from maverick_dashboard.app import app

    headers = {"Origin": "http://testserver"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return TestClient(app, headers=headers)


def test_settings_renders_readiness_for_every_provider_row():
    from maverick_dashboard.settings_store import PROVIDERS

    response = _client().get("/settings")
    assert response.status_code == 200
    for provider in PROVIDERS:
        assert provider["label"] in response.text
    assert response.text.count("Connection test:") >= len(PROVIDERS)
    assert "Not set up" in response.text


def test_provider_unknown_then_fingerprint_bound_live_verified(monkeypatch):
    from maverick import providers
    from maverick_dashboard import integration_readiness, settings_store

    settings_store.set_provider(
        "openai",
        api_key="test-provider-key",  # pragma: allowlist secret
    )
    monkeypatch.setattr(providers, "missing_sdks", lambda _specs: [])
    monkeypatch.setattr(
        integration_readiness,
        "probe_provider",
        lambda _name, _config: {
            "verified": True,
            "detail": "bounded provider probe passed",
            "evidence": {"kind": "live_status_probe", "http_status": 200},
        },
    )
    client = _client()

    before = client.get("/api/v1/providers").json()["providers"]
    openai_before = next(row for row in before if row["name"] == "openai")
    assert openai_before["readiness"]["configured"] is True
    assert openai_before["readiness"]["live"]["status"] == "unknown"
    assert openai_before["readiness"]["live"]["verified"] is None

    tested = client.post(
        "/settings/providers/test",
        data={"provider": "openai"},
        follow_redirects=False,
    )
    assert tested.status_code == 303
    after = client.get("/api/v1/providers").json()["providers"]
    openai_after = next(row for row in after if row["name"] == "openai")
    assert openai_after["readiness"]["runtime"]["status"] == "passed"
    assert openai_after["readiness"]["live"]["status"] == "verified"
    assert openai_after["readiness"]["state"] == "live_verified"
    settings_page = client.get("/settings")
    assert settings_page.status_code == 200
    assert "bounded provider probe passed" in settings_page.text
    providers_page = client.get("/providers")
    assert providers_page.status_code == 200
    assert "1 live verified" in providers_page.text


def test_provider_probe_truth_for_loopback_runtime_and_unsupported(monkeypatch):
    from maverick import config, providers
    from maverick.tools import _ssrf
    from maverick_dashboard import integration_readiness

    def scoped_safe_get(url: str, **_kwargs):
        host = urlsplit(url).hostname or ""
        assert _ssrf.resolve_pinned_ip(host) == "127.0.0.1"
        return SimpleNamespace(status_code=200)

    with pytest.raises(_ssrf.BlockedHost):
        _ssrf.resolve_pinned_ip("127.0.0.1")
    monkeypatch.setattr(_ssrf, "safe_get", scoped_safe_get)
    ollama = integration_readiness.probe_provider("ollama", {})
    assert ollama["verified"] is True
    with pytest.raises(_ssrf.BlockedHost):
        _ssrf.resolve_pinned_ip("127.0.0.1")

    private = integration_readiness.probe_provider(
        "ollama", {"base_url": "http://10.0.0.7:11434"}
    )
    assert private["verified"] is False
    assert "egress policy" in private["detail"]

    material = {"api_key": "test-bedrock-key"}  # pragma: allowlist secret
    unsupported = integration_readiness.probe_provider("bedrock", material)
    assert unsupported["verified"] is None
    assert unsupported["evidence"]["kind"] == "unsupported"
    integration_readiness.record(
        "provider",
        "bedrock",
        material,
        verified=unsupported["verified"],
        detail=unsupported["detail"],
        evidence=unsupported["evidence"],
    )
    monkeypatch.setattr(config, "get_provider_config", lambda _name: material)
    monkeypatch.setattr(providers, "missing_sdks", lambda _specs: [])
    readiness = integration_readiness.provider_readiness("bedrock", configured=True)
    assert readiness["runtime"]["status"] == "unknown"
    assert readiness["live"]["status"] == "unknown"
    assert readiness["live"]["checked"] is True
    assert readiness["state"] == "dependency_passed_runtime_unknown"

    cli_material = {"binary": "codex-test"}
    monkeypatch.setattr(integration_readiness.shutil, "which", lambda _binary: "codex")
    monkeypatch.setattr(
        integration_readiness.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    cli_probe = integration_readiness.probe_provider("codex_cli", cli_material)
    assert cli_probe["verified"] is None
    integration_readiness.record(
        "provider",
        "codex_cli",
        cli_material,
        verified=cli_probe["verified"],
        detail=cli_probe["detail"],
        evidence=cli_probe["evidence"],
    )
    monkeypatch.setattr(config, "get_provider_config", lambda _name: cli_material)
    cli_readiness = integration_readiness.provider_readiness(
        "codex_cli", configured=True
    )
    assert cli_readiness["runtime"]["status"] == "passed"
    assert cli_readiness["live"]["status"] == "unknown"
    assert cli_readiness["state"] == "runtime_passed_live_unknown"


def test_channel_failure_and_mcp_unknown_then_failed(_isolate: Path):
    _isolate.parent.mkdir(parents=True, exist_ok=True)
    _isolate.write_text(
        """
[channels.slack]
enabled = true
bot_token = "xoxb-test"

[mcp_servers.broken]
command = "lightwork-command-that-does-not-exist"
""".strip(),
        encoding="utf-8",
    )
    from maverick import config

    config.reset_config_cache()
    client = _client()

    channel = client.get("/api/v1/channels").json()["channels"][0]
    assert channel["configured"] is True
    assert channel["readiness"]["runtime"]["status"] == "failed"
    assert channel["readiness"]["live"]["status"] == "unknown"
    assert channel["readiness"]["state"] == "runtime_failed"
    channel_page = client.get("/channels")
    assert channel_page.status_code == 200
    assert "Connection test:" in channel_page.text

    server = client.get("/api/v1/mcp").json()["servers"][0]
    assert server["readiness"]["dependency"]["status"] == "failed"
    assert server["readiness"]["runtime"]["status"] == "unknown"
    assert server["readiness"]["live"]["status"] == "unknown"

    tested = client.post(
        "/mcp/test",
        data={"name": "broken"},
        follow_redirects=False,
    )
    assert tested.status_code == 303
    failed = client.get("/api/v1/mcp").json()["servers"][0]
    assert failed["readiness"]["runtime"]["status"] == "failed"
    assert failed["readiness"]["live"]["status"] == "failed"
    mcp_page = client.get("/mcp")
    assert mcp_page.status_code == 200
    assert "MCP readiness handshake failed" in mcp_page.text


def test_corrupt_config_is_visible_unavailable_not_empty(
    _isolate: Path, monkeypatch
):
    _isolate.parent.mkdir(parents=True, exist_ok=True)
    _isolate.write_text("[channels\n", encoding="utf-8")
    from maverick import config

    config.reset_config_cache()
    token = "test-dashboard-admin-token"  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", token)
    client = _client(auth_token=token)
    for path in ("/settings", "/providers", "/mcp", "/channels"):
        response = client.get(path, headers={"Accept": "text/html"})
        assert response.status_code == 503, path
        assert "Temporarily unavailable" in response.text
        assert "No tool servers connected yet" not in response.text
        assert "No channels configured yet" not in response.text

    assert client.get("/api/v1/mcp").status_code == 500
    assert client.get("/api/v1/channels").status_code == 500
