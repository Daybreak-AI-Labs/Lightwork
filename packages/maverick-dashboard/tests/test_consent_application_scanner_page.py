from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import consent_application_scanner_page as page

ROUTE = "/consent/application-scans"
PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(page, "current_tenant_id_strict", lambda: "tenant-a")
    monkeypatch.setattr(page, "require_permission", lambda *_args: None)
    monkeypatch.setattr(page, "has_permission", lambda *_args: True)
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
    app.include_router(page.create_consent_application_scanner_page_router(templates))
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
    assert "Mobile and connected-app manifest analysis" in response.text
    assert "Manifest evidence is not live-device proof" in response.text
    assert "never automatic tracker or legal classifications" in response.text
    assert "Binary APK AXML" in response.text
    assert "Authorization follows the active tenant" in response.text
    assert 'id="cas-operations" disabled' in response.text
    assert 'data-copilot-page="/consent/application-scans"' in response.text
    assert "/static/lightwork-consent-application-scanner.css" in response.text
    assert "/static/lightwork-consent-application-scanner.js" in response.text

    css = (PACKAGE / "static" / "lightwork-consent-application-scanner.css").read_text(
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


def test_forms_expose_no_tenant_actor_or_raw_authority_selector(client: TestClient) -> None:
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
        "platform",
        "bundle_id",
        "display_name",
        "application_id",
        "expected_application_revision",
        "files",
        "scan_id",
        "expected_revision",
        "decision",
        "review_note_code",
    }.issubset(controls)
    for selector in controls:
        for forbidden in (
            "tenant_id",
            "actor",
            "principal",
            "client_credential",
            "raw_manifest",
            "subject_id",
        ):
            assert forbidden not in selector.lower()


def test_bounded_script_has_no_browser_persistence_or_html_injection() -> None:
    source = (PACKAGE / "static" / "lightwork-consent-application-scanner.js").read_text(
        encoding="utf-8"
    )
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
        "actor",
        "principal",
    ):
        assert forbidden not in source
    for required in (
        "MAX_REQUEST_BYTES = 6 * 1024 * 1024",
        "MAX_RESPONSE_BYTES = 2 * 1024 * 1024",
        "MAX_TOTAL_FILE_BYTES = 4 * 1024 * 1024",
        'credentials: "same-origin"',
        'cache: "no-store"',
        'referrerPolicy: "no-referrer"',
        "TextEncoder",
        "TextDecoder",
        "reader.cancel()",
        "arrayBuffer()",
        "btoa(",
        "textContent",
        "encodeURIComponent",
        "operations.disabled = true",
        'call("/readiness", undefined, [503])',
    ):
        assert required in source


def test_custom_theme_style_is_bound_to_the_page_csp_nonce(monkeypatch) -> None:
    monkeypatch.setattr(page, "current_tenant_id_strict", lambda: "tenant-a")
    monkeypatch.setattr(page, "require_permission", lambda *_args: None)
    monkeypatch.setattr(page, "has_permission", lambda *_args: True)
    templates = Jinja2Templates(directory=str(PACKAGE / "templates"))
    templates.env.globals.update(
        theme="custom",
        font="default",
        lang="en",
        density="comfortable",
        custom_theme_css=":root { --bg: #101010; }",
        custom_theme_names=["custom"],
        dir="ltr",
        nav_groups=[],
        t=lambda key: key,
    )
    app = FastAPI()
    app.include_router(page.create_consent_application_scanner_page_router(templates))
    response = TestClient(app, headers={"Origin": "http://testserver"}).get(ROUTE)
    assert response.status_code == 200
    nonce_match = re.search(r'<style nonce="([A-Za-z0-9_-]+)">', response.text)
    assert nonce_match is not None
    assert (
        f"style-src 'self' 'nonce-{nonce_match.group(1)}'"
        in response.headers["content-security-policy"]
    )


def test_page_rejects_unknown_query_keys(client: TestClient) -> None:
    assert client.get(f"{ROUTE}?tenant=tenant-a").status_code == 404
