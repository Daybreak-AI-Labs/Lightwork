from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import consent_scan_notice_proposals_api as api


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
    app.include_router(api.router, prefix="/api/v1")

    @app.get("/consent/scan-notice-proposals")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "consent_scan_notice_proposals.html",
            {"consent_scan_notice_proposals_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_exposes_complete_honest_operator_workbench(page_client) -> None:
    response = page_client.get("/consent/scan-notice-proposals")
    assert response.status_code == 200
    page = response.text
    assert "Scan Disclosure Proposals" in page
    assert "server-enrolled, complete, independently reviewed scan" in page
    assert "this browser cannot submit them" in page
    assert "accepts no hosts, origins, paths, URLs, query strings" in page
    assert "does not generate legal prose, mutate a site or app" in page
    assert "not browser-estate, customer, accessibility, legal, provider" in page
    assert "/api/v1/consent/scan-notice-proposals" in page
    for operation in (
        "status",
        "propose",
        "get",
        "rollback",
        "approve",
        "revoke",
        "accept",
        "export",
        "integrity",
    ):
        assert f'value="{operation}"' in page
    assert "acknowledge_local" in page
    assert "textContent" in page


def test_page_fails_closed_and_template_uses_safe_dom(page_client) -> None:
    disabled = page_client.get(
        "/consent/scan-notice-proposals",
        params={"enabled": False},
    )
    assert disabled.status_code == 200
    assert 'data-module-gate="consent_preferences.scanner.notice_proposals"' in disabled.text
    assert "Scan notice proposals is turned off." in disabled.text
    assert "[consent_preferences.scanner.notice_proposals]" not in disabled.text

    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "consent_scan_notice_proposals.html"
    ).read_text(encoding="utf-8")
    for unsafe in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
    ):
        assert unsafe not in template
    for forbidden in (
        'name="tenant"',
        'name="tenant_id"',
        'name="actor"',
        'name="actor_ref"',
        'name="principal_ref"',
        'name="property_id"',
        'name="report_id"',
        'name="inventory_revision"',
        'name="raw_scan_data"',
        'name="scanner_report"',
        'name="host"',
        'name="origin"',
        'name="url"',
        'name="legal_copy"',
        'name="notice_text"',
        'name="signing_key"',
        'name="provider_target"',
        'name="site_target"',
        'name="publication_target"',
    ):
        assert forbidden not in template


def test_assumed_page_and_api_prefix_contract_is_mountable(page_client) -> None:
    paths = set(page_client.app.openapi()["paths"])
    assert "/consent/scan-notice-proposals" in paths
    assert "/api/v1/consent/scan-notice-proposals/status" in paths
    assert "/api/v1/consent/scan-notice-proposals/proposals" in paths
    assert "/api/v1/consent/scan-notice-proposals/proposals/{proposal_id}/accept-local" in paths
    assert "/api/v1/consent/scan-notice-proposals/integrity" in paths

    source = Path(api.__file__).read_text(encoding="utf-8")
    assert 'prefix="/consent/scan-notice-proposals"' in source
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "consent_scan_notice_proposals.html"
    ).read_text(encoding="utf-8")
    assert 'var API = "/api/v1/consent/scan-notice-proposals";' in template


def test_main_dashboard_mounts_page_api_visibility_tips_and_parent_links(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-scan-notice-proposals.toml")),
    )
    for name in (
        "MAVERICK_DASHBOARD_ADMINS",
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_PROXY_AUTH",
        "MAVERICK_TENANT_BY_USER",
    ):
        monkeypatch.delenv(name, raising=False)
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/consent/scan-notice-proposals" in paths
    assert "/api/v1/consent/scan-notice-proposals/status" in paths
    page = page_for("/consent/scan-notice-proposals")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False

    from maverick_dashboard.copilot_context import context_for_page

    assert (
        context_for_page("/consent/scan-notice-proposals").path
        == "/consent/scan-notice-proposals"
    )
    templates = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"
    assert 'href="/consent/scan-notice-proposals"' in (
        templates / "consent_preferences.html"
    ).read_text(encoding="utf-8")
    assert 'href="/consent/scan-notice-proposals"' in (
        templates / "consent_scan_inventory.html"
    ).read_text(encoding="utf-8")

    client = TestClient(app, headers={"Origin": "http://testserver"})
    response = client.get("/consent/scan-notice-proposals")
    assert response.status_code == 200, response.text
    assert "Scan Disclosure Proposals" in response.text
    assert 'data-module-gate="consent_preferences.scanner.notice_proposals"' in response.text
