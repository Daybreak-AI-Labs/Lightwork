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


def _app(
    monkeypatch,
    allowed,
    *,
    global_allowed=frozenset(),
    shared_halt: bool = False,
) -> TestClient:
    monkeypatch.setattr(page, "require_permission", lambda *_: None)
    monkeypatch.setattr(page, "has_permission",
                        lambda _request, permission: permission in allowed)
    monkeypatch.setattr(
        page,
        "has_global_permission",
        lambda _request, permission: permission in global_allowed,
    )
    monkeypatch.setattr(page, "_shared_halt_backend", lambda: shared_halt)
    app = FastAPI()
    app.include_router(page.create_agent_control_plane_page_router(_templates()))
    return TestClient(app)


def test_page_is_wired_hardened_and_permission_aware(monkeypatch) -> None:
    response = _app(
        monkeypatch,
        {"operate", "audit"},
        global_allowed={"operate"},
    ).get(page.PAGE_PATH)
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
    assert 'data-can-halt="true"' in body
    assert 'data-halt-scope="local"' in body
    # Admin-only verbs are not offered to an operator.
    assert '<option value="contain">' not in body
    assert '<option value="ledger">' in body
    assert 'id="acp-clear-halt" type="button" disabled' in body
    assert 'id="acp-clear-halt" type="button" disabled data-role-disabled="true"' not in body


def test_admin_sees_every_verb(monkeypatch) -> None:
    body = _app(monkeypatch, {"operate", "audit", "admin"}).get(page.PAGE_PATH).text
    assert '<option value="contain"' in body and '<option value="release"' in body
    assert 'data-can-admin="true"' in body


def test_auditor_form_remains_usable_without_mutation_verbs(monkeypatch) -> None:
    body = _app(monkeypatch, {"audit"}).get(page.PAGE_PATH).text

    assert '<option value="suspend"' not in body
    assert '<option value="resume"' not in body
    assert '<option value="contain"' not in body
    assert '<option value="release"' not in body
    assert '<option value="ledger">Ledger</option>' in body
    assert 'id="acp-verb-controls" disabled' not in body
    assert 'id="acp-attenuate-controls" disabled aria-disabled="true" data-role-disabled="true"' in body


def test_shared_halt_requires_global_admin_and_detection_fails_closed(monkeypatch) -> None:
    tenant_admin = _app(
        monkeypatch,
        {"operate", "admin"},
        shared_halt=True,
    ).get(page.PAGE_PATH).text
    assert 'data-can-halt="false"' in tenant_admin
    assert 'data-halt-scope="cluster"' in tenant_admin
    assert 'id="acp-clear-halt" type="button" disabled data-role-disabled="true"' in tenant_admin

    global_admin = _app(
        monkeypatch,
        {"operate"},
        global_allowed={"admin"},
        shared_halt=True,
    ).get(page.PAGE_PATH).text
    assert 'data-can-halt="true"' in global_admin
    assert 'data-halt-scope="cluster"' in global_admin

    client = _app(monkeypatch, {"operate"})
    monkeypatch.setattr(page, "_shared_halt_backend", lambda: (_ for _ in ()).throw(OSError()))
    unknown = client.get(page.PAGE_PATH).text
    assert 'data-can-halt="false"' in unknown
    assert 'data-halt-scope="unknown"' in unknown


def test_local_halt_ignores_tenant_local_operator_role(monkeypatch) -> None:
    tenant_operator = _app(
        monkeypatch,
        {"operate"},
        global_allowed={"view"},
    ).get(page.PAGE_PATH).text
    assert 'data-can-halt="false"' in tenant_operator
    assert 'data-halt-scope="local"' in tenant_operator

    global_operator = _app(
        monkeypatch,
        set(),
        global_allowed={"operate"},
    ).get(page.PAGE_PATH).text
    assert 'data-can-halt="true"' in global_operator
    assert 'data-halt-scope="local"' in global_operator


def test_agent_console_source_uses_bounded_governed_interactions() -> None:
    package = Path(page.__file__).parent
    source = (package / "static" / "lightwork-agent-control-plane.js").read_text(
        encoding="utf-8"
    )
    template = (package / "templates" / "agent_control_plane.html").read_text(
        encoding="utf-8"
    )
    shared_css = (package / "static" / "lightwork.css").read_text(encoding="utf-8")

    assert "window.prompt(" not in source
    assert "window.confirm(" not in source
    assert "window.mvForm" in source
    assert "window.mvConfirm" in source
    assert 'requestAt("/api/v1/halt"' in source
    assert 'request("/halt"' not in source
    assert "/clear-halt" not in source
    assert 'request("/snapshot?limit=" + rosterLimit + "&offset=" + offset)' in source
    assert "planeRequested" in source and "planeRefreshPromise" in source
    assert "controlPlaneReady" in source and "haltAuthorityReady" in source
    assert "AbortController" in source and "request timed out" in source
    assert "response.body.getReader" in source and "utf8ByteLength" in source
    assert 'button.setAttribute("aria-label", label + " principal " + principal)' in source
    assert "restoreRosterFocus" in source and "rosterHeading.focus()" in source
    assert 'principal.scope = "row"' in source
    assert 'aria-live="polite" aria-atomic="true"' in template
    assert 'class="acp-health" role="status" aria-live="polite"' in template
    assert 'class="acp-table" aria-labelledby="acp-roster-title"' in template
    assert '<caption class="sr-only">' in template
    assert ".acp-form fieldset{border:0;margin:0;min-inline-size:0;padding:0}" in template
    assert 'name="reason" maxlength="200" required' in template
    assert 'name="max_dollars" type="number" min="0.01" max="1000000"' in template
    assert 'name="max_wall_seconds" type="number" min="1" max="31536000"' in template
    assert template.count('name="principal" maxlength="512"') == 2
    assert 'request("/principals/" + verb' in source
    assert 'request("/principals/ledger?" + query.toString())' in source
    assert "--text-muted: var(--muted);" in shared_css


def test_unknown_query_params_are_404(monkeypatch) -> None:
    assert _app(monkeypatch, set()).get(page.PAGE_PATH + "?debug=1").status_code == 404


def test_page_requires_a_template_authority() -> None:
    import pytest
    with pytest.raises(TypeError):
        page.create_agent_control_plane_page_router(object())  # type: ignore[arg-type]
