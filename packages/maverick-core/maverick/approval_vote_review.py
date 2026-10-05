"""Exact human-review guards checked only inside a native vote reservation."""
from __future__ import annotations

from dataclasses import asdict

from .approval_cards import digest


def approval_review_identity(approval) -> str:
    material = asdict(approval)
    for key in ("status", "decided_at", "decided_by"):
        material.pop(key, None)
    return digest(material)


def require_locked_vote_review(approval, review: dict, *, status: str, actor: str,
                               event_id: str, approvers: list[str], audit_pending: int,
                               content_owner: str | None) -> None:
    """Check the decrypted row/time/round before any vote or outbox write.

    The caller must hold SQLite's write reservation or the Postgres approval
    row lock and pass quorum data read in that same transaction. This guard
    confers no role, employee-content or settings authority.
    """
    from .channel_approvals import ChannelApprovalRefused, require_current_card

    state = None if approval is None else {
        "status": approval.status, "approvals_required": max(1, int(approval.approvals_required or 1)),
        "approved_count": len(approvers), "approvers": approvers,
        "requested_by": approval.requested_by, "audit_pending_count": audit_pending,
        "effective": approval.status != "pending",
    }
    if (approval is None or approval.status != "pending" or not isinstance(review, dict)
            or review.get("actor") != actor or review.get("approval_id") != approval.id
            or review.get("status") != status or review.get("event_id") != event_id
            or review.get("identity") != approval_review_identity(approval)
            or review.get("content_owner") != content_owner
            or (review.get("state") is not None and review["state"] != state)
            or (approval.claimed_by and approval.claimed_by != actor)):
        raise ChannelApprovalRefused(
            "stale_card", "This exact request, review claim or decision round changed. Read its current status and review it again.",
        )
    if status == "approved":
        require_current_card(approval, binding=review.get("binding"))
