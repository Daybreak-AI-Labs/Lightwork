"""Slack request signing, Block Kit cards, button and slash-command handling."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"  # pragma: allowlist secret -- Slack's doc example


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from maverick import config, world_model

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    import maverick.audit as audit

    monkeypatch.setattr(audit, "record", lambda kind, **p: True)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _sign(body: bytes, ts: float, secret: str = SECRET) -> tuple[str, str]:
    stamp = str(int(ts))
    digest = hmac.new(secret.encode(), f"v0:{stamp}:".encode() + body, hashlib.sha256).hexdigest()
    return stamp, "v0=" + digest


# ------------------------------------------------------------------ signing

def test_signature_accepts_slacks_documented_scheme():
    from maverick.slack_interactive import verify_slack_signature

    body = b"token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&command=%2Flightwork&text=status"
    now = 1531420618
    stamp, sig = _sign(body, now)
    assert verify_slack_signature(SECRET, timestamp=stamp, signature=sig, body=body, now=now)
    assert verify_slack_signature(SECRET, timestamp=stamp, signature=sig, body=body, now=now + 299)


def test_signature_rejects_tampering_wrong_secret_and_replay():
    from maverick.slack_interactive import verify_slack_signature

    body = b"payload=%7B%7D"
    now = 1_700_000_000
    stamp, sig = _sign(body, now)
    assert not verify_slack_signature(SECRET, timestamp=stamp, signature=sig, body=body + b"x", now=now)
    assert not verify_slack_signature("other", timestamp=stamp, signature=sig, body=body, now=now)
    assert not verify_slack_signature(SECRET, timestamp=stamp, signature=sig[:-1] + "0", body=body, now=now)
    assert not verify_slack_signature(SECRET, timestamp=stamp, signature=sig, body=body, now=now + 301)
    assert not verify_slack_signature(SECRET, timestamp=stamp, signature=sig, body=body, now=now - 301)
    assert not verify_slack_signature(SECRET, timestamp="soon", signature=sig, body=body, now=now)
    assert not verify_slack_signature(SECRET, timestamp=stamp, signature="v1=" + sig[3:], body=body, now=now)
    assert not verify_slack_signature("", timestamp=stamp, signature=sig, body=body, now=now)
    assert not verify_slack_signature(SECRET, timestamp=None, signature=None, body=body, now=now)


def test_form_and_payload_parsing():
    from maverick.slack_interactive import parse_form, parse_interaction_body, parse_slash_text

    body = urlencode({"payload": json.dumps({"type": "block_actions", "actions": []})}).encode()
    assert parse_interaction_body(body)["type"] == "block_actions"
    assert parse_interaction_body(b"payload=%7Bnot-json") == {}
    assert parse_interaction_body(b"payload=%5B1%5D") == {}
    form = parse_form(b"command=%2Flightwork&text=approve+12+confirm&user_id=U1")
    assert form == {"command": "/lightwork", "text": "approve 12 confirm", "user_id": "U1"}
    assert parse_slash_text("approve 12 confirm") == ("approve", "12 confirm")
    assert parse_slash_text("  Status ") == ("status", "")
    assert parse_slash_text("") == ("help", "")
    assert parse_slash_text("dance") == ("help", "")
    assert parse_slash_text("ask write the weekly report") == ("ask", "write the weekly report")


# --------------------------------------------------------------- Block Kit

def _card(action="browser.click", *, expires_in=300.0, irreversible=True):
    from maverick.approval_cards import build_approval_card

    return build_approval_card(
        action=action, summary="Approve the reviewed customer change",
        target_label="Customer settings", target={"page": "x"}, parameters={"p": 1},
        parameter_summary=["Press Save once"], actor="user:requester", workspace="t",
        role="operator", authority_versions={}, input_versions={}, credential_versions={},
        budget={"currency": "USD", "maximum": 2.5}, expires_at=time.time() + expires_in,
        known_effects=["The setting changes."], unresolved_assumptions=[],
        before="Unchanged.", after="Saved.", irreversible=irreversible,
    )


def _world(tmp_path):
    from maverick.world_model import WorldModel

    return WorldModel(tmp_path / "world.db")


def _park(world, *, risk="medium", card=None, **kw):
    from maverick.approval_cards import encode_approval_detail

    detail = encode_approval_detail(card) if card is not None else "plain"
    return world.create_approval("browser.click", risk=risk, detail=detail, **kw)


def test_approval_blocks_carry_the_card_and_buttons(tmp_path):
    from maverick.channel_approvals import card_content
    from maverick.slack_interactive import (
        ACTION_APPROVE,
        ACTION_QUESTION,
        ACTION_REJECT,
        approval_blocks,
        decode_button_value,
    )

    world = _world(tmp_path)
    card = _card()
    aid = _park(world, card=card)
    blocks = approval_blocks(card_content(world.get_approval(aid)), dashboard_url="https://lw.test/")
    assert blocks[0]["type"] == "header" and "Browser click" in blocks[0]["text"]["text"]
    text = json.dumps(blocks)
    for phrase in ("What it will do", "Press Save once", "*Risk*", "Medium risk", "Up to $2.50",
                   "Can't be undone", "*Decide by*", "https://lw.test/approvals#approval-"):
        assert phrase in text, phrase
    actions = blocks[-1]
    assert actions["type"] == "actions"
    assert [e["action_id"] for e in actions["elements"]] == [ACTION_APPROVE, ACTION_REJECT, ACTION_QUESTION]
    assert decode_button_value(actions["elements"][0]["value"]) == (aid, card["binding_sha256"])
    assert "confirm" not in actions["elements"][0]  # medium risk: one click


def test_high_risk_buttons_get_a_confirm_dialog_and_expired_cards_lose_buttons(tmp_path):
    from maverick.channel_approvals import card_content
    from maverick.slack_interactive import approval_blocks

    world = _world(tmp_path)
    aid = _park(world, risk="critical", card=_card())
    blocks = approval_blocks(card_content(world.get_approval(aid)))
    approve = blocks[-1]["elements"][0]
    dialog = approve["confirm"]
    assert dialog["title"]["type"] == "plain_text" and len(dialog["title"]["text"]) <= 100
    assert len(dialog["text"]["text"]) <= 300 and len(dialog["confirm"]["text"]) <= 30
    assert dialog["style"] in ("primary", "danger")
    assert "can't be undone" in dialog["text"]["text"].lower()
    expired = _park(world, card=_card(expires_in=-1))
    blocks = approval_blocks(card_content(world.get_approval(expired)))
    assert blocks[-1]["type"] == "section" and "expired" in blocks[-1]["text"]["text"].lower()


def test_decided_and_text_and_digest_blocks(tmp_path):
    from maverick.channel_approvals import card_content
    from maverick.slack_interactive import decided_blocks, digest_blocks, text_blocks

    world = _world(tmp_path)
    aid = _park(world, card=_card())
    content = card_content(world.get_approval(aid))
    done = decided_blocks(content, decision="approved", decided_by="user:alice",
                          effective=True, status="approved")
    assert all(b["type"] != "actions" for b in done)
    assert "Approved" in done[-1]["text"]["text"] and "user:alice" in done[-1]["text"]["text"]
    voted = decided_blocks(content, decision="approved", decided_by="user:alice",
                           effective=True, status="pending")
    assert "More approvers" in voted[-1]["text"]["text"]
    result = text_blocks("Result", "x" * 6000)
    assert result[0]["type"] == "header" and len(result) == 4
    digest = {"date_label": "Monday", "approvals": [{"id": 1, "action": "Wire", "risk_label": "High risk"}],
              "questions": [], "results": [], "spending": {"dollars": 1.5, "limit_dollars": 10}}
    blocks = digest_blocks(digest, dashboard_url="https://lw.test")
    assert "1 waiting for your decision" in blocks[1]["text"]["text"]
    assert "$1.50 of your $10.00" in blocks[-1]["elements"][0]["text"]


# ------------------------------------------------------------ interactions

def _payload(action_id, value, user="U1", url="https://hooks.slack.com/actions/x"):
    return {"type": "block_actions", "user": {"id": user}, "team": {"id": "T1"},
            "channel": {"id": "C1"}, "message": {"ts": "1.2"}, "response_url": url,
            "actions": [{"action_id": action_id, "block_id": "b", "value": value,
                         "type": "button", "action_ts": "1.3"}]}


def test_block_action_approves_and_rewrites_the_card(tmp_path):
    from maverick import channel_identities
    from maverick.slack_interactive import ACTION_APPROVE, encode_button_value, handle_block_action

    world = _world(tmp_path)
    card = _card()
    aid = _park(world, risk="high", card=card)
    channel_identities.link("slack", "U1", "user:alice", actor="admin")
    value = encode_button_value(aid, card["binding_sha256"])
    reply = handle_block_action(_payload(ACTION_APPROVE, value), world=world,
                                principal_for=lambda uid: channel_identities.principal_for("slack", uid))
    assert reply["replace_original"] is True
    assert world.get_approval(aid).status == "approved"
    assert "user:alice" in json.dumps(reply["blocks"])
    again = handle_block_action(_payload(ACTION_APPROVE, value), world=world,
                                principal_for=lambda uid: "user:alice")
    assert again["response_type"] == "ephemeral" and "no longer waiting" in again["text"]


def test_block_action_unlinked_question_and_foreign_actions(tmp_path):
    from maverick.slack_interactive import (
        ACTION_QUESTION,
        ACTION_REJECT,
        encode_button_value,
        handle_block_action,
    )

    world = _world(tmp_path)
    aid = _park(world, card=_card())
    value = encode_button_value(aid, None)
    unlinked = handle_block_action(_payload(ACTION_REJECT, value), world=world,
                                   principal_for=lambda uid: None)
    assert unlinked["response_type"] == "ephemeral" and "/lightwork link" in unlinked["text"]
    assert world.get_approval(aid).status == "pending"
    question = handle_block_action(_payload(ACTION_QUESTION, value), world=world,
                                   principal_for=lambda uid: "user:a")
    assert "question" in question["text"].lower() and world.get_approval(aid).status == "pending"
    assert handle_block_action(_payload("someone_elses_button", value), world=world,
                               principal_for=lambda uid: "user:a") is None
    assert handle_block_action({"type": "view_submission"}, world=world,
                               principal_for=lambda uid: "user:a") is None
    broken = handle_block_action(_payload(ACTION_REJECT, "not-a-number"), world=world,
                                 principal_for=lambda uid: "user:a")
    assert "no longer points" in broken["text"]


def test_slash_commands_end_to_end(tmp_path):
    from maverick import channel_identities
    from maverick.slack_interactive import handle_slash_command

    world = _world(tmp_path)
    card = _card()
    aid = _park(world, risk="high", card=card)
    resolver = lambda uid: channel_identities.principal_for("slack", uid)  # noqa: E731

    def run(text, user="U1", **extra):
        return handle_slash_command({"command": "/lightwork", "text": text, "user_id": user},
                                    world=world, principal_for=resolver, **extra)

    assert "/lightwork approve" in run("help")["text"]
    assert "not linked" in run("status")["text"]
    code = channel_identities.issue_link_code("user:alice", "slack")["code"]
    assert "wrong, used or expired" in run("link NOPE")["text"]
    assert "now linked" in run(f"link {code}")["text"]
    status = run("status")["text"]
    assert "1 waiting for your decision" in status and f"#{aid}" in status
    assert "Say which request" in run("approve")["text"]
    needs = run(f"approve {aid}")["text"]
    assert "confirm" in needs.lower() and world.get_approval(aid).status == "pending"
    assert "approved" in run(f"approve #{aid} confirm")["text"]
    assert world.get_approval(aid).status == "approved"
    assert "Tell Lightwork what you need" in run("ask")["text"]
    assert "not available" in run("ask do the thing")["text"]
    answered = run("ask do the thing", ask=lambda principal, goal: f"{principal} asked: {goal}")
    assert answered["text"] == "user:alice asked: do the thing"
    second = _park(world, card=_card())
    assert "rejected" in run(f"reject {second}")["text"]


# --------------------------------------------------------- outbound helpers

class _Response:
    def __init__(self, status=200, body=None, text="ok"):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Client:
    calls: list = []

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        _Client.calls.append((url, json, headers))
        return self._response


def test_post_message_open_dm_and_respond_use_the_guarded_client(monkeypatch):
    from maverick.slack_interactive import open_dm, post_message, respond, send_dm
    from maverick.tools import _ssrf

    _Client.calls = []
    responses = iter([
        _Response(body={"ok": True, "channel": {"id": "D1"}}),
        _Response(body={"ok": True, "ts": "9.9"}),
        _Response(text="ok"),
        _Response(status=500),
    ])
    monkeypatch.setattr(_ssrf, "safe_client", lambda url, **kw: _Client(next(responses)))
    assert open_dm("xoxb-fake", "U1") == "D1"
    assert post_message("xoxb-fake", "D1", "hi", [{"type": "divider"}]) == "9.9"
    assert respond("https://hooks.slack.com/actions/x", {"text": "hi"}) is True
    assert respond("https://hooks.slack.com/actions/x", {"text": "hi"}) is False
    assert _Client.calls[0][0].endswith("/conversations.open")
    assert _Client.calls[0][2]["Authorization"] == "Bearer xoxb-fake"
    assert _Client.calls[1][1]["blocks"] == [{"type": "divider"}]
    monkeypatch.setattr(_ssrf, "safe_client", lambda url, **kw: (_ for _ in ()).throw(OSError("down")))
    assert send_dm("xoxb-fake", "U1", "hi") is False
    assert respond("", {}) is False
