"""Tenant-pinned recovery and health for consequential authority audit rows."""
from __future__ import annotations

from collections.abc import Callable

from .approval_delivery import deliver_approval_audit_event
from .signoff_delivery import deliver_signoff_audit_event


def reconcile_pending_authority_audits(
    world,
    *,
    limit: int = 1000,
) -> dict[str, int]:
    """Drain one bounded tenant-local page from both authority outboxes.

    Approval and deliverable sign-off events share the same signed audit chain.
    Merge them by durable occurrence time, and stop after the first unavailable
    append so a later authority event cannot overtake it during recovery.
    """
    cap = max(1, min(int(limit), 1000))
    approvals = world.pending_approval_audit_events(limit=cap)
    signoffs = world.pending_signoff_audit_events(limit=cap)
    queued = [
        (float(event.created_at), "approval", event.event_id, event)
        for event in approvals
        if event is not None
    ]
    queued.extend(
        (float(event.occurred_at), "signoff", event.event_id, event)
        for event in signoffs
        if event is not None
    )
    queued.sort(key=lambda item: (item[0], item[1], item[2]))

    counts = {
        "approval_delivered": 0,
        "signoff_effective": 0,
        "signoff_stale": 0,
        "pending": 0,
    }
    for _occurred_at, kind, _event_id, event in queued[:cap]:
        if kind == "approval":
            if not deliver_approval_audit_event(world, event):
                counts["pending"] += 1
                break
            counts["approval_delivered"] += 1
            continue
        state = deliver_signoff_audit_event(world, event)
        if state == "pending":
            counts["pending"] += 1
            break
        counts[f"signoff_{state}"] += 1
    return counts


def current_authority_audit_backlog(world) -> int:
    """Return the exact bounded tenant-local pending authority row count."""
    return len(world.pending_approval_audit_events(limit=1000)) + len(
        world.pending_signoff_audit_events(limit=1000)
    )


def aggregate_authority_audit_backlog(
    world_provider: Callable[[], object],
) -> tuple[bool, int]:
    """Return ``(healthy, backlog)`` across every recoverable tenant scope."""
    from . import automation_queue

    backlog = 0

    def _collect() -> None:
        nonlocal backlog
        backlog += current_authority_audit_backlog(world_provider())

    complete = automation_queue._run_for_active_tenants(
        "authority audit backlog probe",
        _collect,
    )
    return bool(complete and backlog == 0), backlog if complete else -1


__all__ = [
    "aggregate_authority_audit_backlog",
    "current_authority_audit_backlog",
    "reconcile_pending_authority_audits",
]
