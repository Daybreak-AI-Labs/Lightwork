"""Bounded, current metadata search. Sources authorize before projection or matching."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from urllib.parse import quote

KINDS = ("vendor", "evidence", "assessment", "decision", "task", "report", "workflow", "automation", "project", "connection", "fact")
SOURCE_LIMIT = 1000


@dataclass
class Source:
    items: list[dict]
    complete: bool = True


def item(kind, identifier, title, *, href, state="", revision="", source=""):
    """Only these fields may enter the ephemeral index, never arbitrary records."""
    return {"kind": kind, "id": str(identifier)[:256], "title": str(title or identifier)[:500],
            "href": href, "state": str(state or "")[:100],
            "revision": str(revision or "")[:128], "source": source}


def link(path, key, identifier):
    return path + "?" + key + "=" + quote(str(identifier), safe="")


def search(sources, *, query, kind="all", offset=0, limit=20, expected_snapshot=""):
    """No persistent index: each response reflects a fresh authorization-scoped scan.

    Source failures disclose no exception text or inaccessible record counts.
    The snapshot token prevents silently skipping/repeating rows when paging a
    changing index; the UI asks for an explicit refreshed search on conflict.
    """
    rows, coverage = [], []
    for name, loader in sources.items():
        try:
            result = loader()
            if not isinstance(result, Source):
                raise ValueError("invalid source projection")
            selected = [row for row in result.items if kind == "all" or row["kind"] == kind]
            complete = result.complete and len(selected) <= SOURCE_LIMIT
            coverage.append({"source": name, "state": "current" if complete else "partial",
                             "indexed": min(len(selected), SOURCE_LIMIT)})
            rows.extend(selected[:SOURCE_LIMIT])
        except Exception:
            coverage.append({"source": name, "state": "unavailable", "indexed": None})
    rows.sort(key=lambda row: (row["title"].casefold(), row["kind"], row["source"], row["id"]))
    snapshot = hashlib.sha256(json.dumps([rows, coverage], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected_snapshot and expected_snapshot != snapshot:
        raise ValueError("Records or access changed while paging. Refresh the search to continue.")
    terms = query.casefold().split()
    matches = [row for row in rows if (kind == "all" or row["kind"] == kind) and
               all(term in " ".join(row[key] for key in ("id", "title", "state", "revision")).casefold() for term in terms)]
    counts = {key: sum(row["kind"] == key for row in matches) for key in KINDS}
    return {"items": matches[offset:offset + limit], "total": len(matches), "counts": counts,
            "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < len(matches) else None,
            "snapshot": snapshot, "coverage": coverage,
            "complete": all(row["state"] == "current" for row in coverage), "checked_at": time.time()}
