"""Customer-visible inventory errors stay useful without exposing internals."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    from maverick_dashboard.app import app

    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "patch_target", "message"),
    [
        (
            "/api/v1/plugins",
            "maverick.plugins.plugin_status_snapshot",
            "plugin list",
        ),
        (
            "/api/v1/mcp",
            "maverick_dashboard.integration_readiness.mcp_inventory",
            "external tool connections",
        ),
        (
            "/api/v1/channels",
            "maverick_dashboard.integration_readiness.require_readable_config",
            "channel list",
        ),
        (
            "/api/v1/tools",
            "maverick.tools.base_registry",
            "tools available in this workspace",
        ),
    ],
)
def test_inventory_errors_return_safe_support_reference(
    client, monkeypatch, path, patch_target, message
):
    import importlib

    module_name, attribute = patch_target.rsplit(".", 1)
    module = importlib.import_module(module_name)

    def fail(*_args, **_kwargs):
        raise RuntimeError("C:/private/config.toml contains a sensitive value")

    monkeypatch.setattr(module, attribute, fail)
    response = client.get(path)

    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert message in detail
    assert "error reference lw-failure-" in detail
    assert "private/config.toml" not in detail
    assert "sensitive value" not in detail
    assert "RuntimeError" not in detail
    assert response.headers["x-lightwork-failure-reference"].startswith("lw-failure-")
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
