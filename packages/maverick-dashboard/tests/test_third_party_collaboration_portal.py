from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from maverick_dashboard.third_party_collaboration_portal import (
    create_third_party_collaboration_portal_router,
)

PORTAL = "portal_ref_1234567890"
ROUTE = f"/api/v1/external/third-party-collaboration/{PORTAL}/portal"
PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_third_party_collaboration_portal_router())
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_portal_is_a_constant_no_store_same_origin_shell() -> None:
    client = _client()
    response = client.get(ROUTE)
    alternate = client.get(
        "/api/v1/external/third-party-collaboration/alternate_ref_123456789/portal"
    )

    assert response.status_code == 200
    assert alternate.status_code == 200
    assert response.content == alternate.content
    assert PORTAL not in response.text
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    csp = response.headers["content-security-policy"]
    for directive in (
        "default-src 'none'",
        "base-uri 'none'",
        "connect-src 'self'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
    ):
        assert directive in csp

    script = client.get(f"{ROUTE}/assets/client.js")
    stylesheet = client.get(f"{ROUTE}/assets/styles.css")
    assert script.status_code == 200
    assert stylesheet.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert stylesheet.headers["content-type"].startswith("text/css")
    for asset in (script, stylesheet):
        assert asset.headers["cache-control"] == "no-store, private, max-age=0"
        assert asset.headers["x-content-type-options"] == "nosniff"
        assert asset.headers["referrer-policy"] == "no-referrer"


def test_portal_rejects_query_tenant_headers_and_invalid_references_without_a_shell() -> None:
    client = _client()

    for response in (
        client.get(f"{ROUTE}?credential=do-not-accept"),
        client.get(ROUTE, headers={"X-Lightwork-Tenant": "tenant-a"}),
        client.get(ROUTE, headers={"X-Maverick-Tenant": "tenant-a"}),
        client.get(ROUTE, headers={"X-Tenant-Id": "tenant-a"}),
        client.get(f"{ROUTE}/assets/client.js?tenant=tenant-a"),
        client.get(
            f"{ROUTE}/assets/styles.css",
            headers={"X-Lightwork-Tenant": "tenant-a"},
        ),
        client.get("/api/v1/external/third-party-collaboration/short/portal"),
        client.get("/api/v1/external/third-party-collaboration/invalid.reference.123/portal"),
    ):
        assert response.status_code == 404
        assert "Third-party assurance response" not in response.text


def test_portal_markup_has_only_closed_respondent_controls_and_no_byte_upload() -> None:
    response = _client().get(ROUTE)
    parser = _Markup()
    parser.feed(response.text)

    selectors: list[str] = []
    scripts: list[dict[str, str | None]] = []
    stylesheets: list[dict[str, str | None]] = []
    forms: list[dict[str, str | None]] = []
    input_types: list[str | None] = []
    for tag, attrs in parser.tags:
        if tag in {"input", "select", "textarea"}:
            selectors.extend(
                value.lower()
                for name in ("id", "name")
                if isinstance((value := attrs.get(name)), str)
            )
        if tag == "input":
            input_types.append(attrs.get("type"))
        if tag == "script":
            scripts.append(attrs)
        if tag == "link" and attrs.get("rel") == "stylesheet":
            stylesheets.append(attrs)
        if tag == "form":
            forms.append(attrs)

    assert scripts == [{"defer": None, "src": "portal/assets/client.js"}]
    assert stylesheets == [{"rel": "stylesheet", "href": "portal/assets/styles.css"}]
    assert all("action" not in form for form in forms)
    assert "file" not in input_types
    assert "password" in input_types
    for selector in selectors:
        for forbidden in (
            "tenant",
            "provider",
            "risk-score",
            "risk_score",
            "scoring",
            "approval",
            "principal",
        ):
            assert forbidden not in selector
    assert "SHA-256 digest manifest" in response.text
    assert "files and file bytes are never accepted" in response.text
    assert "Respondent attestations remain unverified evidence" in response.text
    assert "does not prove evidence completeness" in response.text
    assert "Save draft and refresh questions" in response.text
    assert "Finalize attested response" in response.text


def test_portal_client_has_no_persistent_credential_or_raw_document_channel() -> None:
    source = (PACKAGE / "static" / "lightwork-third-party-portal.js").read_text(encoding="utf-8")
    css = (PACKAGE / "static" / "lightwork-third-party-portal.css").read_text(encoding="utf-8")

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "URLSearchParams",
        "FileReader",
        "FormData",
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
    ):
        assert forbidden not in source
    for required in (
        "Authorization: `ThirdPartyInvitation ${state.credential}`",
        'cache: "no-store"',
        'credentials: "omit"',
        'redirect: "error"',
        'referrerPolicy: "no-referrer"',
        'portalRequest("/contract", "GET")',
        'portalRequest("/draft", "PUT", body)',
        'portalRequest("/finalize", "POST", body)',
        "validateContract(raw)",
        "attested: true",
        "expected_revision: state.contract.draft_revision",
        "expected_draft_revision: state.contract.draft_revision",
    ):
        assert required in source
    assert "tenant_id" not in source
    assert "provider_id" not in source
    assert "risk_score" not in source
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert "forced-colors" in css
