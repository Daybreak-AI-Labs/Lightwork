"""Typed historical discovery delegates the same current native source adapters."""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from maverick.metadata_history import digest
from pydantic import Field

from .product_operations import Adapter, Outcome, Preview, Snapshot, StrictInput
from .record_history import SOURCES, date_bound, search_history
from .record_search_api import page_context


class HistoryInput(StrictInput):
    source: Literal["tasks", "projects", "workflows", "automations", "connections", "facts", "questionnaires", "vault", "reports", "vendors", "onboarding", "reviews"]
    query: str = Field(default="", max_length=200)
    after: str = Field(default="", pattern=r"^(?:[0-9]{4}-[0-9]{2}-[0-9]{2})?$")
    before: str = Field(default="", pattern=r"^(?:[0-9]{4}-[0-9]{2}-[0-9]{2})?$")
    oldest: bool = False
    cursor: str | None = Field(default=None, max_length=2048)


def snapshot(request, selection):
    context = page_context(request).get("record_search_context")
    if not context:
        raise HTTPException(403, "Record discovery is disabled. An administrator can enable it in Settings.")
    return Snapshot(digest([context, list(SOURCES)]), "Historical record metadata", "Find older records by business metadata; private bodies and document contents are excluded.", fields=[
        {"name": "source", "label": "Source", "kind": "select", "required": True,
         "choices": [{"value": code, "label": value[0]} for code, value in SOURCES.items()]},
        {"name": "query", "label": "Name or reference", "kind": "text"},
        {"name": "after", "label": "On or after (YYYY-MM-DD, UTC)", "kind": "text"},
        {"name": "before", "label": "On or before (YYYY-MM-DD, UTC)", "kind": "text"},
        {"name": "oldest", "label": "Oldest first", "kind": "boolean", "value": False}],
        details={"continuation": "Use the returned next_cursor for the next bounded window with exactly the same filters. No cursor is an access grant."})


def prepare(request, selection, arguments):
    snapshot(request, selection)
    try:
        date_bound(arguments.get("after", ""))
        date_bound(arguments.get("before", ""), end=True)
        if arguments.get("after") and arguments.get("before") and arguments["after"] > arguments["before"]:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(422, "Choose valid UTC dates in chronological order.") from exc
    return Preview("Read one bounded historical metadata window using your current source access. No model, connector action or data change.", native=arguments)


def execute(request, selection, arguments, native, operation):
    snapshot(request, selection)
    try:
        result = search_history(request, **native)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Outcome("confirmed", "Current historical metadata returned. Counts describe this page only; continue when a next cursor is present.", result)


def entries():
    return (Adapter("knowledge.history", "Find historical record metadata", "learning", "/goals?find_history=tasks", ("*",), HistoryInput, snapshot, prepare, execute),)
