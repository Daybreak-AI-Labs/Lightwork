from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import ai_cloud_agent_governance_workbench as workbench


@pytest.fixture()
def environment(monkeypatch: pytest.MonkeyPatch):
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
    state = {"enabled": True}
    monkeypatch.setattr(workbench, "_tenant_id", lambda: "tenant-cloud-agents")
    monkeypatch.setattr(
        workbench,
        "require_permission",
        lambda _request, _permission: None,
    )
    app = FastAPI()
    app.include_router(
        workbench.create_ai_cloud_agent_governance_workbench_router(
            templates,
            enabled_probe=lambda _tenant: bool(state["enabled"]),
        )
    )
    return (
        TestClient(app, headers={"Origin": "http://testserver"}),
        state,
        template_dir,
    )


def test_workbench_exposes_only_governed_bounded_operations(environment) -> None:
    client, _state, _template_dir = environment
    response = client.get("/assurance/cloud-agents")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    page = response.text
    assert "Managed-cloud AI agent governance" in page
    assert "Adapter acceptance is not proof" in page
    assert "/api/v1/assurance/cloud-agents" in page
    for operation in (
        "inventory",
        "integrity",
        "evaluate",
        "prepare",
        "intent-get",
        "dispatch",
        "reconcile",
        "framework-review",
        "framework-attest",
        "framework-activate",
    ):
        assert f'value="{operation}"' in page
    assert "cag-confirm" in page
    assert "textContent" in page
    assert "provider-bindings" not in page
    assert "inventory-sync" not in page


def test_workbench_fails_closed_and_dom_does_not_create_html_sinks(environment) -> None:
    client, state, template_dir = environment
    state["enabled"] = False
    disabled = client.get("/assurance/cloud-agents")
    assert disabled.status_code == 200
    assert "Cloud-agent governance disabled" in disabled.text
    monkeypatch_target = workbench._tenant_id
    workbench._tenant_id = lambda: ""
    try:
        unavailable = client.get("/assurance/cloud-agents")
    finally:
        workbench._tenant_id = monkeypatch_target
    assert unavailable.status_code == 200
    assert "Configuration authority unavailable" in unavailable.text
    template = (template_dir / "ai_cloud_agent_governance.html").read_text(encoding="utf-8")
    for unsafe in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "eval("):
        assert unsafe not in template
    for forbidden in (
        'name="tenant_id"',
        'name="principal_ref"',
        'name="credential"',
        'name="provider"',
        'name="native_resource_id"',
    ):
        assert forbidden not in template


def test_workbench_factory_rejects_invalid_dependencies() -> None:
    with pytest.raises(TypeError, match="Jinja"):
        workbench.create_ai_cloud_agent_governance_workbench_router(object())  # type: ignore[arg-type]
    template_dir = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"
    templates = Jinja2Templates(directory=str(template_dir))
    with pytest.raises(TypeError, match="probe"):
        workbench.create_ai_cloud_agent_governance_workbench_router(
            templates,
            enabled_probe=True,  # type: ignore[arg-type]
        )


def test_dashboard_mounts_page_api_visibility_and_security_link() -> None:
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    assert "/assurance/cloud-agents" in paths
    assert "/api/v1/assurance/cloud-agents/status" in paths
    assert "/api/v1/assurance/cloud-agents/inventory" in paths
    selected = page_for("/assurance/cloud-agents")
    assert selected is not None
    assert selected["floor"] == "operate"
    assert selected["in_nav"] is True
    security = (
        Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates" / "security.html"
    ).read_text(encoding="utf-8")
    assert 'href="/assurance/cloud-agents"' in security
