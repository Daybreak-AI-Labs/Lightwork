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

    @app.get("/privacy/rights-consumer-delivery")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "privacy_rights_consumer_delivery.html",
            {"privacy_rights_consumer_delivery_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_exposes_honest_one_time_operator_workbench(page_client) -> None:
    response = page_client.get("/privacy/rights-consumer-delivery")
    assert response.status_code == 200
    page = response.text
    assert "Privacy Rights Consumer Delivery" in page
    assert "appears only in the first mint response" in page
    assert "stores only its digest and cannot replay it" in page
    assert "never accepts a tenant, actor, consumer subject" in page
    assert "no consumer redeem" in page
    assert "Delivery requirements" in page
    assert "independently verify the intended recipient" in page
    assert "does not prove the recipient's identity" in page
    assert "/api/v1/privacy/rights-consumer-delivery" in page
    for operation in (
        "delivery-mint",
        "delivery-get",
        "delivery-revoke",
        "export-create",
        "export-get",
        "retention-purge",
        "integrity",
    ):
        assert f'value="{operation}"' in page
    assert 'credentials: "same-origin"' in page
    assert 'cache: "no-store"' in page
    assert "textContent" in page


def test_page_fails_closed_and_template_uses_safe_ephemeral_dom(page_client) -> None:
    disabled = page_client.get(
        "/privacy/rights-consumer-delivery",
        params={"enabled": False},
    )
    assert disabled.status_code == 200
    assert 'data-module-gate="privacy_automation.rights_consumer_delivery"' in disabled.text
    assert "Privacy-rights consumer delivery is turned off." in disabled.text
    assert "[privacy_automation.rights_consumer_delivery] enable = true" not in disabled.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "privacy_rights_consumer_delivery.html"
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
        'name="consumer_subject"',
        'name="capability"',
    ):
        assert forbidden not in template


def test_dashboard_mounts_page_api_visibility_and_privacy_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-rights-delivery.toml")),
    )
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/privacy/rights-consumer-delivery" in paths
    assert "/api/v1/privacy/rights-consumer-delivery/status" in paths
    assert "/api/v1/privacy/rights-consumer-delivery/deliveries/{delivery_id}/revoke" in paths
    page = page_for("/privacy/rights-consumer-delivery")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
    privacy = (
        Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates" / "privacy.html"
    ).read_text(encoding="utf-8")
    assert 'href="/privacy/rights-consumer-delivery"' in privacy
    from maverick_dashboard.copilot_context import context_for_page

    assert (
        context_for_page("/privacy/rights-consumer-delivery").path
        == "/privacy/rights-consumer-delivery"
    )
