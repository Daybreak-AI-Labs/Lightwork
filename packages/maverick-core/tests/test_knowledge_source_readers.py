"""Saved remote Knowledge sources: listings and downloads use the configured bounds."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest
from maverick import automation_events, config, connections, doc_discovery, enterprise
from maverick import knowledge_source_readers as readers

ONE_MIB = 1024 * 1024
PATH = "/v1.0/drives/d1/root/children?$top=64"
NEXT_2 = "https://graph.microsoft.com/v1.0/drives/d1/root/children?$skiptoken=2"
NEXT_3 = "https://graph.microsoft.com/v1.0/drives/d1/root/children?$skiptoken=3"
SHAREPOINT = {"kind": "sharepoint", "origin": "https://graph.microsoft.com"}
DRIVE = {"kind": "drive", "connection": "team-drive", "revision": "r1", "connector": "gdrive",
         "origin": "https://www.googleapis.com", "selection": {"folder": "root"}}


def _set_section(monkeypatch, section):
    monkeypatch.setattr(config, "load_config", lambda: {"knowledge_sources": section})


def _serve_pages(monkeypatch, pages):
    """Answer each listing request from ``pages``, keyed by the requested path."""
    def read_json(binding, actor, path, guard):
        return pages[path]

    monkeypatch.setattr(readers, "read_json", read_json)


def _rows(count, start=0):
    return [{"id": f"item-{start + n}", "name": f"Policy {start + n}.txt"} for n in range(count)]


def test_the_default_item_limit_admits_64_items_and_refuses_65(monkeypatch):
    _set_section(monkeypatch, {})
    _serve_pages(monkeypatch, {PATH: {"value": _rows(64)}})
    assert len(readers.collection(SHAREPOINT, "user:alice", PATH, lambda: None)) == 64

    _serve_pages(monkeypatch, {PATH: {"value": _rows(65)}})
    with pytest.raises(readers.SourceReadError, match=r"^This source exceeds 64 items\. Choose a smaller folder or space\.$"):
        readers.collection(SHAREPOINT, "user:alice", PATH, lambda: None)


def test_raised_limits_admit_more_items_and_pages(monkeypatch):
    pages = {
        PATH: {"value": _rows(64), "@odata.nextLink": NEXT_2},
        NEXT_2: {"value": _rows(64, 64), "@odata.nextLink": NEXT_3},
        NEXT_3: {"value": _rows(22, 128)},
    }
    _set_section(monkeypatch, {"max_items": 150, "max_pages": 3})
    _serve_pages(monkeypatch, pages)
    assert len(readers.collection(SHAREPOINT, "user:alice", PATH, lambda: None)) == 150


def test_the_item_refusal_names_the_configured_item_limit(monkeypatch):
    _set_section(monkeypatch, {"max_items": 149, "max_pages": 3})
    _serve_pages(monkeypatch, {PATH: {"value": _rows(64), "@odata.nextLink": NEXT_2},
                               NEXT_2: {"value": _rows(64, 64), "@odata.nextLink": NEXT_3},
                               NEXT_3: {"value": _rows(22, 128)}})
    with pytest.raises(readers.SourceReadError, match=r"^This source exceeds 149 items\."):
        readers.collection(SHAREPOINT, "user:alice", PATH, lambda: None)


def test_the_listing_refusal_names_the_configured_page_limit(monkeypatch):
    _set_section(monkeypatch, {"max_pages": 2})
    _serve_pages(monkeypatch, {PATH: {"value": _rows(10), "@odata.nextLink": NEXT_2},
                               NEXT_2: {"value": _rows(10, 10), "@odata.nextLink": NEXT_3},
                               NEXT_3: {"value": _rows(10, 20)}})
    with pytest.raises(readers.SourceReadError, match=r"exceeded 2 pages\."):
        readers.collection(SHAREPOINT, "user:alice", PATH, lambda: None)


def _transport(monkeypatch):
    """Stand in for the installed authority, egress policy and HTTP transport."""
    calls = []
    monkeypatch.setattr(readers, "authority_effect_fence", lambda: nullcontext())
    monkeypatch.setattr(connections, "_mutation_lock", lambda: nullcontext())
    monkeypatch.setattr(readers, "account", lambda binding, actor: {"token": "token-value"})
    monkeypatch.setattr(enterprise, "enterprise_egress_denial", lambda url, tool="": None)

    def http_read_bytes(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return b"x"

    monkeypatch.setattr(automation_events, "_http_read_bytes", http_read_bytes)
    return calls


@pytest.mark.parametrize(("section", "expected"), [({}, 4 * ONE_MIB), ({"max_download_mib": 8}, 8 * ONE_MIB)])
def test_a_drive_download_uses_the_configured_download_limit(monkeypatch, section, expected):
    _set_section(monkeypatch, section)
    calls = _transport(monkeypatch)
    readers.read_bytes(DRIVE, "user:alice", "/drive/v3/files/f1?alt=media", lambda: None)
    assert calls[0]["max_bytes"] == expected


class _Response:
    status_code = 200
    headers: dict = {}

    def __init__(self, chunks):
        self._chunks = chunks

    def iter_bytes(self):
        return iter(self._chunks)


class _Client:
    def __init__(self, chunks):
        self._chunks = chunks

    @contextmanager
    def stream(self, method, url, headers):
        yield _Response(self._chunks)


@pytest.mark.parametrize(("limit", "refused"), [(1, True), (2, False)])
def test_a_sharepoint_download_stops_at_the_configured_download_limit(monkeypatch, limit, refused):
    _set_section(monkeypatch, {"max_download_mib": limit})
    monkeypatch.setattr(readers, "authority_effect_fence", lambda: nullcontext())
    monkeypatch.setattr(connections, "_mutation_lock", lambda: nullcontext())
    monkeypatch.setattr(readers, "account", lambda binding, actor: {"token": "token-value"})
    chunks = [b"x" * ONE_MIB, b"x" * ONE_MIB]

    @contextmanager
    def guarded_client(url, **kwargs):
        yield _Client(chunks)

    monkeypatch.setattr(doc_discovery, "_guarded_client", guarded_client)
    url = "https://contoso.sharepoint.com/sites/team/download/handbook.txt"
    if refused:
        with pytest.raises(readers.SourceReadError, match="exceeded its bounded download limit"):
            readers._download(SHAREPOINT, "user:alice", url, lambda: None)
    else:
        assert readers._download(SHAREPOINT, "user:alice", url, lambda: None) == b"x" * (2 * ONE_MIB)


def test_a_document_larger_than_the_configured_download_limit_is_refused(monkeypatch):
    _set_section(monkeypatch, {"max_download_mib": 1})
    monkeypatch.setattr(readers, "read_json", lambda binding, actor, path, guard: {"version": "7"})
    monkeypatch.setattr(readers, "read_bytes", lambda binding, actor, path, guard, **kwargs: b"x" * (ONE_MIB + 1))
    item = {"id": "f1", "name": "Handbook.txt", "mime": "text/plain", "kind": "file", "revision": "7"}
    with pytest.raises(readers.SourceReadError, match=r"exceeds the 1 MiB download limit"):
        readers.document(DRIVE, "user:alice", item, lambda: None)


def test_a_document_within_the_raised_download_limit_is_extracted(monkeypatch):
    from maverick_knowledge import parse

    # Larger than the 4 MiB default, so only the raised limit admits it.
    _set_section(monkeypatch, {"max_download_mib": 8})
    monkeypatch.setattr(readers, "read_json", lambda binding, actor, path, guard: {"version": "7"})
    monkeypatch.setattr(readers, "read_bytes", lambda binding, actor, path, guard, **kwargs: b"x" * (5 * ONE_MIB))
    monkeypatch.setattr(parse, "extract_document", lambda path: SimpleNamespace(
        status="complete", omitted_regions=(), text="Handbook text", metadata=lambda: {"pages": 1}))
    item = {"id": "f1", "name": "Handbook.txt", "mime": "text/plain", "kind": "file", "revision": "7"}
    text, _sha, _metadata = readers.document(DRIVE, "user:alice", item, lambda: None)
    assert text == "Handbook text"
