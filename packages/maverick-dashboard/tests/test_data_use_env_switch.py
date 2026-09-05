"""The Data Use Governance page can be turned on by environment alone.

Hosted deployments keep the config file on ephemeral storage, so the module
needs the same env-first switch the flow engine has.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

DISABLED_COPY = 'data-module-gate="data_use_governance"'


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from maverick import world_model
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("MAVERICK_DATA_USE_GOVERNANCE", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_is_off_without_config_or_env():
    r = _client().get("/data-use")
    assert r.status_code == 200
    assert DISABLED_COPY in r.text


def test_env_switch_turns_the_page_on(monkeypatch):
    monkeypatch.setenv("MAVERICK_DATA_USE_GOVERNANCE", "1")
    r = _client().get("/data-use")
    assert r.status_code == 200
    assert DISABLED_COPY not in r.text
