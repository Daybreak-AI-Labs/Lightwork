"""Settings, Providers, Chat, Get started, the health probe, the system checks
and the Copilot context all report the same AI-service verdict for the same
state."""
from __future__ import annotations

import html as _html
import re

import pytest
from fastapi.testclient import TestClient
from maverick.config import PROVIDER_BASE_URL_ENV_VARS, PROVIDER_CREDENTIAL_ENV_VARS


def visible_text(page: str) -> str:
    """Words a person sees without opening a collapsed "Technical details"."""
    main = page.split('<main id="content"', 1)[-1].split("</main>", 1)[0]
    main = re.sub(r"<(script|style|details)\b.*?</\1>", " ", main, flags=re.DOTALL)
    return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", main)))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mvhome"))
    for name in (*PROVIDER_CREDENTIAL_ENV_VARS, *PROVIDER_BASE_URL_ENV_VARS,
                 "MAVERICK_MODEL_OVERRIDE", "MAVERICK_DASHBOARD_TOKEN", "MAVERICK_ENTERPRISE"):
        monkeypatch.delenv(name, raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    from maverick import config, world_model
    from maverick_dashboard import integration_readiness
    from maverick_dashboard import app as dash_app

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    config.reset_config_cache()
    integration_readiness._clear_for_testing()
    dash_app._world_cache.clear()

    class Workspace:
        client = TestClient(dash_app.app, headers={"Origin": "http://testserver"})

        @staticmethod
        def write(text: str) -> None:
            config_path.write_text(text, encoding="utf-8")
            config.reset_config_cache()

        @staticmethod
        def record(name: str, *, verified: bool, detail: str) -> None:
            from maverick.config import get_provider_config

            integration_readiness.record("provider", name, get_provider_config(name) or {},
                                         verified=verified, detail=detail)

    yield Workspace
    config.reset_config_cache()
    integration_readiness._clear_for_testing()


def _pages(client) -> dict[str, str]:
    return {path: client.get(path).text for path in ("/settings", "/providers", "/chat", "/start")}


def _api(client) -> dict:
    return {
        "providers": client.get("/api/v1/providers").json()["workspace"],
        "system": next(r for r in client.get("/api/v1/admin/system/readiness").json()["checks"]
                       if r["key"] == "provider"),
        "healthz": client.get("/healthz").json()["checks"]["provider"],
        "copilot": client.get("/api/v1/copilot/context", params={"page": "/chat"}).json()["workspace_readiness"],
    }


def test_saved_key_never_tested_reads_the_same_everywhere(workspace, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-0000")  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_MODEL_OVERRIDE", "openai:gpt-test")
    pages = _pages(workspace.client)
    for path in ("/settings", "/providers"):
        text = visible_text(pages[path])
        assert "1 set up · 0 connected" in text, path
        assert "Key saved, not tested yet" in text, path
        assert ">Connected<" not in pages[path], path
    # Chat: work may start on a saved key, but the composer says it is untested.
    assert 'id="model-route-setup"' not in pages["/chat"]
    assert 'class="composer__controls" disabled>' not in pages["/chat"]
    assert 'id="composer-readiness"' in pages["/chat"]
    assert "key saved, not tested yet" in visible_text(pages["/chat"])
    assert 'href="/providers">Test the connection</a>' in pages["/chat"]
    # Get started: step 1 is not done and the checks do not say complete.
    start = pages["/start"]
    assert "0 of 4 done" in start and 'class="gs__step gs__step--done"' not in start
    assert "Fix next:" in start and 'href="/providers">Test the connection</a>' in start
    assert "Setup complete" not in visible_text(start).split("Assurance setup")[0]
    api = _api(workspace.client)
    assert api["providers"]["verdict"] == api["copilot"]["verdict"] == "needs_test"
    assert api["system"]["ok"] is False and api["system"]["fix_href"] == "/providers"
    assert api["healthz"].startswith("needs_test: ")
    # The settings deep link the buttons use is a real section on the page.
    assert 'id="settings-integrations"' in pages["/settings"]


def test_failed_test_reads_as_connection_failed_everywhere(workspace, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-0000")  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_MODEL_OVERRIDE", "openai:gpt-test")
    workspace.record("openai", verified=False, detail="provider probe failed (ConnectError)")
    pages = _pages(workspace.client)
    for path, page in pages.items():
        text = visible_text(page)
        assert "connection failed: lightwork could not reach the service" in text.lower(), path
        assert "ConnectError" not in text, path
        assert ">Connected<" not in page, path
    assert "1 set up · 0 connected" in visible_text(pages["/settings"])
    assert 'id="model-route-setup"' in pages["/chat"]
    assert 'class="composer__controls" disabled>' in pages["/chat"]
    assert "0 of 4 done" in pages["/start"] and "Needs attention" in pages["/start"]
    api = _api(workspace.client)
    assert api["providers"]["verdict"] == api["copilot"]["verdict"] == "not_ready"
    assert api["system"]["ok"] is False
    assert api["healthz"].startswith("not_ready: OpenAI connection failed")


def test_passing_test_reads_as_connected_everywhere(workspace, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-0000")  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_MODEL_OVERRIDE", "openai:gpt-test")
    workspace.record("openai", verified=True, detail="provider accepted the bounded models request")
    pages = _pages(workspace.client)
    for path in ("/settings", "/providers"):
        assert "1 set up · 1 connected" in visible_text(pages[path]), path
        assert ">Connected<" in pages[path], path
        assert "not tested yet" not in visible_text(pages[path]), path
    assert 'id="model-route-setup"' not in pages["/chat"]
    assert 'id="composer-readiness"' not in pages["/chat"]
    assert "1 of 4 done" in pages["/start"] and 'class="gs__step gs__step--done"' in pages["/start"]
    assert "OpenAI connected: the last connection test worked." in visible_text(pages["/start"])
    api = _api(workspace.client)
    assert api["providers"]["verdict"] == api["copilot"]["verdict"] == "ready"
    assert api["system"]["ok"] is True
    assert api["healthz"].startswith("ready: ")


def test_fresh_install_with_wizard_ollama_address_is_not_connected(workspace):
    """The installer writes a default Ollama server address; that is one
    service set up, none connected, and the models still use Anthropic."""
    workspace.write('[providers.ollama]\nbase_url = "http://localhost:11434"\n')
    pages = _pages(workspace.client)
    for path in ("/settings", "/providers"):
        text = visible_text(pages[path])
        assert "1 set up · 0 connected" in text, path
        assert "Anthropic (Claude) is not set up, and your models use it." in text, path
        assert ">Connected<" not in pages[path], path
        assert 'href="/settings#settings-integrations">Add an AI provider key</a>' in pages[path], path
    assert 'id="model-route-setup"' in pages["/chat"]
    assert "Anthropic (Claude) is not set up" in visible_text(pages["/chat"])
    assert "0 of 4 done" in pages["/start"]
    api = _api(workspace.client)
    assert api["providers"]["verdict"] == "not_ready"
    assert api["providers"]["configured_count"] == 1 and api["providers"]["connected_count"] == 0
    assert api["system"]["fix_href"] == "/settings#settings-integrations"
    assert api["copilot"]["next_action"]["href"] == "/settings#settings-integrations"
