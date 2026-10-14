"""Customer email replies retain their task files and original thread."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext

import pytest
from maverick_channels.base import Reply
from maverick_channels.delivery import DeliveryNotSent
from maverick_channels.email import EmailChannel, _parse_references


def _channel():
    channel = object.__new__(EmailChannel)
    channel.smtp_user = "lightwork@example.com"
    channel._mail_cfg = {}
    channel.smtp_host = "smtp.example.com"
    channel.smtp_port = 465
    return channel


def test_send_delivers_only_frozen_native_files_and_receipts_metadata(monkeypatch):
    import maverick_channels.delivery as delivery

    data = b"Quarterly summary"
    item = {
        "filename": "summary.txt",
        "mime": "text/plain",
        "sha256": "a" * 64,
        "size_bytes": len(data),
        "source": {"artifact_id": 12, "version": 3},
    }
    reply = Reply("The result is attached.", ["summary.txt"], "<parent@example.com>")
    sent = {}

    async def saved_files(selected):
        assert selected == reply
        return [item]

    async def read_file(selected):
        assert selected == item
        return data

    async def send_chunk(key, payload, create):
        sent["key"] = key
        sent["payload"] = payload
        return await create()

    def send_sync(_recipient, text, subject, **kwargs):
        sent["message"] = (text, subject, kwargs)
        return "<delivery@example.com>"

    monkeypatch.setattr(delivery, "saved_reply_files", saved_files)
    monkeypatch.setattr(delivery, "read_reply_file", read_file)
    monkeypatch.setattr(delivery, "send_chunk", send_chunk)
    channel = _channel()
    monkeypatch.setattr(channel, "_send_sync", send_sync)

    result = asyncio.run(channel.send(
        "owner@example.com", reply, "Re: Results",
        references=["<older@example.com>", "<parent@example.com>"],
    ))

    assert result == "<delivery@example.com>"
    assert sent["key"] == "email-0"
    text, subject, kwargs = sent["message"]
    assert (text, subject) == ("The result is attached.", "Re: Results")
    assert kwargs["in_reply_to"] == "<parent@example.com>"
    assert kwargs["references"] == ["<older@example.com>", "<parent@example.com>"]
    assert kwargs["attachments"] == [{**item, "data": data}]
    assert sent["payload"]["files"] == [{
        "filename": "summary.txt", "mime": "text/plain",
        "size_bytes": len(data), "sha256": "a" * 64,
    }]
    assert sent["payload"]["references"] == ["<older@example.com>", "<parent@example.com>"]


def test_send_refuses_reply_when_saved_file_is_missing(monkeypatch):
    import maverick_channels.delivery as delivery

    async def no_files(_reply):
        return []

    monkeypatch.setattr(delivery, "saved_reply_files", no_files)
    with pytest.raises(DeliveryNotSent, match="attachments are unavailable"):
        asyncio.run(_channel().send(
            "owner@example.com", Reply("Attached", ["missing.txt"]),
        ))


def test_smtp_message_contains_attachment_and_safe_reply_thread(monkeypatch):
    import maverick.microsoft_mail as mail_auth
    import maverick_channels.email as email_module

    sent = {}

    class SMTP:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def send_message(self, message):
            sent["message"] = message
            return {}

    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", lambda *_args, **_kwargs: SMTP())
    monkeypatch.setattr(mail_auth, "authenticate_smtp", lambda *_args: None)
    monkeypatch.setattr(mail_auth, "mail_effect", lambda *_args: nullcontext())
    channel = _channel()

    result = channel._send_sync(
        "owner@example.com", "Finished", "Re: Request",
        message_id="<delivery@example.com>",
        in_reply_to="<parent@example.com>",
        references=["<older@example.com>", "<parent@example.com>"],
        attachments=[{
            "filename": "summary.txt", "mime": "text/plain", "data": b"Quarterly summary",
        }],
    )

    message = sent["message"]
    assert result == "<delivery@example.com>"
    assert message["In-Reply-To"] == "<parent@example.com>"
    assert message["References"] == "<older@example.com> <parent@example.com>"
    parts = list(message.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == "summary.txt"
    assert parts[0].get_payload(decode=True) == b"Quarterly summary"


def test_smtp_message_drops_malformed_reply_reference(monkeypatch):
    import maverick.microsoft_mail as mail_auth
    import maverick_channels.email as email_module

    sent = {}

    class SMTP:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def send_message(self, message):
            sent["message"] = message
            return {}

    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", lambda *_args, **_kwargs: SMTP())
    monkeypatch.setattr(mail_auth, "authenticate_smtp", lambda *_args: None)
    monkeypatch.setattr(mail_auth, "mail_effect", lambda *_args: nullcontext())

    _channel()._send_sync(
        "owner@example.com", "Finished", "Lightwork",
        in_reply_to="<parent@example.com>\r\nBcc: attacker@example.com",
    )

    message = sent["message"]
    assert message["In-Reply-To"] is None
    assert message["References"] is None


def test_reference_parser_rejects_non_id_text_and_bounds_history():
    assert _parse_references("<one@example.com> Bcc: hidden@example.com") == []
    values = " ".join(f"<id-{index}@example.com>" for index in range(30))
    references = _parse_references(values)
    assert len(references) == 20
    assert references[0] == "<id-10@example.com>"
    assert references[-1] == "<id-29@example.com>"
