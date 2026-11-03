"""Signed license keys: mint, verify, tamper, expiry."""
from __future__ import annotations

import pytest
from maverick import licensing


def _pair():
    return licensing.generate_keypair()


def test_roundtrip_preserves_payload():
    private_pem, public_pem = _pair()
    token = licensing.make_license(
        customer="Acme Corp", sku="pia_standalone", private_pem=private_pem,
        days=30, capabilities=["vendor_memory", "speed_story"])
    assert token.startswith("LW1.")
    payload = licensing.verify_license(token, public_pem)
    assert payload["customer"] == "Acme Corp"
    assert payload["sku"] == "pia_standalone"
    assert payload["capabilities"] == ["speed_story", "vendor_memory"]
    assert payload["expires_at"] > payload["issued_at"]


def test_tampered_payload_fails_signature():
    private_pem, public_pem = _pair()
    token = licensing.make_license(customer="Acme", sku="x",
                                   private_pem=private_pem)
    head, body, sig = token.split(".")
    # Flip a character inside the payload segment.
    forged = f"{head}.{body[:-2] + ('AA' if body[-2:] != 'AA' else 'BB')}.{sig}"
    with pytest.raises(licensing.LicenseError):
        licensing.verify_license(forged, public_pem)


def test_wrong_key_and_bad_format_fail():
    private_pem, _ = _pair()
    _, other_pub = _pair()
    token = licensing.make_license(customer="Acme", sku="x",
                                   private_pem=private_pem)
    with pytest.raises(licensing.LicenseError):
        licensing.verify_license(token, other_pub)
    with pytest.raises(licensing.LicenseError):
        licensing.verify_license("garbage", other_pub)
    with pytest.raises(licensing.LicenseError):
        licensing.verify_license("XX1.a.b", other_pub)


def test_expired_license_fails(monkeypatch):
    private_pem, public_pem = _pair()
    token = licensing.make_license(customer="Acme", sku="x",
                                   private_pem=private_pem, days=1)
    real_time = licensing.time.time
    monkeypatch.setattr(licensing.time, "time",
                        lambda: real_time() + 2 * 86400)
    with pytest.raises(licensing.LicenseError, match="expired"):
        licensing.verify_license(token, public_pem)


def test_keygen_creates_private_key_without_overwriting(tmp_path, monkeypatch):
    import os
    import stat
    import sys

    monkeypatch.setattr(sys, "argv", ["licensing", "keygen", "--out-dir", str(tmp_path)])
    assert licensing._cli() == 0
    private_path = tmp_path / "license_signing.pem"
    public_path = tmp_path / "license_pubkey.pem"
    private = private_path.read_text()
    public = public_path.read_text()
    if os.name != "nt":
        assert stat.S_IMODE(private_path.stat().st_mode) == 0o600
    token = licensing.make_license(customer="Acme", sku="test", private_pem=private)
    assert licensing.verify_license(token, public)["customer"] == "Acme"
    with pytest.raises(FileExistsError):
        licensing._cli()
    assert private_path.read_text() == private
    assert public_path.read_text() == public


def test_keygen_rejects_shared_output_directory(tmp_path, monkeypatch):
    import os
    import sys

    if os.name == "nt":
        pytest.skip("POSIX permission test")
    tmp_path.chmod(0o777)
    monkeypatch.setattr(sys, "argv", ["licensing", "keygen", "--out-dir", str(tmp_path)])
    with pytest.raises(PermissionError):
        licensing._cli()
    assert not (tmp_path / "license_signing.pem").exists()
