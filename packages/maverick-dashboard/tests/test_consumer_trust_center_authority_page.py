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

    @app.get("/consent/trust-center-authority")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "consumer_trust_center_authority.html",
            {"consumer_trust_center_authority_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_exposes_full_four_eyes_authority_workbench(page_client) -> None:
    response = page_client.get("/consent/trust-center-authority")
    assert response.status_code == 200
    page = response.text
    assert "Consumer Trust Center Authority" in page
    assert "two other, distinct approvers" in page
    assert "never accepts raw identity, credentials" in page
    assert "/api/v1/consent/trust-center-authority" in page
    for operation in (
        "context-create",
        "context-get",
        "context-revise",
        "context-revoke",
        "rights-context-prepare",
        "grant-propose",
        "grant-approve",
        "grant-revoke",
        "rights-propose",
        "rights-approve",
        "rights-revoke",
        "history",
        "export-create",
        "export-get",
        "export-verify",
        "retention-due",
        "retention-purge",
        "integrity",
    ):
        assert f'value="{operation}"' in page
    assert 'credentials: "same-origin"' in page
    assert 'cache: "no-store"' in page
    assert "textContent" in page


def test_page_fails_closed_and_template_uses_safe_ephemeral_dom(page_client) -> None:
    disabled = page_client.get(
        "/consent/trust-center-authority",
        params={"enabled": False},
    )
    assert disabled.status_code == 200
    assert "Trust Center authority disabled" in disabled.text
    assert "consent_preferences.trust_center" in disabled.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "consumer_trust_center_authority.html"
    ).read_text(encoding="utf-8")
    for unsafe in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "localStorage",
        "sessionStorage",
    ):
        assert unsafe not in template
    for forbidden in (
        'name="tenant"',
        'name="tenant_id"',
        'name="actor"',
        'name="principal"',
        'name="property_id"',
        'name="profile_id"',
        'name="credential"',
        'name="signing_key"',
    ):
        assert forbidden not in template


def test_dashboard_mounts_page_api_visibility_help_and_consent_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-trust-center-authority.toml")),
    )
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/consent/trust-center-authority" in paths
    assert "/api/v1/consent/trust-center-authority/status" in paths
    assert (
        "/api/v1/consent/trust-center-authority/privacy-rights-contexts/prepare"
        in paths
    )
    assert "/api/v1/consent/trust-center-authority/capability-grants/{grant_ref}/approve" in paths
    assert (
        "/api/v1/consent/trust-center-authority/rights-authorizations/{authorization_ref}/approve"
    ) in paths
    page = page_for("/consent/trust-center-authority")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
    consent = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "consent_preferences.html"
    ).read_text(encoding="utf-8")
    assert 'href="/consent/trust-center-authority"' in consent
    base = (
        Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates" / "base.html"
    ).read_text(encoding="utf-8")
    assert '"/consent/trust-center-authority"' in base
