"""Rendered contract for the consent publishing operator workspace."""

from __future__ import annotations

import re

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
    monkeypatch.setenv("MAVERICK_TENANT", "publishing-page")
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


def test_page_exposes_full_publishing_lifecycle_and_default_off_boundary() -> None:
    response = client.get("/consent/publishing")
    assert response.status_code == 200
    page = response.text
    assert "Consent Publishing" in page
    assert 'data-module-gate="consent_preferences.publishing"' in page
    assert "Consent publishing is turned off." in page
    assert "[consent_preferences.publishing] enable = true" not in page
    for element in (
        "cpp-property",
        "cpp-resource",
        "cpp-promotion",
        "cpp-history",
        "cpp-rollback",
        "cpp-withdraw",
        "cpp-withdraw-confirm",
        "cpp-withdraw-submit",
    ):
        assert element in page
    assert "Prior records remain immutable." in page
    assert "Closed structured JSON only" in page
    assert "Brand theme" in page
    assert "contrast-checked hex colors" in page
    assert "content-free immutable tombstone" in page
    assert "Public configuration resolution is denied as withdrawn" in page
    assert "prior publication records remain immutable" in page
    assert "confirm_withdrawal" in page
    assert re.search(r'id="cpp-withdraw-submit"[^>]*disabled', page)
    assert re.search(r'id="cpp-withdraw-confirm"[^>]*required[^>]*disabled', page)
    script_start = page.index('(function(){"use strict"')
    operator_script = page[script_start : page.index("</script>", script_start)]
    assert "innerHTML" not in operator_script
    assert "result.textContent" in operator_script
    assert "status.textContent" in operator_script


def test_enabled_page_removes_disabled_banner(isolated_page) -> None:
    from maverick import config

    isolated_page.write_text(
        "[consent_preferences]\nenable = true\n[consent_preferences.publishing]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    page = client.get("/consent/publishing").text
    assert 'data-module-gate="consent_preferences.publishing"' not in page
    assert re.search(r'id="cpp-withdraw-submit"[^>]*disabled', page) is None
    assert re.search(r'id="cpp-withdraw-confirm"[^>]*disabled', page) is None
    assert "Confirm the destructive withdrawal before continuing." in page
    assert "confirmation.checked" in page
    assert "Experience publishing" in client.get("/consent").text
