"""Enforcement wiring for the product-operations layer: the opt-in gate
(default off), the require() chokepoint, the fleet_memory integration, and the
support-bundle export. See maverick/entitlements.py + fleet_memory.py."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from maverick import entitlements as E

pytest.importorskip("cryptography")

NOW = datetime(2026, 7, 2, tzinfo=timezone.utc)


def _gold(trust_priv):
    payload = dict(customer="Cedar Valley Bank", edition="enterprise", tier="gold",
                   suites=["fleet"], features=[], issued_at="2026-01-01T00:00:00Z",
                   expires_at="2027-01-01T00:00:00Z", grace_days=14, license_id="l1")
    return E.sign_license(payload, trust_priv)


def _license(trust_priv, *, customer="Cedar Valley Bank",
             issued="2026-06-01T00:00:00Z", tier="gold"):
    payload = dict(customer=customer, edition="enterprise", tier=tier,
                   suites=["fleet"], features=[], issued_at=issued,
                   expires_at="2027-01-01T00:00:00Z", grace_days=14,
                   license_id="lic_" + issued.replace(":", ""))
    return E.sign_license(payload, trust_priv)


def test_enforcement_is_off_by_default(monkeypatch):
    monkeypatch.delenv("MAVERICK_LICENSE_ENFORCE", raising=False)
    monkeypatch.setattr(E, "_license_cfg", dict)
    assert E.enforcing() is False
    # fail-open: even an unlicensed deployment runs paid features when not enforcing
    monkeypatch.setattr(E, "current", lambda refresh=False: E.Entitlements(status=E.UNLICENSED))
    assert E.require("fleet_memory") is True
    assert E.require_suite("fleet") is True


def test_require_gates_only_paid_under_enforcement(monkeypatch):
    monkeypatch.setenv("MAVERICK_LICENSE_ENFORCE", "1")
    assert E.enforcing() is True
    monkeypatch.setattr(E, "current", lambda refresh=False: E.Entitlements(status=E.UNLICENSED))
    assert E.require("fleet_memory") is False      # paid gated off
    assert E.require("some_core_feature") is True  # core still runs
    assert E.require_suite("fleet") is False


def test_enforcement_honours_a_valid_license(monkeypatch):
    priv, pub = E.new_keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_ENFORCE", "1")
    ent = E.resolve(_gold(priv), trusted_pubkeys=[pub], now=NOW)
    monkeypatch.setattr(E, "current", lambda refresh=False: ent)
    assert E.require("fleet_memory") is True
    assert E.require_suite("fleet") is True
    assert E.require("advanced_evolve") is False   # platinum feature, gold license


def test_config_knob_supplies_trust_and_enforce(monkeypatch):
    priv, pub = E.new_keypair()
    monkeypatch.delenv("MAVERICK_LICENSE_ENFORCE", raising=False)
    monkeypatch.delenv("MAVERICK_LICENSE_PUBKEYS", raising=False)
    monkeypatch.setattr(E, "_license_cfg",
                        lambda: {"enforce": True, "publisher_pubkeys": [pub]})
    assert E.enforcing() is True
    ent = E.resolve(_gold(priv), now=NOW)   # trust comes from config, not explicit
    assert ent.status == E.LICENSED and ent.allows("fleet_governance")


def test_refresh_no_api_is_a_noop(monkeypatch):
    monkeypatch.delenv("MAVERICK_LICENSE_API", raising=False)
    monkeypatch.setattr(E, "_license_cfg", dict)
    monkeypatch.setattr(E, "current", lambda refresh=False: E.Entitlements(status=E.UNLICENSED))
    res = E.refresh_from_server()
    assert res.ok is False and "no entitlement API" in res.reason


def test_refresh_network_error_is_fail_open(monkeypatch):
    monkeypatch.setattr(E, "current", lambda refresh=False: E.Entitlements(status=E.LICENSED))

    def _boom():
        raise OSError("connection refused")

    res = E.refresh_from_server("https://api.example/license", fetch=_boom)
    assert res.ok is False and "fetch failed" in res.reason
    assert res.status == E.LICENSED           # running entitlement untouched


def test_refresh_rejects_an_unverified_server_reply(monkeypatch, tmp_path):
    priv, _pub = E.new_keypair()             # signed, but by an UNTRUSTED key
    monkeypatch.setenv("MAVERICK_LICENSE_FILE", str(tmp_path / "license.json"))
    forged = _gold(priv)
    res = E.refresh_from_server("https://api.example/license",
                                trusted_pubkeys=["00" * 32], fetch=lambda: forged)
    assert res.ok is False and res.changed is False
    assert not (tmp_path / "license.json").exists()   # never persisted


def test_refresh_persists_a_verified_license(monkeypatch, tmp_path):
    priv, pub = E.new_keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_FILE", str(tmp_path / "license.json"))
    doc = _gold(priv)
    res = E.refresh_from_server("https://api.example/license",
                                trusted_pubkeys=[pub], fetch=lambda: doc)
    assert res.ok and res.changed
    saved = json.loads((tmp_path / "license.json").read_text())
    assert saved["customer"] == "Cedar Valley Bank" and saved["tier"] == "gold"


def test_verify_license_rejects_non_dict_without_crashing():
    # A hostile API / corrupt file returning valid-but-non-object JSON must fail
    # open, not AttributeError at the chokepoint.
    ok, why = E.verify_license(["not", "a", "dict"], ["aa" * 32])
    assert ok is False and why == "malformed"
    ent = E.resolve(["hostile", "array"], trusted_pubkeys=["aa" * 32])
    assert ent.status == E.UNVERIFIED and ent.tier == E.BASE_TIER


def test_resolve_fails_open_on_signed_but_malformed_expiry():
    priv, pub = E.new_keypair()
    doc = E.sign_license(dict(customer="X", tier="gold", suites=[], features=[],
                              issued_at="2026-01-01T00:00:00Z",
                              expires_at="not-a-date", grace_days=14), priv)
    ent = E.resolve(doc, trusted_pubkeys=[pub], now=NOW)
    assert ent.status == E.INVALID and ent.tier == E.BASE_TIER   # signed but unusable


def test_refresh_refuses_a_replayed_older_license(monkeypatch, tmp_path):
    priv, pub = E.new_keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_FILE", str(tmp_path / "license.json"))
    (tmp_path / "license.json").write_text(json.dumps(
        _license(priv, issued="2026-06-01T00:00:00Z")))          # installed floor
    older = _license(priv, issued="2026-01-01T00:00:00Z")        # replayed downgrade
    res = E.refresh_from_server("https://api.example/license",
                                trusted_pubkeys=[pub], fetch=lambda: older)
    assert res.ok is False and "rollback" in res.reason
    kept = json.loads((tmp_path / "license.json").read_text())
    assert kept["issued_at"] == "2026-06-01T00:00:00Z"           # floor untouched


def test_refresh_refuses_a_different_customer(monkeypatch, tmp_path):
    priv, pub = E.new_keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_FILE", str(tmp_path / "license.json"))
    (tmp_path / "license.json").write_text(json.dumps(
        _license(priv, customer="Cedar Valley Bank")))
    other = _license(priv, customer="Someone Else", issued="2027-06-01T00:00:00Z")
    res = E.refresh_from_server("https://api.example/license",
                                trusted_pubkeys=[pub], fetch=lambda: other)
    assert res.ok is False and "different customer" in res.reason


def test_refresh_allows_a_genuine_upgrade(monkeypatch, tmp_path):
    priv, pub = E.new_keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_FILE", str(tmp_path / "license.json"))
    (tmp_path / "license.json").write_text(json.dumps(
        _license(priv, tier="gold", issued="2026-01-01T00:00:00Z")))
    newer = _license(priv, tier="platinum", issued="2026-06-01T00:00:00Z")
    res = E.refresh_from_server("https://api.example/license",
                                trusted_pubkeys=[pub], fetch=lambda: newer)
    assert res.ok
    assert json.loads((tmp_path / "license.json").read_text())["tier"] == "platinum"


def test_refresh_refuses_a_file_scheme_api_url(monkeypatch, tmp_path):
    # a file:// api_url must not become a local-file read (SSRF/LFI); refresh
    # reports a fetch failure and persists nothing.
    monkeypatch.setenv("MAVERICK_LICENSE_FILE", str(tmp_path / "license.json"))
    monkeypatch.delenv("MAVERICK_LICENSE_API_TOKEN", raising=False)
    res = E.refresh_from_server("file:///etc/passwd", trusted_pubkeys=["aa" * 32])
    assert res.ok is False and "fetch failed" in res.reason
    assert not (tmp_path / "license.json").exists()
