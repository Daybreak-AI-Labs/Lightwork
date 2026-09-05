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

    @app.get("/compliance/policy-links")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "policy_publication_links.html",
            {"policy_publication_links_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_operator_page_renders_full_safe_contract(page_client: TestClient) -> None:
    response = page_client.get("/compliance/policy-links")
    assert response.status_code == 200
    assert "Policy Publication Links" in response.text
    assert "Four eyes" in response.text
    assert "Choose the published version" in response.text
    assert "Schedule publication" in response.text
    assert "Process or retry scheduled work" in response.text
    assert "Audit, access, retention, integrity" in response.text
    assert "not live customer" in response.text
    assert "Production use still requires encrypted storage" in response.text
    assert "performs no outbound network call" in response.text
    assert 'type="password"' in response.text
    assert "/api/v1/policy-publication-links" in response.text
    assert "textContent" in response.text
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "policy_publication_links.html"
    ).read_text(encoding="utf-8")
    assert ".innerHTML" not in template
    assert "tenant_id" not in template


def test_operator_page_explains_disabled_gate(page_client: TestClient) -> None:
    response = page_client.get(
        "/compliance/policy-links",
        params={"enabled": False},
    )
    assert response.status_code == 200
    assert 'data-module-gate="tech_risk_compliance.policy_publication_links"' in response.text
    assert "[tech_risk_compliance.policy_publication_links] enable = true" not in response.text
    assert "live customer" in response.text


def test_main_dashboard_mounts_only_the_public_retrieval_auth_exemption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maverick_dashboard.app import app
    from maverick_dashboard.auth import _self_authenticated_path
    from maverick_dashboard.ui_visibility import page_for

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", "dashboard-secret")
    paths = set(app.openapi()["paths"])
    assert "/api/v1/policy-publication-links/links" in paths
    assert "/api/v1/public/policies/{link_id}" in paths
    assert "/compliance/policy-links" in paths
    assert _self_authenticated_path("/api/v1/public/policies/PPL-opaque")
    assert not _self_authenticated_path("/api/v1/policy-publication-links/links")
    assert not _self_authenticated_path("/api/v1/public/policies-admin")

    page = page_for("/compliance/policy-links")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False

    client = TestClient(app, headers={"Origin": "http://testserver"})
    assert client.get("/api/v1/public/policies/PPL-opaque").status_code == 404
    assert client.get("/api/v1/policy-publication-links/audit").status_code == 401
