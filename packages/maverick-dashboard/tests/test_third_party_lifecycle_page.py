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

    @app.get("/third-party/lifecycle")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "third_party_lifecycle.html",
            {"third_party_lifecycle_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_is_complete_honest_and_confirmation_gated(page_client) -> None:
    response = page_client.get("/third-party/lifecycle")
    assert response.status_code == 200
    page = response.text
    assert "Third-Party Lifecycle" in page
    assert "No network transport, provider writes, or live acceptance" in page
    assert "governed delivery intent only" in page
    assert "/api/v1/third-party/lifecycle" in page
    for operation in (
        "template-create",
        "template-approve",
        "assessment-response",
        "assessment-evaluate",
        "assessment-task",
        "offboarding-begin",
        "legal-hold",
        "offboarding-task",
        "independent-completion",
        "callback-claim",
        "callback-ack",
        "callback-requeue",
        "lifecycle-export",
        "retention-purge",
        "integrity",
    ):
        assert f'value="{operation}"' in page
    assert "tpl-confirm" in page
    assert "textContent" in page


def test_page_fails_closed_and_static_dom_is_safe(page_client) -> None:
    disabled = page_client.get("/third-party/lifecycle", params={"enabled": False})
    assert disabled.status_code == 200
    assert 'data-module-gate="third_party_management.lifecycle"' in disabled.text
    assert "[third_party_management] enable = true" not in disabled.text
    assert "[third_party_management.lifecycle] enable = true" not in disabled.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "third_party_lifecycle.html"
    ).read_text(encoding="utf-8")
    for unsafe in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "eval("):
        assert unsafe not in template
    for forbidden in ('name="tenant_id"', 'name="actor_ref"'):
        assert forbidden not in template


def test_dashboard_mounts_page_api_visibility_and_security_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-third-party-lifecycle.toml")),
    )
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/third-party/lifecycle" in paths
    assert "/api/v1/third-party/lifecycle/status" in paths
    assert "/api/v1/third-party/lifecycle/offboarding/{plan_id}/complete" in paths
    page = page_for("/third-party/lifecycle")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
    security = (
        Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates" / "security.html"
    ).read_text(encoding="utf-8")
    assert 'href="/third-party/lifecycle"' in security
