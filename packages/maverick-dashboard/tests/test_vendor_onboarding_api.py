"""Real dashboard routing through the offline vendor application service."""
from __future__ import annotations

import base64
from dataclasses import replace
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from maverick import config, world_model
from maverick.governed_operations import ServiceContext
from maverick.vendor_onboarding import VendorOnboardingService
from maverick.vendor_onboarding_adapters import (
    EvidenceDocument,
    FakeGoogleDriveEvidence,
    FakeJiraTickets,
)
from maverick_dashboard.app import app
from maverick_dashboard.vendor_onboarding_composition import WorkflowBinding

BASE = "/api/v1/vendor-onboarding/cases"
NOW = 1_800_000_000.0


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_TENANT", "synthetic-a")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    (tmp_path / "config.toml").write_text(
        '[vendor_onboarding]\nenable=true\nresource_owner="procurement"\n'
        '[vendor_management]\nenable=true\n[ai_vendor_risk]\nenable=true\n'
        '[model_risk_assurance]\nenable=true\n', encoding="utf-8",
    )
    config.reset_config_cache()
    ctx = ServiceContext("synthetic-a", "user:reviewer", "procurement",
                         "user:reviewer", "jira", human=True)
    docs = FakeGoogleDriveEvidence({(ctx.tenant, "security-paper"): EvidenceDocument(
        "security-paper", "v1", NOW - 100, b"We do not train on customer data.",
    )})
    tickets = FakeJiraTickets()
    service = VendorOnboardingService(docs, tickets, lambda c, cap, res: c.binding() == ctx.binding(),
                                      clock=lambda: NOW)
    monkeypatch.setattr(app.state, "vendor_onboarding_binding_factory",
                        lambda request: WorkflowBinding(service, ctx))
    client = TestClient(app, headers={"Origin": "http://testserver"})
    yield client, service, ctx, tickets
    config.reset_config_cache()


def _intake(client):
    result = client.post(BASE, json={"vendor_name": "Synthetic Support AI",
        "document_id": "security-paper", "project": "VEND", "purpose": "Draft replies"})
    assert result.status_code == 201, result.text
    return result.json()


def _step(client, row, step, **extra):
    result = client.post(f"{BASE}/{row['id']}/{step}",
                         json={"expected_revision": row["revision"], **extra})
    assert result.status_code == 200, result.text
    return result.json()


def test_http_blank_purpose_does_not_leave_a_registered_vendor(web):
    from maverick import vendor_management
    from maverick.paths import tenant_scope

    client, service, ctx, tickets = web
    response = client.post(BASE, json={"vendor_name": "Rejected HTTP intake vendor",
        "document_id": "security-paper", "project": "VEND", "purpose": "   "})
    assert response.status_code == 409
    with tenant_scope(tenant=ctx.tenant):
        assert vendor_management.list_vendors() == []
    assert service.list(ctx) == []
    assert tickets.creates == 0


def test_reviewed_file_preview_is_private_nonmutating_and_requires_exact_confirmation(web):
    client, service, ctx, tickets = web
    row = _intake(client)
    body = dict(expected_revision=row["revision"], name="Synthetic.txt", mime="text/plain",
                document_date=NOW - 100, content_base64=base64.b64encode(b"Model provenance is documented with provider and version.").decode())
    response = client.post(f"{BASE}/{row['id']}/upload-preview", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    preview = response.json()
    assert preview["saved"] is False and preview["text"]
    assert service.get(ctx, row["id"])["uploads"] == []
    result = client.post(f"{BASE}/{row['id']}/upload-reviewed", json=body)
    assert result.status_code == 409
    committed = dict(body, confirm=True, review_digest=preview["review_digest"], expires_at=preview["expires_at"])
    result = client.post(f"{BASE}/{row['id']}/upload-reviewed", json=committed)
    assert result.status_code == 200, result.text
    assert result.json()["uploads"][0]["reviewed_by"] == ctx.actor
    assert tickets.creates == 0
    assert client.post(f"{BASE}/{row['id']}/upload-reviewed", json=committed).status_code == 409


def test_preview_validation_does_not_echo_private_document_fields(web):
    client, *_ = web
    row = _intake(client)
    response = client.post(f"{BASE}/{row['id']}/upload-preview", json={
        "expected_revision": row["revision"], "name": "Synthetic.txt", "mime": "text/plain",
        "document_date": NOW - 100, "content_base64": "PRIVATE INVALID CONTENT", "transcription": "private supplied text",
    })
    assert response.status_code == 422
    assert "PRIVATE" not in response.text and "private supplied" not in response.text


def test_http_workflow_review_correction_external_confirmation_and_acceptance(web):
    client, service, ctx, tickets = web
    row = _intake(client)
    row = _step(client, row, "collect-evidence")
    row = _step(client, row, "assess")
    qid = next(iter(row["assessment"]["answers"]))
    row = _step(client, row, "review", decision="approved", rationale="Restricted pilot",
                corrections={qid: {"answer": "unknown", "note": "Request clearer evidence"}},
                review_seconds=4.5)
    row = _step(client, row, "prepare-action")
    row = _step(client, row, "approve-action", confirm=True)
    row = _step(client, row, "execute", confirm=True)
    assert row["operation"]["external_confirmation"]["verified"] is True
    assert row["outcome"] is None
    row = _step(client, row, "accept", accepted=True, rationale="Accepted for the pilot")
    assert row["outcome"]["accepted"] is True
    assert row["outcome"]["released"] is False
    assert tickets.creates == 1
    assert service.get(ctx, row["id"])["metrics"]["human_review_seconds"] == 4.5
    assert len(client.get(BASE).json()["cases"]) == 1
    check = client.post(f"{BASE}/{row['id']}/check-renewal",
                        json={"expected_revision": row["revision"]})
    assert check.status_code == 200, check.text
    assert check.json()["classification"] == "human_fallback"
    assert tickets.creates == 1  # Readiness never triggers another external action.
    assert client.post(f"{BASE}/{row['id']}/check-renewal", json={
        "expected_revision": row["revision"], "now": 0, "customer_accepted": True,
    }).status_code == 422
    assert client.post(f"{BASE}/{row['id']}/check-renewal", json={
        "expected_revision": row["revision"] - 1,
    }).status_code == 409


def test_http_rejects_forged_authority_unknown_steps_and_stale_versions(web):
    client, *_ = web
    row = _intake(client)
    rejected = client.post(BASE, json={"vendor_name": "Fake", "document_id": "doc",
        "project": "VEND", "purpose": "test", "human": True, "tenant": "other"})
    assert rejected.status_code == 422
    assert client.post(f"{BASE}/{row['id']}/delete-everything", json={"expected_revision": 1}).status_code == 422
    _step(client, row, "collect-evidence")
    assert client.post(f"{BASE}/{row['id']}/assess", json={"expected_revision": row["revision"]}).status_code == 409


def test_http_document_picker_and_case_upload_preserve_dates_and_require_new_assessment(web):
    client, service, ctx, tickets = web
    found = client.get("/api/v1/vendor-onboarding/documents", params={"query": "security"})
    assert found.status_code == 200
    assert found.json()["documents"][0]["id"] == "security-paper"
    row = client.post(BASE, json={"vendor_name": "Synthetic upload", "document_ids": [],
                                 "project": "VEND", "purpose": "Upload in the app"}).json()
    result = client.post(f"{BASE}/{row['id']}/upload", params={"expected_revision": row["revision"],
        "name": "policy.txt", "document_date": NOW - 100}, headers={"Content-Type": "text/plain"},
        content=b"Model provenance is documented with provider and version.")
    assert result.status_code == 200, result.text
    uploaded = result.json()
    assert uploaded["uploads"][0]["modified_at"] == NOW - 100
    assert uploaded["uploads"][0]["date_basis"] == "uploader_asserted_document_date"
    assert uploaded["assessment"] is None
    assert client.post(f"{BASE}/{row['id']}/upload", params={"expected_revision": row["revision"],
        "name": "policy.txt", "document_date": NOW - 100}, headers={"Content-Type": "text/plain"},
        content=b"Stale request").status_code == 409
    collected = _step(client, uploaded, "collect-evidence")
    assert collected["evidence"]["source"] == "upload"
    assert service.get(ctx, row["id"])["metrics"]["document_reads"] == 0
    assert tickets.creates == 0


@pytest.mark.parametrize("name,mime,content,status", [
    ("policy.txt", "text/plain", b"x" * (1024 * 1024 + 1), 413),
    ("policy.bin", "application/octet-stream", b"binary", 415),
    ("../policy.txt", "text/plain", b"policy", 409),
    ("policy.txt", "text/plain", b"\xff", 409),
], ids=["oversized", "unsupported-mime", "path-name", "invalid-utf8"])
def test_http_upload_rejects_unsupported_oversized_or_unsafe_input_without_mutating(web, name, mime, content, status):
    client, service, ctx, tickets = web
    row = _intake(client)
    result = client.post(f"{BASE}/{row['id']}/upload", params={"expected_revision": row["revision"],
        "name": name, "document_date": NOW - 100}, headers={"Content-Type": mime}, content=content)
    assert result.status_code == status, result.text
    assert service.get(ctx, row["id"]) == row
    assert tickets.creates == 0


def test_http_unreadable_pdf_is_visible_and_blocks_assessment(web):
    client, *_ = web
    row = _intake(client)
    result = client.post(f"{BASE}/{row['id']}/upload", params={"expected_revision": row["revision"],
        "name": "scanned.pdf", "document_date": NOW - 100}, headers={"Content-Type": "application/pdf"},
        content=b"%PDF-1.4\nunsupported image-only test document")
    assert result.status_code == 200
    row = result.json()
    assert row["uploads"][0]["status"] == "extraction_failed"
    row = _step(client, row, "collect-evidence")
    assert row["evidence"]["status"] == "extraction_failed"
    assert client.post(f"{BASE}/{row['id']}/assess", json={"expected_revision": row["revision"]}).status_code == 409
    row = _step(client, row, "documents", document_ids=["security-paper"], upload_ids=[])
    assert row["uploads"] == []


def test_http_requires_separate_explicit_approval_and_blocks_cross_origin(web):
    client, _, _, tickets = web
    row = _intake(client)
    response = client.post(f"{BASE}/{row['id']}/execute",
                           json={"expected_revision": row["revision"], "confirm": True})
    assert response.status_code == 409
    assert client.post(f"{BASE}/{row['id']}/approve-action",
                       json={"expected_revision": row["revision"], "confirm": False}).status_code == 422
    assert client.post(BASE, headers={"Origin": "https://attacker.invalid"}, json={}).status_code == 403
    assert tickets.creates == 0


def test_stock_composition_rejects_anonymous_or_static_identity(web, monkeypatch):
    from maverick_dashboard.vendor_onboarding_composition import bind_request

    client, *_ = web
    monkeypatch.setattr(app.state, "vendor_onboarding_binding_factory", bind_request)
    response = client.get(BASE)
    assert response.status_code == 403
    assert "named authenticated" in response.text


def test_stock_composition_allows_upload_only_but_drive_read_still_requires_enrollment(web, monkeypatch):
    from maverick import connections
    from maverick.paths import tenant_scope
    from maverick.vendor_onboarding_adapters import GoogleDriveEvidence
    from maverick_dashboard import vendor_onboarding_composition as composition

    monkeypatch.setattr(composition, "require_permission", lambda *args: None)
    monkeypatch.setattr(composition, "caller_principal", lambda *args: "user:reviewer")
    monkeypatch.setattr(connections, "enabled", lambda: True)
    monkeypatch.setattr(connections, "get_connection", lambda name: {
        "name": "jira", "owner": "user:reviewer", "connector": "jira", "revision": "1",
    } if name == "jira" else None)
    with tenant_scope(tenant="synthetic-a"):
        context = composition._context(object())
    assert context.credential_authority == "user:reviewer"
    with pytest.raises(PermissionError, match="authority.*unavailable"):
        GoogleDriveEvidence().search(context, "policy")


def test_model_context_cannot_review(web, monkeypatch):
    client, service, ctx, _ = web
    row = _step(client, _step(client, _intake(client), "collect-evidence"), "assess")
    machine = replace(ctx, human=False)
    service.authorize = lambda c, cap, res: c.actor == ctx.actor
    monkeypatch.setattr(app.state, "vendor_onboarding_binding_factory",
                        lambda request: WorkflowBinding(service, machine))
    response = client.post(f"{BASE}/{row['id']}/review", json={
        "expected_revision": row["revision"], "decision": "approved", "rationale": "Model says so"})
    assert response.status_code == 403


def test_existing_dashboard_page_and_asset_are_usable(web):
    client, *_ = web
    response = client.get("/third-party/vendor-onboarding")
    assert response.status_code == 200, response.text
    assert "Human review" in response.text
    assert "Approve this exact Jira task" in response.text
    assert "no-store" in response.headers["cache-control"]
    asset = client.get("/static/lightwork-vendor-onboarding.js")
    assert asset.status_code == 200
    assert "textContent" in asset.text
    assert "innerHTML" not in asset.text


def test_vendor_page_has_one_main_landmark_and_labelled_decision_fields(web):
    class Markup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = []

        def handle_starttag(self, tag, attrs):
            self.elements.append((tag, dict(attrs)))

    client, *_ = web
    response = client.get("/third-party/vendor-onboarding")
    assert response.status_code == 200, response.text
    markup = Markup()
    markup.feed(response.text)
    main = [attrs for tag, attrs in markup.elements if tag == "main"]
    assert len(main) == 1
    assert main[0]["id"] == "content"
    root = next((tag, attrs) for tag, attrs in markup.elements if attrs.get("id") == "vendor-onboarding")
    assert root[0] == "section"
    assert root[1]["aria-labelledby"] == "vo-title"
    ids = [attrs["id"] for _, attrs in markup.elements if "id" in attrs]
    assert len(ids) == len(set(ids)), "Duplicate IDs break labels and request controls"
    label_targets = {attrs.get("for") for tag, attrs in markup.elements if tag == "label"}
    for field in ("vo-rationale", "vo-acceptance-reason", "vo-vendor", "vo-document",
                  "vo-project", "vo-purpose", "vo-freshness", "vo-renewal"):
        assert field in label_targets
    message = next(attrs for _, attrs in markup.elements if attrs.get("id") == "vo-message")
    assert message["aria-live"] == "polite"
