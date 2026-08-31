from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient


@pytest.fixture
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

    @app.get("/governance-agents")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "governance_agents.html",
            {"governance_agents_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_exposes_all_agents_and_confirmation_gated_operations(page_client) -> None:
    response = page_client.get("/governance-agents")
    assert response.status_code == 200
    page = response.text
    for label in (
        "Breach response",
        "Privacy risk",
        "Program copilot",
        "Knowledgebase governance",
        "Third-party risk",
    ):
        assert label in page
    assert "Decision support, not autonomous authority" in page
    assert "/api/v1/governance-agents" in page
    for operation in (
        "policy-publish",
        "policy-revoke",
        "analysis",
        "review",
        "execute",
        "recover",
        "run-revoke",
        "retention-purge",
        "integrity",
    ):
        assert f'value="{operation}"' in page
    assert "ga-confirm" in page
    assert "textContent" in page


def test_page_fails_closed_and_static_dom_is_safe(page_client) -> None:
    disabled = page_client.get("/governance-agents", params={"enabled": False})
    assert disabled.status_code == 200
    assert "Governance agents disabled" in disabled.text
    assert "governance_agents" in disabled.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "governance_agents.html"
    ).read_text(encoding="utf-8")
    for unsafe in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "eval("):
        assert unsafe not in template
    for forbidden in ('name="tenant_id"', 'name="actor_ref"', 'name="content"'):
        assert forbidden not in template


def test_dashboard_mounts_page_api_visibility_and_security_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-governance-agents.toml")),
    )
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/governance-agents" in paths
    assert "/api/v1/governance-agents/status" in paths
    assert "/api/v1/governance-agents/runs/{run_id}/execute" in paths
    page = page_for("/governance-agents")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
    security = (
        Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates" / "security.html"
    ).read_text(encoding="utf-8")
    assert 'href="/governance-agents"' in security
