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
        "[consent_preferences.publishing]\nenable = true\n"
        "[consent_preferences.public_api]\nenable = true\n",
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


def test_public_clients_page_explains_once_only_credentials_and_sdk() -> None:
    response = client.get("/consent/public-clients")
    assert response.status_code == 200
    assert "Public Consent Clients" in response.text
    assert "returned once" in response.text
    assert "LightworkConsent" in response.text
    assert "/api/v1/public/consent/sdk.js" in response.text
    assert "Revoke profile capability" in response.text
    assert "List safe profile bindings" in response.text
    assert "call('/profiles')" in response.text


def test_public_clients_page_reports_gate_off(isolated_page: Path) -> None:
    isolated_page.write_text(
        "[consent_preferences]\nenable = true\n"
        "[consent_preferences.publishing]\nenable = true\n"
        "[consent_preferences.public_api]\nenable = false\n",
        encoding="utf-8",
    )
    from maverick import config

    config.reset_config_cache()
    response = client.get("/consent/public-clients")
    assert response.status_code == 200
    assert "Public clients disabled" in response.text
