"""Cryptographic approval gate for high-blast-radius self-changes.

The governed ladder gates ``code``/``weights`` behind a human. A self-settable
boolean is not a real control for a self-modifying agent, so these tests pin
that (a) a valid Ed25519 signature over (candidate, rung, payload) authorises,
(b) the signature is bound to the payload (an approval can't be replayed onto a
swapped one), (c) forgery/absence fails closed, and (d) with no keys configured
the legacy boolean path is untouched.
"""
from __future__ import annotations

import pytest
from maverick import approval_signing as ap
from maverick import self_improvement as si

ed25519 = pytest.importorskip(
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    reason="cryptography required for approval signing",
)
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _keypair():
    priv = ed25519.Ed25519PrivateKey.generate()
    priv_hex = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    return priv_hex, pub_hex


def _candidate(**kw):
    base = dict(rung="code", summary="s", baseline_score=0.5, candidate_score=0.9,
                samples=20, payload={"diff": "x"}, capability_widens=False,
                rollback="snap-1")
    base.update(kw)
    return si.Candidate(**base)


class TestSignVerify:
    def test_valid_signature_identifies_approver(self):
        priv, pub = _keypair()
        cand = _candidate()
        req = ap.ApprovalRequest.for_candidate(cand)
        sig = ap.sign_request(req, priv)
        assert ap.verify(req, sig, [pub]) == ap.key_id(pub)

    def test_wrong_key_does_not_verify(self):
        priv, _pub = _keypair()
        _priv2, pub2 = _keypair()
        req = ap.ApprovalRequest.for_candidate(_candidate())
        sig = ap.sign_request(req, priv)
        assert ap.verify(req, sig, [pub2]) is None

    def test_signature_bound_to_payload_toctou(self):
        # Approve a benign payload, then swap the payload: signature must break.
        priv, pub = _keypair()
        benign = _candidate(payload={"diff": "benign"})
        sig = ap.sign_request(ap.ApprovalRequest.for_candidate(benign), priv)
        malicious = _candidate(id=benign.id, payload={"diff": "rm -rf /"})
        req_mal = ap.ApprovalRequest.for_candidate(malicious)
        assert ap.verify(req_mal, sig, [pub]) is None

    def test_signature_bound_to_rung(self):
        priv, pub = _keypair()
        cand = _candidate(rung="code")
        sig = ap.sign_request(ap.ApprovalRequest.for_candidate(cand), priv)
        weights = _candidate(id=cand.id, rung="weights", payload=cand.payload)
        assert ap.verify(ap.ApprovalRequest.for_candidate(weights), sig, [pub]) is None

    def test_empty_signature_is_none(self):
        _priv, pub = _keypair()
        assert ap.verify(ap.ApprovalRequest.for_candidate(_candidate()), "", [pub]) is None

    def test_explicit_payload_sha256_overrides_payload(self):
        priv, pub = _keypair()
        digest = ap.payload_digest({"canonical": "form"})
        cand = _candidate(payload={"anything": 1}, payload_sha256=digest)
        req = ap.ApprovalRequest.for_candidate(cand)
        assert req.payload_sha256 == digest
        sig = ap.sign_request(req, priv)
        assert ap.verify(req, sig, [pub]) == ap.key_id(pub)


class TestEnforcement:
    def test_not_enforced_without_keys(self, monkeypatch):
        monkeypatch.delenv("MAVERICK_APPROVER_KEYS", raising=False)
        monkeypatch.delenv("MAVERICK_APPROVER_KEYS_DIR", raising=False)
        monkeypatch.delenv("MAVERICK_REQUIRE_SIGNED_APPROVAL", raising=False)
        monkeypatch.setattr(ap, "_config_approval", dict)
        assert ap.signing_enforced() is False

    def test_enforced_with_env_keys(self, monkeypatch):
        _priv, pub = _keypair()
        monkeypatch.setenv("MAVERICK_APPROVER_KEYS", pub)
        monkeypatch.setattr(ap, "_config_approval", dict)
        assert ap.signing_enforced() is True
        assert pub in ap.trusted_approver_keys()

    def test_require_flag_enforces_even_without_keys(self, monkeypatch):
        monkeypatch.delenv("MAVERICK_APPROVER_KEYS", raising=False)
        monkeypatch.delenv("MAVERICK_APPROVER_KEYS_DIR", raising=False)
        monkeypatch.setenv("MAVERICK_REQUIRE_SIGNED_APPROVAL", "1")
        monkeypatch.setattr(ap, "_config_approval", dict)
        assert ap.signing_enforced() is True

    def test_keys_loaded_from_dir_and_used_to_verify(self, tmp_path, monkeypatch):
        # A raw 32-byte *.pub in the approver dir (the audit key-dir format) is
        # picked up, and a signature under its key verifies end-to-end.
        priv = ed25519.Ed25519PrivateKey.generate()
        priv_hex = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ).hex()
        pub_bytes = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        (tmp_path / "approver.pub").write_bytes(pub_bytes)      # raw 32 bytes
        (tmp_path / "not-a-key.pub").write_bytes(b"too short")  # skipped
        monkeypatch.delenv("MAVERICK_APPROVER_KEYS", raising=False)
        monkeypatch.setenv("MAVERICK_APPROVER_KEYS_DIR", str(tmp_path))
        monkeypatch.setattr(ap, "_config_approval", dict)

        keys = ap.trusted_approver_keys()
        assert keys == [pub_bytes.hex()]  # only the valid 32-byte key
        assert ap.signing_enforced() is True
        cand = _candidate()
        cand.approval_signature = ap.sign_request(
            ap.ApprovalRequest.for_candidate(cand), priv_hex)
        assert ap.verify_candidate(cand) == ap.key_id(pub_bytes.hex())


class TestGateIntegration:
    """The controller's human-approval gate under enforcement."""

    def _controller(self):
        # Enabled, honest verifier, no ledger; humans required at code rung.
        return si.SelfImprovementController(
            min_improvement=0.0, max_auto_rung="policy",
            frozen_fn=lambda: False, audit_fn=lambda **k: None,
        )

    def test_boolean_ignored_when_signing_enforced(self, monkeypatch):
        _priv, pub = _keypair()
        monkeypatch.setattr(ap, "signing_enforced", lambda: True)
        monkeypatch.setattr(ap, "trusted_approver_keys", lambda: [pub])
        ctrl = self._controller()
        # approved=True but NO signature -> gate must still refuse.
        cand = _candidate(approved=True, approval_signature=None)
        verdict = ctrl.evaluate(cand)
        human = [g for g in verdict.gates if g.gate == "human_approval"][0]
        assert human.ok is False
        assert "signature" in human.reason

    def test_valid_signature_passes_gate_and_records_approver(self, monkeypatch):
        priv, pub = _keypair()
        monkeypatch.setattr(ap, "signing_enforced", lambda: True)
        monkeypatch.setattr(ap, "trusted_approver_keys", lambda: [pub])
        ctrl = self._controller()
        cand = _candidate(approved=False)  # Candidate is a mutable dataclass
        cand.approval_signature = ap.sign_request(ap.ApprovalRequest.for_candidate(cand), priv)
        verdict = ctrl.evaluate(cand)
        human = [g for g in verdict.gates if g.gate == "human_approval"][0]
        assert human.ok is True, verdict.blocking_reason
        assert verdict.approver_id == ap.key_id(pub)
        assert verdict.promote is True

    def test_forged_signature_fails_gate(self, monkeypatch):
        priv, _pub = _keypair()
        _priv2, pub2 = _keypair()          # controller trusts pub2 only
        monkeypatch.setattr(ap, "signing_enforced", lambda: True)
        monkeypatch.setattr(ap, "trusted_approver_keys", lambda: [pub2])
        ctrl = self._controller()
        cand = _candidate(approved=False)
        cand.approval_signature = ap.sign_request(  # signed by an untrusted key
            ap.ApprovalRequest.for_candidate(cand), priv)
        verdict = ctrl.evaluate(cand)
        human = [g for g in verdict.gates if g.gate == "human_approval"][0]
        assert human.ok is False
        assert verdict.promote is False

    def test_boolean_still_works_when_not_enforced(self, monkeypatch):
        # Backward compatibility: no keys -> the legacy boolean authorises.
        monkeypatch.setattr(ap, "signing_enforced", lambda: False)
        ctrl = self._controller()
        approved = ctrl.evaluate(_candidate(approved=True))
        refused = ctrl.evaluate(_candidate(approved=False))
        h_ok = [g for g in approved.gates if g.gate == "human_approval"][0]
        h_no = [g for g in refused.gates if g.gate == "human_approval"][0]
        assert h_ok.ok is True and h_no.ok is False
