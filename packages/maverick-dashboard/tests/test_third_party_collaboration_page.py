from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient


def _client(*, enabled: bool) -> TestClient:
    templates = Jinja2Templates(
        directory=str(
            Path(__file__).parents[1]
            / "maverick_dashboard"
            / "templates"
        )
    )
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

    @app.get("/third-party/collaboration")
    async def page(request: Request):
        return templates.TemplateResponse(
            request,
            "third_party_collaboration.html",
            {"third_party_collaboration_enabled": enabled},
        )

    return TestClient(app)


def test_enabled_page_exposes_only_closed_operator_workbench() -> None:
    page = _client(enabled=True).get("/third-party/collaboration")
    assert page.status_code == 200
    text = page.text
    assert "/api/v1/third-party/collaboration" in text
    assert "Issue invitation" in text
    assert "Rotate credential" in text
    assert "Request follow-up" in text
    assert "Verify integrity" in text
    assert "credential, provider decision" in text
    assert "independently verify respondent attestations" in text
    assert "provider_decision" in text
    assert "risk_score" in text
    assert "content" in text
    assert "bytes" in text


def test_disabled_page_is_inert_and_names_all_required_gates() -> None:
    page = _client(enabled=False).get("/third-party/collaboration")
    assert page.status_code == 200
    text = page.text
    assert "Collaboration portal disabled" in text
    assert "third_party_management.lifecycle.portal" in text
    assert "deployment-owned public-reference" in text
    assert "/api/v1/third-party/collaboration" not in text


def test_shipped_app_wires_page_api_and_security_link() -> None:
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/third-party/collaboration" in paths
    assert "/api/v1/third-party/collaboration/status" in paths
    assert (
        "/api/v1/external/third-party-collaboration/{portal_ref}/contract"
        in paths
    )
    page = page_for("/third-party/collaboration")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
    security = (
        Path(__file__).parents[1]
        / "maverick_dashboard"
        / "templates"
        / "security.html"
    ).read_text(encoding="utf-8")
    assert 'href="/third-party/collaboration"' in security
