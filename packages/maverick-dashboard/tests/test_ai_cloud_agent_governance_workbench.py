from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import ai_cloud_agent_governance_workbench as workbench


@pytest.fixture()
def environment(monkeypatch: pytest.MonkeyPatch):
    from maverick_dashboard.app import _theme_context

    template_dir = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"
    templates = Jinja2Templates(
        directory=str(template_dir),
        context_processors=[_theme_context],
    )
    templates.env.globals.update(
        nav_groups=[],
    )
    state = {"enabled": True}
    monkeypatch.setattr(workbench, "_tenant_id", lambda: "tenant-cloud-agents")
    monkeypatch.setattr(
        workbench,
        "require_permission",
        lambda _request, _permission: None,
    )
    app = FastAPI()
    app.state.ai_cloud_agent_governance_runtime_factory = lambda *_args: None
    app.include_router(
        workbench.create_ai_cloud_agent_governance_workbench_router(
            templates,
            enabled_probe=lambda _tenant: bool(state["enabled"]),
        )
    )
    return (
        TestClient(app, base_url="http://127.0.0.1", headers={"Origin": "http://127.0.0.1"}),
        state,
        template_dir,
    )


def test_workbench_lists_records_with_row_actions_in_plain_language(environment) -> None:
    client, _state, template_dir = environment
    response = client.get("/assurance/cloud-agents")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    page = response.text
    assert "Cloud AI agents" in page
    assert 'data-api="/api/v1/assurance/cloud-agents"' in page
    for table in ("cag-agents", "cag-checks", "cag-changes", "cag-reviews"):
        assert f'id="{table}"' in page
    for label in ("Check records haven", "Run the check", "Confirm what happened",
                  "Give the second approval", "Technical details"):
        assert label in page
    # No operation console, typed references or JSON boxes remain.
    assert "cag-operation" not in page and "cag-path-values" not in page
    assert "<textarea" not in page
    text = _visible_text(page)
    assert _BANNED.search(text) is None, _BANNED.search(text)
    script = (template_dir.parent / "static" / "lightwork-ai-cloud-agent-governance.js").read_text(
        encoding="utf-8"
    )
    for path in ('"/inventory?limit=500"', '"/policy-evaluations"', '"/enforcement-intents"',
                 '"/databricks/framework"', '"/policies"'):
        assert path in script
    for unsafe in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "eval(", "window.confirm("):
        assert unsafe not in script
    assert "window.mvConfirm" in script and "crypto.subtle.digest" in script


_BANNED = re.compile(
    r"\b(tenant|principals?|payload|endpoint|schema|uuid|sha-?256|digest|"
    r"idempoten\w*|revision|json|yaml|toml|adapter|intent|reconcil\w*|attest\w*)\b"
    r"|\[[a-z_]+\]|/api/v1|HTTP \d{3}",
    re.IGNORECASE,
)


def _visible_text(html: str) -> str:
    main = html.split('<main id="content"', 1)[-1].split("</main>", 1)[0]
    main = re.sub(r"<(script|style)\b.*?</\1>", " ", main, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"<[^>]+>", " ", main)


def test_workbench_fails_closed_and_dom_does_not_create_html_sinks(environment) -> None:
    client, state, template_dir = environment
    state["enabled"] = False
    disabled = client.get("/assurance/cloud-agents")
    assert disabled.status_code == 200
    assert "Cloud AI agents are turned off" in disabled.text
    monkeypatch_target = workbench._tenant_id
    workbench._tenant_id = lambda: ""
    try:
        unavailable = client.get("/assurance/cloud-agents")
    finally:
        workbench._tenant_id = monkeypatch_target
    assert unavailable.status_code == 200
    assert "Choose a workspace first" in unavailable.text
    assert 'id="cag-root"' not in unavailable.text  # fails closed: no working controls
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


def _page(monkeypatch, *, tenant, probe=None, app_probe=None, with_factory=True):
    """Render the page for one availability situation and return its visible text."""
    from maverick_dashboard.app import _theme_context

    template_dir = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"
    templates = Jinja2Templates(directory=str(template_dir), context_processors=[_theme_context])
    templates.env.globals.update(
        {
            "theme": "dark", "font": "default", "lang": "en", "density": "comfortable",
            "custom_theme_css": "", "custom_theme_names": [], "dir": "ltr",
            "nav_groups": [], "t": lambda key: key,
        }
    )
    monkeypatch.setattr(workbench, "_tenant_id", lambda: tenant)
    monkeypatch.setattr(workbench, "require_permission", lambda _request, _permission: None)
    app = FastAPI()
    if app_probe is not None:
        app.state.ai_cloud_agent_governance_enabled_probe = app_probe
    if with_factory:
        app.state.ai_cloud_agent_governance_runtime_factory = lambda *_args: None
    app.include_router(
        workbench.create_ai_cloud_agent_governance_workbench_router(templates, enabled_probe=probe)
    )
    response = TestClient(app, base_url="http://127.0.0.1", headers={"Origin": "http://127.0.0.1"}).get(
        "/assurance/cloud-agents"
    )
    assert response.status_code == 200
    assert 'id="cag-root"' not in response.text  # never a working page in these states
    return _visible_text(response.text), response.text


def test_workbench_asks_for_a_workspace_only_when_none_is_selected(monkeypatch) -> None:
    text, html = _page(monkeypatch, tenant="", probe=lambda _tenant: True)
    assert "Choose a workspace first" in text
    assert 'href="/workspace/tenant?next=/assurance/cloud-agents"' in html
    assert "not available in this installation" not in text


def test_workbench_does_not_blame_the_workspace_when_the_service_is_not_connected(
    monkeypatch,
) -> None:
    """A workspace IS selected; nothing on this installation supplies the cloud
    agent service. Telling the user to choose a workspace (and offering only
    Reload) sent them round a loop that can never end."""
    text, html = _page(monkeypatch, tenant="local", probe=None)
    assert "Choose a workspace" not in text
    assert "not available in this installation" in text
    assert 'href="/help"' in html  # a real next step
    assert "Reload" not in text  # reloading cannot fix it
    assert _BANNED.search(text) is None, _BANNED.search(text)

    no_workspace, _html = _page(monkeypatch, tenant="", probe=None)
    assert "not available in this installation" in no_workspace
    assert "Choose a workspace" not in no_workspace


def test_workbench_does_not_offer_actions_without_runtime_factory(monkeypatch) -> None:
    text, html = _page(
        monkeypatch, tenant="local", app_probe=lambda _tenant: True, with_factory=False
    )
    assert "not available in this installation" in text
    assert 'id="cag-root"' not in html


def test_workbench_offers_reload_only_when_the_service_could_not_be_reached(
    monkeypatch,
) -> None:
    def _broken(_tenant):
        raise RuntimeError("probe failed")

    text, html = _page(monkeypatch, tenant="local", probe=_broken)
    assert "couldn't reach" in text
    assert "Reload" in text
    assert "Choose a workspace" not in text
    assert _BANNED.search(text) is None, _BANNED.search(text)

    not_a_bool, _html = _page(monkeypatch, tenant="local", probe=lambda _tenant: "yes")
    assert "couldn't reach" in not_a_bool


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


def test_dashboard_mounts_page_api_but_hides_unavailable_workbench() -> None:
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import not_ready_paths, page_for

    paths = set(app.openapi()["paths"])
    assert "/assurance/cloud-agents" in paths
    assert "/api/v1/assurance/cloud-agents/status" in paths
    assert "/api/v1/assurance/cloud-agents/inventory" in paths
    selected = page_for("/assurance/cloud-agents")
    assert selected is not None
    assert selected["floor"] == "operate"
    assert selected["in_nav"] is True
    assert "/assurance/cloud-agents" in not_ready_paths()
    security = (
        Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates" / "security.html"
    ).read_text(encoding="utf-8")
    assert 'href="/assurance/cloud-agents"' not in security


def test_cloud_agent_script_is_served() -> None:
    from maverick_dashboard.app import app

    response = TestClient(app, base_url="http://127.0.0.1").get("/static/lightwork-ai-cloud-agent-governance.js")
    assert response.status_code == 200
    assert "cag-root" in response.text
