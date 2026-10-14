"""Bounded remote document reads through the installed connection/SSRF gates.

A saved source names an account and container, never an arbitrary credential
or download address. Every physical read resolves the pinned account again.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from base64 import b64encode
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

from . import config, connections
from .authority_effects import authority_effect_fence

# The item, page and download bounds are [knowledge_sources] settings, read at
# every listing, download and refresh (config.get_knowledge_sources). This is
# the most items any saved manifest can hold, whatever the current setting.
MAX_ITEMS_CEILING = 1000
# Metadata, listing pages and Confluence page bodies are read as JSON under this
# fixed limit. max_download_mib governs file downloads only.
_JSON_READ_BYTES = 2 * 1024 * 1024
SOURCES = {
    "sharepoint": {"label": "SharePoint library folder", "connector": "msgraph"},
    "onedrive": {"label": "OneDrive folder", "connector": "msgraph"},
    "drive": {"label": "Google Drive folder", "connector": "gdrive"},
    "confluence": {"label": "Confluence space", "connector": "confluence"},
}


class SourceReadError(ValueError):
    """A controlled diagnostic; never includes a credential or signed URL."""


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.,:!~+-]{0,511}", value):
        raise SourceReadError("Choose a current folder or space from this account.")
    return value


def origin(kind, value):
    if kind not in SOURCES or not isinstance(value, str):
        raise SourceReadError("Choose a supported saved account.")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise SourceReadError("Reconnect this account using its secure service origin in Connections.")
    host = parsed.hostname.lower()
    allowed = (host == "graph.microsoft.com" if kind in {"sharepoint", "onedrive"}
               else host == "www.googleapis.com" if kind == "drive"
               else host.endswith(".atlassian.net"))
    if not allowed:
        raise SourceReadError("This saved account has a different service destination.")
    return "https://" + host


def selection(kind, value):
    fields = {"sharepoint": {"site", "drive", "folder"}, "onedrive": {"drive", "folder"},
              "drive": {"folder"}, "confluence": {"space"}}.get(kind)
    if not isinstance(value, dict) or set(value) != fields:
        raise SourceReadError("Choose all of this source's folder or space fields.")
    return {key: identifier(child) for key, child in value.items()}


def validate(binding):
    if not isinstance(binding, dict) or set(binding) != {"kind", "connection", "revision", "connector", "origin", "selection"}:
        raise SourceReadError("The saved source account binding is unavailable.")
    kind = binding["kind"]
    if kind not in SOURCES or binding["connector"] != SOURCES[kind]["connector"]:
        raise SourceReadError("The saved source provider changed.")
    if (not isinstance(binding["connection"], str) or connections.normalize_name(binding["connection"]) != binding["connection"]
            or not isinstance(binding["revision"], str) or not binding["revision"]):
        raise SourceReadError("Select the current saved account again.")
    return {**binding, "origin": origin(kind, binding["origin"]), "selection": selection(kind, binding["selection"])}


def account(binding, actor):
    cfg = validate(binding)
    with connections.bind_principal(actor):
        return connections.resolve_named(cfg["connection"], expected_revision=cfg["revision"],
                                         connector=cfg["connector"], origin=cfg["origin"])


def _url(binding, path):
    from .automation_events import _same_origin_url

    url = _same_origin_url(binding["origin"], path)
    prefix = "/v1.0/" if binding["kind"] in {"sharepoint", "onedrive"} else "/drive/v3/" if binding["kind"] == "drive" else "/wiki/"
    if not urlsplit(url).path.startswith(prefix):
        raise SourceReadError("The provider returned an unexpected document endpoint.")
    return url


def read_bytes(binding, actor, path, guard, *, limit=None):
    """Use the native pinned transport, with no cross-origin credential redirect."""
    from .automation_events import _http_read_bytes
    from .enterprise import enterprise_egress_denial

    if limit is None:
        limit = config.get_knowledge_sources()["max_download_mib"] * 1024 * 1024
    cfg = validate(binding)
    url = _url(cfg, path)
    with authority_effect_fence(), connections._mutation_lock():
        guard()
        current = account(cfg, actor)
        denial = enterprise_egress_denial(url, tool="doc_discovery")
        if denial:
            raise SourceReadError("Current outbound policy does not permit this document read.")
        token = str(current["token"])
        authorization = ("Basic " + b64encode(token.encode()).decode("ascii")
                         if cfg["kind"] == "confluence" and ":" in token else "Bearer " + token)
        return _http_read_bytes(url, headers={"Authorization": authorization,
                                            "Accept": "application/json"}, max_bytes=limit)


def _download_too_large(max_mib):
    return f"The document is larger than the {max_mib} MiB download limit (max_download_mib), so it was not imported."


def _download(binding, actor, url, guard):
    from .doc_discovery import _guarded_client

    max_mib = config.get_knowledge_sources()["max_download_mib"]
    max_bytes = max_mib * 1024 * 1024
    deadline = time.monotonic() + 45
    for _ in range(5):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not parsed.hostname.lower().endswith((".sharepoint.com", ".1drv.com", ".onedrive.com"))):
            raise SourceReadError("The selected drive did not return a permitted download address.")
        with authority_effect_fence(), connections._mutation_lock():
            guard()
            account(binding, actor)
            if time.monotonic() >= deadline:
                raise SourceReadError("The document download exceeded its time limit.")
            with _guarded_client(url, timeout=min(15, deadline - time.monotonic())) as client:
                with client.stream("GET", url, headers={}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = str(response.headers.get("location") or "")
                        if not location:
                            raise SourceReadError("The provider returned an incomplete document redirect.")
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise SourceReadError("The selected drive could not deliver this document.")
                    chunks, total = [], 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if time.monotonic() >= deadline:
                            raise SourceReadError("The document download exceeded its time limit.")
                        if total > max_bytes:
                            raise SourceReadError(_download_too_large(max_mib))
                        chunks.append(chunk)
                    return b"".join(chunks)
    raise SourceReadError("The provider returned too many document redirects.")


def read_json(binding, actor, path, guard):
    raw = read_bytes(binding, actor, path, guard, limit=_JSON_READ_BYTES)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise SourceReadError("The selected account did not return readable document metadata.") from exc
    if not isinstance(body, dict):
        raise SourceReadError("The selected account returned incomplete document metadata.")
    return body


def collection(binding, actor, path, guard, *, key="value"):
    bounds = config.get_knowledge_sources()
    rows, seen = [], set()
    initial = urlsplit(_url(binding, path))
    for _ in range(bounds["max_pages"]):
        if path in seen:
            raise SourceReadError("The provider repeated a result page. Nothing was marked complete.")
        seen.add(path)
        body = read_json(binding, actor, path, guard)
        page = body.get(key)
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise SourceReadError("The selected account did not return a complete document list.")
        rows.extend(page)
        if len(rows) > bounds["max_items"]:
            raise SourceReadError(f"This source exceeds {bounds['max_items']} items. Choose a smaller folder or space.")
        if binding["kind"] == "drive":
            token = body.get("nextPageToken")
            path = (path.split("&pageToken=", 1)[0] + "&pageToken=" + quote(str(token), safe="")) if token else None
        elif binding["kind"] == "confluence":
            path = body.get("_links", {}).get("next")
            if isinstance(path, str) and path.startswith("/rest/api/"):
                path = "/wiki" + path
        else:
            path = body.get("@odata.nextLink")
        if not path:
            return rows
        path = _url(binding, path)
        parsed = urlsplit(path)
        if parsed.path != initial.path:
            raise SourceReadError("The provider changed containers between result pages. Nothing was marked complete.")
        if binding["kind"] == "confluence":
            before, after = parse_qs(initial.query), parse_qs(parsed.query)
            if any(before.get(name) != after.get(name) for name in ("spaceKey", "type", "status") if name in before):
                raise SourceReadError("The provider changed the selected space between result pages.")
    raise SourceReadError(f"The document listing exceeded {bounds['max_pages']} pages. Nothing was marked complete.")


def picker(binding, actor, step, chosen, guard):
    def esc(value):
        return quote(identifier(value), safe="")
    kind = binding["kind"]
    if kind == "sharepoint" and step == "sites" and not chosen:
        rows = collection(binding, actor, "/v1.0/sites?search=*", guard)
    elif kind == "sharepoint" and step == "drives" and set(chosen) == {"site"}:
        rows = collection(binding, actor, "/v1.0/sites/" + esc(chosen["site"]) + "/drives", guard)
    elif kind == "onedrive" and step == "drives" and not chosen:
        rows = collection(binding, actor, "/v1.0/me/drives", guard)
    elif kind in {"sharepoint", "onedrive"} and step == "folders" and set(chosen) in ({"drive"}, {"drive", "folder"}):
        parent = chosen.get("folder", "root")
        suffix = "root" if parent == "root" else "items/" + esc(parent)
        rows = collection(binding, actor, "/v1.0/drives/" + esc(chosen["drive"]) + "/" + suffix + "/children?$top=64", guard)
        rows = [row for row in rows if isinstance(row.get("folder"), dict)]
        rows.insert(0, {"id": parent, "name": "Use this folder"})
    elif kind == "drive" and step == "folders" and set(chosen) <= {"folder"}:
        parent = identifier(chosen.get("folder", "root"))
        query = f"'{parent}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder'"
        rows = collection(binding, actor, "/drive/v3/files?" + urlencode({"q": query, "fields": "nextPageToken,files(id,name)", "pageSize": 64}), guard, key="files")
        rows.insert(0, {"id": parent, "name": "Use this folder"})
    elif kind == "confluence" and step == "spaces" and not chosen:
        rows = collection(binding, actor, "/wiki/rest/api/space?limit=64", guard, key="results")
        return [{"value": identifier(row.get("key")), "label": str(row.get("name") or row["key"])[:200]} for row in rows]
    else:
        raise SourceReadError("Choose this account's site, drive, folder or space in order.")
    return [{"value": identifier(row.get("id")), "label": str(row.get("name") or row.get("displayName") or row["id"])[:200]} for row in rows]


def inventory(binding, actor, guard):
    cfg = validate(binding)
    kind, chosen = cfg["kind"], cfg["selection"]
    def esc(value):
        return quote(identifier(value), safe="")
    if kind in {"sharepoint", "onedrive"}:
        if kind == "sharepoint":
            # Prove the reviewed drive still belongs to the selected site.
            drives = collection(cfg, actor, "/v1.0/sites/" + esc(chosen["site"]) + "/drives", guard)
            if chosen["drive"] not in {str(row.get("id")) for row in drives}:
                raise SourceReadError("The selected library no longer belongs to this site.")
        suffix = "root" if chosen["folder"] == "root" else "items/" + esc(chosen["folder"])
        rows = collection(cfg, actor, "/v1.0/drives/" + esc(chosen["drive"]) + "/" + suffix + "/children?$top=64", guard)
        result = [{"id": identifier(row.get("id")), "name": str(row.get("name") or "Document")[:200],
                   "revision": str(row.get("eTag") or row.get("cTag") or ""),
                   "mime": str(row.get("file", {}).get("mimeType") or ""), "size": row.get("size"),
                   "citation": str(row.get("webUrl") or ""), "kind": "file" if isinstance(row.get("file"), dict) else "folder"} for row in rows]
    elif kind == "drive":
        query = f"'{identifier(chosen['folder'])}' in parents and trashed = false"
        rows = collection(cfg, actor, "/drive/v3/files?" + urlencode({"q": query, "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,version,md5Checksum,size,webViewLink)", "pageSize": 64}), guard, key="files")
        result = [{"id": identifier(row.get("id")), "name": str(row.get("name") or "Document")[:200],
                   "revision": str(row.get("version") or row.get("md5Checksum") or row.get("modifiedTime") or ""),
                   "mime": str(row.get("mimeType") or ""), "size": row.get("size"), "citation": str(row.get("webViewLink") or ""),
                   "kind": "folder" if row.get("mimeType") == "application/vnd.google-apps.folder" else "file"} for row in rows]
    else:
        rows = collection(cfg, actor, "/wiki/rest/api/content?" + urlencode({"spaceKey": chosen["space"], "type": "page", "status": "current", "expand": "version", "limit": 64}), guard, key="results")
        result = [{"id": identifier(row.get("id")), "name": str(row.get("title") or "Page")[:200],
                   "revision": str(row.get("version", {}).get("number") or ""), "mime": "text/html", "size": None,
                   "citation": cfg["origin"] + "/wiki" + str(row.get("_links", {}).get("webui") or ""), "kind": "file"} for row in rows]
    if len({row["id"] for row in result}) != len(result):
        raise SourceReadError("The provider returned ambiguous duplicate document identities.")
    # This is deliberately the selected folder's direct children, not a crawl.
    return result


def document(binding, actor, item, guard):
    """Read a selected stable item; metadata changes during the read are refused."""
    from .automation_events import EventSourceTooLarge

    kind, chosen = binding["kind"], binding["selection"]
    item_id = quote(identifier(item["id"]), safe="")
    name, mime = item["name"], item["mime"]
    max_mib = config.get_knowledge_sources()["max_download_mib"]
    if item["kind"] != "file":
        raise SourceReadError("Subfolders are not imported. Add each desired folder as a separate source.")
    if kind in {"sharepoint", "onedrive"}:
        # Graph's /content redirects to signed storage URLs. Read its current
        # metadata first, then use the installed guarded downloader with no
        # Authorization forwarded to that separately validated public URL.
        path = "/v1.0/drives/" + quote(identifier(chosen["drive"]), safe="") + "/items/" + item_id
        current = read_json(binding, actor, path, guard)
        if str(current.get("eTag") or current.get("cTag") or "") != item["revision"]:
            raise SourceReadError("The document changed after listing. Refresh this source again.")
        download = current.get("@microsoft.graph.downloadUrl")
        parsed = urlsplit(str(download or ""))
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not parsed.hostname.lower().endswith((".sharepoint.com", ".1drv.com", ".onedrive.com"))):
            raise SourceReadError("The selected drive did not return a permitted document download address.")
        raw = _download(binding, actor, download, guard)
    elif kind == "drive":
        path = "/drive/v3/files/" + item_id
        current = read_json(binding, actor, path + "?fields=version,md5Checksum,modifiedTime", guard)
        revision = str(current.get("version") or current.get("md5Checksum") or current.get("modifiedTime") or "")
        if revision != item["revision"]:
            raise SourceReadError("The document changed after listing. Refresh this source again.")
        if mime == "application/vnd.google-apps.document":
            path += "/export?mimeType=text%2Fplain"
            name += ".txt"
        elif mime.startswith("application/vnd.google-apps."):
            raise SourceReadError("This Google document type is not supported; export it to PDF, text, Word or Excel first.")
        else:
            path += "?alt=media"
        try:
            raw = read_bytes(binding, actor, path, guard, limit=max_mib * 1024 * 1024)
        except EventSourceTooLarge as exc:
            raise SourceReadError(_download_too_large(max_mib)) from exc
    else:
        try:
            current = read_json(binding, actor, "/wiki/rest/api/content/" + item_id + "?expand=body.storage,version", guard)
        except EventSourceTooLarge as exc:
            raise SourceReadError(
                f"This Confluence page is larger than its {_JSON_READ_BYTES // (1024 * 1024)} MiB read limit, so it was not imported. "
                "Split it into smaller pages; max_download_mib does not apply to Confluence pages.") from exc
        if str(current.get("version", {}).get("number") or "") != item["revision"]:
            raise SourceReadError("The page changed after listing. Refresh this source again.")
        value = current.get("body", {}).get("storage", {}).get("value")
        if not isinstance(value, str):
            raise SourceReadError("This page has no complete readable body.")
        raw, name = value.encode("utf-8"), name + ".html"
    if not raw or len(raw) > max_mib * 1024 * 1024:
        raise SourceReadError(f"The document is empty or exceeds the {max_mib} MiB download limit.")
    from maverick_knowledge.parse import extract_document
    suffix = Path(name).suffix.lower()
    if suffix not in {".txt", ".md", ".csv", ".html", ".htm", ".pdf", ".docx", ".xlsx"}:
        suffix = ".txt" if mime.startswith("text/") else suffix
    if suffix not in {".txt", ".md", ".csv", ".html", ".htm", ".pdf", ".docx", ".xlsx"}:
        raise SourceReadError("This file type cannot be imported as complete company knowledge.")
    with tempfile.TemporaryDirectory(prefix="lightwork-knowledge-source-") as directory:
        path = Path(directory) / ("document" + suffix)
        path.write_bytes(raw)
        parsed = extract_document(path)
    if parsed.status != "complete" or parsed.omitted_regions:
        raise SourceReadError("The document extractor omitted content. The file was not marked imported.")
    text = parsed.text
    if not text.strip():
        raise SourceReadError("The document contains no readable text.")
    return text, hashlib.sha256(raw).hexdigest(), parsed.metadata()
