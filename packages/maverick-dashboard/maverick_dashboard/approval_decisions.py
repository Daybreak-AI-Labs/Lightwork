"""One decision path for human approvals, whatever surface the vote comes from.

The ``/api/v1/approvals/{id}/approve|deny`` routes and the Microsoft Teams
approval cards both go through the functions here: the current-card check,
the audited vote, and the signed audit delivery. A chat channel adds only its
own identity mapping (Teams account -> Lightwork user) and permission check on
top; it never records a decision another way.
"""
from __future__ import annotations

import hmac
import logging
import time

from fastapi import HTTPException

log = logging.getLogger(__name__)

# Chat channels may decide these risk levels only after a second, explicit
# confirmation step (the dashboard shows them with a blocked badge).
CONFIRM_RISKS = frozenset({"high", "critical"})


def approval_undo_grace_seconds() -> float:
    """How long a decision can still be undone (``[approval] undo_grace_seconds``,
    env ``MAVERICK_APPROVAL_UNDO_GRACE_SECONDS``; default 30; 0 disables undo).
    Uses the kernel's own function when the consent module provides it."""
    try:
        from maverick.safety.consent import approval_undo_grace_seconds as kernel_value

        return float(kernel_value())
    except (ImportError, AttributeError):
        pass
    import math
    import os

    raw = os.environ.get("MAVERICK_APPROVAL_UNDO_GRACE_SECONDS", "")
    if not raw.strip():
        try:
            from maverick.config import load_config

            value = ((load_config() or {}).get("approval") or {}).get("undo_grace_seconds")
        except Exception:  # noqa: BLE001 -- config never blocks the approval
            value = None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return max(0.0, min(float(value), 3600.0))
        raw = "30"
    try:
        return max(0.0, min(float(raw), 3600.0))
    except ValueError:
        return 30.0


def approval_requires_acknowledgement() -> bool:
    """Whether approving high/critical risk needs an explicit acknowledgement
    (``[approval] require_acknowledgement``, env
    ``MAVERICK_APPROVAL_REQUIRE_ACKNOWLEDGEMENT``; default on)."""
    try:
        from maverick.safety.consent import approval_requires_acknowledgement as kernel_value

        return bool(kernel_value())
    except (ImportError, AttributeError):
        pass
    import os

    raw = os.environ.get("MAVERICK_APPROVAL_REQUIRE_ACKNOWLEDGEMENT", "").strip().lower()
    if raw:
        return raw not in ("0", "false", "no", "off")
    try:
        from maverick.config import load_config

        value = ((load_config() or {}).get("approval") or {}).get("require_acknowledgement")
    except Exception:  # noqa: BLE001 -- config never weakens the confirmation
        value = None
    return value if isinstance(value, bool) else True


def _safe_action_label(approval) -> str:
    """The queued action as plain, redacted text for audit summaries."""
    from maverick.safety.secret_detector import redact

    try:
        text, _matches = redact(" ".join(str(getattr(approval, "action", "") or "").split()))
    except Exception:  # noqa: BLE001
        return "the reviewed action"
    return (text[:200] or "the reviewed action").replace(".", " ").replace("_", " ")


def _require_acknowledgement(world, approval_id: int, payload: dict, who: str, *,
                             channel: str = "dashboard") -> None:
    """High and critical risk approvals need an explicit acknowledgement.

    The Approvals page collects it in its confirmation step; API callers send
    ``{"acknowledged": true}`` and, for critical risk, ``"confirmation":
    "approve"`` typed by the person; the Teams confirm card sends the same
    payload. Off via ``[approval] require_acknowledgement = false``. The outcome
    is written to the audit log as ``approval_confirmation`` with the channel.
    """
    approval = world.get_approval(approval_id)
    if approval is None or approval.status != "pending":
        return
    risk = str(approval.risk or "").lower()
    if risk not in CONFIRM_RISKS or not approval_requires_acknowledgement():
        return
    payload = payload if isinstance(payload, dict) else {}
    acknowledged = payload.get("acknowledged") is True
    typed = " ".join(str(payload.get("confirmation") or "").split()).lower()
    if not acknowledged:
        raise HTTPException(
            status_code=422,
            detail="Confirm you have read what this action will do before approving it.",
        )
    if risk == "critical" and typed != "approve":
        raise HTTPException(
            status_code=422,
            detail="Type APPROVE to confirm this critical action.",
        )
    from maverick import audit

    label = _safe_action_label(approval)
    recorded = audit.audit_event(
        audit.EventKind.CONSENT_RESULT,
        agent=who,
        action="approval_confirmation",
        approval_id=int(approval_id),
        risk=risk,
        acknowledged=True,
        typed_confirmation=(risk == "critical"),
        channel=channel,
        summary=(
            f"{who} confirmed reading what '{label}' will do ({risk} risk) before approving it."
        ),
    )
    if not recorded:
        log.warning("approval confirmation audit write failed (approval_id=%s)", approval_id)


class ApprovalDecisionRefused(Exception):
    """A vote was not accepted; ``detail`` is a plain sentence for the person."""

    def __init__(self, detail: str, *, status_code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def require_current_decision_card(world, approval_id: int, binding: str) -> None:
    """Reject a stale card, expired card, or corrupted structured approval."""
    from maverick.approval_cards import (
        ApprovalCardError,
        decode_approval_detail,
        require_current_approval_card,
    )

    approval = world.get_approval(approval_id)
    if approval is None or approval.status != "pending":
        return
    try:
        card = decode_approval_detail(approval.detail)
        if card is None:
            raise ApprovalCardError(
                "this approval predates exact-operation review; reject it and "
                "ask the requester to prepare a fresh review"
            )
        if not hmac.compare_digest(card["action"], approval.action):
            raise ApprovalCardError(
                "the approval card does not match the queued action; request a fresh review"
            )
        if not binding:
            raise ApprovalCardError(
                "review the current approval card before submitting this decision"
            )
        require_current_approval_card(card, expected_binding=binding)
    except ApprovalCardError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _deliver_approval_audit_event(world, event) -> bool:
    """Deliver exactly one queued vote and persist its delivery marker."""
    if event.delivered_at is not None:
        return True

    from maverick.audit import AuditRefused, EventKind, audit_event

    try:
        written = audit_event(
            EventKind.APPROVAL_DECISION,
            approval_event_id=event.event_id,
            approval_id=int(event.approval_id),
            status=event.status,
            decided_by=event.decided_by,
            occurred_at=float(event.created_at),
        )
    except AuditRefused as exc:
        # This is not refusal swallowing: no approval becomes effective. The
        # exact event remains in the durable outbox for a policy/key repair and
        # idempotent retry.
        log.error(
            "approval audit refused; decision remains non-effective (event_id=%s): %s",
            event.event_id,
            exc,
        )
        return False
    if not written:
        log.error(
            "approval audit write failed; decision remains non-effective (event_id=%s)",
            event.event_id,
        )
        return False

    try:
        return bool(world.mark_approval_audit_delivered(event.event_id))
    # The signed append may have succeeded while the delivery-marker commit
    # acknowledgement failed. Reconcile instead of returning an error that
    # invites an unsafe, differently-keyed retry.
    # failure-policy: fail_soft_with_audit
    except Exception:
        log.exception(
            "approval audit was written but delivery finalization failed (event_id=%s)",
            event.event_id,
        )
        try:
            reconciled = world.get_approval_audit_event(event.event_id)
        except Exception:
            return False
        return bool(reconciled and reconciled.delivered_at is not None)


def _audit_approval_decision(world, event) -> bool:
    """Deliver an approval's queued votes in durable occurrence order.

    The world-model outbox is the authority seam: an audit refusal leaves every
    later vote durable but non-effective. A retry uses stable event ids and
    resumes at the oldest undelivered row, so a quorum-reaching vote cannot
    overtake an earlier sign-off in the signed chain.
    """
    try:
        pending = world.pending_approval_audit_events(
            approval_id=event.approval_id,
            limit=1000,
        )
    # The vote/outbox commit already succeeded. A read failure here is an
    # explicit audit-pending response, not a 500 that falsely invites a new
    # decision.
    # failure-policy: fail_soft_with_audit
    except Exception:
        log.exception(
            "approval audit outbox could not be read (event_id=%s)",
            event.event_id,
        )
        return False
    for queued in pending:
        if not _deliver_approval_audit_event(world, queued):
            return False
    try:
        current = world.get_approval_audit_event(event.event_id)
    except Exception:
        return False
    return bool(current and current.delivered_at is not None)


def _record_vote_or_raise(world, approval_id: int, status: str, who: str):
    """Apply one approver's vote, mapping rejection to the right HTTP error.

    Under N-of-M dual control a vote can be refused for segregation-of-duties
    reasons (the requester can't self-approve, or an approver identity is needed)
    -- distinguish that (403) from an unknown/already-decided approval (404)."""
    event_id = world.approval_audit_event_id(approval_id, status, who)
    try:
        event = world.decide_approval_audited(
            approval_id,
            status,
            decided_by=who,
        )
    except Exception as decision_error:
        # A commit acknowledgement can be lost after both the vote and outbox
        # row are durable. The stable id lets us prove that outcome before
        # deciding whether this request failed.
        try:
            event = world.get_approval_audit_event(event_id)
        except Exception as reconcile_error:
            log.exception(
                "approval decision outcome is uncertain (event_id=%s)",
                event_id,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "decision": "outcome_uncertain",
                    "decision_id": event_id,
                    "retry_safe": True,
                },
                headers={
                    "Retry-After": "1",
                    "X-Lightwork-Decision-Id": event_id,
                    "X-Lightwork-Approval-State": "uncertain",
                },
            ) from reconcile_error
        if event is not None and not event.matches(approval_id, status, who):
            log.critical(
                "approval outbox identity mismatch during commit reconciliation (event_id=%s)",
                event_id,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "decision": "outbox_integrity_error",
                    "decision_id": event_id,
                    "retry_safe": False,
                },
                headers={
                    "X-Lightwork-Decision-Id": event_id,
                    "X-Lightwork-Approval-State": "integrity_error",
                },
            ) from decision_error
        if event is None:
            raise decision_error
    if event is not None:
        return event

    st = world.approval_state(approval_id)
    if st is None or st.get("status") != "pending":
        raise HTTPException(status_code=404, detail="no such pending approval")
    if world.approval_has_pending_finalization(approval_id):
        raise HTTPException(
            status_code=409,
            detail="a final decision is accepted and awaiting signed audit delivery",
        )
    raise HTTPException(
        status_code=403,
        detail="vote not accepted: the requester can't approve their own request "
        "(segregation of duties), or an approver identity is required for "
        "multi-party approval",
    )


# ----------------------------------------------------------------- channels


def _plain(detail: object) -> str:
    if isinstance(detail, dict):
        return str(detail.get("decision") or detail)
    return str(detail)


def _channel_authority(principal: str, *, channel: str, approval_id: int) -> None:
    """The linked Lightwork user must hold ``operate``, like a dashboard voter.

    A denial is audited the same way ``require_approval_authority`` audits one.
    """
    from maverick.approval_cards import digest

    from . import rbac
    from .auth import role_for_principal

    role = role_for_principal(principal)
    if "operate" in rbac.permissions_for(role):
        return
    from maverick import audit
    from maverick.paths import current_tenant_id_strict

    audit.audit_event(
        audit.EventKind.CAPABILITY_DENIED,
        agent=principal,
        action="approval.decide",
        principal=principal,
        channel=channel,
        tenant=current_tenant_id_strict() or "",
        resource_sha256=digest({"kind": "approval", "resource": str(approval_id)}),
        reason="approval_authority_missing",
    )
    raise ApprovalDecisionRefused(
        "Your Lightwork account can't decide approvals. Ask an administrator for "
        "the operator role.", status_code=403,
    )


def decide_from_channel(*, approval_id: int, status: str, principal: str, binding: str,
                        channel: str, channel_user: str = "", confirmed: bool = False,
                        acknowledgement: dict | None = None, world=None) -> dict:
    """Record a vote that arrived through a chat channel.

    Returns ``{"status", "effective", "decided_by", "risk", "audit"}``. Raises
    :class:`ApprovalDecisionRefused` with a plain sentence when the vote cannot
    be accepted: not linked to a permitted user, a protected-settings change
    (dashboard only), a high or critical risk without the confirmation step
    (``confirmed``) or without the acknowledgement the dashboard also demands
    (``acknowledgement={"acknowledged": True, "confirmation": "approve"}``, the
    typed word only for critical risk), an expired or already-decided card, or
    a segregation-of-duties refusal. A decision made after an undo is a new
    signed audit round, exactly as on the Approvals page.
    """
    from ._shared import _world
    from .settings_quorum import is_settings_approval

    if status not in ("approved", "denied"):
        raise ValueError("status must be 'approved' or 'denied'")
    who = str(principal or "").strip()
    if not who:
        raise ApprovalDecisionRefused(
            "Your Teams account isn't linked to a Lightwork user yet.", status_code=403,
        )
    world = world if world is not None else _world()
    approval = world.get_approval(int(approval_id))
    if approval is None or approval.status != "pending":
        raise ApprovalDecisionRefused(
            "This request was already decided or no longer exists.", status_code=404,
        )
    _channel_authority(who, channel=channel, approval_id=int(approval_id))
    if is_settings_approval(approval):
        raise ApprovalDecisionRefused(
            "Changes to protected settings are decided in the Lightwork dashboard, "
            "not from chat.", status_code=403,
        )
    if status == "approved" and str(approval.risk) in CONFIRM_RISKS and not confirmed:
        raise ApprovalDecisionRefused(
            f"This is a {approval.risk} risk action. Confirm it before it is approved.",
            status_code=428,
        )
    try:
        if status == "approved":
            require_current_decision_card(world, int(approval_id), binding)
            _require_acknowledgement(world, int(approval_id), acknowledgement or {}, who,
                                     channel=channel)
        event = _record_vote_or_raise(world, int(approval_id), status, who)
    except HTTPException as exc:
        raise ApprovalDecisionRefused(_plain(exc.detail), status_code=exc.status_code) from exc
    delivered = _audit_approval_decision(world, event)
    try:
        state = world.approval_state(int(approval_id)) or {}
    except Exception:  # noqa: BLE001 -- the vote is durable; the read is diagnostic
        state = {}
    from maverick import audit

    audit.audit_event(
        audit.EventKind.CONSENT_RESULT,
        agent=who,
        action="approval_decided_via_channel",
        channel=channel,
        channel_user=str(channel_user or ""),
        approval_id=int(approval_id),
        decision=status,
        decision_id=event.event_id,
        effective=bool(delivered and state.get("status") not in (None, "pending")),
    )
    effective = bool(delivered and state.get("status") not in (None, "pending"))
    return {
        "status": str(state.get("status") or ("pending" if not delivered else status)),
        "effective": effective,
        "decided_by": who,
        "risk": str(approval.risk),
        "audit": "delivered" if delivered else "pending",
        "approvals_required": int(state.get("approvals_required") or 1),
        "approved_count": int(state.get("approved_count") or 0),
        # The card offers Undo for this long; the agent-side consumer waits
        # the same window before acting.
        "undo_seconds": int(approval_undo_grace_seconds()) if effective else 0,
        "decided_at": time.time(),
    }


def undo_from_channel(*, approval_id: int, principal: str, channel: str,
                      channel_user: str = "", world=None) -> dict:
    """Undo a decision made moments ago from a chat card (the dashboard's
    ``/approvals/{id}/undo``): the request returns to pending while the grace
    window is open, and the undo is written to the audit log with the channel.
    """
    from ._shared import _world

    who = str(principal or "").strip()
    if not who:
        raise ApprovalDecisionRefused(
            "Your Teams account isn't linked to a Lightwork user yet.", status_code=403,
        )
    world = world if world is not None else _world()
    before = world.get_approval(int(approval_id))
    if before is None:
        raise ApprovalDecisionRefused("This request no longer exists.", status_code=404)
    _channel_authority(who, channel=channel, approval_id=int(approval_id))
    undo = getattr(world, "undo_approval_decision", None)
    if undo is None:
        raise ApprovalDecisionRefused("Undo isn't available on this server yet.", status_code=409)
    grace = approval_undo_grace_seconds()
    outcome = undo(int(approval_id), undone_by=who, within_seconds=grace)
    if outcome == "not_found":
        raise ApprovalDecisionRefused("This request no longer exists.", status_code=404)
    if outcome == "not_decided":
        raise ApprovalDecisionRefused("There is no decision to undo yet.", status_code=409)
    if outcome != "undone":
        raise ApprovalDecisionRefused(
            "Too late to undo: the agent has already been told about this decision. "
            "Ask for a new request if it should not go ahead.", status_code=409,
        )
    from maverick import audit

    label = _safe_action_label(before)
    previous = "approval" if before.status == "approved" else "rejection"
    recorded = audit.audit_event(
        audit.EventKind.CONSENT_RESULT,
        agent=who,
        action="approval_decision_undone",
        approval_id=int(approval_id),
        previous_status=before.status,
        previously_decided_by=before.decided_by or "",
        channel=channel,
        channel_user=str(channel_user or ""),
        summary=(
            f"{who} undid the {previous} of '{label}'; it is waiting for a decision again."
        ),
    )
    if not recorded:
        raise ApprovalDecisionRefused(
            "The decision was undone but could not be recorded in the audit log.",
            status_code=503,
        )
    return {"status": "pending", "previous_status": before.status, "undone_by": who,
            "approval_id": int(approval_id)}


def send_back_from_channel(*, approval_id: int, principal: str, rationale: str,
                           channel: str, channel_user: str = "", world=None) -> dict:
    """The chat form of "Send back for changes": a denial plus the note.

    Mirrors ``/api/v1/approvals/{id}/revise`` so the requester gets the same
    superseded record and rationale.
    """
    from maverick.approval_cards import ApprovalCardError, decode_approval_detail

    text = " ".join(str(rationale or "").split())
    if not text:
        raise ApprovalDecisionRefused("Type the question or note to send back.", status_code=422)
    if len(text) > 2_000:
        raise ApprovalDecisionRefused("The note is too long.", status_code=422)
    try:
        from maverick.safety.secret_detector import redact

        text, _matches = redact(text)
    except Exception:  # noqa: BLE001
        text = "Revision requested; rationale was removed because redaction failed."
    from ._shared import _world

    world = world if world is not None else _world()
    row = world.get_approval(int(approval_id))
    if row is None or row.status != "pending":
        raise ApprovalDecisionRefused(
            "This request was already decided or no longer exists.", status_code=404,
        )
    old_binding = ""
    try:
        card = decode_approval_detail(row.detail)
        old_binding = str(card["binding_sha256"]) if card is not None else ""
    except (ApprovalCardError, KeyError, TypeError, ValueError):
        pass
    result = decide_from_channel(approval_id=approval_id, status="denied", principal=principal,
                                 binding="", channel=channel, channel_user=channel_user,
                                 world=world)
    if not result["effective"]:
        return {**result, "rationale": text}
    from maverick import audit

    recorded = audit.audit_event(
        audit.EventKind.CONSENT_RESULT,
        agent=result["decided_by"],
        action="approval_revision_requested",
        approval_id=int(approval_id),
        channel=channel,
        superseded_binding_sha256=old_binding,
        producer=row.provenance or "consent",
        original_action=row.action,
        rationale=text,
    )
    if not recorded:
        raise ApprovalDecisionRefused(
            "The request was sent back but the note could not be recorded.", status_code=503,
        )
    return {**result, "status": "superseded", "rationale": text}


def approval_view(approval_id: int, *, world=None) -> dict | None:
    """The queue row as the dashboard shows it: safe labels, decoded card, source.

    ``None`` when the approval does not exist. ``card`` is ``None`` for a legacy
    or corrupted structured detail, which a channel must refuse to approve just
    as the dashboard disables its Approve button.
    """
    from maverick.approval_cards import ApprovalCardError, decode_approval_detail
    from maverick.safety.secret_detector import redact

    from ._shared import _world

    world = world if world is not None else _world()
    approval = world.get_approval(int(approval_id))
    if approval is None:
        return None

    def safe(value: object, *, fallback: str = "") -> str:
        try:
            text, _matches = redact(" ".join(str(value or "").split()))
            return text[:2000]
        except Exception:  # noqa: BLE001 -- the fallback is a plain label
            return fallback

    card = None
    card_status = "legacy"
    detail = safe(approval.detail)
    try:
        card = decode_approval_detail(approval.detail)
    except ApprovalCardError:
        detail = "Structured approval details failed their integrity check."
        card_status = "invalid"
    if card is not None:
        import time as _time

        card_status = "expired" if _time.time() >= card["expires_at"] else "current"
        if card_status == "expired":
            card = None
    try:
        from .app import _approval_source

        source = _approval_source(approval.provenance)
    except Exception:  # noqa: BLE001 -- the label is decoration, never authority
        source = None
    return {
        "approval": {
            "id": approval.id,
            "action": safe(approval.action, fallback="reviewed operation"),
            "risk": str(approval.risk),
            "scope": safe(approval.scope, fallback="reviewed target"),
            "detail": detail,
            "status": approval.status,
            "provenance": approval.provenance,
            "requested_by": approval.requested_by,
        },
        "card": card,
        "card_status": card_status,
        "source_label": source,
    }


__all__ = [
    "CONFIRM_RISKS",
    "approval_view",
    "ApprovalDecisionRefused",
    "decide_from_channel",
    "require_current_decision_card",
    "send_back_from_channel",
    "undo_from_channel",
]
