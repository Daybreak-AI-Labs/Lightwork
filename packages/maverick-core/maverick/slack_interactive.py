"""Slack Block Kit cards, request signing checks and interaction handling.

Pure functions the Socket Mode adapter (``maverick_channels.slack``) and the
dashboard's HTTP receivers (``/webhook/slack/interactions``,
``/webhook/slack/commands``) both use, so a button press is handled the same
way whichever transport delivered it. Decisions go through
:mod:`maverick.channel_approvals`; identities through
:mod:`maverick.channel_identities`.

What Slack sends and expects (docs.slack.dev, read September 2026):

* Request signing (authentication/verifying-requests-from-slack): headers
  ``X-Slack-Request-Timestamp`` and ``X-Slack-Signature``; the signature is
  ``"v0=" + hex(HMAC-SHA256(signing_secret, "v0:" + timestamp + ":" + raw_body))``;
  refuse a timestamp more than five minutes from local time (replay).
* Interactivity (interactivity/handling-user-interaction): an
  ``application/x-www-form-urlencoded`` POST with one ``payload`` field of
  JSON; ``type: "block_actions"`` carries ``user.id``, ``team.id``,
  ``channel.id``, ``message.ts``, ``response_url`` and ``actions[]`` with
  ``action_id``/``value``. Answer 200 within 3 seconds; POST to
  ``response_url`` with ``replace_original: true`` to update the message, or
  ``response_type: "ephemeral"`` for a private note.
* Slash commands (interactivity/implementing-slash-commands): form fields
  ``command``, ``text``, ``user_id``, ``channel_id``, ``team_id``,
  ``response_url``, ``trigger_id``; reply within 3000 ms with text or JSON
  (``response_type`` ``ephemeral`` (default) or ``in_channel``).
* Socket Mode (apis/events-api/using-socket-mode): envelope ``type`` is
  ``events_api``, ``interactive`` or ``slash_commands``; ack with the
  ``envelope_id`` and, when ``accepts_response_payload`` is true, an optional
  ``payload``.
* Confirmation dialog (reference/block-kit/composition-objects): ``title``
  (plain_text, 100), ``text`` (300), ``confirm``/``deny`` (30), ``style``
  ``primary`` or ``danger``; a button element takes it in ``confirm``.
* chat.postMessage (reference/methods/chat.postMessage): scope
  ``chat:write``; ``text`` is the notification fallback when ``blocks`` are
  set. To message a person as the app, open the DM with
  ``conversations.open`` (``users=U...``, scope ``im:write``) and post to the
  returned ``D...`` id.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs

log = logging.getLogger(__name__)

SIGNATURE_VERSION = "v0"
SIGNATURE_TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 256 * 1024
ACTION_APPROVE = "lightwork_approve"
ACTION_REJECT = "lightwork_reject"
ACTION_QUESTION = "lightwork_question"
ACTION_UNDO = "lightwork_undo"
CONFIRM_VIEW_CALLBACK = "lightwork_confirm_critical"
CONFIRM_BLOCK = "lightwork_typed"
CONFIRM_INPUT = "typed_confirmation"
SLASH_VERBS = ("approve", "reject", "status", "help", "ask", "link", "undo")
SLACK_API = "https://slack.com/api"
_HELP = (
    "*Lightwork commands*\n"
    "• `/lightwork status` — what is waiting for you\n"
    "• `/lightwork approve <number> [confirm]` — approve a request (High needs `confirm`; "
    "Critical needs `confirm APPROVE`)\n"
    "• `/lightwork reject <number> [confirm]` — reject a request\n"
    "• `/lightwork undo <number>` — take back a decision made moments ago\n"
    "• `/lightwork ask <what you need>` — give Lightwork a task\n"
    "• `/lightwork link <code>` — tie this Slack account to your Lightwork account\n"
    "• `/lightwork help` — this list"
)

PrincipalResolver = Callable[[str], "str | None"]
# ``authorize(principal, approval_id)`` -- the host's permission check for a vote
# (the dashboard passes its ``operate`` role rule); None skips it.
Authorizer = Callable[[str, int], None]


# ----------------------------------------------------------------------------
# Request signing
# ----------------------------------------------------------------------------

def verify_slack_signature(
    signing_secret: str,
    *,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float | None = None,
    tolerance: int = SIGNATURE_TOLERANCE_SECONDS,
) -> bool:
    """True when ``signature`` is Slack's HMAC over ``timestamp`` and the raw body.

    Constant-time comparison; a timestamp more than ``tolerance`` seconds from
    now is refused so a captured request cannot be replayed later.
    """
    if not signing_secret or not timestamp or not signature or body is None:
        return False
    try:
        sent = float(str(timestamp).strip())
    except ValueError:
        return False
    current = time.time() if now is None else float(now)
    if abs(current - sent) > tolerance:
        return False
    if not str(signature).startswith(SIGNATURE_VERSION + "="):
        return False
    base = f"{SIGNATURE_VERSION}:{str(timestamp).strip()}:".encode() + bytes(body)
    expected = SIGNATURE_VERSION + "=" + hmac.new(
        signing_secret.encode("utf-8"), base, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, str(signature).strip())


def parse_form(body: bytes) -> dict[str, str]:
    """Decode an ``application/x-www-form-urlencoded`` Slack body (first values)."""
    if body is None or len(body) > MAX_BODY_BYTES:
        return {}
    pairs = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=64)
    return {key: values[0] for key, values in pairs.items() if values}


def parse_interaction_body(body: bytes) -> dict[str, Any]:
    """The JSON object inside the form's ``payload`` field (``{}`` when malformed)."""
    raw = parse_form(body).get("payload", "")
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# ----------------------------------------------------------------------------
# Button values and slash text
# ----------------------------------------------------------------------------

def encode_button_value(approval_id: int, binding: str | None) -> str:
    return f"{int(approval_id)}:{binding or ''}"[:2000]


def decode_button_value(value: object) -> tuple[int | None, str | None]:
    text = str(value or "")
    head, _, binding = text.partition(":")
    try:
        approval_id = int(head)
    except ValueError:
        return None, None
    return approval_id, (binding or None)


def parse_slash_text(text: str | None) -> tuple[str, str]:
    """``"approve 12 confirm"`` -> ``("approve", "12 confirm")``; empty -> ``("help", "")``."""
    words = " ".join(str(text or "").split()).split(" ", 1)
    verb = (words[0] if words and words[0] else "help").lower()
    rest = words[1] if len(words) > 1 else ""
    if verb not in SLASH_VERBS:
        return "help", ""
    return verb, rest


def _decision_args(rest: str) -> tuple[int | None, bool, str | None]:
    """``"12 confirm APPROVE"`` -> ``(12, True, "APPROVE")``."""
    parts = rest.split()
    if not parts:
        return None, False, None
    head = parts[0].lstrip("#")
    try:
        approval_id = int(head)
    except ValueError:
        return None, False, None
    words = [p.lower() for p in parts[1:]]
    confirmed = any(w in ("confirm", "yes") for w in words)
    typed = next((p for p in parts[1:] if p.lower() == "approve"), None)
    return approval_id, confirmed, typed


# ----------------------------------------------------------------------------
# Block Kit
# ----------------------------------------------------------------------------

def _plain(text: str, limit: int = 150) -> dict[str, Any]:
    return {"type": "plain_text", "text": str(text)[:limit], "emoji": False}


def _mrkdwn(text: str, limit: int = 3000) -> dict[str, Any]:
    return {"type": "mrkdwn", "text": str(text)[:limit]}


def _when(ts: float | None) -> str:
    if not ts:
        return "No deadline given"
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")


def _confirm_dialog(content: dict[str, Any], decision: str) -> dict[str, Any]:
    verb = "Approve" if decision == "approved" else "Reject"
    undo = " It can't be undone." if content.get("irreversible") and decision == "approved" else ""
    return {
        "title": _plain(f"{verb} a {content['risk']}-risk request?", 100),
        "text": _plain(
            f"{verb} request #{content['id']}: {content['action']}.{undo} "
            "Your decision is recorded in the audit log.", 300),
        "confirm": _plain(f"Yes, {verb.lower()}", 30),
        "deny": _plain("Go back", 30),
        "style": "danger" if decision == "denied" or content.get("irreversible") else "primary",
    }


def approval_blocks(content: dict[str, Any], *, dashboard_url: str = "",
                    include_actions: bool = True) -> list[dict[str, Any]]:
    """The approval card: what it will do, risk, spending, undo, decide-by, buttons."""
    lines = [f"*{content['summary']}*"] if content.get("summary") else []
    if content.get("what_it_will_do"):
        lines.append("*What it will do*\n" + "\n".join(
            f"• {item}" for item in content["what_it_will_do"][:12]))
    if content.get("known_effects"):
        lines.append("*What happens next*\n" + "\n".join(
            f"• {item}" for item in content["known_effects"][:8]))
    fields = [
        _mrkdwn(f"*Risk*\n{content['risk_label']}", 2000),
        _mrkdwn(f"*Spending*\n{content['spending']}", 2000),
        _mrkdwn(f"*Applies to*\n{content['scope']}", 2000),
        _mrkdwn(f"*Decide by*\n{_when(content.get('decide_by'))}", 2000),
    ]
    if content.get("irreversible"):
        fields.append(_mrkdwn("*Can't be undone*\nYes — there is no automatic way back.", 2000))
    if content.get("requested_by"):
        fields.append(_mrkdwn(f"*Requested by*\n{content['requested_by']}", 2000))
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": _plain(f"Approval needed: {content['action']}")},
    ]
    if lines:
        blocks.append({"type": "section", "text": _mrkdwn("\n\n".join(lines))})
    blocks.append({"type": "section", "fields": fields[:10]})
    note = (f"Request #{content['id']} · This is Lightwork's suggestion — the decision is "
            "yours, and it applies only to exactly what is described here.")
    if dashboard_url:
        note += f" <{dashboard_url.rstrip('/')}/approvals#approval-{content['id']}|Open on the dashboard>"
    blocks.append({"type": "context", "elements": [_mrkdwn(note)]})
    status = content.get("status")
    if status == "expired":
        blocks.append({"type": "section", "text": _mrkdwn(
            ":hourglass: *This request has expired.* Ask the requester for a fresh review.")})
        return blocks
    if status == "invalid":
        blocks.append({"type": "section", "text": _mrkdwn(
            ":warning: *These details failed their integrity check.* Decide it on the dashboard.")})
        return blocks
    if not include_actions:
        return blocks
    value = encode_button_value(content["id"], content.get("binding"))
    approve = {"type": "button", "action_id": ACTION_APPROVE, "value": value,
               "text": _plain("Approve", 75), "style": "primary"}
    reject = {"type": "button", "action_id": ACTION_REJECT, "value": value,
              "text": _plain("Reject", 75), "style": "danger"}
    if content.get("needs_confirmation"):
        approve["confirm"] = _confirm_dialog(content, "approved")
        reject["confirm"] = _confirm_dialog(content, "denied")
    ask = {"type": "button", "action_id": ACTION_QUESTION, "value": value,
           "text": _plain("Ask a question", 75)}
    blocks.append({"type": "actions", "block_id": f"lightwork_approval_{content['id']}",
                   "elements": [approve, reject, ask]})
    return blocks


def decided_blocks(content: dict[str, Any], *, decision: str, decided_by: str,
                   effective: bool, status: str, undo_seconds: float = 0.0) -> list[dict[str, Any]]:
    """The same card after a decision: buttons gone, outcome and decider shown."""
    blocks = approval_blocks(content, include_actions=False)
    if status == "pending":
        outcome = (f":ballot_box_with_check: *{decided_by}* voted to "
                   f"{'approve' if decision == 'approved' else 'reject'}. "
                   "More approvers are needed before it goes ahead.")
    elif decision == "approved":
        outcome = f":white_check_mark: *Approved* by {decided_by}."
    else:
        outcome = f":no_entry_sign: *Rejected* by {decided_by}."
    if not effective:
        outcome += " Recorded; it takes effect once the audit record is confirmed."
    if undo_seconds and status != "pending":
        outcome += f" You can undo this for {int(undo_seconds)} seconds."
    blocks.append({"type": "section", "text": _mrkdwn(outcome)})
    if undo_seconds and status != "pending":
        blocks.append({"type": "actions", "block_id": f"lightwork_undo_{content['id']}", "elements": [
            {"type": "button", "action_id": ACTION_UNDO, "value": encode_button_value(content["id"], None),
             "text": _plain("Undo", 75)}]})
    return blocks


def text_blocks(title: str, body: str) -> list[dict[str, Any]]:
    """A result message: a heading and the answer, split to Slack's section limit."""
    blocks: list[dict[str, Any]] = [{"type": "header", "text": _plain(title)}]
    text = str(body or "")
    for start in range(0, max(1, len(text)), 2900):
        blocks.append({"type": "section", "text": _mrkdwn(text[start:start + 2900] or "(no text)")})
        if len(blocks) > 40:
            break
    return blocks


def digest_blocks(digest: dict[str, Any], *, dashboard_url: str = "") -> list[dict[str, Any]]:
    """The daily summary as Block Kit: the same content as the My work page."""
    blocks: list[dict[str, Any]] = [{"type": "header", "text": _plain(
        f"Your Lightwork summary · {digest.get('date_label', '')}".strip(" ·"))}]
    approvals = digest.get("approvals") or []
    questions = digest.get("questions") or []
    results = digest.get("results") or []
    spend = digest.get("spending") or {}
    if approvals:
        blocks.append({"type": "section", "text": _mrkdwn(
            f"*{len(approvals)} waiting for your decision*\n" + "\n".join(
                f"• #{a['id']} {a['action']} — {a['risk_label']}" for a in approvals[:10]))})
    if questions:
        blocks.append({"type": "section", "text": _mrkdwn(
            f"*{len(questions)} question{'s' if len(questions) != 1 else ''} an agent is waiting on*\n"
            + "\n".join(f"• {q['question']}" for q in questions[:10]))})
    if results:
        blocks.append({"type": "section", "text": _mrkdwn(
            f"*{len(results)} recent result{'s' if len(results) != 1 else ''}*\n"
            + "\n".join(f"• {r['title']} — {r['status_label']}" for r in results[:10]))})
    keys = digest.get("expiring_keys") or []
    if keys:
        blocks.append({"type": "section", "text": _mrkdwn(
            f"*{len(keys)} access key{'s' if len(keys) != 1 else ''} stop working within 7 days*\n"
            + "\n".join(f"• {k.get('label') or k.get('prefix')} — in {k.get('days_left', 0)} day(s)"
                        for k in keys[:10]))})
    if not (approvals or questions or results or keys):
        blocks.append({"type": "section", "text": _mrkdwn("Nothing is waiting for you today.")})
    limit = spend.get("limit_dollars") or 0
    spent = f"${float(spend.get('dollars') or 0):.2f}"
    against = f" of your ${float(limit):.2f} monthly limit" if limit else ""
    blocks.append({"type": "context", "elements": [_mrkdwn(
        f"Spending this month: {spent}{against}."
        + (f" <{dashboard_url.rstrip('/')}/me|Open My work>" if dashboard_url else ""))]})
    return blocks


def confirm_view(content: dict[str, Any], *, binding: str | None, response_url: str) -> dict[str, Any]:
    """The modal a Critical approval opens: the person types APPROVE."""
    metadata = json.dumps({"approval_id": content["id"], "binding": binding,
                           "response_url": response_url})[:3000]
    return {
        "type": "modal",
        "callback_id": CONFIRM_VIEW_CALLBACK,
        "private_metadata": metadata,
        "title": _plain("Confirm a critical action", 24),
        "submit": _plain("Approve", 24),
        "close": _plain("Go back", 24),
        "blocks": [
            {"type": "section", "text": _mrkdwn(
                f"*Request #{content['id']}: {content['action']}*\n{content['summary']}\n\n"
                "This is a *critical-risk* action"
                + (" that *can't be undone*" if content.get("irreversible") else "") + ".")},
            {"type": "input", "block_id": CONFIRM_BLOCK,
             "label": _plain("Type APPROVE to confirm", 2000),
             "element": {"type": "plain_text_input", "action_id": CONFIRM_INPUT,
                         "placeholder": _plain("APPROVE", 150)}},
        ],
    }


# ----------------------------------------------------------------------------
# Handling interactions and slash commands
# ----------------------------------------------------------------------------

def _ephemeral(text: str) -> dict[str, Any]:
    return {"response_type": "ephemeral", "replace_original": False, "text": text[:3000]}


def _unlinked_note(user_id: str) -> dict[str, Any]:
    return _ephemeral(
        "Your Slack account is not linked to Lightwork yet. Open *My work* on the "
        "dashboard, choose *Link Slack*, and send the code here with "
        f"`/lightwork link <code>` (your Slack id is {user_id})."
    )


def _decide(world, *, approval_id: int, decision: str, principal: str,
            binding: str | None, confirmed: bool, typed: str | None = None,
            authorize: Authorizer | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """Run the shared decision; return ``(result, error_text)``."""
    from .channel_approvals import ChannelApprovalRefused, decide_from_channel

    try:
        result = decide_from_channel(
            world, approval_id, decision, principal=principal, channel="slack",
            binding=binding, confirmed=confirmed, typed_confirmation=typed,
            authorize=authorize,
        )
    except ChannelApprovalRefused as exc:
        return None, exc.message
    except Exception:  # noqa: BLE001 -- never leak internals into a chat app
        log.exception("slack approval decision failed")
        return None, "Lightwork could not record that decision. Try again or use the dashboard."
    return result, None


def _decided_message(world, approval_id: int, *, decision: str, principal: str,
                     result: dict[str, Any]) -> dict[str, Any]:
    from .channel_approvals import card_content

    content = card_content(world.get_approval(approval_id))
    label = result["status"] if result["status"] != "pending" else "voted on"
    return {
        "replace_original": True,
        "text": f"Request #{approval_id} {label} by {principal}",
        "blocks": decided_blocks(content, decision=decision, decided_by=principal,
                                 effective=result["effective"], status=result["status"],
                                 undo_seconds=result.get("undo_seconds", 0.0)),
    }


def _undo_message(world, approval_id: int, *, principal: str,
                  authorize: Authorizer | None = None) -> dict[str, Any]:
    """After an undo the card is live again, with its buttons."""
    from .channel_approvals import ChannelApprovalRefused, card_content, undo_decision

    try:
        undo_decision(world, approval_id, undone_by=principal, channel="slack",
                      authorize=authorize)
    except ChannelApprovalRefused as exc:
        return _ephemeral(exc.message)
    content = card_content(world.get_approval(approval_id))
    blocks = approval_blocks(content)
    blocks.append({"type": "context", "elements": [_mrkdwn(
        f"{principal} took back their decision; the request is waiting again.")]})
    return {"replace_original": True,
            "text": f"Request #{approval_id} is waiting for a decision again", "blocks": blocks}


def handle_block_action(payload: dict[str, Any], *, world, principal_for: PrincipalResolver,
                        open_view: Callable[[str, dict[str, Any]], bool] | None = None,
                        authorize: Authorizer | None = None) -> dict[str, Any] | None:
    """Handle one ``block_actions`` payload; return the message to send back.

    A returned mapping with ``replace_original: True`` rewrites the card in
    place; ``response_type: "ephemeral"`` is a private note. ``None`` means
    nothing to say (an action we do not own). Approving a Critical request
    opens the typed-confirmation modal through ``open_view(trigger_id, view)``
    (``views.open``) instead of deciding at once.
    """
    from .channel_approvals import card_content

    if payload.get("type") != "block_actions":
        return None
    actions = [a for a in (payload.get("actions") or []) if isinstance(a, dict)]
    action = next((a for a in actions if a.get("action_id") in
                   (ACTION_APPROVE, ACTION_REJECT, ACTION_QUESTION, ACTION_UNDO)), None)
    if action is None:
        return None
    approval_id, binding = decode_button_value(action.get("value"))
    if approval_id is None:
        return _ephemeral("That button no longer points at a request.")
    user_id = str((payload.get("user") or {}).get("id") or "")
    principal = principal_for(user_id) if user_id else None
    if not principal:
        return _unlinked_note(user_id)
    if action["action_id"] == ACTION_QUESTION:
        return _ephemeral(
            f"Ask your question about request #{approval_id} in a reply to this message, "
            f"or with `/lightwork ask question about request #{approval_id}: ...`. "
            "The requester and Lightwork see it; the request stays waiting until you decide."
        )
    if action["action_id"] == ACTION_UNDO:
        return _undo_message(world, approval_id, principal=principal, authorize=authorize)
    decision = "approved" if action["action_id"] == ACTION_APPROVE else "denied"
    approval = world.get_approval(approval_id)
    if decision == "approved" and approval is not None and approval.status == "pending":
        content = card_content(approval)
        if content["needs_typed_confirmation"]:
            trigger = str(payload.get("trigger_id") or "")
            view = confirm_view(content, binding=binding,
                                response_url=str(payload.get("response_url") or ""))
            if open_view is not None and trigger and open_view(trigger, view):
                return _ephemeral("Type APPROVE in the window that opened to confirm this "
                                  "critical action.")
            return _ephemeral("This critical action needs a typed confirmation: "
                              f"`/lightwork approve {approval_id} confirm APPROVE`.")
    # The button's confirm dialog is the acknowledgement for High/Critical:
    # Slack only sends this action after the person pressed "Yes" in it.
    result, error = _decide(world, approval_id=approval_id, decision=decision,
                            principal=principal, binding=binding, confirmed=True,
                            authorize=authorize)
    if error:
        return _ephemeral(error)
    return _decided_message(world, approval_id, decision=decision, principal=principal,
                            result=result)


def handle_view_submission(payload: dict[str, Any], *, world,
                           principal_for: PrincipalResolver,
                           authorize: Authorizer | None = None) -> dict[str, Any] | None:
    """The typed-confirmation modal was submitted.

    Returns ``{"ack": <view response>, "respond_to": url, "message": <update>}``:
    ``ack`` answers Slack (``response_action`` ``clear`` or ``errors``), and
    ``message`` (when present) rewrites the original card through ``respond_to``.
    """
    view = payload.get("view") if isinstance(payload.get("view"), dict) else {}
    if payload.get("type") != "view_submission" or view.get("callback_id") != CONFIRM_VIEW_CALLBACK:
        return None
    try:
        meta = json.loads(view.get("private_metadata") or "{}")
    except ValueError:
        meta = {}
    approval_id = meta.get("approval_id")
    if not isinstance(approval_id, int):
        return {"ack": {"response_action": "clear"}, "respond_to": "", "message": None}
    user_id = str((payload.get("user") or {}).get("id") or "")
    principal = principal_for(user_id) if user_id else None
    errors = {"response_action": "errors", "errors": {}}
    if not principal:
        errors["errors"][CONFIRM_BLOCK] = "Link your Slack account to Lightwork first."
        return {"ack": errors, "respond_to": "", "message": None}
    values = ((view.get("state") or {}).get("values") or {}).get(CONFIRM_BLOCK) or {}
    typed = str((values.get(CONFIRM_INPUT) or {}).get("value") or "")
    result, error = _decide(world, approval_id=approval_id, decision="approved",
                            principal=principal, binding=meta.get("binding"),
                            confirmed=True, typed=typed, authorize=authorize)
    if error:
        errors["errors"][CONFIRM_BLOCK] = error[:200]
        return {"ack": errors, "respond_to": "", "message": None}
    return {"ack": {"response_action": "clear"},
            "respond_to": str(meta.get("response_url") or ""),
            "message": _decided_message(world, approval_id, decision="approved",
                                        principal=principal, result=result)}


def _status_text(world, principal: str) -> str:
    from .daily_digest import compose_digest

    digest = compose_digest(world, principal, can_decide=True)
    parts = [
        f"{len(digest['approvals'])} waiting for your decision",
        f"{len(digest['questions'])} question(s) an agent is waiting on",
        f"{len(digest['results'])} recent result(s)",
        f"spent ${digest['spending']['dollars']:.2f} this month",
    ]
    lines = ["*Your Lightwork status*: " + "; ".join(parts) + "."]
    for row in digest["approvals"][:10]:
        lines.append(f"• #{row['id']} {row['action']} — {row['risk_label']}")
    return "\n".join(lines)


def handle_slash_command(form: dict[str, Any], *, world, principal_for: PrincipalResolver,
                         ask: Callable[[str, str], str] | None = None,
                         authorize: Authorizer | None = None) -> dict[str, Any]:
    """``/lightwork approve|reject|status|help|ask|link`` -> a Slack response."""
    from . import channel_identities

    verb, rest = parse_slash_text(form.get("text"))
    user_id = str(form.get("user_id") or "")
    if verb == "help":
        return _ephemeral(_HELP)
    if verb == "link":
        principal = channel_identities.redeem_link_code("slack", user_id, rest) if rest else None
        if not principal:
            return _ephemeral("That code is wrong, used or expired. Get a fresh one from My work.")
        return _ephemeral(f"Done. This Slack account is now linked to {principal}.")
    principal = principal_for(user_id) if user_id else None
    if not principal:
        return _unlinked_note(user_id)
    if verb == "status":
        return _ephemeral(_status_text(world, principal))
    if verb == "ask":
        if not rest:
            return _ephemeral("Tell Lightwork what you need: `/lightwork ask <your request>`.")
        if ask is None:
            return _ephemeral("Asking for work is not available over this connection. "
                              "Message Lightwork directly instead.")
        try:
            answer = ask(principal, rest)
        except Exception:  # noqa: BLE001 -- the person gets a plain sentence
            log.exception("slack ask failed")
            answer = "Lightwork could not take that request right now."
        return {"response_type": "ephemeral", "replace_original": False,
                "text": str(answer)[:3000]}
    approval_id, confirmed, typed = _decision_args(rest)
    if approval_id is None:
        return _ephemeral(f"Say which request: `/lightwork {verb} <number>`.")
    if verb == "undo":
        reply = _undo_message(world, approval_id, principal=principal, authorize=authorize)
        if reply.get("replace_original"):
            return _ephemeral(f"Your decision on request #{approval_id} is taken back; "
                              "it is waiting for a decision again.")
        return reply
    decision = "approved" if verb == "approve" else "denied"
    approval = world.get_approval(approval_id)
    binding = None
    if approval is not None:
        from .channel_approvals import card_content

        binding = card_content(approval).get("binding")
    result, error = _decide(world, approval_id=approval_id, decision=decision,
                            principal=principal, binding=binding, confirmed=confirmed, typed=typed,
                            authorize=authorize)
    if error:
        hint = ""
        if "Confirm you really mean it" in error:
            hint = f" Add `confirm`: `/lightwork {verb} {approval_id} confirm`."
        elif "Type APPROVE" in error:
            hint = f" Send `/lightwork approve {approval_id} confirm APPROVE`."
        return _ephemeral(error + hint)
    word = "approved" if decision == "approved" else "rejected"
    if result["status"] == "pending":
        return _ephemeral(f"Your vote to have request #{approval_id} {word} is recorded; "
                          "more approvers are needed.")
    note = "" if result["effective"] else " It takes effect once the audit record is confirmed."
    if result.get("undo_seconds"):
        note += f" You can take it back for {int(result['undo_seconds'])} seconds with `/lightwork undo {approval_id}`."
    return _ephemeral(f"Request #{approval_id} {word}." + note)


# ----------------------------------------------------------------------------
# Talking back to Slack (guarded outbound HTTP)
# ----------------------------------------------------------------------------

def post_json(url: str, payload: dict[str, Any], *, token: str | None = None,
              timeout: float = 10.0) -> dict[str, Any] | None:
    """POST JSON through the SSRF-guarded client; ``None`` on any failure.

    Logs only the failure type: the response URL and token are secrets.
    """
    try:
        from .tools._ssrf import safe_client

        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with safe_client(url, timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            log.warning("slack post refused: HTTP %s", response.status_code)
            return None
        try:
            body = response.json()
        except ValueError:
            body = {"ok": response.text.strip().lower() == "ok"}
        return body if isinstance(body, dict) else {"ok": True}
    except Exception as exc:  # noqa: BLE001 -- never raise into a request handler
        log.warning("slack post failed: %s", type(exc).__name__)
        return None


def respond(response_url: str, payload: dict[str, Any]) -> bool:
    """Update or answer a message through its ``response_url``."""
    if not response_url:
        return False
    result = post_json(response_url, payload)
    return bool(result and result.get("ok", True))


def slack_bot_token() -> str:
    """``SLACK_BOT_TOKEN`` or the saved ``[channels.slack] bot_token``."""
    import os

    try:
        from .config import load_config

        section = (load_config().get("channels") or {}).get("slack") or {}
    except Exception:  # noqa: BLE001 -- fall back to the environment
        section = {}
    from .notifications import _resolve_env_ref

    saved = _resolve_env_ref(section.get("bot_token")) if isinstance(section.get("bot_token"), str) else None
    return (os.environ.get("SLACK_BOT_TOKEN") or saved or "").strip()


def open_view(bot_token: str, trigger_id: str, view: dict[str, Any]) -> bool:
    """``views.open``: show a modal for the interaction ``trigger_id`` (3-second life)."""
    result = post_json(f"{SLACK_API}/views.open", {"trigger_id": trigger_id, "view": view},
                       token=bot_token)
    return bool(result and result.get("ok"))


def open_dm(bot_token: str, user_id: str) -> str | None:
    """The person's DM channel id with the app (``conversations.open``)."""
    result = post_json(f"{SLACK_API}/conversations.open", {"users": user_id}, token=bot_token)
    if not result or not result.get("ok"):
        return None
    channel = result.get("channel") or {}
    identifier = channel.get("id") if isinstance(channel, dict) else None
    return identifier if isinstance(identifier, str) else None


def post_message(bot_token: str, channel: str, text: str,
                 blocks: list[dict[str, Any]] | None = None,
                 thread_ts: str | None = None) -> str | None:
    """``chat.postMessage``; returns the message ``ts`` or ``None``."""
    payload: dict[str, Any] = {"channel": channel, "text": str(text)[:4000]}
    if blocks:
        payload["blocks"] = blocks[:50]
    if thread_ts:
        payload["thread_ts"] = thread_ts
    result = post_json(f"{SLACK_API}/chat.postMessage", payload, token=bot_token)
    if not result or not result.get("ok"):
        return None
    ts = result.get("ts")
    return ts if isinstance(ts, str) else None


def send_dm(bot_token: str, user_id: str, text: str,
            blocks: list[dict[str, Any]] | None = None) -> bool:
    channel = open_dm(bot_token, user_id)
    return bool(channel and post_message(bot_token, channel, text, blocks))


__all__ = [
    "ACTION_APPROVE",
    "ACTION_QUESTION",
    "ACTION_REJECT",
    "ACTION_UNDO",
    "CONFIRM_VIEW_CALLBACK",
    "confirm_view",
    "handle_view_submission",
    "open_view",
    "slack_bot_token",
    "SIGNATURE_TOLERANCE_SECONDS",
    "SLASH_VERBS",
    "approval_blocks",
    "decided_blocks",
    "decode_button_value",
    "digest_blocks",
    "encode_button_value",
    "handle_block_action",
    "handle_slash_command",
    "open_dm",
    "parse_form",
    "parse_interaction_body",
    "parse_slash_text",
    "post_message",
    "respond",
    "send_dm",
    "text_blocks",
    "verify_slack_signature",
]
