from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import consent_global_opt_out_page as page

ROUTE = "/consent/global-opt-out"
PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(page, "current_tenant_id_strict", lambda: "alpha")
    monkeypatch.setattr(
        page,
        "require_global_permission",
        lambda _request, _permission: None,
    )
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
        nav_groups=[],
        t=lambda key: key,
    )
    app = FastAPI()
    app.include_router(page.create_consent_global_opt_out_page_router(templates))
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_workbench_is_store_free_closed_and_liquid_glass(client: TestClient) -> None:
    response = client.get(ROUTE)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    csp = response.headers["content-security-policy"]
    for directive in (
        "default-src 'none'",
        "connect-src 'self'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
    ):
        assert directive in csp
    assert "Global Opt-Out" in response.text
    assert "real <code>opt_out</code>" in response.text
    assert "retains only result digests" in response.text
    assert "live mobile, email, connected-device" in response.text
    assert 'id="goo-operations" disabled' in response.text
    assert 'data-copilot-page="/consent/global-opt-out"' in response.text
    assert "/static/lightwork-consent-global-opt-out.css" in response.text
    assert "/static/lightwork-consent-global-opt-out.js" in response.text

    css = (PACKAGE / "static" / "lightwork-consent-global-opt-out.css").read_text(
        encoding="utf-8"
    )
    for required in (
        "backdrop-filter: blur(24px)",
        ":focus-visible",
        "prefers-reduced-motion",
        "forced-colors",
        "color-mix",
    ):
        assert required in css


def test_workbench_forms_have_no_tenant_profile_or_raw_case_selector(client: TestClient) -> None:
    response = client.get(ROUTE)
    parser = _Markup()
    parser.feed(response.text)
    forms = [attrs for tag, attrs in parser.tags if tag == "form"]
    controls = {
        value
        for tag, attrs in parser.tags
        if tag in {"input", "select", "textarea"}
        for name in ("id", "name")
        if isinstance((value := attrs.get(name)), str)
    }
    assert forms and all("action" not in item for item in forms)
    assert {
        "application_ref",
        "configuration_id",
        "expected_revision",
        "idempotency_key",
        "mobile_title",
        "email_title",
        "supported_devices",
        "supported_services",
    }.issubset(controls)
    for selector in controls:
        for forbidden in (
            "tenant_id",
            "profile_id",
            "client_credential",
            "origin",
            "service_client_id",
            "policy_id",
            "case_id",
            "case_payload",
        ):
            assert forbidden not in selector.lower()


def test_bounded_script_has_no_browser_persistence_or_html_injection() -> None:
    source = (
        PACKAGE / "static" / "lightwork-consent-global-opt-out.js"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "tenant_id",
        "profile_id",
        "client_credential",
        "case_id",
    ):
        assert forbidden not in source
    for required in (
        "MAX_REQUEST_BYTES = 64 * 1024",
        "MAX_RESPONSE_BYTES = 256 * 1024",
        'credentials: "same-origin"',
        'cache: "no-store"',
        'referrerPolicy: "no-referrer"',
        "TextEncoder",
        "textContent",
        "encodeURIComponent",
        "operations.disabled = true",
    ):
        assert required in source


def test_page_rejects_unknown_query_keys(client: TestClient) -> None:
    response = client.get(f"{ROUTE}?tenant=alpha")
    assert response.status_code == 404
