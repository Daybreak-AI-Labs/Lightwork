"""Durable delivery and recovery for production approval audit intents."""
from __future__ import annotations

import logging

log = logging.getLogger("maverick_dashboard.approval_delivery")


def _record_indeterminate() -> None:
    try:
        from maverick.production_observability import (
            INDETERMINATE_SIDE_EFFECT,
            record_event,
        )

        record_event(INDETERMINATE_SIDE_EFFECT)
    except Exception:  # pragma: no cover - observability cannot alter authority
        pass


def deliver_approval_audit_event(world, event) -> bool:
    """Deliver exactly one queued vote and persist its delivery marker."""
    if event.delivered_at is not None:
        return True

    from maverick.audit import (
        AuditRefused,
        AuditRequiredWriteFailed,
        EventKind,
        required_audit_event,
    )

    try:
        required_audit_event(
            EventKind.APPROVAL_DECISION,
            approval_event_id=event.event_id,
            approval_id=int(event.approval_id),
            status=event.status,
            decided_by=event.decided_by,
            occurred_at=float(event.created_at),
        )
    except (AuditRefused, AuditRequiredWriteFailed):
        # The vote/outbox transaction remains durable but non-effective. Keep
        # custody/path details out of this secondary plaintext channel.
        log.error(
            "approval audit unavailable; decision remains non-effective "
            "(event_id=%s)",
            event.event_id,
        )
        return False

    try:
        return bool(world.mark_approval_audit_delivered(event.event_id))
    except Exception:
        # The signed append may have succeeded while the marker commit
        # acknowledgement failed. Prove the authoritative row before retrying.
        _record_indeterminate()
        log.exception(
            "approval audit finalization outcome is uncertain (event_id=%s)",
            event.event_id,
        )
        try:
            reconciled = world.get_approval_audit_event(event.event_id)
        except Exception:
            return False
        return bool(reconciled and reconciled.delivered_at is not None)


def reconcile_approval_audit_event(world, event) -> bool:
    """Drain the exact approval's queued votes in durable occurrence order."""
    try:
        pending = world.pending_approval_audit_events(
            approval_id=event.approval_id,
            limit=1000,
        )
    except Exception:
        _record_indeterminate()
        log.exception(
            "approval audit outbox could not be read (event_id=%s)",
            event.event_id,
        )
        return False
    for queued in pending:
        if not deliver_approval_audit_event(world, queued):
            return False
    try:
        current = world.get_approval_audit_event(event.event_id)
    except Exception:
        _record_indeterminate()
        return False
    return bool(current and current.delivered_at is not None)


__all__ = [
    "deliver_approval_audit_event",
    "reconcile_approval_audit_event",
]
