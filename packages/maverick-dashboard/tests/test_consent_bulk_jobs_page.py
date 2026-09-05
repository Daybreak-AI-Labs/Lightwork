from __future__ import annotations

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import consent_bulk_jobs_page as page

ROUTE = "/consent/bulk-jobs"
PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def _templates(*, custom_theme_css: str = "") -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(PACKAGE / "templates"))
    templates.env.globals.update(
        theme="dark",
        font="default",
        lang="en",
        density="comfortable",
        custom_theme_css=custom_theme_css,
        custom_theme_names=["custom"] if custom_theme_css else [],
        dir="ltr",
        nav_groups=[],
        t=lambda key: key,
    )
    return templates


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    monkeypatch.setattr(page, "require_permission", lambda _request, _permission: None)
    monkeypatch.setattr(page, "has_global_permission", lambda _request, _permission: True)
    app = FastAPI()
    app.include_router(page.create_consent_bulk_jobs_page_router(_templates()))
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_workbench_is_store_free_closed_and_copilot_enabled(client: TestClient) -> None:
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
    assert "Manage bulk consent changes" in response.text
    assert 'id="cbj-operations" disabled' in response.text
    assert "Checking availability before enabling bulk actions" in response.text
    assert "Checking status does not replenish job credits" in response.text
    assert 'data-api-base="/api/v1/consent/bulk-jobs"' in response.text
    assert 'data-copilot-page="/consent/bulk-jobs"' in response.text
    assert 'src="/static/lightwork-copilot.js"' in response.text
    assert 'src="/static/lightwork-consent-bulk-jobs.js"' in response.text
    parser = _Markup()
    parser.feed(response.text)
    nonces = [attrs.get("nonce") for tag, attrs in parser.tags if tag == "style"]
    assert len(nonces) == 1
    assert nonces[0]
    assert f"'nonce-{nonces[0]}'" in csp


def test_download_starts_as_an_explained_disabled_button(client: TestClient) -> None:
    response = client.get(ROUTE)
    parser = _Markup()
    parser.feed(response.text)
    matches = [
        (tag, attrs)
        for tag, attrs in parser.tags
        if attrs.get("id") == "cbj-download"
    ]

    assert matches == [
        (
            "button",
            {
                "class": "btn",
                "id": "cbj-download",
                "type": "button",
                "disabled": None,
                "aria-describedby": "cbj-download-hint",
            },
        )
    ]
    assert 'id="cbj-download-hint" role="status"' in response.text
    assert "Select a job to make its export available." in response.text

    source = (
        PACKAGE / "static" / "lightwork-consent-bulk-jobs.js"
    ).read_text(encoding="utf-8")
    assert 'var downloadUrl = ""' in source
    assert "download.disabled = !canAudit" in source
    assert "global.location.assign(downloadUrl)" in source
    assert "download.href" not in source


@pytest.mark.parametrize(
    ("permissions", "audit", "admin"),
    (
        (frozenset(), False, False),
        (frozenset({"audit"}), True, False),
        (frozenset({"admin"}), False, True),
        (frozenset({"audit", "admin"}), True, True),
    ),
)
def test_page_renders_exact_global_permission_controls(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    permissions: frozenset[str],
    audit: bool,
    admin: bool,
) -> None:
    monkeypatch.setattr(
        page,
        "has_global_permission",
        lambda _request, permission: permission in permissions,
    )
    parser = _Markup()
    parser.feed(client.get(ROUTE).text)
    by_id = {
        attrs["id"]: attrs
        for _tag, attrs in parser.tags
        if isinstance(attrs.get("id"), str)
    }
    shell = by_id["consent-bulk-jobs-shell"]
    assert (shell["data-can-audit"] == "true") is audit
    assert (shell["data-can-admin"] == "true") is admin
    for control in (
        "cbj-export-controls",
        "cbj-action-controls",
        "cbj-job-admin-controls",
        "cbj-credit-controls",
    ):
        assert ("disabled" not in by_id[control]) is admin
    for button in ("cbj-run-next", "cbj-recover", "cbj-purge"):
        assert ("disabled" not in by_id[button]) is admin


def test_forms_collect_only_closed_opaque_bulk_job_fields(client: TestClient) -> None:
    parser = _Markup()
    parser.feed(client.get(ROUTE).text)
    forms = [attrs for tag, attrs in parser.tags if tag == "form"]
    names = {
        value
        for tag, attrs in parser.tags
        if tag in {"input", "select", "textarea"}
        if isinstance((value := attrs.get("name")), str)
    }
    assert forms and all("action" not in form for form in forms)
    for required in (
        "state",
        "limit",
        "items_json",
        "operation",
        "scheduled_for",
        "idempotency_key",
        "job_id",
        "expected_revision",
        "amount",
    ):
        assert required in names
    for forbidden in (
        "tenant_id",
        "actor_sha256",
        "principal_ref",
        "worker_sha256",
        "lease_owner_sha256",
        "database_path",
        "storage_path",
        "recovery_path",
        "provider",
        "executor",
        "initial_credits",
    ):
        assert forbidden not in names


def test_page_rejects_query_material_and_requires_tenant(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert client.get(f"{ROUTE}?profile_id=caller-selected").status_code == 404
    assert client.get(f"{ROUTE}?tenant_id=caller-selected").status_code == 404
    assert client.get(f"{ROUTE}?theme=light&density=compact").status_code == 200

    def missing_tenant() -> None:
        raise page.HTTPException(
            status_code=409,
            detail="select an active tenant before using consent bulk jobs",
        )

    monkeypatch.setattr(page, "_tenant_selected", missing_tenant)
    assert client.get(ROUTE).status_code == 409


def test_custom_theme_uses_same_csp_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    monkeypatch.setattr(page, "require_permission", lambda _request, _permission: None)
    monkeypatch.setattr(page, "has_global_permission", lambda _request, _permission: True)
    app = FastAPI()
    app.include_router(
        page.create_consent_bulk_jobs_page_router(
            _templates(
                custom_theme_css=(
                    "body.theme-custom { --bg:#000; --panel:#111; "
                    "--text:#fff; --accent:#0f0; }"
                )
            )
        )
    )
    response = TestClient(app, headers={"Origin": "http://testserver"}).get(ROUTE)
    parser = _Markup()
    parser.feed(response.text)
    nonces = [attrs.get("nonce") for tag, attrs in parser.tags if tag == "style"]
    assert len(nonces) == 2
    assert len(set(nonces)) == 1
    assert nonces[0]
    assert f"'nonce-{nonces[0]}'" in response.headers["content-security-policy"]


def test_liquid_glass_client_is_bounded_accessible_and_injection_safe() -> None:
    template = (PACKAGE / "templates" / "consent_bulk_jobs.html").read_text(
        encoding="utf-8"
    )
    source = (
        PACKAGE / "static" / "lightwork-consent-bulk-jobs.js"
    ).read_text(encoding="utf-8")
    for required in (
        "backdrop-filter: blur(26px)",
        "color-mix(",
        ":focus-visible",
        "prefers-reduced-motion",
        "forced-colors",
        'aria-live="polite"',
        "data-copilot-page",
    ):
        assert required in template
    for required in (
        "MAX_RESPONSE_BYTES = 64 * 1024",
        "MAX_JSON_INPUT_BYTES = 48 * 1024",
        'headers.get("Content-Length")',
        "response.body.getReader()",
        "reader.cancel()",
        "JSON.parse(responseText)",
        'credentials: "same-origin"',
        'cache: "no-store"',
        'redirect: "error"',
        'referrerPolicy: "no-referrer"',
        "element.textContent" if False else "operationOutput.textContent",
        "operations.disabled = true",
        'request("/worker/run-next"',
        'request("/operations/integrity")',
        'request("/operations/recover"',
        'request("/operations/retention/purge"',
        "parseItems",
        "exactShape",
    ):
        assert required in source
    for forbidden in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "eval(",
        "new Function",
    ):
        assert forbidden not in source


def test_client_script_has_valid_node_syntax() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    source = PACKAGE / "static" / "lightwork-consent-bulk-jobs.js"
    completed = subprocess.run(
        [node, "--check", str(source)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
