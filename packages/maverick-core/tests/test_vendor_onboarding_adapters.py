"""Provider wire checks are HTTP fakes, explicitly not live acceptance."""
from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
from maverick import connections, doc_discovery, enterprise
from maverick.governed_operations import ServiceContext
from maverick.paths import current_tenant_id_strict, tenant_scope
from maverick.tools import _ssrf
from maverick.vendor_onboarding_adapters import (
    GOOGLE_DOC_MIME,
    MAX_PDF_BYTES,
    MAX_TEXT_BYTES,
    GoogleDriveEvidence,
    JiraTickets,
    jira_body,
    resolve_connection,
)


@pytest.fixture
def credential(monkeypatch):
    ctx = ServiceContext("customer-a", "reviewer", "procurement", "credential-admin", "jira-a", "drive-a", True)
    records = {
        "jira-a": {"name": "jira-a", "connector": "jira", "owner": "credential-admin",
                   "allowed_principals": ["reviewer"], "access": "owner", "revision": "jira-v1",
                   "base_url": "https://example.atlassian.net", "token": "synthetic-jira-token"},
        "drive-a": {"name": "drive-a", "connector": "gdrive", "owner": "credential-admin",
                    "allowed_principals": ["reviewer"], "access": "owner", "revision": "drive-v1",
                    "base_url": "https://www.googleapis.com", "token": "synthetic-drive-token"},
    }
    monkeypatch.setattr(connections, "enabled", lambda: True)
    monkeypatch.setattr(connections, "get_connection", lambda name: records.get(name))
    monkeypatch.setattr(enterprise, "enterprise_egress_denial", lambda *a, **kw: None)
    return ctx, records


def payload():
    return {"project": "VEND", "summary": "Reviewed synthetic vendor",
            "description": "Exact accepted assessment digest and evidence reference",
            "marker": "lightwork-vop-" + "1" * 32}


def test_jira_create_readback_search_uses_exact_payload_and_named_credential(credential, monkeypatch):
    ctx, _ = credential
    calls = []

    def transport(request):
        calls.append(request)
        assert current_tenant_id_strict() == ctx.tenant
        assert request.headers["authorization"] == "Bearer synthetic-jira-token"
        if request.method == "POST" and request.url.path == "/rest/api/3/issue":
            assert json.loads(request.content) == jira_body(payload())
            return httpx.Response(201, json={"key": "VEND-1"})
        if request.url.path.endswith("/search/jql"):
            body = json.loads(request.content)
            assert payload()["marker"] in body["jql"]
            return httpx.Response(200, json={"issues": [{"key": "VEND-1"}]})
        fields = jira_body(payload())["fields"]
        return httpx.Response(200, json={"key": "VEND-1", "fields": dict(fields, updated="2026-09-04T00:00:00Z")})

    monkeypatch.setattr(_ssrf, "safe_client", lambda *a, **kw: httpx.Client(transport=httpx.MockTransport(transport)))
    jira = JiraTickets()
    receipt = jira.create(ctx, payload(), "VOP-" + "1" * 32)
    assert receipt["key"] == "VEND-1"
    assert jira.verify(ctx, receipt["key"], payload())["verified"] is True
    assert jira.find(ctx, payload()) == ["VEND-1"]
    assert len(calls) == 3


def test_jira_readback_compares_content_and_rejects_redirects(credential, monkeypatch):
    ctx, _ = credential
    fields = jira_body(payload())["fields"]
    fields["summary"] = "Unexpected external change"
    monkeypatch.setattr(_ssrf, "safe_client", lambda *a, **kw: httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"key": "VEND-1", "fields": fields}))))
    jira = JiraTickets()
    assert jira.verify(ctx, "VEND-1", payload())["verified"] is False
    monkeypatch.setattr(_ssrf, "safe_client", lambda *a, **kw: httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(302, headers={"Location": "https://untrusted.example"}))))
    with pytest.raises(RuntimeError, match="HTTP 302"):
        jira.create(ctx, payload(), "VOP-" + "1" * 32)


def test_jira_search_cannot_interpolate_unvalidated_payload(credential):
    ctx, _ = credential
    with pytest.raises(ValueError, match="unsupported Jira"):
        JiraTickets().find(ctx, dict(payload(), project='VEND" OR project = "OTHER'))


def test_drive_brackets_complete_bounded_text_download_with_provider_versions(credential, monkeypatch):
    ctx, _ = credential
    versions = iter(["42", "42"])

    def metadata(url, token, params):
        assert token == "synthetic-drive-token"
        assert url.endswith("/files/doc%2Fid")
        assert "version" in params["fields"]
        return 200, {"id": "doc/id", "version": next(versions), "mimeType": "text/plain",
                     "modifiedTime": "2026-09-01T12:00:00Z", "trashed": False}

    def download(base, token, ref, doc_id, max_bytes):
        assert current_tenant_id_strict() == ctx.tenant
        assert max_bytes == MAX_TEXT_BYTES
        return b"Internally authored synthetic evidence", "text/plain"

    monkeypatch.setattr(doc_discovery, "_get", metadata)
    monkeypatch.setattr(doc_discovery, "_fetch_gdrive", download)
    doc = GoogleDriveEvidence().fetch(ctx, "doc/id")
    assert doc.version == "42"
    assert doc.content == b"Internally authored synthetic evidence"


@pytest.mark.parametrize("changed", [False, True])
def test_native_google_docs_uses_bounded_export_and_brackets_source_version(credential, monkeypatch, changed):
    ctx, _ = credential
    versions = iter(["42", "43" if changed else "42"])
    monkeypatch.setattr(doc_discovery, "_get", lambda *a, **kw: (200, {
        "id": "native/doc", "version": next(versions), "mimeType": GOOGLE_DOC_MIME,
        "modifiedTime": "2026-09-01T12:00:00Z",
    }))
    calls = []

    def download(url, token, max_bytes):
        calls.append(url)
        assert url == "https://www.googleapis.com/drive/v3/files/native%2Fdoc/export?mimeType=text%2Fplain"
        assert token == "synthetic-drive-token"
        assert max_bytes == MAX_TEXT_BYTES
        return b"Native document exported as text", "text/plain"

    monkeypatch.setattr(doc_discovery, "_download", download)
    if changed:
        with pytest.raises(ValueError, match="changed during download"):
            GoogleDriveEvidence().fetch(ctx, "native/doc")
    else:
        document = GoogleDriveEvidence().fetch(ctx, "native/doc")
        assert document.mime == GOOGLE_DOC_MIME
        assert document.export_mime == "text/plain"
        assert document.identity()["mime"] == GOOGLE_DOC_MIME
    assert len(calls) == 1


def test_drive_pdf_download_has_separate_bound_and_credential_rotation_guard(credential, monkeypatch):
    ctx, records = credential
    monkeypatch.setattr(doc_discovery, "_get", lambda *a, **kw: (200, {
        "id": "pdf", "version": "42", "mimeType": "application/pdf",
        "modifiedTime": "2026-09-01T12:00:00Z",
    }))

    def download(base, token, ref, doc_id, max_bytes):
        assert max_bytes == MAX_PDF_BYTES
        records["drive-a"]["revision"] = "rotated"
        return b"%PDF-", "application/pdf"

    monkeypatch.setattr(doc_discovery, "_fetch_gdrive", download)
    with pytest.raises(PermissionError, match="credential changed"):
        GoogleDriveEvidence().fetch(ctx, "pdf")


def test_drive_picker_escapes_query_and_limits_results_to_supported_types(credential, monkeypatch):
    ctx, _ = credential

    def search(url, token, params):
        assert url == "https://www.googleapis.com/drive/v3/files"
        assert "name contains 'Alice\\'s\\\\paper'" in params["q"]
        assert params["pageSize"] == 20
        assert GOOGLE_DOC_MIME in params["q"]
        return 200, {"files": [{"id": "doc", "name": "Policy", "mimeType": GOOGLE_DOC_MIME},
                                {"id": "exe", "mimeType": "application/octet-stream"}], "nextPageToken": "more"}

    monkeypatch.setattr(doc_discovery, "_get", search)
    result = GoogleDriveEvidence().search(ctx, "Alice's\\paper")
    assert result == {"documents": [{"id": "doc", "name": "Policy", "mime": GOOGLE_DOC_MIME}], "has_more": True}


def test_drive_changed_version_during_download_is_not_evidence(credential, monkeypatch):
    ctx, _ = credential
    versions = iter(["42", "43"])
    monkeypatch.setattr(doc_discovery, "_get", lambda *a, **kw: (200, {
        "id": "doc", "version": next(versions), "mimeType": "text/plain",
        "modifiedTime": "2026-09-01T12:00:00Z",
    }))
    monkeypatch.setattr(doc_discovery, "_fetch_gdrive", lambda *a: (b"text", "text/plain"))
    with pytest.raises(ValueError, match="changed during download"):
        GoogleDriveEvidence().fetch(ctx, "doc")


@pytest.mark.parametrize("change", ["owner", "principal", "reference", "connector", "missing_revision", "http"])
def test_credential_authority_and_enrollment_fail_closed(credential, change):
    ctx, records = credential
    if change == "owner":
        ctx = replace(ctx, credential_authority="somebody-else")
    elif change == "principal":
        ctx = replace(ctx, actor="unauthorized")
    elif change == "reference":
        ctx = replace(ctx, credential_ref="other-credential")
    elif change == "connector":
        records["jira-a"]["connector"] = "salesforce"
    elif change == "missing_revision":
        records["jira-a"]["revision"] = ""
    else:
        records["jira-a"]["base_url"] = "http://example.atlassian.net"
    with pytest.raises(PermissionError):
        resolve_connection(ctx)


def test_explicit_credential_resolution_does_not_inherit_ambient_tenant(credential, monkeypatch):
    ctx, records = credential
    observed = []

    def get(name):
        observed.append(current_tenant_id_strict())
        return records[name]

    monkeypatch.setattr(connections, "get_connection", get)
    with tenant_scope(tenant="ambient-customer-b"):
        resolve_connection(ctx)
        assert current_tenant_id_strict() == "ambient-customer-b"
    assert observed == ["customer-a"]
