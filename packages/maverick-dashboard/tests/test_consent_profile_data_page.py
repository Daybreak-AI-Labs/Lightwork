from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient


@pytest.fixture()
def page_client() -> TestClient:
    template_dir = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"
    templates = Jinja2Templates(directory=str(template_dir))
    templates.env.globals.update(
        {
            "theme": "dark",
            "font": "default",
            "lang": "en",
            "density": "comfortable",
            "custom_theme_css": "",
            "custom_theme_names": [],
            "dir": "ltr",
            "nav_groups": [],
            "t": lambda key: key,
        }
    )
    app = FastAPI()

    @app.get("/consent/profile-data")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "consent_profile_data.html",
            {"consent_profile_data_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_exposes_complete_governed_operator_contract(page_client: TestClient) -> None:
    response = page_client.get("/consent/profile-data")
    assert response.status_code == 200
    page = response.text
    assert "Consent Profile Data" in page
    assert "Tenant and actor are server-derived" in page
    assert "Local authority only" in page
    assert "No protected-trait inference" in page
    assert "live-traffic operation" in page
    assert "accessibility review" in page
    assert "customer acceptance" in page
    assert "production-scale acceptance" in page
    assert "creator" in page and "cannot approve" in page
    assert "/api/v1/consent/profile-data" in page
    for element in (
        "cpd-load-status",
        "cpd-operation",
        "cpd-path-values",
        "cpd-body",
        "cpd-confirm",
        "cpd-submit",
        "cpd-example",
        "cpd-output",
    ):
        assert f'id="{element}"' in page
    for operation in (
        "status",
        "catalog-create",
        "catalog-get",
        "catalog-approve",
        "journey-create",
        "journey-get",
        "journey-approve",
        "journey-activate",
        "journey-pause",
        "journey-kill",
        "journey-rollback",
        "integrity",
        "retention-purge",
    ):
        assert f'value="{operation}"' in page
    assert "textContent" in page
    assert "encodeURIComponent" in page
    assert 'credentials:"same-origin"' in page


def test_page_fails_closed_and_uses_only_safe_dom(page_client: TestClient) -> None:
    disabled = page_client.get("/consent/profile-data", params={"enabled": False})
    assert disabled.status_code == 200
    assert 'data-module-gate="consent_preferences.profile_data"' in disabled.text
    assert "Consent profile data is turned off." in disabled.text
    assert "[consent_preferences.profile_data] enable = true" not in disabled.text
    assert 'id="cpd-submit"' not in disabled.text

    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "consent_profile_data.html"
    ).read_text(encoding="utf-8")
    for unsafe in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "Function(",
    ):
        assert unsafe not in template
    for forbidden in (
        'name="tenant"',
        'name="tenant_id"',
        'name="actor"',
        'name="actor_ref"',
        'name="profile_id"',
        'name="profile_capability"',
        'name="raw_capability"',
        'name="key"',
        'name="key_material"',
        'name="email"',
        'name="free_text"',
    ):
        assert forbidden not in template


def test_main_api_router_mounts_profile_data_under_v1_prefix() -> None:
    from maverick_dashboard.api import router

    app = FastAPI()
    app.include_router(router)
    paths = set(app.openapi()["paths"])
    assert "/api/v1/consent/profile-data/status" in paths
    assert "/api/v1/consent/profile-data/catalogs" in paths
    assert "/api/v1/consent/profile-data/journeys" in paths
    assert "/api/v1/consent/profile-data/integrity" in paths
    assert "/api/v1/consent/profile-data/retention/purge" in paths


def test_main_dashboard_mounts_profile_page_and_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-consent-profile-data.toml")),
    )
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/consent/profile-data" in paths
    assert "/api/v1/consent/profile-data/status" in paths
    page = page_for("/consent/profile-data")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False

    from maverick_dashboard.copilot_context import context_for_page

    assert context_for_page("/consent/profile-data").path == "/consent/profile-data"

    consent_home = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "consent_preferences.html"
    ).read_text(encoding="utf-8")
    assert 'href="/consent/profile-data"' in consent_home
