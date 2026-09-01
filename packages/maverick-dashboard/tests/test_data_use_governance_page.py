"""Rendered contract for the Data Use Governance workspace."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def isolated_page(tmp_path, monkeypatch):
    from maverick import config

    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(path))
    monkeypatch.setenv("MAVERICK_TENANT", "data-use-page")
    for name in (
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


def test_default_off_page_exposes_authority_workflows_and_limits() -> None:
    response = client.get("/data-use")
    assert response.status_code == 200
    page = response.text
    assert "Data Use Governance" in page
    assert 'data-module-gate="data_use_governance"' in page
    assert "[data_use_governance] enable = true" not in page
    for form in ("dug-catalog", "dug-context", "dug-policy", "dug-request", "dug-records"):
        assert form in page
    assert "All four roots must be fresh and independently sourced." in page
    assert "enforces four eyes" in page
    assert "Native provider writes" in page
    assert "Live provider acceptance" in page


def test_enabled_authority_still_labels_disabled_compiler(isolated_page) -> None:
    from maverick import config

    isolated_page.write_text(
        "[data_use_governance]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    page = client.get("/data-use").text
    assert 'data-module-gate="data_use_governance"' not in page
    assert 'data-module-gate="data_use_governance.compiler"' in page
    assert "[data_use_governance.compiler] enable = true" not in page

    isolated_page.write_text(
        "[data_use_governance]\nenable = true\n"
        "[data_use_governance.compiler]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    assert (
        'data-module-gate="data_use_governance.compiler"'
        not in client.get("/data-use").text
    )
