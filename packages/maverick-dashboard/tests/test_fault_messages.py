"""CTL-5 fault injection: when an AI service fails, every page says what broke,
whether work is safe, when Lightwork tries again and another way to carry on,
and never shows a raw code as the main text."""
from __future__ import annotations

import re
from html import unescape

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from maverick import config, world_model
    from maverick_dashboard import app as dash_app

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    dash_app._world_cache.clear()
    yield TestClient(dash_app.app, headers={"Origin": "http://testserver"})
    config.reset_config_cache()
    dash_app._world_cache.clear()


def _timeouts(monkeypatch, providers, *, in_use):
    """The provider timed out often enough that the circuit breaker paused it."""
    from maverick import circuit_breaker, service_status

    rows = [{"key": "llm:" + name, "state": "open", "opened_at": 2_000_000_000.0, "cooldown_seconds": 60}
            for name in providers]
    monkeypatch.setattr(circuit_breaker, "snapshot", lambda: rows)
    monkeypatch.setattr(service_status, "providers_in_use", lambda: set(in_use))


def _banner(page: str) -> str:
    found = re.search(r'id="service-status-banner".*?</div>', page, re.DOTALL)
    assert found, "no service banner"
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", found.group(0))).split())


@pytest.mark.parametrize("path", ["/", "/chat", "/approvals", "/settings"])
def test_a_provider_outage_says_what_broke_what_is_safe_when_and_another_way(client, monkeypatch, path):
    _timeouts(monkeypatch, ["anthropic"], in_use={"anthropic"})
    text = _banner(client.get(path).text)
    assert "Anthropic is paused after repeated errors" in text          # what broke
    assert "What they already finished is kept" in text                 # what is safe
    assert re.search(r"tries Anthropic again at .*\d", text)             # when it returns
    assert "set up another AI service under Settings > AI models" in text  # another way
    assert not re.search(r"\b(5\d\d|Traceback|Exception|HTTPError)\b", text)  # no raw code as the message


def test_with_a_healthy_backup_the_banner_says_new_work_carries_on(client, monkeypatch):
    _timeouts(monkeypatch, ["anthropic"], in_use={"anthropic", "openai"})
    text = _banner(client.get("/").text)
    assert "still working with OpenAI" in text and "start new work as usual: it uses OpenAI" in text
