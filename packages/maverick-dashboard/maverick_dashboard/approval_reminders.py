"""One reminder for a risky step still waiting for a decision.

Ten minutes after a request is parked on the Approvals page with no decision,
each person who could decide it in Lightwork gets one in-app alert (the bell,
through the personal alerts that are on by default for approvals waiting).
It goes out only while the step is still waiting (the task's wait,
``[approval] dashboard_wait_seconds``, 30 minutes by default, has not ended)
and only in the app: no email, Slack or Teams copy. The approval card then
says "Reminder sent".

The reminder is derived from the request, not stored: each person's bell keeps
an alert once per request and parking time, so it is sent exactly once.
"""
from __future__ import annotations

import time

from maverick.channel_approvals import SETTINGS_PROVENANCE

REMINDER_AFTER_SECONDS = 600.0


def _now() -> float:
    return time.time()


def reminder_due_at(approval) -> float | None:
    """When this waiting request's reminder goes out, or ``None`` when it never
    does: already decided, a protected-settings change (no step is waiting), or
    the task's wait ends before ten minutes."""
    from maverick.safety.consent import dashboard_wait_seconds

    if approval is None or getattr(approval, "status", "") != "pending":
        return None
    if getattr(approval, "provenance", None) == SETTINGS_PROVENANCE:
        return None
    requested_at = getattr(approval, "requested_at", None)
    if not isinstance(requested_at, (int, float)) or isinstance(requested_at, bool):
        return None
    if dashboard_wait_seconds() <= REMINDER_AFTER_SECONDS:
        return None
    return float(requested_at) + REMINDER_AFTER_SECONDS


def _due(approval, now: float) -> bool:
    due = reminder_due_at(approval)
    return due is not None and now >= due


def reminded(world, approval, principal: str) -> bool:
    """Whether ``principal`` gets the reminder: a named account that could
    decide the request in Lightwork now (it can open the card, holds
    ``operate``, has not voted yet, and is not the requester who must leave it
    to someone else)."""
    from .approval_decisions import _may_decide, own_request_note
    from .content_access import approval_readers

    if not isinstance(principal, str) or not principal.startswith("user:"):
        return False
    if principal not in approval_readers(approval, world) or not _may_decide(principal):
        return False
    state = world.approval_state(approval.id) or {}
    if principal in (state.get("approvers") or []):
        return False
    return not own_request_note(world, approval, principal)


def reminder_sent(world, approval, *, now: float | None = None) -> bool:
    """Whether the card may say "Reminder sent": the reminder is due, in-app
    alerts are on, and someone can receive it."""
    from maverick.personal_notifications import personal_alerts_enabled

    from .content_access import approval_readers

    if not _due(approval, _now() if now is None else now) or not personal_alerts_enabled():
        return False
    return any(reminded(world, approval, person) for person in approval_readers(approval, world))


def reminder_sources(world, actor: str, *, suites=None) -> list[dict]:
    """The reminder alerts ``actor``'s bell carries, one per waiting request."""
    now = _now()
    sources = []
    for approval in world.pending_approvals():
        if not _due(approval, now):
            continue
        if approval.department and suites is not None and approval.department not in suites:
            continue
        if not reminded(world, approval, actor):
            continue
        sources.append({"ref": f"approval-reminder:{approval.id}", "version": str(approval.requested_at),
                        "category": "needs-decision", "title": "Approval still waiting",
                        "label": "Still waiting for a decision",
                        "href": f"/approvals#approval-{approval.id}", "goal_id": None,
                        "project_id": None})
    return sources


__all__ = ["REMINDER_AFTER_SECONDS", "reminded", "reminder_due_at", "reminder_sent",
           "reminder_sources"]
