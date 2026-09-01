from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient


@pytest.fixture()
def page_client():
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

    @app.get("/privacy/data-discovery")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "privacy_data_discovery.html",
            {
                "privacy_data_discovery_enabled": enabled,
                "privacy_data_discovery_api_base": "/api/v1/privacy/data-discovery",
                "privacy_data_discovery_page_path": "/privacy/data-discovery",
                "privacy_data_discovery_can_operate": True,
                "privacy_data_discovery_can_audit": True,
                "privacy_data_discovery_can_admin": False,
            },
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_renders_full_operator_contract(page_client: TestClient) -> None:
    response = page_client.get("/privacy/data-discovery")
    assert response.status_code == 200
    assert "Privacy Data Discovery" in response.text
    assert "Six stock adapter contracts now ship" in response.text
    for provider in (
        "AWS S3",
        "Azure Blob",
        "Google Cloud Storage",
        "Snowflake metadata",
        "ServiceNow CMDB",
        "Microsoft Entra assets",
    ):
        assert provider in response.text
    assert "Unsupported formats" in response.text
    assert "Cooperative deadline" in response.text
    assert "Remediation boundary" in response.text
    assert "Managed retry boundary" in response.text
    assert "remediation.scheduler.enable" in response.text
    assert "Managed worker status is API-derived" in response.text
    assert "durable retry outbox" in response.text
    assert "Run leased scan — external side effect" in response.text
    assert "Four-eyes approval" in response.text
    assert "Run bounded outbox worker" in response.text
    assert "List bounded remediation work" in response.text
    assert "Reconcile exact data-map outcome" in response.text
    assert "verify integrity" in response.text
    assert "Purge expired reports" in response.text
    assert "Verify audit chain" in response.text
    assert "/api/v1/privacy/data-discovery" in response.text
    assert 'data-copilot-page="/privacy/data-discovery"' in response.text
    assert "/static/lightwork-privacy-data-discovery.css" in response.text
    assert "/static/lightwork-privacy-data-discovery.js" in response.text
    assert response.text.count("/static/lightwork-privacy-data-discovery.js") == 1
    assert 'data-can-admin="false"' in response.text
    assert 'data-requires="admin"' in response.text
    assert response.text.count('data-requires="audit"') == 2
    assert 'href="/">Back to Overview</a>' in response.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "privacy_data_discovery.html"
    ).read_text(encoding="utf-8")
    assert ".innerHTML" not in template
    assert "<script>" not in template
    assert "credential_value" not in template
    assert "evidence_key_value" not in template


def test_page_renders_fail_closed_gate_guidance(page_client: TestClient) -> None:
    response = page_client.get(
        "/privacy/data-discovery",
        params={"enabled": False},
    )
    assert response.status_code == 200
    assert 'data-module-gate="privacy_automation.data_discovery"' in response.text
    assert "Privacy data discovery is turned off." in response.text
    assert "privacy_automation.data_discovery.enable" not in response.text


def test_main_dashboard_mounts_privacy_discovery_api_page_and_visibility() -> None:
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/privacy/data-discovery" in paths
    assert "/api/v1/privacy/data-discovery/status" in paths
    page = page_for("/privacy/data-discovery")
    assert page is not None
    assert page["floor"] == "view"
    assert page["default"] == "view"
    assert page["in_nav"] is True
