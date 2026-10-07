"""Inbound email must have a trusted, From-aligned sender result."""
from __future__ import annotations

import email

import pytest
from maverick.mail_auth import authenticated_from, valid_trusted_mail_server
from maverick_channels.email import EmailChannel


def _msg(*auth_results: str, sender: str = "alice@example.com"):
    raw = f"From: {sender}\r\n"
    for result in auth_results:
        raw += f"Authentication-Results: {result}\r\n"
    raw += "Subject: hi\r\n\r\nbody\r\n"
    return email.message_from_string(raw)


def test_only_trusted_server_dmarc_pass_for_exact_from_domain_is_admitted():
    assert authenticated_from(
        _msg("mx.example.com; spf=pass smtp.mailfrom=example.com; "
             "dmarc=pass header.from=example.com"), "mx.example.com",
    ) == "alice@example.com"


@pytest.mark.parametrize("result", [
    "mx.example.com; spf=pass smtp.mailfrom=evil.com; dkim=none",
    "mx.example.com; dkim=pass header.d=evil.com; dmarc=fail header.from=example.com",
    "mx.example.com; dmarc=pass header.from=evil.com",
    "other.example.com; dmarc=pass header.from=example.com",
    "mx.example.com; dmarc=none header.from=example.com",
])
def test_unaligned_missing_or_untrusted_results_are_refused(result):
    assert authenticated_from(_msg(result), "mx.example.com") == ""


def test_no_result_or_no_configured_server_is_refused():
    assert authenticated_from(_msg(), "mx.example.com") == ""
    assert authenticated_from(
        _msg("mx.example.com; dmarc=pass header.from=example.com"), "",
    ) == ""


def test_forged_lower_pass_cannot_override_topmost_failed_result():
    assert authenticated_from(_msg(
        "mx.example.com; dmarc=fail header.from=example.com",
        "mx.example.com; dmarc=pass header.from=example.com",
    ), "mx.example.com") == ""


def test_forged_header_above_own_server_result_is_refused():
    assert authenticated_from(_msg(
        "relay.evil.com; dmarc=pass header.from=example.com",
        "mx.example.com; dmarc=pass header.from=example.com",
    ), "mx.example.com") == ""


def test_multiple_from_headers_are_refused():
    msg = _msg("mx.example.com; dmarc=pass header.from=example.com")
    msg["From"] = "mallory@example.com"
    assert authenticated_from(msg, "mx.example.com") == ""


def test_server_identity_must_be_configured_before_email_channel_starts():
    assert valid_trusted_mail_server("mx.example.com")
    assert not valid_trusted_mail_server("mx..example.com")
    with pytest.raises(ValueError, match="incoming mail server identity"):
        EmailChannel(lambda _: None, "imap.example.com", "bot@example.com", "p",
                     "smtp.example.com", "bot@example.com", "p",
                     allowed_user_ids=["alice@example.com"])
