from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import agent_control_plane_page as page


def _templates() -> Jinja2Templates:
    selected = Jinja2Templates(directory=Path(page.__file__).parent / "templates")
    selected.env.globals.update(
        theme="dark", font="default", lang="en", density="comfortable",
        custom_theme_css="", custom_theme_names=[], dir="ltr", nav_groups=[],
        t=lambda key: key,
    )
    return selected


def _app(monkeypatch, allowed) -> TestClient:
    monkeypatch.setattr(page, "require_permission", lambda *_: None)
    monkeypatch.setattr(page, "has_permission",
                        lambda _request, permission: permission in allowed)
    app = FastAPI()
    app.include_router(page.create_agent_control_plane_page_router(_templates()))
    return TestClient(app)


def test_page_is_wired_hardened_and_permission_aware(monkeypatch) -> None:
    response = _app(monkeypatch, {"operate", "audit"}).get(page.PAGE_PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    csp = response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in csp and "style-src 'self' 'nonce-" in csp
    body = response.text
    assert "Agent Control Plane" in body
    assert f'data-copilot-page="{page.PAGE_PATH}"' in body
    assert f'data-api-base="{page.API_BASE}"' in body
    assert page.ASSET_PATH in body
    assert 'data-can-operate="true"' in body
    assert 'data-can-audit="true"' in body
    assert 'data-can-admin="false"' in body
    # Admin-only verbs are not offered to an operator.
    assert '<option value="contain">' not in body
    assert '<option value="ledger">' in body
    assert 'id="acp-clear-halt" type="button" disabled' in body


def test_admin_sees_every_verb(monkeypatch) -> None:
    body = _app(monkeypatch, {"operate", "audit", "admin"}).get(page.PAGE_PATH).text
    assert '<option value="contain">' in body and '<option value="release">' in body
    assert 'data-can-admin="true"' in body


def test_unknown_query_params_are_404(monkeypatch) -> None:
    assert _app(monkeypatch, set()).get(page.PAGE_PATH + "?debug=1").status_code == 404


def test_page_requires_a_template_authority() -> None:
    import pytest
    with pytest.raises(TypeError):
        page.create_agent_control_plane_page_router(object())  # type: ignore[arg-type]
