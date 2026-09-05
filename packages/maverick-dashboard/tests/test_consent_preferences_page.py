"""Rendered contract for the Consent & Preferences operator workspace."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _isolated_consent_page(tmp_path, monkeypatch):
    from maverick import config

    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    monkeypatch.setenv("MAVERICK_TENANT", "consent-page-test")
    for name in (
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_PROXY_AUTH",
        "MAVERICK_TENANT_BY_USER",
    ):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    yield config_path
    config.reset_config_cache()


def test_consent_page_is_explicit_about_default_off_and_product_boundary():
    response = client.get("/consent")

    assert response.status_code == 200
    page = response.text
    assert "Consent &amp; Preferences" in page
    # Default-off is explicit, and the gate is switchable IN-APP: the shared
    # module-gate banner with a Turn on control, no config keys in the UI.
    assert 'data-module-gate="consent_preferences"' in page
    assert "Consent preferences is turned off." in page
    assert "[consent_preferences] enable = true" not in page
    assert "Consent management" in page
    assert "cp-policy-form" in page
    assert "cp-preference-form" in page
    assert "cp-decision-form" in page
    assert "cp-tracker-form" in page
    assert "cp-analytics-form" in page
    assert 'data-module-gate="consent_preferences.analytics"' in page
    assert "[consent_preferences.analytics] enable = true" not in page
    assert "small cohorts are hidden" in page
    assert "cp-identity-form" in page
    assert "Link, unlink, relink &amp; account transition evidence" in page
    assert "/identity-links/integrity?limit=500" in page
    assert "Unlinking or changing the link requires a newly verified identity" in page
    assert 'value="web_tracker"' in page
    assert "consent_required" not in page


def test_enabled_consent_page_uses_integer_policy_versions(
    _isolated_consent_page,
):
    from maverick import config

    _isolated_consent_page.write_text(
        "[consent_preferences]\nenable = true\n"
        "[consent_preferences.analytics]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()

    response = client.get("/consent")

    assert response.status_code == 200
    page = response.text
    assert "Module disabled." not in page
    assert 'name="version" type="number" min="1"' in page
    assert 'name="policy_version" type="number" min="1"' in page
    assert "version:Number(val(f,'version'))" in page
    assert "policy_version:Number(val(f,'policy_version'))" in page
    assert "Analytics is independently disabled." not in page
