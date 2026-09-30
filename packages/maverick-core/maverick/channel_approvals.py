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
* The acknowledgement rule the Approvals page enforces applies here too:
  a High request needs an explicit acknowledgement (the Slack confirm
  dialog), a Critical one also a typed ``APPROVE`` (a Slack modal, or
  ``/lightwork approve <n> confirm APPROVE``); both write the same
  ``approval_confirmation`` audit event, tagged with the channel.
* :func:`undo_decision` returns a just-decided request to pending while the
  undo window is open, with the same audit event as the dashboard's Undo.
* An expired, decided, corrupted or protected-settings request is refused.

Nothing here talks to a network except :func:`announce_new_approval`, which
posts a new card best-effort. Identity linking (channel user id ->
Lightwork principal) lives in :mod:`maverick.channel_identities`.
"""
from __future__ import annotations

import hmac
import logging
import math
import os
import time
from typing import Any

log = logging.getLogger(__name__)

CHANNELS = ("slack", "teams", "email", "dashboard")
CONFIRM_RISKS = frozenset({"high", "critical"})
TYPED_CONFIRMATION = "approve"
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


# ----------------------------------------------------------------------------
# The acknowledgement and undo policy (shared with the Approvals page).
# ``maverick.safety.consent`` owns these knobs once the confirmable-queue
# change lands; until then the same readers live here with the same names.
# ----------------------------------------------------------------------------

def approval_requires_acknowledgement() -> bool:
    """Whether approving a High/Critical request needs an explicit acknowledgement
    (``MAVERICK_APPROVAL_REQUIRE_ACKNOWLEDGEMENT``, else ``[approval]
    require_acknowledgement``; default on)."""
    try:
        from .safety.consent import approval_requires_acknowledgement as shared

        return bool(shared())
    except ImportError:
        pass
    raw = os.environ.get("MAVERICK_APPROVAL_REQUIRE_ACKNOWLEDGEMENT", "").strip().lower()
    if raw:
        return raw not in ("0", "false", "no", "off")
    try:
        from .config import load_config

        value = ((load_config() or {}).get("approval") or {}).get("require_acknowledgement")
    except Exception:  # noqa: BLE001 -- config never weakens the confirmation
        value = None
    return value if isinstance(value, bool) else True


def approval_undo_grace_seconds() -> float:
    """How long (seconds) a decision can still be undone
    (``MAVERICK_APPROVAL_UNDO_GRACE_SECONDS``, else ``[approval]
    undo_grace_seconds``; default 30; 0 disables undo)."""
    try:
        from .safety.consent import approval_undo_grace_seconds as shared

        return float(shared())
    except ImportError:
        pass
    raw = os.environ.get("MAVERICK_APPROVAL_UNDO_GRACE_SECONDS", "")
    if not raw.strip():
        try:
            from .config import load_config

            value = ((load_config() or {}).get("approval") or {}).get("undo_grace_seconds")
        except Exception:  # noqa: BLE001 -- config never blocks the decision
            value = None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return max(0.0, min(float(value), 3600.0))
        raw = "30"
    try:
        return max(0.0, min(float(raw), 3600.0))
    except ValueError:
        return 30.0


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
        "needs_typed_confirmation": risk == "critical",
        "approval_status": str(getattr(approval, "status", "pending") or "pending"),
        "decided_by": getattr(approval, "decided_by", None) or "",
        "decided_at": getattr(approval, "decided_at", None),
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


def undo_seconds_left(approval: Any, *, now: float | None = None) -> float:
    """Seconds the decision on ``approval`` can still be undone (0 when it cannot)."""
    if approval is None or getattr(approval, "status", "pending") == "pending":
        return 0.0
    decided_at = getattr(approval, "decided_at", None)
    if not isinstance(decided_at, (int, float)) or isinstance(decided_at, bool):
        return 0.0
    current = time.time() if now is None else float(now)
    return max(0.0, float(decided_at) + approval_undo_grace_seconds() - current)


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


def _require_acknowledgement(approval, *, who: str, channel: str, decision: str,
                             confirmed: bool, typed_confirmation: str | None) -> None:
    """The Approvals page's confirmation step, on a channel.

    High: an explicit acknowledgement (the confirm dialog). Critical, when
    approving: also the typed word ``APPROVE``. Writes the same
    ``approval_confirmation`` audit event as the dashboard, with the channel.
    """
    from . import audit

    risk = str(approval.risk or "").lower()
    if risk not in CONFIRM_RISKS:
        return
    if not confirmed:
        raise ChannelApprovalRefused(
            "confirm_required",
            f"This is a {risk}-risk request. Confirm you really mean it.",
        )
    typed = " ".join(str(typed_confirmation or "").split()).lower()
    if (risk == "critical" and decision == "approved" and approval_requires_acknowledgement()
            and typed != TYPED_CONFIRMATION):
        raise ChannelApprovalRefused(
            "typed_confirmation_required",
            "Type APPROVE to confirm this critical action.",
        )
    recorded = audit.audit_event(
        audit.EventKind.CONSENT_RESULT,
        agent=who,
        action="approval_confirmation",
        approval_id=int(approval.id),
        risk=risk,
        acknowledged=True,
        typed_confirmation=(risk == "critical" and decision == "approved"),
        channel=channel,
        summary=(f"{who} confirmed reading what '{_plain_action(approval.action)}' will do "
                 f"({risk} risk) before deciding it on {channel}."),
    )
    if not recorded:
        log.warning("approval confirmation audit write failed (approval_id=%s)", approval.id)


def decide_from_channel(
    world,
    approval_id: int,
    decision: str,
    *,
    principal: str,
    channel: str,
    binding: str | None = None,
    confirmed: bool = False,
    typed_confirmation: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Record one approver's vote from ``channel`` and deliver its signed audit.

    ``decision`` is ``"approved"`` or ``"denied"``. Raises
    :class:`ChannelApprovalRefused` when nothing was recorded. The returned
    mapping says whether the vote is already effective (audit delivered), the
    approval's status afterwards and how long it can still be undone.
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
    _require_acknowledgement(approval, who=who, channel=channel, decision=decision,
                             confirmed=confirmed, typed_confirmation=typed_confirmation)
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
    undo_seconds = approval_undo_grace_seconds() if (delivered and status != "pending") else 0.0
    return {
        "approval_id": int(approval_id),
        "decision": decision,
        "decided_by": who,
        "channel": channel,
        "effective": bool(delivered),
        "status": status,
        "decision_id": event.event_id,
        "undo_seconds": undo_seconds,
    }


def undo_decision(world, approval_id: int, *, undone_by: str, channel: str) -> dict[str, Any]:
    """Undo a decision made moments ago, returning the request to pending.

    Same rule and audit event as the Approvals page's Undo: allowed only inside
    the undo window (the agent-side consumer waits that long before acting).
    The original decision stays in the signed chain; the next decision is a
    new audit round.
    """
    from . import audit

    who = " ".join(str(undone_by or "").split())
    if not who:
        raise ChannelApprovalRefused("unlinked", "Link your chat account to Lightwork first.")
    before = world.get_approval(int(approval_id))
    if before is None:
        raise ChannelApprovalRefused("not_found", f"There is no request #{int(approval_id)}.")
    undo = getattr(world, "undo_approval_decision", None)
    if undo is None:
        raise ChannelApprovalRefused(
            "unavailable", "Undo is not available on this version of Lightwork.")
    outcome = undo(int(approval_id), undone_by=who, within_seconds=approval_undo_grace_seconds())
    if outcome == "not_found":
        raise ChannelApprovalRefused("not_found", f"There is no request #{int(approval_id)}.")
    if outcome == "not_decided":
        raise ChannelApprovalRefused("not_decided", "There is no decision to undo yet.")
    if outcome != "undone":
        raise ChannelApprovalRefused(
            "too_late",
            "Too late to undo: the agent has already been told about this decision. "
            "Ask for a new request if it should not go ahead.",
        )
    previous = "approval" if before.status == "approved" else "rejection"
    recorded = audit.audit_event(
        audit.EventKind.CONSENT_RESULT,
        agent=who,
        action="approval_decision_undone",
        approval_id=int(approval_id),
        previous_status=before.status,
        previously_decided_by=before.decided_by or "",
        channel=channel,
        summary=(f"{who} undid the {previous} of '{_plain_action(before.action)}' on {channel}; "
                 "it is waiting for a decision again."),
    )
    if not recorded:
        log.warning("approval undo audit write failed (approval_id=%s)", approval_id)
    return {"approval_id": int(approval_id), "status": "pending",
            "previous_status": before.status, "undone_by": who, "channel": channel,
            "audited": bool(recorded)}


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
    from .config import load_config
    from .daily_digest import dashboard_url
    from .slack_interactive import approval_blocks, post_message, slack_bot_token

    section = (load_config().get("channels") or {}).get("slack") or {}
    if not isinstance(section, dict):
        return False
    room = str(section.get("approvals_channel") or os.environ.get("SLACK_APPROVALS_CHANNEL") or "").strip()
    token = slack_bot_token()
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
    "TYPED_CONFIRMATION",
    "ChannelApprovalRefused",
    "announce_new_approval",
    "approval_requires_acknowledgement",
    "approval_undo_grace_seconds",
    "audit_approval_decision",
    "card_content",
    "decide_from_channel",
    "deliver_approval_audit_event",
    "needs_confirmation",
    "register_approval_announcer",
    "undo_decision",
    "undo_seconds_left",
]
