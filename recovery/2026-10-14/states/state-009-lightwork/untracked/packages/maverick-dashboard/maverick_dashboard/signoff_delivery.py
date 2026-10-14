"""Durable delivery and recovery for production sign-off audit intents."""
from __future__ import annotations

import logging

log = logging.getLogger("maverick_dashboard.signoff_delivery")


def deliver_signoff_audit_event(world, event) -> str:
    """Deliver one staged sign-off and finalize it behind the signed chain.

    Returns ``effective``, ``stale``, or ``pending``. Stable audit identity
    makes the append safe to repeat after a lost write/commit acknowledgement
    and when more than one control-plane replica observes the same outbox row.
    """
    if event.effective:
        return "effective"
    if event.state == "STALE":
        return "stale"

    from maverick.audit import (
        AuditRefused,
        AuditRequiredWriteFailed,
        EventKind,
        required_audit_event,
    )

    try:
        required_audit_event(
            EventKind.DELIVERABLE_SIGNOFF,
            goal_id=int(event.goal_id),
            signoff_event_id=event.event_id,
            tenant_id=event.tenant_id,
            deliverable_updated_at=float(event.deliverable_updated_at),
            goal_domain=event.goal_domain,
            governing_gate=event.governing_gate,
            policy_sha256=event.policy_sha256,
            decision=event.decision,
            decided_by=event.decided_by,
            request_sha256=event.request_sha256,
            note_sha256=event.note_sha256,
            occurred_at=float(event.occurred_at),
        )
    except (AuditRefused, AuditRequiredWriteFailed):
        # The staged decision is still non-effective and retryable by the same
        # idempotency identity or the background reconciler. Do not reflect
        # signer/path internals into the client or a secondary plaintext log.
        log.error(
            "sign-off audit unavailable; decision remains non-effective "
            "(event_id=%s)",
            event.event_id,
        )
        return "pending"

    try:
        return (
            "effective"
            if world.mark_signoff_audit_delivered(event.event_id)
            else "stale"
        )
    except Exception:
        # The signed append or final DB commit acknowledgement may have been
        # lost. Reconcile from the authoritative outbox before inviting retry.
        try:
            from maverick.production_observability import (
                INDETERMINATE_SIDE_EFFECT,
                record_event,
            )

            record_event(INDETERMINATE_SIDE_EFFECT)
        except Exception:  # pragma: no cover - observability cannot alter authority
            pass
        log.exception(
            "sign-off audit finalization outcome is uncertain (event_id=%s)",
            event.event_id,
        )
        try:
            reconciled = world.get_signoff_audit_event(event.event_id)
        except Exception:
            return "pending"
        if reconciled is None:
            return "pending"
        if reconciled.effective:
            return "effective"
        if reconciled.state == "STALE":
            return "stale"
        return "pending"


def reconcile_pending_signoff_audits(
    world,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Retry a bounded tenant-local page of staged sign-off audit intents.

    The database outbox and signed audit event identity are the authorities;
    this function keeps no host-local cursor. Repeated or concurrent sweeps are
    therefore safe, and a still-unavailable sink simply leaves the row pending
    for the next cadence.
    """
    counts = {"effective": 0, "stale": 0, "pending": 0}
    events = world.pending_signoff_audit_events(limit=limit)
    for event in events:
        if event is None:
            # A row disappearing between the inventory and exact read is not
            # authority to fail or skip the rest of the bounded recovery page.
            counts["pending"] += 1
            continue
        state = deliver_signoff_audit_event(world, event)
        counts[state] += 1
    return counts


__all__ = [
    "deliver_signoff_audit_event",
    "reconcile_pending_signoff_audits",
]
