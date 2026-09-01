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
    async def page(request: Request, enabled: bool = True, role: str = "admin"):
        permissions = {
            "admin": {"operate", "admin", "audit"},
            "operator": {"operate"},
            "auditor": {"audit"},
            "viewer": set(),
        }.get(role, set())
        return templates.TemplateResponse(
            request,
            "governance_agents.html",
            {
                "governance_agents_enabled": enabled,
                "governance_can_operate": "operate" in permissions,
                "governance_can_admin": "admin" in permissions,
                "governance_can_audit": "audit" in permissions,
            },
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
        "maintenance-readiness",
        "adapter-rotate",
        "adapter-revoke",
        "dispatcher-rotate",
        "dispatcher-revoke",
        "custody-rotate",
        "custody-retire",
        "token-revoke",
        "token-reconcile",
        "integrity",
        "intent-list",
    ):
        assert f'value="{operation}"' in page
    assert "ga-confirm" in page
    assert "textContent" in page
    assert 'id="ga-operation-controls" disabled aria-disabled="true"' in page
    operation_options = page.split('<select id="ga-operation">', 1)[1].split(
        "</select>", 1
    )[0]
    assert operation_options.count("<option ") == operation_options.count(
        "<option value="
    )
    assert operation_options.count("<option value=") == operation_options.count(
        " disabled>"
    )
    assert "configured for an attempt" in page
    assert "does not open the store" in page


@pytest.mark.parametrize(
    ("role", "present", "absent"),
    [
        (
            "operator",
            {"analysis", "run-get", "run-list", "review"},
            {
                "policy-publish",
                "policy-revoke",
                "execute",
                "recover",
                "run-revoke",
                "retention-purge",
                "maintenance-readiness",
                "adapter-rotate",
                "adapter-revoke",
                "dispatcher-rotate",
                "dispatcher-revoke",
                "custody-rotate",
                "custody-retire",
                "token-revoke",
                "token-reconcile",
                "integrity",
                "intent-list",
            },
        ),
        (
            "auditor",
            {"run-get", "run-list", "integrity", "intent-list"},
            {
                "policy-publish",
                "policy-revoke",
                "analysis",
                "review",
                "execute",
                "recover",
                "run-revoke",
                "retention-purge",
                "maintenance-readiness",
                "adapter-rotate",
                "adapter-revoke",
                "dispatcher-rotate",
                "dispatcher-revoke",
                "custody-rotate",
                "custody-retire",
                "token-revoke",
                "token-reconcile",
            },
        ),
        (
            "viewer",
            {"run-get", "run-list"},
            {
                "policy-publish",
                "policy-revoke",
                "analysis",
                "review",
                "execute",
                "recover",
                "run-revoke",
                "retention-purge",
                "maintenance-readiness",
                "adapter-rotate",
                "adapter-revoke",
                "dispatcher-rotate",
                "dispatcher-revoke",
                "custody-rotate",
                "custody-retire",
                "token-revoke",
                "token-reconcile",
                "integrity",
                "intent-list",
            },
        ),
    ],
)
def test_page_server_renders_only_exact_role_operations(
    page_client: TestClient,
    role: str,
    present: set[str],
    absent: set[str],
) -> None:
    page = page_client.get("/governance-agents", params={"role": role}).text

    for operation in present:
        assert f'value="{operation}"' in page
    for operation in absent:
        assert f'value="{operation}"' not in page


def test_page_client_fails_closed_on_status_and_response_contracts() -> None:
    template = (
        Path(__file__).resolve().parents[1]
        / "maverick_dashboard"
        / "templates"
        / "governance_agents.html"
    ).read_text(encoding="utf-8")

    for required in (
        "MAX_RESPONSE_BYTES = 64 * 1024",
        'headers.get("Content-Length")',
        "response.body.getReader()",
        "reader.cancel()",
        "operations_configured_for_attempt",
        'assessment !== "configured_for_attempt_only"',
        'credentials: "same-origin"',
        'cache: "no-store"',
    ):
        assert required in template
    for forbidden in (
        "response.json()",
        "response.text()",
        "detail: text",
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "eval(",
    ):
        assert forbidden not in template


@pytest.mark.parametrize(
    ("role", "visible", "hidden"),
    [
        ("operator", "analysis", "integrity"),
        ("auditor", "integrity", "analysis"),
        ("admin", "policy-publish", None),
    ],
)
def test_dashboard_route_passes_exact_tenant_and_global_permissions(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    visible: str,
    hidden: str | None,
) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv(
        "MAVERICK_CONFIG",
        str(Path(__file__).with_name("nonexistent-governance-agents.toml")),
    )
    from maverick import config
    from maverick_dashboard import app as app_module
    from maverick_dashboard import auth, governance_agents_api

    config.reset_config_cache()
    permissions = {
        "operator": {"operate", "view"},
        "auditor": {"audit", "view"},
        "admin": {"admin", "audit", "operate", "view"},
    }

    def selected(request: Request) -> set[str]:
        return permissions[request.headers["X-Test-Role"]]

    def require_selected(request: Request, permission: str) -> None:
        assert permission in selected(request)

    monkeypatch.setattr(app_module, "require_permission", require_selected)
    monkeypatch.setattr(
        app_module,
        "has_permission",
        lambda request, permission: permission in selected(request),
    )
    monkeypatch.setattr(
        auth,
        "has_global_permission",
        lambda request, permission: permission in selected(request),
    )
    monkeypatch.setattr(
        governance_agents_api,
        "governance_agents_enabled_for_page",
        lambda: True,
    )
    response = TestClient(
        app_module.app,
        headers={"Origin": "http://testserver", "X-Test-Role": role},
    ).get("/governance-agents")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert f'value="{visible}"' in response.text
    if hidden is not None:
        assert f'value="{hidden}"' not in response.text


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
    assert page["floor"] == "view"
    assert page["in_nav"] is True
    security = (
        Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates" / "security.html"
    ).read_text(encoding="utf-8")
    assert 'href="/governance-agents"' in security
