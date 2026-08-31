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

    @app.get("/privacy/notices")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "privacy_notice_governance.html",
            {"privacy_notice_governance_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_exposes_complete_honest_confirmed_workbench(page_client) -> None:
    response = page_client.get("/privacy/notices")
    assert response.status_code == 200
    page = response.text
    assert "Privacy Notice Governance" in page
    assert "No raw notice content, personal data, secret values, network I/O" in page
    assert "automatic publication, or live acceptance" in page
    assert "/api/v1/privacy/notices" in page
    for operation in (
        "policy-set",
        "policy-get",
        "release-create",
        "release-get",
        "release-submit",
        "release-approve",
        "approval-revoke",
        "release-schedule",
        "intent-claim",
        "intent-fail",
        "intent-requeue",
        "intent-acknowledge",
        "release-rollback",
        "release-withdraw",
        "release-effective",
        "proposal-create",
        "proposal-get",
        "proposal-review",
        "notice-export",
        "export-get",
        "retention-purge",
        "integrity",
    ):
        assert f'value="{operation}"' in page
    assert "png-confirm" in page
    assert "textContent" in page


def test_page_fails_closed_and_template_uses_safe_dom(page_client) -> None:
    disabled = page_client.get("/privacy/notices", params={"enabled": False})
    assert disabled.status_code == 200
    assert "Privacy notice governance disabled" in disabled.text
    assert "privacy_automation.notice_governance" in disabled.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "privacy_notice_governance.html"
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
        'name="raw_content"',
        'name="notice_content"',
        'name="signing_key"',
    ):
        assert forbidden not in template
