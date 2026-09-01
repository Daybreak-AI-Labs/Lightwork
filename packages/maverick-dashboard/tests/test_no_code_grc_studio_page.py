from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import no_code_grc_studio_page as page


def templates() -> Jinja2Templates:
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
    selected_templates = templates()
    monkeypatch.setattr(page, "require_permission", lambda *_: None)
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    monkeypatch.setattr(
        page, "has_permission", lambda _request, permission: permission == "operate"
    )
    monkeypatch.setattr(
        page, "has_global_permission", lambda _request, permission: permission == "audit"
    )
    app = FastAPI()
    app.include_router(page.create_no_code_grc_studio_page_router(selected_templates))
    response = TestClient(app).get(page.PAGE_PATH)
    assert response.status_code == 200
    body = response.text
    assert "No-code GRC studio" in body
    assert f'data-copilot-page="{page.PAGE_PATH}"' in body
    assert "backdrop-filter: blur(26px)" in body
    assert page.ASSET_PATH in body
    assert "Configuration without executable surface area" in body
    assert 'id="grcs-builder-form"' in body
    assert 'data-can-operate="true"' in body
    assert 'data-can-audit="true"' in body
    assert 'data-can-admin="false"' in body
    for composer_control in (
        "grcs-add-object",
        "grcs-add-field",
        "grcs-add-form",
        "grcs-add-rule",
        "grcs-add-binding",
        "grcs-add-view",
        "grcs-compose-blueprint",
    ):
        assert f'id="{composer_control}"' in body
    assert 'id="grcs-projection-form"' in body
    assert 'id="grcs-projection-apply"' in body
    assert 'id="grcs-retention-purge"' in body
    assert 'id="grcs-backup-verify-form"' in body
    assert "Content-Security-Policy" in response.headers
    assert "'unsafe-inline'" not in response.headers["Content-Security-Policy"]


def test_page_rejects_unknown_query_parameters(monkeypatch) -> None:
    selected_templates = templates()
    monkeypatch.setattr(page, "require_permission", lambda *_: None)
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    app = FastAPI()
    app.include_router(page.create_no_code_grc_studio_page_router(selected_templates))
    assert (
        TestClient(app).get(page.PAGE_PATH + "?return_to=https://attacker.invalid").status_code
        == 404
    )


def test_shipped_app_mounts_page_api_and_private_asset(monkeypatch) -> None:
    monkeypatch.setenv("MAVERICK_TENANT", "test-tenant")
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    from maverick_dashboard.app import app

    client = TestClient(app, headers={"Origin": "http://testserver"})
    assert client.get(page.PAGE_PATH).status_code == 200
    assert client.get(f"{page.API_BASE}/status").status_code == 200
    asset = client.get(page.ASSET_PATH)
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "no-store, private, max-age=0"
    assert asset.headers["x-content-type-options"] == "nosniff"
