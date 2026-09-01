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

    @app.get("/privacy/regulatory-intelligence")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "privacy_regulatory_intelligence.html",
            {"privacy_regulatory_intelligence_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_renders_operator_contract_and_no_unsafe_dom_sink(
    page_client: TestClient,
) -> None:
    response = page_client.get("/privacy/regulatory-intelligence")
    assert response.status_code == 200
    assert "Privacy Regulatory Intelligence" in response.text
    assert "does not bundle or reproduce OneTrust DataGuidance" in response.text
    assert "different authenticated reviewer" in response.text
    assert "explicit abstention" in response.text
    assert "not legal advice" in response.text
    assert "Saved-query tracker and durable delivery" in response.text
    assert "Verify retained-state integrity" in response.text
    assert "/api/v1/privacy/regulatory-intelligence" in response.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "privacy_regulatory_intelligence.html"
    ).read_text(encoding="utf-8")
    assert ".innerHTML" not in template
    assert "textContent" in template


def test_page_renders_fail_closed_gate_guidance(page_client: TestClient) -> None:
    response = page_client.get(
        "/privacy/regulatory-intelligence",
        params={"enabled": False},
    )
    assert response.status_code == 200
    assert 'data-module-gate="privacy_automation.regulatory_intelligence"' in response.text
    assert "Privacy regulatory intelligence is turned off." in response.text
    assert "privacy_automation.regulatory_intelligence.enable" not in response.text


def test_main_dashboard_mounts_privacy_intelligence_page_api_and_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-privacy-intelligence.toml")),
    )
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/privacy/regulatory-intelligence" in paths
    assert "/api/v1/privacy/regulatory-intelligence/status" in paths
    page = page_for("/privacy/regulatory-intelligence")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
