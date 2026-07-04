"""Signed approve/reject tokens for actionable channel approvals."""
from __future__ import annotations

import time

import pytest
from maverick.flow import approvals


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("MAVERICK_WEBHOOK_SECRET", "test-signing-secret")  # pragma: allowlist secret


def test_mint_verify_roundtrip():
    tok = approvals.mint_token("run-1", "approved")
    assert tok
    claim = approvals.verify_token(tok)
    assert claim == {"run_id": "run-1", "decision": "approved"}


def test_reject_token_carries_its_decision():
    claim = approvals.verify_token(approvals.mint_token("run-2", "rejected"))
    assert claim["decision"] == "rejected"


def test_tampered_token_is_rejected():
    tok = approvals.mint_token("run-1", "approved")
    body, _, sig = tok.partition(".")
    # flip the last hex char of the signature
    bad = body + "." + sig[:-1] + ("0" if sig[-1] != "0" else "1")
    assert approvals.verify_token(bad) is None


def test_swapped_body_is_rejected():
    # a body from a different run won't match the signature of this one
    other = approvals.mint_token("run-OTHER", "approved").partition(".")[0]
    sig = approvals.mint_token("run-1", "approved").partition(".")[2]
    assert approvals.verify_token(f"{other}.{sig}") is None


def test_expired_token_is_rejected():
    past = time.time() - 10
    tok = approvals.mint_token("run-1", "approved", ttl=1, now=past)
    assert approvals.verify_token(tok) is None            # expired ~9s ago
    assert approvals.verify_token(tok, now=past + 0.5)     # still valid at mint+0.5s


def test_invalid_decision_mints_nothing():
    assert approvals.mint_token("run-1", "maybe") is None


def test_no_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("MAVERICK_WEBHOOK_SECRET", raising=False)
    # no [webhooks] secret either -> mint + verify both fail closed
    assert approvals.mint_token("run-1", "approved") is None
    assert approvals.verify_token("anything.deadbeef") is None


def test_approval_links_build_both_urls():
    links = approvals.approval_links("https://ops.example.com/", "run-9")
    assert links["approve"].startswith("https://ops.example.com/flow/approve?token=")
    assert "/flow/approve?token=" in links["reject"]
    # each link's token verifies to the matching decision
    atok = links["approve"].split("token=")[1]
    rtok = links["reject"].split("token=")[1]
    assert approvals.verify_token(atok)["decision"] == "approved"
    assert approvals.verify_token(rtok)["decision"] == "rejected"
