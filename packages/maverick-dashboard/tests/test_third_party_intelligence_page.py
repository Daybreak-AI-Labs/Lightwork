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

    @app.get("/third-party/intelligence")
    async def page(request: Request, enabled: bool = True):
        return templates.TemplateResponse(
            request,
            "third_party_intelligence.html",
            {"third_party_intelligence_enabled": enabled},
        )

    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_exposes_complete_governed_workflows_and_exact_boundaries(
    page_client: TestClient,
) -> None:
    response = page_client.get("/third-party/intelligence")
    assert response.status_code == 200
    page = response.text
    assert "Third-party Intelligence" in page
    assert "No bundled corpus" in page
    assert "No provider acceptance" in page
    assert "No silent match dismissal" in page
    assert "Low-risk autoapproval is conditional" in page
    assert "No assumed exchange anonymity or privacy" in page
    assert "No raw credential or profile values" in page
    assert "external-operation acceptance" in page
    assert "different authenticated human" in page
    assert "/api/v1/third-party/intelligence" in page
    assert "Named provider runtime readiness" in page
    assert "RiskRecon" in page
    assert "SupplyWisdom" in page
    assert "ISS Corporate Solutions" in page
    assert "/provider-runtime/status" in page
    assert "/provider-runtime/sources/" in page
    assert "ready for an attempt" in page
    for heading in (
        "Source approval, revocation, and snapshots",
        "Pseudonymous vendor and relationship graph",
        "Screening, findings, review, and completion",
        "Risk policy, evaluation, override, and rollback",
        "Enhanced due diligence tasks, evidence, and decision",
        "Monitor, change alert, and durable outbox",
        "Consent-bound risk exchange",
        "Retention and integrity",
    ):
        assert heading in page
    operations = (
        "source-enroll",
        "source-approve",
        "source-revoke",
        "snapshot-record",
        "vendor-upsert",
        "relationship-upsert",
        "screening-start",
        "finding-record",
        "finding-review",
        "screening-complete",
        "policy-create",
        "policy-approve",
        "risk-evaluate",
        "risk-override",
        "risk-rollback",
        "edd-open",
        "edd-task",
        "edd-evidence",
        "edd-decision",
        "monitor-create",
        "monitor-observe",
        "alert-get",
        "outbox-claim",
        "outbox-run",
        "outbox-fail",
        "outbox-ack",
        "monitor-revoke",
        "exchange-consent",
        "exchange-review",
        "exchange-contribute",
        "exchange-query",
        "exchange-withdraw",
        "retention-purge",
        "integrity",
    )
    for operation in operations:
        assert f'value="{operation}"' in page
    assert "tpi-load-status" in page
    assert "tpi-load-provider-status" in page
    assert "tpi-provider-source-readiness" in page
    assert "tpi-provider-output" in page
    assert "tpi-output" in page
    assert "textContent" in page
    assert "window.confirm" in page
    assert "external notification side effect" in page


def test_page_is_fail_closed_and_uses_no_unsafe_dom_or_secret_inputs(
    page_client: TestClient,
) -> None:
    response = page_client.get(
        "/third-party/intelligence",
        params={"enabled": False},
    )
    assert response.status_code == 200
    assert "Third-party intelligence disabled" in response.text
    assert "third_party_management.intelligence" in response.text
    assert "fails closed" in response.text

    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "third_party_intelligence.html"
    ).read_text(encoding="utf-8")
    assert ".innerHTML" not in template
    assert ".outerHTML" not in template
    assert "insertAdjacentHTML" not in template
    assert "document.write" not in template
    assert "eval(" not in template
    assert 'name="tenant_id"' not in template
    assert 'name="actor_ref"' not in template
    assert 'name="credential_value"' not in template
    assert 'name="raw_profile"' not in template


def test_main_dashboard_mounts_third_party_intelligence_page_api_and_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-third-party-intelligence.toml")),
    )
    from maverick import config

    config.reset_config_cache()
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/third-party/intelligence" in paths
    assert "/api/v1/third-party/intelligence/status" in paths
    assert "/api/v1/third-party/intelligence/provider-runtime/status" in paths
    assert (
        "/api/v1/third-party/intelligence/provider-runtime/sources/"
        "{source_id}/readiness"
    ) in paths
    assert "/api/v1/third-party/intelligence/outbox/run" in paths
    assert app.state.third_party_intelligence_provider_runtime is not None
    assert app.state.third_party_intelligence_feed_scheduler is not None
    page = page_for("/third-party/intelligence")
    assert page is not None
    assert page["floor"] == "operate"
    assert page["in_nav"] is False
