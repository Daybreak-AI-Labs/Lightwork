"""Existing Google Drive / Jira protocols with explicit credential authority.

UTF-8 files, digital PDFs, native Google Docs exports and Jira Task
creation/readback/search are supported. No ambient credentials or live acceptance are
implied. Fake providers keep externally observable state independently of cases.
"""
from __future__ import annotations

import copy
import functools
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlsplit

from .governed_operations import ServiceContext, digest
from .paths import tenant_scope

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
SUPPORTED_DRIVE_MIMES = frozenset({"text/plain", "application/pdf", GOOGLE_DOC_MIME})
MAX_TEXT_BYTES = 1024 * 1024
MAX_PDF_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class EvidenceDocument:
    document_id: str
    version: str
    modified_at: float
    content: bytes
    mime: str = "text/plain"
    export_mime: str | None = None

    def identity(self) -> dict:
        return {"document_id": self.document_id, "version": self.version,
                "modified_at": self.modified_at, "mime": self.mime}


def resolve_connection(ctx: ServiceContext, *, document: bool = False, refresh: bool = True) -> dict:
    """Resolve the *named* tenant connection; never choose another credential."""
    from . import connections

    reference = ctx.document_credential_ref if document else ctx.credential_ref
    with tenant_scope(tenant=ctx.tenant):
        if not connections.enabled():
            raise PermissionError("named connections are disabled")
        record = connections.get_connection(reference)
    connector = "gdrive" if document else "jira"
    if (not record or record.get("connector") != connector
            or record.get("owner") != ctx.credential_authority
            or not connections._may_use(record, ctx.actor)
            or not record.get("token") or not record.get("revision")):
        raise PermissionError("named credential authority is unavailable or denied")
    base = str(record.get("base_url") or "").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise PermissionError("connector requires a pinned HTTPS origin")
    if document and base != "https://www.googleapis.com":
        raise PermissionError("Drive requires the Google API origin")
    if refresh and record.get("oauth"):
        from .connection_oauth import renew
        with tenant_scope(tenant=ctx.tenant):
            record = renew(record, actor=ctx.actor)
    return dict(record, base_url=base)


def _scoped(method):
    @functools.wraps(method)
    def invoke(self, ctx, *args, **kwargs):
        if not isinstance(ctx, ServiceContext):
            raise TypeError("explicit ServiceContext required")
        with tenant_scope(tenant=ctx.tenant):
            return method(self, ctx, *args, **kwargs)
    return invoke


class GoogleDriveEvidence:
    """Version-bracketed read through existing guarded Drive fetch machinery."""
    mode = "live_unverified"

    @_scoped
    def authority_version(self, ctx):
        return resolve_connection(ctx, document=True, refresh=False)["revision"]

    @_scoped
    def head(self, ctx, document_id):
        from . import doc_discovery

        credential = resolve_connection(ctx, document=True)
        url = f"{credential['base_url']}/drive/v3/files/{quote(document_id, safe='')}"
        status, row = doc_discovery._get(url, credential["token"], {
            "fields": "id,version,modifiedTime,mimeType,trashed,size",
        })
        if status == 404:
            raise FileNotFoundError("Drive evidence is missing")
        if status != 200 or not isinstance(row, dict):
            raise RuntimeError("Drive evidence verification unavailable")
        if row.get("trashed") or row.get("mimeType") not in SUPPORTED_DRIVE_MIMES:
            raise ValueError("supported Drive evidence: UTF-8 text, digital PDF, and Google Docs")
        if not row.get("version") or row.get("id") != document_id:
            raise ValueError("Drive evidence metadata is incomplete")
        modified = datetime.fromisoformat(row["modifiedTime"].replace("Z", "+00:00"))
        return {"document_id": document_id, "version": str(row["version"]),
                "modified_at": modified.timestamp(), "mime": row["mimeType"]}

    @_scoped
    def search(self, ctx, query, *, limit=20):
        from . import doc_discovery

        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError("enter a document name of up to 200 characters")
        credential = resolve_connection(ctx, document=True)
        term = query.strip().replace("\\", "\\\\").replace("'", "\\'")
        kinds = " or ".join(f"mimeType = '{mime}'" for mime in sorted(SUPPORTED_DRIVE_MIMES))
        status, data = doc_discovery._get(f"{credential['base_url']}/drive/v3/files", credential["token"], {
            "q": f"trashed = false and name contains '{term}' and ({kinds})",
            "pageSize": min(20, limit), "fields": "files(id,name,mimeType),nextPageToken",
            "orderBy": "modifiedTime desc",
        })
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get("files"), list):
            raise RuntimeError("Drive document search is unavailable")
        return {"documents": [{"id": str(row["id"]), "name": str(row.get("name") or row["id"])[:256],
                               "mime": row["mimeType"]} for row in data["files"][:20]
                              if isinstance(row, dict) and row.get("id") and row.get("mimeType") in SUPPORTED_DRIVE_MIMES],
                "has_more": bool(data.get("nextPageToken"))}

    @_scoped
    def fetch(self, ctx, document_id):
        from . import doc_discovery

        before = self.head(ctx, document_id)
        credential = resolve_connection(ctx, document=True)
        export_mime = None
        if before["mime"] == GOOGLE_DOC_MIME:
            # Native Docs have no stable page numbering in their text export.
            # Cite paragraphs in this exact version; do not fabricate PDF pages.
            url = f"{credential['base_url']}/drive/v3/files/{quote(document_id, safe='')}/export?mimeType=text%2Fplain"
            data, _ = doc_discovery._download(url, credential["token"], MAX_TEXT_BYTES)
            export_mime = "text/plain"
        else:
            data, _ = doc_discovery._fetch_gdrive(
                credential["base_url"], credential["token"], {}, document_id,
                MAX_PDF_BYTES if before["mime"] == "application/pdf" else MAX_TEXT_BYTES,
            )
        if resolve_connection(ctx, document=True)["revision"] != credential["revision"]:
            raise PermissionError("Drive credential changed during download")
        if before != self.head(ctx, document_id):
            raise ValueError("Drive evidence changed during download")
        return EvidenceDocument(content=data, export_mime=export_mime, **before)


def jira_body(payload: dict) -> dict:
    """Same v3 Task/ADF operation already supported by tools.jira."""
    if (not isinstance(payload, dict)
            or set(payload) != {"project", "summary", "description", "marker"}
            or not all(isinstance(value, str) for value in payload.values())
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,31}", payload["project"])
            or not re.fullmatch(r"lightwork-vop-[0-9a-f]{32}", payload["marker"])
            or not 1 <= len(payload["summary"]) <= 255
            or not 1 <= len(payload["description"]) <= 12000):
        raise ValueError("unsupported Jira Task payload")
    return {"fields": {
        "project": {"key": payload["project"]},
        "summary": payload["summary"], "issuetype": {"name": "Task"},
        "labels": [payload["marker"]],
        "description": {"type": "doc", "version": 1, "content": [{
            "type": "paragraph", "content": [
                {"type": "text", "text": payload["description"]},
            ],
        }]},
    }}


class JiraTickets:
    """Jira Cloud v3 with an enrolled Bearer token; create is non-idempotent."""
    mode = "live_unverified"
    provider_idempotency = False

    def authority_version(self, ctx):
        return resolve_connection(ctx, refresh=False)["revision"]

    @_scoped
    def _request(self, ctx, method, path, *, expected_credential_version=None, **kwargs):
        from .enterprise import enterprise_egress_denial
        from .tools._ssrf import safe_client

        credential = resolve_connection(ctx)
        if expected_credential_version is not None and credential["revision"] != expected_credential_version:
            raise PermissionError("approved Jira credential generation changed")
        prefix = str((credential.get("oauth") or {}).get("api_prefix") or "")
        if prefix and (credential["base_url"] != "https://api.atlassian.com"
                       or not re.fullmatch(r"/ex/jira/[0-9a-f-]{36}", prefix)):
            raise PermissionError("Jira account destination is invalid")
        url = credential["base_url"] + prefix + path
        if enterprise_egress_denial(url, tool="jira"):
            raise PermissionError("Jira egress is not permitted")
        with safe_client(url, timeout=30) as client:
            response = client.request(method, url, headers={
                "Authorization": f"Bearer {credential['token']}",
                "Accept": "application/json",
            }, **kwargs)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"Jira operation unavailable: HTTP {response.status_code}")
        return response.json()

    def create(self, ctx, payload, operation_id, *, expected_credential_version=None):
        row = self._request(ctx, "POST", "/rest/api/3/issue", json=jira_body(payload),
                            expected_credential_version=expected_credential_version)
        from .tools.jira import _require_key

        return {"key": _require_key(str(row.get("key") or "")),
                "operation_id": operation_id}

    def verify(self, ctx, key, payload, *, expected_credential_version=None):
        from .tools.jira import _require_key

        row = self._request(ctx, "GET", f"/rest/api/3/issue/{_require_key(key)}",
                            expected_credential_version=expected_credential_version, params={
            "fields": "project,summary,description,labels,issuetype,updated",
        })
        expected = jira_body(payload)["fields"]
        fields = row.get("fields") or {}
        updated = fields.get("updated")
        try:
            version_available = (isinstance(updated, str) and bool(updated)
                                 and datetime.fromisoformat(updated.replace("Z", "+00:00")).tzinfo is not None)
        except ValueError:
            version_available = False
        matches = (row.get("key") == key
                   and version_available
                   and (fields.get("project") or {}).get("key") == payload["project"]
                   and fields.get("summary") == expected["summary"]
                   and fields.get("description") == expected["description"]
                   and payload["marker"] in (fields.get("labels") or [])
                   and (fields.get("issuetype") or {}).get("name") == "Task")
        return {"verified": matches, "key": key, "external_version": updated,
                "observed_sha256": digest(fields)}

    def find(self, ctx, payload, *, expected_credential_version=None):
        # Both project and marker are service-generated/validated, never free JQL.
        jira_body(payload)
        row = self._request(ctx, "POST", "/rest/api/3/search/jql",
                            expected_credential_version=expected_credential_version, json={
            "jql": f'project = "{payload["project"]}" AND labels = "{payload["marker"]}"',
            "fields": ["summary"], "maxResults": 2,
        })
        if row.get("nextPageToken"):
            raise RuntimeError("multiple matching tickets require human reconciliation")
        return [str(item["key"]) for item in row.get("issues", [])]


class FakeGoogleDriveEvidence:
    mode = "offline_synthetic"

    def __init__(self, documents=None):
        # Keys include tenant; no implicit cross-customer fallback.
        self.documents = dict(documents or {})
        self.version = "synthetic-drive-credential-v1"
        self.unavailable = False

    def authority_version(self, ctx):
        return self.version

    def fetch(self, ctx, document_id):
        if self.unavailable:
            raise RuntimeError("synthetic Drive unavailable")
        try:
            return self.documents[(ctx.tenant, document_id)]
        except KeyError:
            raise FileNotFoundError("evidence is missing") from None

    def head(self, ctx, document_id):
        return self.fetch(ctx, document_id).identity()

    def search(self, ctx, query, *, limit=20):
        matches = [{"id": key, "name": key, "mime": value.mime}
                   for (tenant, key), value in sorted(self.documents.items())
                   if tenant == ctx.tenant and query.lower() in key.lower()]
        return {"documents": matches[:limit], "has_more": len(matches) > limit}


class FakeJiraTickets:
    mode = "offline_synthetic"
    provider_idempotency = False

    def __init__(self, state=None):
        self.state = state if state is not None else {}
        self.version = "synthetic-jira-credential-v1"
        self.creates = 0
        self.timeout_after_success = False
        self.verify_unavailable = False

    def authority_version(self, ctx):
        return self.version

    def _credential(self, expected_credential_version):
        if expected_credential_version is not None and self.version != expected_credential_version:
            raise PermissionError("approved synthetic Jira credential generation changed")

    def create(self, ctx, payload, operation_id, *, expected_credential_version=None):
        self._credential(expected_credential_version)
        jira_body(payload)
        self.creates += 1
        key = f"{payload['project']}-{len(self.state) + 1}"
        self.state[(ctx.tenant, key)] = {"payload": copy.deepcopy(payload), "version": "1"}
        if self.timeout_after_success:
            raise TimeoutError("synthetic lost success response")
        return {"key": key, "operation_id": operation_id}

    def verify(self, ctx, key, payload, *, expected_credential_version=None):
        self._credential(expected_credential_version)
        if self.verify_unavailable:
            raise RuntimeError("synthetic Jira readback unavailable")
        observed = self.state.get((ctx.tenant, key))
        return {"verified": bool(observed and observed["payload"] == payload),
                "key": key, "external_version": (observed or {}).get("version"),
                "observed_sha256": digest(observed)}

    def find(self, ctx, payload, *, expected_credential_version=None):
        self._credential(expected_credential_version)
        if self.verify_unavailable:
            raise RuntimeError("synthetic Jira search unavailable")
        return [key for (tenant, key), value in self.state.items()
                if tenant == ctx.tenant and value["payload"]["marker"] == payload["marker"]]
