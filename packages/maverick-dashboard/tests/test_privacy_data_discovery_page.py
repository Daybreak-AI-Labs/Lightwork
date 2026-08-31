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
            {"privacy_data_discovery_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_renders_full_operator_contract(page_client: TestClient) -> None:
    response = page_client.get("/privacy/data-discovery")
    assert response.status_code == 200
    assert "Privacy Data Discovery" in response.text
    assert "No live provider adapters or provider/customer acceptance" in response.text
    assert "Unsupported formats" in response.text
    assert "Cooperative deadline" in response.text
    assert "Callback outbox limitation" in response.text
    assert "Run leased scan — external side effect" in response.text
    assert "Four-eyes approval" in response.text
    assert "Purge expired reports" in response.text
    assert "Verify audit chain" in response.text
    assert "/api/v1/privacy/data-discovery" in response.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "privacy_data_discovery.html"
    ).read_text(encoding="utf-8")
    assert ".innerHTML" not in template
    assert "credential_value" not in template
    assert "evidence_key_value" not in template


def test_page_renders_fail_closed_gate_guidance(page_client: TestClient) -> None:
    response = page_client.get(
        "/privacy/data-discovery",
        params={"enabled": False},
    )
    assert response.status_code == 200
    assert "Privacy data discovery disabled" in response.text
    assert "privacy_automation.data_discovery.enable" in response.text
    assert "fails closed" in response.text


def test_main_dashboard_mounts_privacy_discovery_api_page_and_visibility() -> None:
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/privacy/data-discovery" in paths
    assert "/api/v1/privacy/data-discovery/status" in paths
    page = page_for("/privacy/data-discovery")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
