"""Channels hardening regressions: the inbound size cap."""
from __future__ import annotations


def test_incoming_message_truncates_oversized_text(monkeypatch):
    monkeypatch.setenv("MAVERICK_MAX_INBOUND_CHARS", "100")
    # Re-evaluate the cap (it's read per-construction).
    from maverick_channels.base import IncomingMessage
    big = "x" * 5000
    msg = IncomingMessage(user_id="u", text=big, channel="test")
    assert len(msg.text) < 5000
    assert "truncated by Lightwork inbound cap" in msg.text


def test_incoming_message_cap_disabled_with_zero(monkeypatch):
    monkeypatch.setenv("MAVERICK_MAX_INBOUND_CHARS", "0")
    from maverick_channels.base import IncomingMessage
    big = "y" * 5000
    msg = IncomingMessage(user_id="u", text=big, channel="test")
    assert msg.text == big  # cap disabled


def test_incoming_message_short_text_untouched(monkeypatch):
    monkeypatch.delenv("MAVERICK_MAX_INBOUND_CHARS", raising=False)
    from maverick_channels.base import IncomingMessage
    msg = IncomingMessage(user_id="u", text="hello", channel="test")
    assert msg.text == "hello"
