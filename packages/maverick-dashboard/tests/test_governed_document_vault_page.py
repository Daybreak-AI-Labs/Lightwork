from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import governed_document_vault_page as page

ROUTE = "/administration/document-vault"
PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    monkeypatch.setattr(page, "require_permission", lambda _request, _permission: None)
    monkeypatch.setattr(page, "has_permission", lambda _request, _permission: True)
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
    app.include_router(page.create_governed_document_vault_page_router(templates))
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_is_liquid_glass_candid_and_copilot_scoped(client: TestClient) -> None:
    response = client.get(ROUTE)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"
    csp = response.headers["content-security-policy"]
    for directive in (
        "default-src 'none'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "script-src 'self'",
        "style-src 'self' 'nonce-",
    ):
        assert directive in csp
    for text in (
        "Governed document vault",
        "Protect documents and access",
        "Documents are encrypted before storage",
        "can be used once",
        "does not prove external object-store custody",
    ):
        assert text in response.text
    assert "backdrop-filter: blur(24px)" in response.text
    assert "prefers-reduced-motion" in response.text
    assert "forced-colors" in response.text
    assert 'data-copilot-page="/administration/document-vault"' in response.text
    assert 'id="document-vault-operations" disabled' in response.text


def test_page_has_live_ingest_revoke_mint_redeem_and_audit_controls(client: TestClient) -> None:
    response = client.get(ROUTE)
    parser = _Markup()
    parser.feed(response.text)
    identifiers = {
        value
        for _tag, attrs in parser.tags
        if isinstance((value := attrs.get("id")), str)
    }
    assert {
        "document-vault-ingest-form",
        "document-vault-file",
        "document-vault-revoke",
        "document-vault-token-form",
        "document-vault-delivery-form",
        "document-vault-audit",
        "document-vault-integrity",
        "document-vault-reconcile",
    }.issubset(identifiers)
    scripts = [attrs.get("src") for tag, attrs in parser.tags if tag == "script"]
    assert "/static/lightwork-governed-document-vault.js" in scripts


def test_page_rejects_authority_query_material_and_requires_tenant(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get(f"{ROUTE}?tenant_id=forbidden").status_code == 404

    def missing() -> None:
        raise page.HTTPException(status_code=409, detail="select a tenant")

    monkeypatch.setattr(page, "_tenant_selected", missing)
    assert client.get(ROUTE).status_code == 409


def test_client_is_bounded_and_has_no_persistent_browser_secret_channels() -> None:
    source = (PACKAGE / "static" / "lightwork-governed-document-vault.js").read_text(
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
        "principal_ref",
        "object_locator",
        "filesystem_path",
    ):
        assert forbidden not in source
    for required in (
        "100 * 1024 * 1024",
        "512 * 1024",
        "TextEncoder",
        "response.body.getReader",
        'cache: "no-store"',
        'credentials: "same-origin"',
        'redirect: "error"',
        'referrerPolicy: "no-referrer"',
        "operations.disabled = true",
        "operations.disabled = false",
        "URL.revokeObjectURL",
        "textContent",
    ):
        assert required in source


def test_page_assets_are_packaged() -> None:
    for relative in (
        "templates/governed_document_vault.html",
        "static/lightwork-governed-document-vault.js",
    ):
        selected = PACKAGE / relative
        assert selected.is_file()
        assert selected.stat().st_size > 100
