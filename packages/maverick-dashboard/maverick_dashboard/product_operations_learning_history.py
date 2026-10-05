"""Audit-authorized Copilot reads from the same learning history as the page."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from maverick.evolution_ledger import EvolutionLedgerError
from maverick.governed_operations import digest

from . import evolution_ledger_api as native
from .auth import require_permission
from .product_operations import Adapter, EmptyInput, Preview, Snapshot

_PAGE = "/audit/evolution-ledger"
_KINDS = {"promotion": "Adopted a learning change", "rollback": "Restored an earlier learning state"}


def snapshot(request, selection):
    require_permission(request, "audit")
    if selection:
        raise HTTPException(422, "Open Learning history to check the current workspace record.")
    try:
        current = native._snapshot()
    except (EvolutionLedgerError, OSError, RuntimeError) as exc:
        raise HTTPException(503, "Learning history could not be checked. Open the page and select Refresh.") from exc
    projection = current["projection"]
    entries = projection["entries"]
    source = current["sources"]["promotion_ledger"]
    history = []
    for row in reversed(entries[-20:]):
        stamp = row.get("occurred_at")
        try:
            when = datetime.fromtimestamp(float(stamp), tz=timezone.utc).strftime("%b %d, %Y at %H:%M UTC")
        except (ValueError, TypeError, OverflowError, OSError):
            when = "Date not recorded"
        history.append({"change": _KINDS.get(row.get("kind"), "Recorded learning activity"), "when": when,
                        "record_check": "Stored chain checks match" if row.get("integrity") == "hash_chained_local_promotion_ledger"
                        else "No sealed history for this record"})
    summary = ("The stored learning history was checked. These are recorded changes, not proof of improved results."
               if source == "available" else "No adopted changes are recorded in this history yet.")
    details = {"adopted_changes_shown": sum(row.get("kind") == "promotion" for row in entries),
               "restorations_shown": sum(row.get("kind") == "rollback" for row in entries),
               "records_shown_on_the_page": len(entries), "total_recorded_changes": projection["total_entries"],
               "recent_changes": history,
               "scope": "The latest 20 changes are summarized here. Open Learning history to download and check a recorded receipt. "
                        "Matching stored fingerprints do not establish who approved a change or detect replacement of the entire history.",
               "open_history": {"href": _PAGE, "label": "Open Learning history"}}
    return Snapshot(digest({"sources": current["sources"], "projection": projection}),
                    "The current workspace's recorded learning history", summary, details)


def prepare(request, selection, _arguments):
    current = snapshot(request, selection)
    return Preview(current.summary, native=current.details)


def entries():
    return (Adapter("learning.history", "Check learning history", "learning", _PAGE, (_PAGE,),
                    EmptyInput, snapshot, prepare,
                    aliases=("check learning history", "show recorded learning changes", "show learning receipts")),)
