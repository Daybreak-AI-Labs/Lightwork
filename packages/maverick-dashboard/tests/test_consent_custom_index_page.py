from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import consent_custom_index_page as page

ROUTE = "/consent/custom-indexes"
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
    app.include_router(page.create_consent_custom_index_page_router(templates))
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_workbench_is_store_free_closed_and_liquid_glass(client: TestClient) -> None:
    response = client.get(ROUTE)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    csp = response.headers["content-security-policy"]
    for directive in (
        "default-src 'none'",
        "connect-src 'self'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'nonce-",
    ):
        assert directive in csp
    assert "Find People by Preference" in response.text
    assert "What you search for is never saved" in response.text
    assert "searching stops until the question is rebuilt" in response.text
    assert 'id="custom-index-operations" disabled' in response.text
    assert 'data-copilot-page="/consent/custom-indexes"' in response.text


def test_workbench_has_only_closed_fields_and_external_script(client: TestClient) -> None:
    response = client.get(ROUTE)
    parser = _Markup()
    parser.feed(response.text)
    scripts = [attrs for tag, attrs in parser.tags if tag == "script"]
    forms = [attrs for tag, attrs in parser.tags if tag == "form"]
    selectors = {
        value
        for tag, attrs in parser.tags
        if tag in {"input", "select", "textarea"}
        for name in ("id", "name")
        if isinstance((value := attrs.get(name)), str)
    }
    assert "/static/lightwork-consent-custom-index.js" in {
        item.get("src") for item in scripts
    }
    assert forms and all("action" not in form for form in forms)
    # Answers are rendered as choices by the script; versions ride on rows.
    assert {
        "index_id",
        "element_name",
        "idempotency_key",
        "value_type",
        "cursor",
    }.issubset(selectors)
    for selector in selectors:
        for forbidden in (
            "tenant",
            "actor",
            "principal",
            "secret",
            "key_version",
            "retention",
            "quota",
        ):
            assert forbidden not in selector.lower()


@pytest.mark.parametrize(
    ("allowed", "admin_disabled", "audit_disabled"),
    (
        (frozenset(), True, True),
        (frozenset({"admin"}), False, True),
        (frozenset({"audit"}), True, False),
        (frozenset({"admin", "audit"}), False, False),
    ),
)
def test_workbench_server_renders_exact_role_controls(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    allowed: frozenset[str],
    admin_disabled: bool,
    audit_disabled: bool,
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
    for fieldset in (
        "custom-index-create-controls",
        "custom-index-search-controls",
        "custom-index-new",
    ):
        assert ("disabled" in by_id[fieldset]) is admin_disabled
    assert ("disabled" in by_id["custom-index-audit-controls"]) is audit_disabled


def test_client_bounds_json_disables_until_ready_and_has_no_injection_channels() -> None:
    source = (
        PACKAGE / "static" / "lightwork-consent-custom-index.js"
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
        "actor_ref",
        "principal_ref",
        "key_secret_ref",
        "key_version",
        "retention_seconds",
    ):
        assert forbidden not in source
    for required in (
        "64 * 1024",
        "TextEncoder",
        "response.body.getReader()",
        'cache: "no-store"',
        'credentials: "same-origin"',
        'redirect: "error"',
        'referrerPolicy: "no-referrer"',
        "operations.disabled = true",
        "operations.disabled = false",
        'post(`/${selectedIndex()}/search`',
        'request("/operations/integrity")',
        "textContent",
    ):
        assert required in source


def test_workbench_rejects_query_material_and_requires_tenant(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = client.get(f"{ROUTE}?tenant_id=caller-selected")
    assert response.status_code == 404
    assert "Find People by Preference" not in response.text

    def missing_tenant() -> None:
        raise page.HTTPException(
            status_code=409,
            detail="select an active tenant before using consent custom indexes",
        )

    monkeypatch.setattr(page, "_tenant_selected", missing_tenant)
    assert client.get(ROUTE).status_code == 409


def test_workbench_assets_are_packaged() -> None:
    for relative in (
        "templates/consent_custom_indexes.html",
        "static/lightwork-consent-custom-index.js",
    ):
        selected = PACKAGE / relative
        assert selected.is_file()
        assert selected.stat().st_size > 100


def test_workbench_lists_questions_with_row_buttons_and_speaks_plainly(client: TestClient) -> None:
    from test_support.plain_language import developer_terms

    page_html = client.get(ROUTE).text
    source = (PACKAGE / "static" / "lightwork-consent-custom-index.js").read_text(encoding="utf-8")
    assert 'id="custom-index-table"' in page_html
    for label in ('label: "Search"', '"Build now"', '"Rebuild"', 'label: "Remove"'):
        assert label in source
    assert "expected_revision: index.revision" in source
    assert "CW.confirm(" in source
    for typed in ("Index reference", "Page size", "After profile", "Opaque value ID",
                  "idempotency key", "Current version"):
        assert typed not in page_html
    assert developer_terms(page_html) == []
