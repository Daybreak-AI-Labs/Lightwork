"""Signed-license entitlements: fail-open core, fail-closed paid add-ons,
offline verification, grace window. See maverick/entitlements.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from maverick import entitlements as E

pytest.importorskip("cryptography")  # licenses are Ed25519-signed

NOW = datetime(2026, 7, 2, tzinfo=timezone.utc)


def _lic(**kw):
    base = dict(
        customer="Cedar Valley Bank", edition="enterprise", tier="gold",
        suites=["fleet"], features=[], seats=50,
        issued_at="2026-01-01T00:00:00Z", expires_at="2027-01-01T00:00:00Z",
        grace_days=14, license_id="lic_001",
    )
    base.update(kw)
    return base


@pytest.fixture
def keys():
    priv, pub = E.new_keypair()
    return priv, pub, [pub]


def test_valid_gold_license(keys):
    priv, _pub, trust = keys
    e = E.resolve(E.sign_license(_lic(), priv), trusted_pubkeys=trust, now=NOW)
    assert e.status == E.LICENSED and e.tier == "gold"
    assert e.allows("fleet_governance") and e.suite_enabled("fleet")
    assert e.allows("any_core_feature")            # core => fail-open
    assert not e.allows("advanced_evolve")         # platinum feature under gold
    assert not e.tier_at_least("platinum")


def test_unlicensed_runs_core_only(keys):
    _priv, _pub, trust = keys
    e = E.resolve(None, trusted_pubkeys=trust, now=NOW)
    assert e.status == E.UNLICENSED
    assert e.allows("any_core_feature")
    assert not e.allows("fleet_governance") and not e.suite_enabled("fleet")


def test_tamper_is_caught(keys):
    priv, _pub, trust = keys
    doc = E.sign_license(_lic(), priv)
    doc["tier"] = "platinum"  # flip a field after signing
    e = E.resolve(doc, trusted_pubkeys=trust, now=NOW)
    assert e.status == E.INVALID
    assert not e.allows("fleet_governance") and e.allows("core")


def test_untrusted_key_grants_nothing(keys):
    _priv, _pub, trust = keys
    rogue_priv, _rogue_pub = E.new_keypair()
    e = E.resolve(E.sign_license(_lic(), rogue_priv), trusted_pubkeys=trust, now=NOW)
    assert e.status == E.INVALID and e.reason == "untrusted_key"
    assert not e.allows("fleet_governance")


def test_no_trust_anchor_is_unverified(keys):
    priv, _pub, _trust = keys
    e = E.resolve(E.sign_license(_lic(), priv), trusted_pubkeys=[], now=NOW)
    assert e.status == E.UNVERIFIED
    assert e.allows("core") and not e.allows("fleet_governance")


def test_grace_window_keeps_paid_live(keys):
    priv, _pub, trust = keys
    e = E.resolve(E.sign_license(_lic(expires_at="2026-06-25T00:00:00Z"), priv),
                  trusted_pubkeys=trust, now=NOW)  # 7d expired, 14d grace
    assert e.status == E.GRACE and e.allows("fleet_governance")


def test_expired_beyond_grace_gates_paid_not_core(keys):
    priv, _pub, trust = keys
    e = E.resolve(E.sign_license(_lic(expires_at="2026-06-01T00:00:00Z"), priv),
                  trusted_pubkeys=trust, now=NOW)  # 31d expired > grace
    assert e.status == E.EXPIRED
    assert not e.allows("fleet_governance") and e.allows("core")


def test_platinum_unlocks_platinum(keys):
    priv, _pub, trust = keys
    e = E.resolve(E.sign_license(_lic(tier="platinum"), priv), trusted_pubkeys=trust, now=NOW)
    assert e.tier_at_least("platinum") and e.allows("advanced_evolve")
    assert e.allows("fleet_governance")  # higher tier includes lower


def test_file_roundtrip(tmp_path, keys):
    priv, _pub, trust = keys
    f = tmp_path / "license.json"
    f.write_text(json.dumps(E.sign_license(_lic(), priv)))
    e = E.load_entitlements(f, trusted_pubkeys=trust, now=NOW)
    assert e.status == E.LICENSED and e.summary().startswith("Cedar Valley Bank")


def test_missing_file_is_unlicensed(tmp_path, keys):
    _priv, _pub, trust = keys
    e = E.load_entitlements(tmp_path / "nope.json", trusted_pubkeys=trust, now=NOW)
    assert e.status == E.UNLICENSED and e.allows("core")
