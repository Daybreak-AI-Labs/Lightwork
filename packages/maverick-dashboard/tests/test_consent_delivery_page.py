from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def isolated_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from maverick import config, world_model

    path = tmp_path / "config.toml"
    path.write_text(
        "[consent_preferences]\nenable = true\n"
        "[consent_preferences.delivery]\nenable = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(path))
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    for name in (
        "MAVERICK_DASHBOARD_ADMINS",
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_PROXY_AUTH",
        "MAVERICK_TENANT_BY_USER",
    ):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    yield path
    config.reset_config_cache()


def test_delivery_page_exposes_safe_operations_and_external_warning() -> None:
    response = client.get("/consent/delivery")
    assert response.status_code == 200
    assert "Consent Delivery" in response.text
    assert "External side effect" in response.text
    assert "key ID" in response.text
    assert "Dead letters" in response.text
    assert "/api/v1/consent/delivery" in response.text


def test_delivery_page_reports_disabled_gate(
    isolated_page: Path,
) -> None:
    isolated_page.write_text(
        "[consent_preferences]\nenable = true\n"
        "[consent_preferences.delivery]\nenable = false\n",
        encoding="utf-8",
    )
    from maverick import config

    config.reset_config_cache()
    response = client.get("/consent/delivery")
    assert response.status_code == 200
    assert "Delivery disabled" in response.text
