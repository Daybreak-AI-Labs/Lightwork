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

    @app.get("/compliance/scoping")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "compliance_scoping.html",
            {"compliance_scoping_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_exposes_complete_honest_confirmed_workbench(page_client) -> None:
    response = page_client.get("/compliance/scoping")
    assert response.status_code == 200
    page = response.text
    assert "Technology Risk &amp; Compliance Scoping" in page
    assert "No licensed framework text, legal advice, external discovery" in page
    assert "never a secret value" in page
    assert "/api/v1/compliance/scoping" in page
    for operation in (
        "questionnaire-create",
        "questionnaire-get",
        "questionnaire-publish",
        "assessment-create",
        "assessment-get",
        "required-questions",
        "response-record",
        "scope-compute",
        "scope-get",
        "scope-overlaps",
        "scope-crosswalks",
        "scope-responsibilities",
        "scope-approve",
        "scope-finalize",
        "scope-export",
        "export-get",
        "retention-purge",
        "integrity",
    ):
        assert f'value="{operation}"' in page
    assert "cs-confirm" in page
    assert "textContent" in page


def test_page_fails_closed_and_template_uses_safe_dom(page_client) -> None:
    disabled = page_client.get("/compliance/scoping", params={"enabled": False})
    assert disabled.status_code == 200
    assert 'data-module-gate="tech_risk_compliance.scoping"' in disabled.text
    assert "[tech_risk_compliance] enable = true" not in disabled.text
    assert "[tech_risk_compliance.scoping] enable = true" not in disabled.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "compliance_scoping.html"
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
        'name="raw_evidence"',
        'name="signing_key"',
    ):
        assert forbidden not in template


def test_dashboard_mounts_page_api_visibility_and_compliance_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-compliance-scoping.toml")),
    )
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/compliance/scoping" in paths
    assert "/api/v1/compliance/scoping/status" in paths
    assert "/api/v1/compliance/scoping/scopes/{scope_id}/exports/{export_id}" in paths
    page = page_for("/compliance/scoping")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
    compliance = (
        Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates" / "compliance.html"
    ).read_text(encoding="utf-8")
    assert 'href="/compliance/scoping"' in compliance
