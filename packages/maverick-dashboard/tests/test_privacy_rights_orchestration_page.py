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

    @app.get("/privacy/rights-orchestration")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "privacy_rights_orchestration.html",
            {"privacy_rights_orchestration_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_exposes_complete_honest_confirmed_workbench(page_client) -> None:
    response = page_client.get("/privacy/rights-orchestration")
    assert response.status_code == 200
    page = response.text
    assert "Privacy Rights Orchestration" in page
    assert "Provider execution stays closed" in page
    assert "Microsoft Graph, Google Workspace, Salesforce, and" in page
    assert "ambiguity-safe reconciliation" in page
    assert "never\n  returned" in page
    assert "/api/v1/privacy/rights-orchestration" in page
    for operation in (
        "source-enroll",
        "case-open",
        "identity-evidence",
        "legal-hold",
        "retention-gate",
        "task-plan",
        "destructive-approve",
        "callback-claim",
        "callback-list",
        "callback-fail",
        "callback-ack",
        "callback-requeue",
        "source-runtime-run",
        "source-runtime-reconcile",
        "source-evidence",
        "manifest-build",
        "package-create",
        "closure-approve",
        "case-close",
        "case-export",
        "retention-purge",
        "integrity",
    ):
        assert f'value="{operation}"' in page
    assert "pro-confirm" in page
    assert "textContent" in page


def test_page_fails_closed_and_new_template_uses_safe_dom(page_client) -> None:
    disabled = page_client.get("/privacy/rights-orchestration", params={"enabled": False})
    assert disabled.status_code == 200
    assert 'data-module-gate="privacy_automation.rights_orchestration"' in disabled.text
    assert "Privacy-rights orchestration is turned off." in disabled.text
    assert "[privacy_automation.rights_orchestration] enable = true" not in disabled.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "privacy_rights_orchestration.html"
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
    ):
        assert forbidden not in template


def test_dashboard_mounts_page_api_visibility_and_privacy_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-privacy-rights.toml")),
    )
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/privacy/rights-orchestration" in paths
    assert "/api/v1/privacy/rights-orchestration/status" in paths
    assert "/api/v1/privacy/rights-orchestration/cases/{case_id}/closure/approve" in paths
    page = page_for("/privacy/rights-orchestration")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
    privacy = (
        Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates" / "privacy.html"
    ).read_text(encoding="utf-8")
    assert 'href="/privacy/rights-orchestration"' in privacy
