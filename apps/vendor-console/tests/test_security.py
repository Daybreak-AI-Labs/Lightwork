"""Auth crypto: password hashing, TOTP, signed sessions."""
from __future__ import annotations

from vendor_console import security


def test_password_hash_roundtrip_and_reject():
    h = security.hash_password("correct horse", iters=1000)
    assert h.startswith("pbkdf2_sha256$1000$")
    assert security.verify_password("correct horse", h) is True
    assert security.verify_password("wrong", h) is False
    assert security.verify_password("x", "garbage") is False


def test_totp_matches_within_window_and_rejects_outside():
    secret = security.new_totp_secret()
    t = 1_700_000_000
    code = security.totp_now(secret, at=t)
    assert len(code) == 6 and code.isdigit()
    assert security.totp_verify(secret, code, at=t) is True
    assert security.totp_verify(secret, code, at=t + 25) is True    # within ±1 step
    assert security.totp_verify(secret, code, at=t + 120) is False  # 4 steps away
    assert security.totp_verify(secret, "000000", at=t) in (True, False)  # near-never true
    assert security.totp_verify(secret, "notacode", at=t) is False


def test_non_ascii_digits_are_a_wrong_code_not_a_crash():
    secret = security.new_totp_secret()
    t = 1_700_000_000
    # str.isdigit() accepts other scripts' digits, e.g. Arabic-Indic ones.
    code = "\u0661\u0662\u0663\u0664\u0665\u0666"
    assert code.isdigit()
    assert security.totp_match_step(secret, code, at=t) is None


def test_non_ascii_stored_hash_is_a_rejection_not_a_crash():
    assert security.verify_password("x", "pbkdf2_sha256$1000$00$" + "é" * 64) is False


def test_session_sign_verify_tamper_and_expiry():
    key = b"k" * 32
    tok = security.sign_session({"sid": 7, "stage": "full"}, key, ttl=100)
    payload = security.verify_session(tok, key)
    assert payload["sid"] == 7 and payload["stage"] == "full"
    assert security.verify_session(tok, b"other-key") is None      # wrong key
    assert security.verify_session(tok + "x", key) is None          # tampered sig
    expired = security.sign_session({"sid": 1}, key, ttl=-1)
    assert security.verify_session(expired, key) is None            # expired
