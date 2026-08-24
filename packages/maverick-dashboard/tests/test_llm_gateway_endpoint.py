"""The dashboard's OpenAI-compatible gateway endpoint: self-authenticating.

The endpoint sits outside /api/v1 (foreign agents hold the gateway bearer,
never the dashboard bearer or a browser origin), 403s with guidance while
the feature is off, and relays exactly what the governed core decides.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick import config
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", str(tmp_path / "world.db"))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    for name in (
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_TENANT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAVERICK_GATEWAY_TOKEN", "gw-secret")
    monkeypatch.setenv("LLM_GATEWAY_UPSTREAM_TOKEN", "up-secret")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _enable(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[llm_gateway]\nenable = true\nupstream_url = "https://api.openai.com"\n',
        encoding="utf-8",
    )
    config.reset_config_cache()


def test_disabled_endpoint_explains_the_knob():
    response = client.post(
        "/gateway/v1/chat/completions",
        json={"model": "gpt-5.2", "messages": []},
    )
    assert response.status_code == 403
    assert "[llm_gateway]" in response.json()["error"]["message"]


def test_gateway_bearer_gates_the_endpoint(tmp_path, monkeypatch):
    _enable(tmp_path)
    from maverick import llm_gateway as gw

    monkeypatch.setattr(
        gw,
        "_default_upstream",
        lambda url, headers, body: (
            200,
            {
                "id": "chatcmpl-1",
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        ),
    )
    denied = client.post(
        "/gateway/v1/chat/completions",
        json={"model": "gpt-5.2", "messages": [{"role": "user", "content": "q"}]},
        headers={"Authorization": "Bearer wrong"},
    )
    assert denied.status_code == 401

    granted = client.post(
        "/gateway/v1/chat/completions",
        json={"model": "gpt-5.2", "messages": [{"role": "user", "content": "q"}]},
        headers={"Authorization": "Bearer gw-secret"},
    )
    assert granted.status_code == 200
    assert granted.json()["choices"][0]["message"]["content"] == "hi"
    assert gw.list_callers()[0]["last_status"] == "ok"


def test_malformed_json_is_a_client_error(tmp_path):
    _enable(tmp_path)
    response = client.post(
        "/gateway/v1/chat/completions",
        content=b"not json",
        headers={
            "Authorization": "Bearer gw-secret",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400
