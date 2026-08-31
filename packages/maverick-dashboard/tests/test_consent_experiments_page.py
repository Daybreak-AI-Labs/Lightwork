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

    @app.get("/consent/experiments")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "consent_experiments.html",
            {"consent_experiments_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_renders_full_governed_experiment_workflow(page_client: TestClient) -> None:
    response = page_client.get("/consent/experiments")
    assert response.status_code == 200
    page = response.text
    assert "Consent Experiments" in page
    assert "No live-estate acceptance" in page
    assert "No causal certainty" in page
    assert "creator cannot approve" in page
    assert "Legal privacy signals" in page
    assert "2–10 immutable variants" in page
    assert "20 sequential looks" in page
    assert "90 days" in page
    assert "/api/v1/consent/experiments" in page
    for element in (
        "ce-status-load",
        "ce-create",
        "ce-lifecycle",
        "ce-assignment",
        "ce-outcome",
        "ce-inspect",
        "ce-retention",
        "ce-output",
    ):
        assert f'id="{element}"' in page
    for action in (
        "approve",
        "activate",
        "pause",
        "resume",
        "kill",
        "rollback",
        "assign",
        "exposure",
        "analysis",
        "audit-export",
    ):
        assert action in page
    assert 'type="password"' in page
    assert "textContent" in page


def test_page_is_safe_and_explicit_when_independent_gate_is_off(
    page_client: TestClient,
) -> None:
    response = page_client.get("/consent/experiments", params={"enabled": False})
    assert response.status_code == 200
    assert "Consent experiments disabled" in response.text
    assert "consent_preferences.experiments" in response.text
    assert "production-scale acceptance" in response.text

    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "consent_experiments.html"
    ).read_text(encoding="utf-8")
    assert ".innerHTML" not in template
    assert "document.write" not in template
    assert "eval(" not in template


def test_main_dashboard_mounts_consent_experiment_page_api_and_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-consent-experiments.toml")),
    )
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/consent/experiments" in paths
    assert "/api/v1/consent/experiments/status" in paths
    page = page_for("/consent/experiments")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
