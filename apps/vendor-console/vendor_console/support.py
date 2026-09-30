"""Support desk — turn a posted support bundle into a triaged ticket.

Reuses :func:`maverick.support_bundle.ticket_summary` to derive the redacted,
routable summary (SKU, build, failing readiness checks, recent failure modes)
from a bundle the customer's ``maverick support`` already secret-scrubbed. Intake
de-dupes on authenticated customer plus ``correlation_id``. A resend appends
one note; a closed/resolved ticket remains historical and a new one is created.
"""
from __future__ import annotations

import sqlite3
import time

from . import audit, db, store
from .models import Ticket


def _summary_of(bundle: dict) -> dict:
    if not isinstance(bundle, dict):
        return {}
    from maverick.support_bundle import ticket_summary
    return ticket_summary(bundle)   # pure, .get-based — safe on a redacted bundle


def intake_bundle(conn: sqlite3.Connection, *, customer_id: int | None, bundle: dict,
                  subject: str = "", actor: str = "deployment",
                  allow_unassigned: bool = False) -> Ticket:
    """Create (or update) a ticket from a support bundle. Priority is raised to
    ``high`` when the bundle reports failing readiness checks."""
    summary = _summary_of(bundle)
    corr = str(summary.get("correlation_id")
               or (bundle.get("correlation_id") if isinstance(bundle, dict) else "") or "")
    tier = str(summary.get("tier") or "")
    ver = str(summary.get("agent_version") or "")
    failing = summary.get("readiness_failing") or []
    if not subject:
        subject = f"{'; '.join(failing) if failing else 'diagnostics'} — {ver or 'unknown build'}"

    return store.intake_ticket(
        conn, customer_id=customer_id, correlation_id=corr, subject=subject[:200],
        priority="high" if failing else "normal", tier=tier, agent_version=ver,
        summary=summary, bundle=bundle if isinstance(bundle, dict) else {}, actor=actor,
        resend_note="New support bundle received (same correlation id). "
                    f"failing checks: {', '.join(failing) if failing else 'none'}.",
        allow_unassigned=allow_unassigned)


def set_status(conn: sqlite3.Connection, ticket_id: int, status: str, *,
               actor: str, note: str = "") -> None:
    """Change a ticket's status and record it on the ticket timeline + audit."""
    body = f"status → {status}" + (f": {note}" if note else "")
    from .models import TICKET_STATUSES
    if status not in TICKET_STATUSES:
        raise ValueError("invalid ticket status")
    with db.transaction(conn) as tx:
        if store.get_ticket(tx, ticket_id) is None:
            raise ValueError("no such ticket")
        try:
            tx.execute("UPDATE tickets SET status=? WHERE id=?", (status, ticket_id))
        except sqlite3.IntegrityError as exc:
            raise store.SupportIntakeConflict("another active ticket owns this intake identity") from exc
        now = time.time()
        tx.execute("INSERT INTO ticket_comments(ticket_id,author,body,kind,created_at) "
                   "VALUES (?,?,?,'status',?)", (ticket_id, actor, body, now))
        tx.execute("UPDATE tickets SET updated_at=? WHERE id=?", (now, ticket_id))
        audit.record_in_transaction(tx, actor=actor, action="ticket.status",
                                    target=str(ticket_id), detail={"status": status})
