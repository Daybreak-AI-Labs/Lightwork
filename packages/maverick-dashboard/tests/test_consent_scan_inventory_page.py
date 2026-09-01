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

    @app.get("/consent/scan-inventory")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "consent_scan_inventory.html",
            {"consent_scan_inventory_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_scan_inventory_page_renders_safe_operator_contract(page_client: TestClient) -> None:
    response = page_client.get("/consent/scan-inventory")
    assert response.status_code == 200
    assert "Consent Scan Inventory" in response.text
    assert "External side effect" in response.text
    assert "prove that no tracker exists" in response.text
    assert "Every discovery starts as unknown" in response.text
    assert "Run leased scan" in response.text
    assert "Dead letters" in response.text
    assert "Bridge fresh reviewed mappings" in response.text
    assert "Retention status" in response.text
    assert 'type="password"' in response.text
    assert 'name="after_tracker_key"' in response.text
    assert "next exact discovery page" in response.text
    assert "/api/v1/consent/scan-inventory" in response.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "consent_scan_inventory.html"
    ).read_text(encoding="utf-8")
    assert ".innerHTML" not in template


def test_scan_inventory_page_renders_when_gate_is_off(page_client: TestClient) -> None:
    response = page_client.get("/consent/scan-inventory", params={"enabled": False})
    assert response.status_code == 200
    assert "Scan inventory disabled" in response.text
    assert "consent_preferences.scanner" in response.text
    assert "Live acceptance flags remain false" in response.text
