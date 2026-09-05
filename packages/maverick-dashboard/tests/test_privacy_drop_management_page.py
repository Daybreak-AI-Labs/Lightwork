from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import privacy_drop_management_page as page

ROUTE = "/privacy/drop"
PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    monkeypatch.setattr(page, "require_permission", lambda _request, _permission: None)
    monkeypatch.setattr(page, "has_permission", lambda _request, _permission: True)
    monkeypatch.setattr(
        page,
        "has_global_permission",
        lambda _request, _permission: True,
    )
    templates = Jinja2Templates(directory=str(PACKAGE / "templates"))
    templates.env.globals.update(
        theme="dark",
        font="default",
        lang="en",
        density="comfortable",
        custom_theme_css="",
        custom_theme_names=[],
        dir="ltr",
        nav_groups=[
            SimpleNamespace(
                id="privacy",
                label="Privacy",
                default_open=True,
                links=[
                    SimpleNamespace(href="/privacy", label="Privacy", icon="shield"),
                    SimpleNamespace(
                        href="/privacy/drop",
                        label="California DROP",
                        icon="shield",
                    ),
                ],
            )
        ],
        t=lambda key: key,
    )
    app = FastAPI()
    app.include_router(page.create_privacy_drop_management_page_router(templates))
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_drop_workbench_uses_shared_shell_and_fail_closed_controls(
    client: TestClient,
) -> None:
    response = client.get(ROUTE)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert "California DROP requests" in response.text
    assert 'id="drop-operations" disabled' in response.text
    assert 'src="/static/lightwork-privacy-drop-management.js"' in response.text
    assert 'src="/static/lightwork-copilot.js"' in response.text
    assert 'href="/static/lightwork.css"' in response.text
    assert "/api/v1/privacy/drop" in response.text
    assert "No subject identity enters this page" in response.text
    for control_id in (
        "drop-authorities",
        "drop-revisions",
        "drop-authority-integrity",
        "drop-broker-form",
        "drop-record-form",
        "drop-outcome-form",
        "drop-key-form",
        "drop-approval-form",
    ):
        assert f'id="{control_id}"' in response.text
    assert 'name="binding_digest"' in response.text
    assert "different authenticated administrator" in response.text


def test_drop_page_never_collects_server_owned_identity_material(
    client: TestClient,
) -> None:
    response = client.get(ROUTE)
    lowered = response.text.lower()
    for forbidden in (
        'name="tenant',
        'name="actor',
        'name="principal',
        'name="subject',
        'name="broker_registration',
        'name="outcome_evidence',
        'name="credential',
    ):
        assert forbidden not in lowered
    for required in (
        'name="drop_id"',
        'name="request_type"',
        'name="template_id"',
        'name="idempotency_key"',
        'name="expected_revision"',
        'name="status"',
        'name="confirmation"',
    ):
        assert required in lowered


def test_drop_page_marks_only_the_most_specific_navigation_link(
    client: TestClient,
) -> None:
    body = client.get(ROUTE).text

    assert body.count('aria-current="page"') == 1
    assert '<a href="/privacy/drop" class="active" aria-current="page">' in body
    assert '<a href="/privacy" class="active"' not in body


def test_drop_page_hides_global_controls_without_their_exact_permissions(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        page,
        "has_global_permission",
        lambda _request, permission: permission == "audit",
    )
    audit_only = client.get(ROUTE)

    assert audit_only.status_code == 200
    assert 'id="drop-integrity"' in audit_only.text
    assert 'id="drop-update-form"' not in audit_only.text
    assert 'id="drop-authorities"' in audit_only.text
    assert 'id="drop-broker-form"' not in audit_only.text
    assert "Global administrator permission is required" in audit_only.text

    monkeypatch.setattr(
        page,
        "has_global_permission",
        lambda _request, permission: permission == "admin",
    )
    admin_only = client.get(ROUTE)

    assert admin_only.status_code == 200
    assert 'id="drop-integrity"' not in admin_only.text
    assert 'id="drop-update-form"' in admin_only.text
    assert 'id="drop-authorities"' not in admin_only.text
    assert 'id="drop-broker-form"' in admin_only.text
    assert "Global audit permission is required" in admin_only.text


def test_drop_page_server_disables_create_for_non_operators(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(page, "has_permission", lambda _request, _permission: False)

    response = client.get(ROUTE)

    assert response.status_code == 200
    assert 'id="drop-create-controls" disabled aria-disabled="true"' in response.text
    assert "Operator permission is required to create" in response.text
    assert 'id="drop-get-form"' in response.text


def test_drop_page_rejects_query_material_and_requires_tenant(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert client.get(f"{ROUTE}?drop_id=12").status_code == 404
    assert client.get(f"{ROUTE}?theme=light&density=compact").status_code == 200

    def missing_tenant() -> None:
        raise page.HTTPException(
            status_code=409,
            detail="select an active tenant before using DROP management",
        )

    monkeypatch.setattr(page, "_tenant_selected", missing_tenant)
    assert client.get(ROUTE).status_code == 409


def test_drop_page_and_asset_are_mounted_and_registered() -> None:
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    routes = []
    for route in app.routes:
        if hasattr(route, "effective_candidates"):
            routes.extend(route.effective_candidates())
        else:
            routes.append(route)
    assert any(getattr(route, "path", None) == ROUTE for route in routes)
    assert any(
        getattr(route, "path", None) == "/static/lightwork-privacy-drop-management.js"
        for route in routes
    )
    selected = page_for(ROUTE)
    assert selected is not None
    assert selected["floor"] == "view"
    assert selected["in_nav"] is True
    for relative in (
        "privacy_drop_management_page.py",
        "templates/privacy_drop_management.html",
        "static/lightwork-privacy-drop-management.js",
    ):
        assert (PACKAGE / relative).is_file()


def test_drop_client_has_bounded_transport_and_safe_rendering() -> None:
    source = (PACKAGE / "static" / "lightwork-privacy-drop-management.js").read_text(
        encoding="utf-8"
    )
    for required in (
        'credentials: "same-origin"',
        'cache: "no-store"',
        "operations.disabled = true",
        "operations.disabled = false",
        "element.textContent",
        "Number.isSafeInteger",
        "encodeURIComponent(dropId)",
        "refreshStatus();",
        "MAX_RESPONSE_BYTES = 64 * 1024",
        'headers.get("Content-Length")',
        "response.body.getReader()",
        "reader.cancel()",
        "JSON.parse(responseText)",
        "readiness was verified for this workspace".capitalize(),
    ):
        assert required in source
    for forbidden in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "eval(",
        "response.json()",
    ):
        assert forbidden not in source
