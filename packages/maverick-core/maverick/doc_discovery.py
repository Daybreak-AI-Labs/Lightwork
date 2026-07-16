"""Document discovery for assessments: find the SOW/contract/DPA for them.

When someone is answering assessment questions, the documents that answer
half of them -- the statement of work, the contract, the DPA, the security
overview -- usually already exist in the company's own systems. This module
searches the CONNECTED sources (Microsoft Graph / SharePoint+OneDrive, Slack
files, Google Drive) for documents related to the assessment subject and lets
the caller pull one down and attach it as evidence, instead of making the
respondent go hunt for files.

Credentials resolve per source, env var first then a named
:mod:`maverick.connections` record (the citizen-user path -- wired from the
dashboard, sealed at rest, never code):

  ==========  =========================  ==================  ============
  source      env token                  env base override   connection
  ==========  =========================  ==================  ============
  msgraph     MSGRAPH_ACCESS_TOKEN       MSGRAPH_BASE_URL    ``msgraph``
  slack       SLACK_SEARCH_TOKEN /       SLACK_BASE_URL      ``slack``
              SLACK_BOT_TOKEN
  gdrive      GDRIVE_ACCESS_TOKEN        GDRIVE_BASE_URL     ``gdrive``
  ==========  =========================  ==================  ============

The base-URL override is what makes this testable and demoable against a
simulated tenant while keeping ONE real code path. Everything is fail-open:
a source that isn't configured is skipped, a source that errors contributes
nothing (logged), and discovery never raises. Fetched bytes are capped at the
attachment limit so discovery can't smuggle in what a direct upload couldn't.
Gated by ``[assessments] doc_discovery`` (default on -- it only activates when
a source is actually configured).
"""
from __future__ import annotations

import logging
import os
import urllib.parse
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# The document kinds worth proactively finding for an assessment. Matched
# against filenames for ranking; folded into search queries.
DOC_KEYWORDS = (
    "sow", "statement of work", "contract", "msa", "dpa",
    "data processing", "security", "questionnaire", "privacy",
    "architecture", "agreement",
)

_TIMEOUT = 15.0


@dataclass
class DocHit:
    source: str
    doc_id: str
    name: str
    url: str = ""          # human-facing web link
    snippet: str = ""
    mime: str = ""
    size: int = 0
    score: float = 0.0
    # opaque fetch details a source needs to download content (e.g. msgraph
    # drive id, slack private download url). Round-tripped by the caller.
    ref: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"source": self.source, "doc_id": self.doc_id, "name": self.name,
                "url": self.url, "snippet": self.snippet, "mime": self.mime,
                "size": self.size, "score": round(self.score, 3),
                "ref": self.ref}


def enabled() -> bool:
    try:
        from .config import get_assessments
        return bool(get_assessments()["doc_discovery"])
    except Exception:  # pragma: no cover -- config never crashes a read
        return True


# --------------------------------------------------------------------------- #
# Credential resolution (env first, then a sealed named connection).
# --------------------------------------------------------------------------- #

_ENV_TOKENS = {
    "msgraph": ("MSGRAPH_ACCESS_TOKEN",),
    "slack": ("SLACK_SEARCH_TOKEN", "SLACK_BOT_TOKEN"),
    "gdrive": ("GDRIVE_ACCESS_TOKEN",),
}
_ENV_BASES = {
    "msgraph": "MSGRAPH_BASE_URL",
    "slack": "SLACK_BASE_URL",
    "gdrive": "GDRIVE_BASE_URL",
}
_DEFAULT_BASES = {
    "msgraph": "https://graph.microsoft.com/v1.0",
    "slack": "https://slack.com/api",
    "gdrive": "https://www.googleapis.com",
}


def _creds(source: str) -> tuple[str, str] | None:
    """(base_url, token) for a source, or None when unconfigured."""
    token = ""
    for var in _ENV_TOKENS.get(source, ()):
        token = os.environ.get(var, "").strip()
        if token:
            break
    base = os.environ.get(_ENV_BASES.get(source, ""), "").strip()
    if not token:
        try:
            from . import connections
            rec = connections.resolve(source)
        except Exception:  # pragma: no cover -- connections layer optional
            rec = None
        if rec:
            base = base or rec[0]
            token = rec[1]
    if not token:
        return None
    return (base or _DEFAULT_BASES[source]).rstrip("/"), token


def configured_sources() -> list[str]:
    """Sources that currently have credentials, in stable order."""
    from .config import get_assessments
    wanted = get_assessments()["sources"]
    return [s for s in wanted if _creds(s) is not None]


def _get(url: str, token: str, params: dict | None = None,
         *, auth: str = "bearer") -> tuple[int, object]:
    import httpx
    headers = {"Authorization": f"Bearer {token}"} if auth == "bearer" else {}
    r = httpx.get(url, headers=headers, params=params or {}, timeout=_TIMEOUT)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:300]


def _post(url: str, token: str, body: dict) -> tuple[int, object]:
    import httpx
    r = httpx.post(url, headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json"},
                   json=body, timeout=_TIMEOUT)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:300]


# --------------------------------------------------------------------------- #
# Source adapters: search(query) -> [DocHit], fetch(hit) -> (name, bytes, mime)
# --------------------------------------------------------------------------- #

def _search_msgraph(base: str, token: str, query: str, limit: int) -> list[DocHit]:
    """Microsoft Search over driveItems: OneDrive + every SharePoint site the
    caller can read -- where the SOW/contract/DPA actually live."""
    status, data = _post(f"{base}/search/query", token, {
        "requests": [{
            "entityTypes": ["driveItem"],
            "query": {"queryString": query},
            "size": limit,
        }],
    })
    if status != 200 or not isinstance(data, dict):
        log.debug("doc_discovery msgraph search %s: %s", status, data)
        return []
    hits: list[DocHit] = []
    for value in data.get("value", []):
        for container in value.get("hitsContainers", []):
            for h in container.get("hits", []):
                res = h.get("resource", {}) or {}
                parent = res.get("parentReference", {}) or {}
                hits.append(DocHit(
                    source="msgraph",
                    doc_id=str(res.get("id", "")),
                    name=str(res.get("name", "")) or "(unnamed)",
                    url=str(res.get("webUrl", "")),
                    snippet=str(h.get("summary", ""))[:300],
                    mime=str((res.get("file") or {}).get("mimeType", "")),
                    size=int(res.get("size", 0) or 0),
                    ref={"drive_id": str(parent.get("driveId", ""))},
                ))
    return hits


def _fetch_msgraph(base: str, token: str, hit_ref: dict, doc_id: str):
    import httpx
    drive = hit_ref.get("drive_id", "")
    path = (f"/drives/{urllib.parse.quote(drive)}/items/"
            f"{urllib.parse.quote(doc_id)}/content" if drive
            else f"/me/drive/items/{urllib.parse.quote(doc_id)}/content")
    r = httpx.get(f"{base}{path}", headers={"Authorization": f"Bearer {token}"},
                  timeout=_TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "application/octet-stream")


def _search_slack(base: str, token: str, query: str, limit: int) -> list[DocHit]:
    status, data = _get(f"{base}/search.files", token,
                        {"query": query, "count": limit})
    if status != 200 or not isinstance(data, dict) or not data.get("ok"):
        log.debug("doc_discovery slack search %s: %s", status, data)
        return []
    hits: list[DocHit] = []
    for f in ((data.get("files") or {}).get("matches") or [])[:limit]:
        hits.append(DocHit(
            source="slack",
            doc_id=str(f.get("id", "")),
            name=str(f.get("name", "")) or "(unnamed)",
            url=str(f.get("permalink", "")),
            mime=str(f.get("mimetype", "")),
            size=int(f.get("size", 0) or 0),
            ref={"download_url": str(f.get("url_private_download")
                                     or f.get("url_private") or "")},
        ))
    return hits


def _fetch_slack(base: str, token: str, hit_ref: dict, doc_id: str):
    import httpx
    url = hit_ref.get("download_url", "")
    if not url:
        raise ValueError("slack hit carries no download url")
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"},
                  timeout=_TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "application/octet-stream")


def _search_gdrive(base: str, token: str, query: str, limit: int) -> list[DocHit]:
    q = " or ".join(f"name contains '{t}'" for t in query.split()[:6]
                    if t and "'" not in t)
    if not q:
        return []
    status, data = _get(f"{base}/drive/v3/files", token, {
        "q": q, "pageSize": limit,
        "fields": "files(id,name,mimeType,size,webViewLink)",
    })
    if status != 200 or not isinstance(data, dict):
        log.debug("doc_discovery gdrive search %s: %s", status, data)
        return []
    return [DocHit(
        source="gdrive", doc_id=str(f.get("id", "")),
        name=str(f.get("name", "")) or "(unnamed)",
        url=str(f.get("webViewLink", "")), mime=str(f.get("mimeType", "")),
        size=int(f.get("size", 0) or 0),
    ) for f in data.get("files", [])[:limit]]


def _fetch_gdrive(base: str, token: str, hit_ref: dict, doc_id: str):
    import httpx
    r = httpx.get(f"{base}/drive/v3/files/{urllib.parse.quote(doc_id)}",
                  headers={"Authorization": f"Bearer {token}"},
                  params={"alt": "media"}, timeout=_TIMEOUT,
                  follow_redirects=True)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "application/octet-stream")


_SEARCHERS = {"msgraph": _search_msgraph, "slack": _search_slack,
              "gdrive": _search_gdrive}
_FETCHERS = {"msgraph": _fetch_msgraph, "slack": _fetch_slack,
             "gdrive": _fetch_gdrive}


# --------------------------------------------------------------------------- #
# Discovery + fetch
# --------------------------------------------------------------------------- #

def _rank(hit: DocHit, subject_tokens: set[str]) -> float:
    """Explainable relevance: doc-keyword hits in the filename + subject-token
    overlap. No model, no magic -- a reviewer can see why a doc surfaced."""
    name = hit.name.lower()
    score = sum(1.0 for kw in DOC_KEYWORDS if kw in name)
    score += sum(0.5 for t in subject_tokens if t and t in name)
    if hit.snippet:
        snip = hit.snippet.lower()
        score += sum(0.25 for t in subject_tokens if t and t in snip)
    return score


def discover(subject: str, *, keywords: tuple[str, ...] | None = None,
             sources: list[str] | None = None, limit: int = 8) -> list[DocHit]:
    """Search every configured source for documents related to ``subject``.

    Returns ranked, deduped hits (best first), at most ``limit``. Never
    raises: unconfigured sources are skipped, erroring sources contribute
    nothing. An empty subject returns nothing rather than everything.
    """
    subject = (subject or "").strip()
    if not subject or not enabled():
        return []
    use = sources if sources is not None else None
    if use is None:
        try:
            use = configured_sources()
        except Exception:  # pragma: no cover
            use = [s for s in _SEARCHERS if _creds(s)]
    kws = keywords if keywords is not None else ("sow", "contract", "dpa",
                                                 "security", "agreement")
    subject_tokens = {t.lower() for t in subject.split() if len(t) > 2}
    seen: set[tuple[str, str]] = set()
    out: list[DocHit] = []
    for source in use:
        creds = _creds(source)
        searcher = _SEARCHERS.get(source)
        if creds is None or searcher is None:
            continue
        base, token = creds
        # One broad query per source: subject + the doc vocabulary. Sources
        # tokenize server-side; per-keyword fan-out costs N requests for the
        # same recall on every backend tested.
        query = f"{subject} {' '.join(kws)}".strip()
        try:
            found = searcher(base, token, query, max(limit, 8))
        except Exception as e:  # noqa: BLE001 -- one source must not kill discovery
            log.warning("doc_discovery: %s search failed: %s", source, e)
            continue
        for h in found:
            key = (h.source, h.doc_id)
            if h.doc_id and key not in seen:
                seen.add(key)
                h.score = _rank(h, subject_tokens)
                out.append(h)
    out.sort(key=lambda h: h.score, reverse=True)
    return out[:limit]


def fetch(source: str, doc_id: str, ref: dict | None = None,
          *, max_bytes: int | None = None) -> tuple[bytes, str]:
    """Download one discovered document's content: ``(bytes, mime)``.

    Raises ValueError on an unknown/unconfigured source or an oversized file
    (capped at the attachment limit so discovery can't smuggle in what a
    direct upload couldn't)."""
    creds = _creds(source)
    fetcher = _FETCHERS.get(source)
    if creds is None or fetcher is None:
        raise ValueError(f"source {source!r} is not configured")
    if max_bytes is None:
        from .attachments import MAX_FILE_BYTES
        max_bytes = MAX_FILE_BYTES
    base, token = creds
    data, mime = fetcher(base, token, ref or {}, doc_id)
    if len(data) > max_bytes:
        raise ValueError(f"document too large: {len(data)} bytes "
                         f"(limit {max_bytes})")
    return data, mime


__all__ = ["DocHit", "DOC_KEYWORDS", "configured_sources", "discover",
           "enabled", "fetch"]
