from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from maverick import windows_installer_verification as verification

TAURI_FIXTURES = json.loads((Path(__file__).parent / "fixtures/tauri_cli_signatures.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture", TAURI_FIXTURES, ids=lambda value: value["filename"])
def test_actual_tauri_cli_signatures_and_mutations(tmp_path, fixture):
    # Public material produced by the actual Rust-backed Tauri 2.11.4 CLI,
    # retained for interoperability. No private signing key is checked in.
    path = tmp_path / fixture["filename"]
    payload = base64.b64decode(fixture["payload_base64"])
    path.write_bytes(payload)
    assert verification.verify_minisign(path, fixture["public_key"], fixture["signature"]) == hashlib.sha256(payload).hexdigest()
    envelope = base64.b64decode(fixture["signature"]).decode("utf-8").splitlines()
    for index, replacement in ((2, envelope[2] + " changed"), (3, base64.b64encode(bytes(64)).decode())):
        changed = [*envelope]
        changed[index] = replacement
        encoded = base64.b64encode("\n".join(changed).encode("utf-8")).decode()
        with pytest.raises(verification.InstallerVerificationError):
            verification.verify_minisign(path, fixture["public_key"], encoded)
    path.write_bytes(payload + b"changed installer bytes")
    with pytest.raises(verification.InstallerVerificationError):
        verification.verify_minisign(path, fixture["public_key"], fixture["signature"])


def signed(payload: bytes, *, prehashed: bool = True):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    identifier = b"fixture1"
    message = hashlib.blake2b(payload, digest_size=64).digest() if prehashed else payload
    signature = key.sign(message)
    encoded_key = base64.b64encode(b"untrusted comment: generated test key\n" + base64.b64encode(b"Ed" + identifier + public) + b"\n").decode()
    comment = b"test-only installer"
    envelope = b"\n".join([b"untrusted comment: generated test signature",
                             base64.b64encode((b"ED" if prehashed else b"Ed") + identifier + signature),
                             b"trusted comment: " + comment,
                             base64.b64encode(key.sign(signature + comment))])
    return encoded_key, base64.b64encode(envelope).decode()


@pytest.mark.parametrize("prehashed", [False, True])
def test_retained_installer_requires_real_signature_on_exact_bytes(tmp_path, prehashed):
    path = tmp_path / "installer.msi"
    path.write_bytes(b"generated fixture installer bytes")
    public, signature = signed(path.read_bytes(), prehashed=prehashed)
    assert verification.verify_minisign(path, public, signature) == hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(verification.InstallerVerificationError, match="signature"):
        verification.verify_minisign(path, public, signature)


def test_trusted_comment_and_wrong_key_cannot_be_substituted(tmp_path):
    path = tmp_path / "installer.msi"
    path.write_bytes(b"generated fixture")
    public, signature = signed(path.read_bytes())
    changed = base64.b64encode(base64.b64decode(signature).replace(b"test-only installer", b"other installer")).decode()
    with pytest.raises(verification.InstallerVerificationError):
        verification.verify_minisign(path, public, changed)
    other, _ = signed(path.read_bytes())
    with pytest.raises(verification.InstallerVerificationError):
        verification.verify_minisign(path, other, signature)
