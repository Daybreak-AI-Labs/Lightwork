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


def test_fleet_memory_respects_entitlement(monkeypatch):
    from maverick import fleet_memory as FM
    monkeypatch.setenv("MAVERICK_FLEET_MEMORY", "1")   # base-enabled
    monkeypatch.setattr("maverick.entitlements.require", lambda f: False)
    assert FM.enabled() is False                        # gated off
    monkeypatch.setattr("maverick.entitlements.require", lambda f: True)
    assert FM.enabled() is True                         # granted


def test_support_export_writes_redacted_bundle(tmp_path):
    from maverick import support_bundle as S
    out, bundle = S.export(tmp_path / "support.json")
    assert out.exists()
    on_disk = json.loads(out.read_text())
    assert on_disk["correlation_id"].startswith("sup_")
    assert "entitlement" in on_disk and "status" in on_disk["entitlement"]
