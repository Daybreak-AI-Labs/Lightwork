from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import ai_evidence_analysis_page as page

ROUTE = "/ai-evidence-analysis"
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
    app = FastAPI()
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
    app.include_router(page.create_ai_evidence_analysis_page_router(templates))
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_workbench_is_store_free_no_store_and_csp_closed(client: TestClient) -> None:
    response = client.get(ROUTE)

    assert response.status_code == 200
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
    assert "style-src 'self' 'nonce-" in csp

    assert "AI evidence analysis" in response.text
    assert 'id="evidence-operations" disabled' in response.text
    assert "not legal advice, certification, or compliance conclusions" in response.text
    assert "Uploaded files are processed within size limits" in response.text
    assert "Extracted text is not saved" in response.text
    assert "distinct authorized human" in response.text
    assert "Immediate erasure from processing memory is not guaranteed" in response.text
    assert "/api/v1/ai-evidence-analysis" in response.text
    assert "may be sent to that provider" in response.text
    assert "Do not enter document contents, secrets, personal" in response.text
    assert 'aria-describedby="mv-copilot-egress"' in response.text


def test_workbench_has_only_closed_evidence_controls(client: TestClient) -> None:
    response = client.get(ROUTE)
    parser = _Markup()
    parser.feed(response.text)

    scripts: list[dict[str, str | None]] = []
    styles: list[dict[str, str | None]] = []
    forms: list[dict[str, str | None]] = []
    inputs: list[dict[str, str | None]] = []
    selectors: list[str] = []
    for tag, attrs in parser.tags:
        if tag == "script":
            scripts.append(attrs)
        if tag == "link" and attrs.get("rel") == "stylesheet":
            styles.append(attrs)
        if tag == "form":
            forms.append(attrs)
        if tag in {"input", "select", "textarea"}:
            if tag == "input":
                inputs.append(attrs)
            selectors.extend(
                value.lower()
                for name in ("id", "name")
                if isinstance((value := attrs.get(name)), str)
            )

    # Shell assets carry a content-hash query (W3-77); compare the paths.
    script_sources = {(item.get("src") or "").split("?")[0] for item in scripts}
    assert {
        "/static/lightwork-copilot.js",
        "/static/lightwork-ui.js",
        "/static/lightwork-voice.js",
        "/static/lightwork-ai-evidence-analysis.js",
    }.issubset(script_sources)
    style_sources = {(item.get("href") or "").split("?")[0] for item in styles}
    assert {
        "/static/lightwork.css",
        "/static/lightwork-ai-evidence-analysis.css",
    }.issubset(style_sources)
    assert forms and all("action" not in form for form in forms)
    assert sum(item.get("type") == "file" for item in inputs) == 1
    for selector in selectors:
        for forbidden in (
            "tenant",
            "actor",
            "principal",
            "reviewer",
            "adapter",
            "model",
            "provider",
            "valid_until",
            "retained_until",
            "retention_policy",
        ):
            assert forbidden not in selector

    required = {
        "artifact_file",
        "artifact_id",
        "source_ref",
        "source_revision",
        "acquired_at",
        "expected_artifact_authority_sha256",
        "criteria_set_id",
        "expected_criteria_revision",
        "expected_criteria_authority_sha256",
        "analysis_id",
        "decision",
        "reason_code",
        "limit",
    }
    assert required.issubset(set(selectors))


@pytest.mark.parametrize(
    ("allowed", "operate_disabled", "audit_disabled", "admin_disabled"),
    (
        (frozenset(), True, True, True),
        (frozenset({"operate"}), False, True, True),
        (frozenset({"audit"}), True, False, True),
        (frozenset({"view", "operate", "audit", "admin"}), False, False, False),
    ),
)
def test_workbench_server_renders_exact_role_controls(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    allowed: frozenset[str],
    operate_disabled: bool,
    audit_disabled: bool,
    admin_disabled: bool,
) -> None:
    monkeypatch.setattr(
        page,
        "has_permission",
        lambda _request, permission: permission in allowed,
    )
    response = client.get(ROUTE)
    parser = _Markup()
    parser.feed(response.text)
    by_id = {
        attrs["id"]: attrs
        for _tag, attrs in parser.tags
        if isinstance(attrs.get("id"), str)
    }

    assert ("disabled" in by_id["evidence-analyze-controls"]) is operate_disabled
    assert ("disabled" in by_id["evidence-review-controls"]) is operate_disabled
    assert ("disabled" in by_id["load-history"]) is audit_disabled
    assert ("disabled" in by_id["verify-integrity"]) is audit_disabled
    assert ("disabled" in by_id["retention-due"]) is audit_disabled
    assert ("disabled" in by_id["evidence-revoke-controls"]) is admin_disabled
    assert ("disabled" in by_id["evidence-purge-controls"]) is admin_disabled


def test_custom_theme_css_uses_a_per_response_csp_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    monkeypatch.setattr(page, "require_permission", lambda _request, _permission: None)
    templates = Jinja2Templates(directory=str(PACKAGE / "templates"))
    templates.env.globals.update(
        theme="custom",
        font="default",
        lang="en",
        density="comfortable",
        custom_theme_css="body.theme-custom { --bg:#000; --panel:#111; --text:#fff; --accent:#0f0; }",
        custom_theme_names=["custom"],
        dir="ltr",
        nav_groups=[],
        t=lambda key: key,
    )
    app = FastAPI()
    app.include_router(page.create_ai_evidence_analysis_page_router(templates))
    response = TestClient(app, headers={"Origin": "http://testserver"}).get(ROUTE)

    parser = _Markup()
    parser.feed(response.text)
    nonces = [attrs.get("nonce") for tag, attrs in parser.tags if tag == "style"]
    assert len(nonces) == 1
    assert nonces[0]
    assert f"'nonce-{nonces[0]}'" in response.headers["content-security-policy"]


def test_client_uses_raw_file_body_without_persistent_or_html_injection_channels() -> None:
    source = (PACKAGE / "static" / "lightwork-ai-evidence-analysis.js").read_text(encoding="utf-8")
    css = (PACKAGE / "static" / "lightwork-ai-evidence-analysis.css").read_text(encoding="utf-8")
    copilot_source = (PACKAGE / "static" / "lightwork-copilot.js").read_text(encoding="utf-8")

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "FormData",
        "FileReader",
        "arrayBuffer",
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "tenant_id",
        "actor_ref",
        "reviewer_ref",
        "extraction_adapter",
        "analysis_adapter",
        "valid_until",
        "retained_until",
    ):
        assert forbidden not in source
    for required in (
        "body: file",
        '"Content-Type": file.type',
        '"X-Lightwork-Evidence-Metadata": JSON.stringify(metadata)',
        'cache: "no-store"',
        'credentials: "same-origin"',
        'redirect: "error"',
        'referrerPolicy: "no-referrer"',
        'schema: "lightwork.ai-evidence.upload-metadata.v1"',
        'fileInput.value = ""',
        'apiRequest("/integrity")',
        'apiRequest("/retention/purge"',
    ):
        assert required in source
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert "forced-colors" in css
    assert "configured_for_operation_attempt" in source
    assert "operational_composition_verified" in source
    assert "Evidence analysis is configured." in source
    assert "Image text recognition is unavailable because OCR support is not installed" in source
    assert "enabled and composed for operations" not in source
    assert ".status-card > .governance-details" in css
    assert "Grounded in the current page" not in copilot_source
    assert "Model-generated guidance is unverified" in copilot_source
    assert 'history.push({role: "user", content: message})' in copilot_source
    assert '{role: "assistant", content: payload.reply}' not in copilot_source


def test_workbench_rejects_query_material_and_requires_tenant(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = client.get(f"{ROUTE}?tenant_id=caller-selected")
    assert query.status_code == 404
    assert "AI evidence analysis" not in query.text

    def missing_tenant() -> None:
        raise page.HTTPException(
            status_code=409,
            detail="select an active tenant before using AI evidence analysis",
        )

    monkeypatch.setattr(page, "_tenant_selected", missing_tenant)
    missing = client.get(ROUTE)
    assert missing.status_code == 409


def test_workbench_template_and_assets_are_packaged() -> None:
    for relative in (
        "templates/ai_evidence_analysis.html",
        "static/lightwork-ai-evidence-analysis.js",
        "static/lightwork-ai-evidence-analysis.css",
    ):
        selected = PACKAGE / relative
        assert selected.is_file()
        assert selected.stat().st_size > 100


def test_dashboard_mounts_page_api_visibility_and_security_link() -> None:
    from maverick_dashboard.app import app
    from maverick_dashboard.ui_visibility import page_for

    paths = set(app.openapi()["paths"])
    effective_routes = []
    for route in app.routes:
        if hasattr(route, "effective_candidates"):
            effective_routes.extend(route.effective_candidates())
        else:
            effective_routes.append(route)
    assert any(
        getattr(route, "path", None) == "/ai-evidence-analysis"
        for route in effective_routes
    )
    assert "/api/v1/ai-evidence-analysis/status" in paths
    assert "/api/v1/ai-evidence-analysis/analyses" in paths
    selected = page_for("/ai-evidence-analysis")
    assert selected is not None
    assert selected["floor"] == "view"
    assert selected["in_nav"] is True
    security = (PACKAGE / "templates" / "security.html").read_text(encoding="utf-8")
    assert 'href="/ai-evidence-analysis"' in security
