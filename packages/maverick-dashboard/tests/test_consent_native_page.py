from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import consent_native_page as page
from maverick_dashboard import copilot_context, ui_visibility


def _templates() -> Jinja2Templates:
    selected = Jinja2Templates(directory=Path(page.__file__).parent / "templates")
    selected.env.globals.update(
        theme="dark",
        font="default",
        lang="en",
        density="comfortable",
        custom_theme_css="",
        custom_theme_names=[],
        dir="ltr",
        nav_groups=[],
        t=lambda key: key,
    )
    return selected


def test_page_is_liquid_glass_copilot_wired_and_permission_aware(monkeypatch) -> None:
    monkeypatch.setattr(page, "require_permission", lambda *_: None)
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    monkeypatch.setattr(
        page,
        "has_global_permission",
        lambda _request, permission: permission == "audit",
    )
    app = FastAPI()
    app.include_router(page.create_consent_native_page_router(_templates()))

    response = TestClient(app).get(page.PAGE_PATH)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "'unsafe-inline'" not in response.headers["content-security-policy"]
    assert "style-src 'self' 'nonce-" in response.headers["content-security-policy"]
    body = response.text
    assert "Native consent" in body
    assert f'data-copilot-page="{page.PAGE_PATH}"' in body
    assert page.ASSET_PATH in body
    assert "backdrop-filter:blur(26px)" in body
    assert 'data-can-audit="true"' in body
    assert 'data-can-admin="false"' in body
    assert 'id="nca-register-form"' in body
    assert 'id="nca-device-rotate-form"' in body
    assert 'id="nca-device-revoke-form"' in body
    assert 'id="nca-integrity" type="button"' in body
    assert f'id="nca-public-key-link" href="{page.PUBLIC_KEYS_BASE}"' in body
    assert '<fieldset disabled>' in body
    assert "tenant_id" not in body
    assert "principal_ref" not in body


def test_page_rejects_unknown_query_parameters(monkeypatch) -> None:
    monkeypatch.setattr(page, "require_permission", lambda *_: None)
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    app = FastAPI()
    app.include_router(page.create_consent_native_page_router(_templates()))

    response = TestClient(app).get(page.PAGE_PATH + "?tenant_id=attacker")

    assert response.status_code == 404


def test_page_has_nav_and_closed_copilot_context() -> None:
    visibility = next(item for item in ui_visibility.PAGES if item["path"] == page.PAGE_PATH)
    assert visibility == {
        "path": "/consent/native",
        "label": "Native Consent SDK",
        "group": "Consent",
        "icon": "key",
        "floor": "view",
        "default": "audit",
        "in_nav": True,
    }
    context = copilot_context.context_for_page(page.PAGE_PATH)
    assert context.title == "Native Consent SDK"
    assert any("no permissive fallback" in item for item in context.boundaries)
    assert any("live-device" in item for item in context.boundaries)
