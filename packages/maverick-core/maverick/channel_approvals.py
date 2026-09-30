"""Approval decisions taken from a chat channel (Slack, Teams, email).

One neutral seam shared by every channel adapter and the dashboard:

* :func:`card_content` turns an approval row into plain-language card
  content (what it will do, risk, spending, "can't be undone", decide-by).
* :func:`decide_from_channel` records a vote with the SAME world-model
  primitives and signed-audit delivery the dashboard uses
  (``WorldModel.decide_approval_audited`` + the audit outbox), so a decision
  from Slack is exactly as accountable as one from the Approvals page.
  The dashboard imports :func:`audit_approval_decision` from here; the
  logic is not duplicated.
* A High/Critical request needs a second confirmation (``confirmed=True``:
  the Slack confirm dialog, or ``confirm`` on a slash command); an expired,
  decided, corrupted or protected-settings request is refused.

Nothing here talks to a network. Identity linking (channel user id ->
Lightwork principal) lives in :mod:`maverick.channel_identities`.
"""
from __future__ import annotations

import hmac
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

CHANNELS = ("slack", "teams", "email", "dashboard")
CONFIRM_RISKS = frozenset({"high", "critical"})
_RISK_LABELS = {
    "low": "Low risk",
    "medium": "Medium risk",
    "high": "High risk",
    "critical": "Critical risk",
}
# Protected-settings requests are decided by a global administrator on the
# dashboard (settings_quorum enforces who may decide); a chat channel cannot
# prove that role, so it never decides them.
_SETTINGS_PROVENANCE = "settings_quorum"


class ChannelApprovalRefused(Exception):
    """A decision the channel path will not record. ``reason`` is a stable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def needs_confirmation(risk: str | None) -> bool:
    """High and Critical requests need a second confirmation on every channel."""
    return str(risk or "").strip().lower() in CONFIRM_RISKS


def _plain_action(action: str | None) -> str:
    text = " ".join(str(action or "").replace("_", " ").replace(".", " ").split())
    return (text[:1].upper() + text[1:]) if text else "Reviewed action"


def _spending_text(budget: dict[str, Any] | None) -> str:
    if not isinstance(budget, dict) or not budget:
        return "Not stated"
    maximum = budget.get("maximum")
    if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
        currency = str(budget.get("currency") or "USD")
        amount = f"${maximum:.2f}" if currency == "USD" else f"{currency} {maximum:.2f}"
        minutes = budget.get("minutes")
        return f"Up to {amount}" + (f" · {minutes} minutes" if minutes else "")
    if budget.get("state"):
        return "Checked again when it runs"
    return "; ".join(f"{k}: {v}" for k, v in budget.items())[:200]


def card_content(approval: Any, *, now: float | None = None) -> dict[str, Any]:
    """Plain-language content for an approval card on any channel.

    Never exposes a malformed structured envelope; a corrupted card becomes
    ``status="invalid"`` with a fixed sentence, like the dashboard row.
    """
    from .approval_cards import ApprovalCardError, decode_approval_detail

    current = time.time() if now is None else float(now)
    risk = str(getattr(approval, "risk", "") or "medium").lower()
    content: dict[str, Any] = {
        "id": int(approval.id),
        "action": _plain_action(approval.action),
        "risk": risk,
        "risk_label": _RISK_LABELS.get(risk, risk.capitalize() + " risk"),
        "scope": " ".join(str(getattr(approval, "scope", "") or "").split())[:300]
        or "The item described",
        "summary": " ".join(str(getattr(approval, "detail", "") or "").split())[:600],
        "what_it_will_do": [],
        "spending": "Not stated",
        "irreversible": False,
        "decide_by": None,
        "requested_by": getattr(approval, "requested_by", None) or "",
        "status": "legacy",
        "binding": None,
        "needs_confirmation": needs_confirmation(risk),
        "approval_status": str(getattr(approval, "status", "pending") or "pending"),
        "decided_by": getattr(approval, "decided_by", None) or "",
    }
    if content["approval_status"] != "pending":
        content["status"] = "decided"
    try:
        card = decode_approval_detail(getattr(approval, "detail", None))
    except ApprovalCardError:
        content["summary"] = "Structured approval details failed their integrity check."
        content["status"] = "invalid"
        return content
    if card is None:
        return content
    content.update({
        "summary": card["summary"],
        "scope": card["target"]["label"],
        "what_it_will_do": list(card["parameters"]["summary"]),
        "spending": _spending_text(card.get("budget")),
        "irreversible": bool(card["consequences"]["irreversible"]),
        "decide_by": float(card["expires_at"]),
        "requested_by": card["authority"]["actor"],
        "binding": card["binding_sha256"],
        "known_effects": list(card["consequences"]["known_effects"]),
    })
    if content["status"] != "decided":
        content["status"] = "expired" if current >= card["expires_at"] else "current"
    return content


# ----------------------------------------------------------------------------
# Signed-audit delivery: the ONE implementation the dashboard and channels share.
# ----------------------------------------------------------------------------

def deliver_approval_audit_event(world, event) -> bool:
    """Deliver exactly one queued vote and persist its delivery marker."""
    if event.delivered_at is not None:
        return True

    from .audit import AuditRefused, EventKind, audit_event

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
        # Not refusal swallowing: no approval becomes effective. The exact event
        # stays in the durable outbox for a policy/key repair and idempotent retry.
        log.error(
            "approval audit refused; decision remains non-effective (event_id=%s): %s",
            event.event_id, exc,
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
    # acknowledgement failed. Reconcile instead of inviting a differently-keyed retry.
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


def audit_approval_decision(world, event) -> bool:
    """Deliver an approval's queued votes in durable occurrence order.

    The world-model outbox is the authority seam: an audit refusal leaves every
    later vote durable but non-effective. A retry uses stable event ids and
    resumes at the oldest undelivered row, so a quorum-reaching vote cannot
    overtake an earlier sign-off in the signed chain.
    """
    try:
        pending = world.pending_approval_audit_events(
            approval_id=event.approval_id, limit=1000,
        )
    # failure-policy: fail_soft_with_audit
    except Exception:
        log.exception("approval audit outbox could not be read (event_id=%s)", event.event_id)
        return False
    for queued in pending:
        if not deliver_approval_audit_event(world, queued):
            return False
    try:
        current = world.get_approval_audit_event(event.event_id)
    except Exception:
        return False
    return bool(current and current.delivered_at is not None)


# ----------------------------------------------------------------------------
# The channel decision.
# ----------------------------------------------------------------------------

def _require_current_card(approval, *, binding: str | None, now: float) -> None:
    """Mirror the dashboard's approve-time card check (``_require_current_decision_card``)."""
    from .approval_cards import (
        ApprovalCardError,
        decode_approval_detail,
        require_current_approval_card,
    )

    try:
        card = decode_approval_detail(approval.detail)
        if card is None:
            raise ApprovalCardError(
                "this request predates exact-operation review; reject it on the "
                "dashboard and ask the requester to prepare a fresh review"
            )
        if not hmac.compare_digest(card["action"], approval.action):
            raise ApprovalCardError(
                "the approval card does not match the queued action; request a fresh review"
            )
        if not binding:
            raise ApprovalCardError("review the current approval card before deciding")
        require_current_approval_card(card, expected_binding=binding, now=now)
    except ApprovalCardError as exc:
        reason = "expired" if "expired" in str(exc) else "stale_card"
        raise ChannelApprovalRefused(reason, str(exc)) from exc


def decide_from_channel(
    world,
    approval_id: int,
    decision: str,
    *,
    principal: str,
    channel: str,
    binding: str | None = None,
    confirmed: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Record one approver's vote from ``channel`` and deliver its signed audit.

    ``decision`` is ``"approved"`` or ``"denied"``. Raises
    :class:`ChannelApprovalRefused` when nothing was recorded. The returned
    mapping says whether the vote is already effective (audit delivered) and
    the approval's status afterwards.
    """
    from .audit import EventKind, audit_event

    if decision not in ("approved", "denied"):
        raise ValueError("decision must be 'approved' or 'denied'")
    who = " ".join(str(principal or "").split())
    if not who:
        raise ChannelApprovalRefused(
            "unlinked", "Link your chat account to Lightwork first, then decide.",
        )
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}")
    current = time.time() if now is None else float(now)
    approval = world.get_approval(int(approval_id))
    if approval is None or approval.status != "pending":
        raise ChannelApprovalRefused(
            "not_pending", f"Request #{int(approval_id)} is no longer waiting for a decision.",
        )
    if approval.provenance == _SETTINGS_PROVENANCE:
        raise ChannelApprovalRefused(
            "dashboard_only",
            "A protected-settings request is decided by an administrator on the dashboard.",
        )
    if decision == "approved":
        _require_current_card(approval, binding=binding, now=current)
    if needs_confirmation(approval.risk) and not confirmed:
        raise ChannelApprovalRefused(
            "confirm_required",
            f"This is a {approval.risk}-risk request. Confirm you really mean it.",
        )
    event = world.decide_approval_audited(int(approval_id), decision, decided_by=who)
    if event is None:
        state = world.approval_state(int(approval_id)) or {}
        if state.get("status") != "pending":
            raise ChannelApprovalRefused(
                "not_pending", f"Request #{int(approval_id)} was already decided.",
            )
        raise ChannelApprovalRefused(
            "vote_not_accepted",
            "Your vote was not accepted: the requester can't approve their own request, "
            "and multi-party approval needs a different approver.",
        )
    delivered = audit_approval_decision(world, event)
    state = world.approval_state(int(approval_id)) or {}
    status = str(state.get("status") or ("pending" if not delivered else decision))
    try:
        audit_event(
            EventKind.CHANNEL_APPROVAL_DECISION,
            agent=who,
            approval_id=int(approval_id),
            decision=decision,
            channel=channel,
            decided_by=who,
            effective=bool(delivered),
            approval_event_id=event.event_id,
        )
    except Exception:  # noqa: BLE001 -- the signed decision chain above is the authority
        log.warning("channel approval audit note failed", exc_info=True)
    return {
        "approval_id": int(approval_id),
        "decision": decision,
        "decided_by": who,
        "channel": channel,
        "effective": bool(delivered),
        "status": status,
        "decision_id": event.event_id,
    }


# ----------------------------------------------------------------------------
# Announcing a new request on chat channels (best-effort, never blocks consent)
# ----------------------------------------------------------------------------

_announcers: dict[str, Any] = {}


def register_approval_announcer(channel: str, announcer) -> None:
    """``announcer(approval, content) -> bool`` posts a new card on ``channel``
    (the Teams adapter registers here; Slack is built in). ``None`` removes it."""
    if announcer is None:
        _announcers.pop(channel, None)
    else:
        _announcers[channel] = announcer


def _slack_announcer(approval, content: dict[str, Any]) -> bool:
    import os

    from .config import load_config
    from .daily_digest import dashboard_url
    from .notifications import _resolve_env_ref
    from .slack_interactive import approval_blocks, post_message

    section = (load_config().get("channels") or {}).get("slack") or {}
    if not isinstance(section, dict):
        return False
    room = str(section.get("approvals_channel") or os.environ.get("SLACK_APPROVALS_CHANNEL") or "").strip()
    token = (os.environ.get("SLACK_BOT_TOKEN") or _resolve_env_ref(section.get("bot_token")) or "").strip()
    if not room or not token:
        return False
    blocks = approval_blocks(content, dashboard_url=dashboard_url())
    return post_message(token, room, f"Approval needed: {content['action']} (#{content['id']})",
                        blocks) is not None


def announce_new_approval(world, approval_id: int) -> dict[str, bool]:
    """Post a freshly parked request to every configured chat channel.

    Called by ``safety.consent`` right after ``create_approval``. Failures are
    logged by type only and never delay or change the approval itself.
    """
    try:
        approval = world.get_approval(int(approval_id))
    except Exception:  # noqa: BLE001
        return {}
    if approval is None:
        return {}
    content = card_content(approval)
    outcomes: dict[str, bool] = {}
    for name, announcer in {"slack": _slack_announcer, **_announcers}.items():
        try:
            outcomes[name] = bool(announcer(approval, content))
        except Exception as exc:  # noqa: BLE001 -- best-effort by design
            log.warning("approval announcement on %s failed: %s", name, type(exc).__name__)
            outcomes[name] = False
    return outcomes


__all__ = [
    "CHANNELS",
    "announce_new_approval",
    "register_approval_announcer",
    "ChannelApprovalRefused",
    "audit_approval_decision",
    "card_content",
    "decide_from_channel",
    "deliver_approval_audit_event",
    "needs_confirmation",
]
