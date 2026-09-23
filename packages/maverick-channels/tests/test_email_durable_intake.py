"""Local fake IMAP/SMTP transports over the actual channel journal."""
from __future__ import annotations

import asyncio
import email.message

import pytest
from maverick import world_model
from maverick_channels import delivery
from maverick_channels.email import EmailChannel


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    runs = []
    async def handler(msg):
        runs.append(msg.text)
        return "saved answer"
    channel = EmailChannel(handler, "imap.example.test", "bot@example.test", "fixture",
                           "smtp.example.test", "bot@example.test", "fixture", allowed_user_ids=["alice@example.test"])
    return channel, runs


def _mail():
    msg = email.message.EmailMessage()
    msg["From"], msg["Subject"] = "alice@example.test", "Review"
    msg.set_content("Question")
    return msg.as_bytes()


def _imap(monkeypatch, on_seen):
    class IMAP:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def login(self, *args):
            pass
        def select(self, *args):
            return "OK", [b"1"]
        def response(self, *args):
            return "UIDVALIDITY", [b"42"]
        def uid(self, command, *args):
            if command == "search":
                return "OK", [b"7"]
            if command == "fetch":
                return "OK", [(b"7", _mail())]
            assert command == "store" and args == (b"7", "+FLAGS", "\\Seen")
            on_seen()
            return "OK", []
    monkeypatch.setattr("maverick_channels.email.imaplib.IMAP4_SSL", IMAP)


def test_seen_ack_occurs_after_durable_intake_and_restart_recovers(environment, monkeypatch):
    channel, runs = environment
    def seen():
        rows = delivery.DeliveryJournal().store.list(limit=2)
        assert len(rows) == 1 and rows[0]["execution"] == "received"
        assert not runs
    _imap(monkeypatch, seen)
    rows = channel._fetch_unseen()
    assert rows[0]["external_id"] == "42:7" and not runs
    sent = []
    async def send(user_id, text, subject):
        sent.append((user_id, text, subject))
        channel._stop = True
    monkeypatch.setattr(channel, "send", send)
    monkeypatch.setattr(channel, "_fetch_unseen", list)
    async def no_sleep(*args):
        channel._stop = True
    monkeypatch.setattr("maverick_channels.email.asyncio.sleep", no_sleep)
    asyncio.run(channel.start())
    assert len(runs) == 1 and sent == [("alice@example.test", "saved answer", "Re: Review")]
    assert delivery.DeliveryJournal().get(rows[0]["id"])["delivery"] == "delivered"


def test_failed_intake_does_not_ack_seen(environment, monkeypatch):
    channel, runs = environment
    _imap(monkeypatch, lambda: pytest.fail("Acknowledged before durable intake"))
    monkeypatch.setattr(delivery.DeliveryJournal, "admit", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        channel._fetch_unseen()
    assert not runs


@pytest.mark.parametrize("phase", ["connect", "accepted_then_disconnect", "explicit_rejection"])
def test_smtp_failure_classification_retains_ambiguity(environment, monkeypatch, phase):
    channel, _ = environment
    import smtplib
    class SMTP:
        def __init__(self, *args, **kwargs):
            if phase == "connect":
                raise OSError("no connection")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def login(self, *args):
            pass
        def send_message(self, message):
            assert message["Message-ID"] == "<stable@fixture>"
            if phase == "explicit_rejection":
                raise smtplib.SMTPDataError(550, b"rejected")
            raise smtplib.SMTPServerDisconnected("lost after possible acceptance")
    monkeypatch.setattr("maverick_channels.email.smtplib.SMTP_SSL", SMTP)
    expected = smtplib.SMTPServerDisconnected if phase == "accepted_then_disconnect" else delivery.DeliveryNotSent
    with pytest.raises(expected):
        channel._send_sync("alice@example.test", "reply", "subject", message_id="<stable@fixture>")


def test_rotating_recovery_window_finds_later_records_and_preserves_failed_cursor(environment):
    channel, _ = environment
    journal = delivery.DeliveryJournal()
    for i in range(8):
        msg = delivery.IncomingMessage(user_id="alice@example.test", text=str(i), channel="email", message_id=str(i))
        journal.admit(msg, external_id=str(i), account=channel._account(), route={"subject": "Review"})
    first, checkpoint = journal.recovery_window(channel="email", account=channel._account(), limit=2)
    repeated, _ = journal.recovery_window(channel="email", account=channel._account(), limit=2)
    assert [row["id"] for row in first] == [row["id"] for row in repeated]
    found = set()
    for _ in range(4):
        rows, checkpoint = journal.recovery_window(channel="email", account=channel._account(), limit=2)
        found.update(row["id"] for row in rows)
        journal.finish_recovery_window(checkpoint)
    assert len(found) == 8
