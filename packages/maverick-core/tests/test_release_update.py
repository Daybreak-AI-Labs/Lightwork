"""Governed release updates: offline manifest + bundle verification and safe
upgrade planning. See maverick/release_update.py."""
from __future__ import annotations

import json

import pytest
from maverick import entitlements as E
from maverick import release_update as R

pytest.importorskip("cryptography")


@pytest.fixture
def keys():
    priv, pub = E.new_keypair()
    return priv, [pub]


def _artifact(tmp_path, name, data=b"binary-bytes"):
    (tmp_path / name).write_bytes(data)
    return {"name": name, "sha256": R.sha256_file(tmp_path / name), "size": len(data)}


def test_manifest_sign_verify_and_tamper(keys):
    priv, trust = keys
    doc = R.build_manifest("v0.1.7", [], min_from="v0.1.5", migrations=["m1"],
                           private_key_hex=priv)
    ok, why = R.verify_manifest(doc, trust)
    assert ok and why == "ok"
    doc["version"] = "v9.9.9"  # tamper
    ok, why = R.verify_manifest(doc, trust)
    assert not ok and why == "bad_signature"


def test_bundle_verifies_signature_and_hashes(tmp_path, keys):
    priv, trust = keys
    art = _artifact(tmp_path, "maverick-macos-arm64")
    doc = R.build_manifest("v0.1.7", [art], min_from="v0.1.5", migrations=[],
                           private_key_hex=priv)
    (tmp_path / "manifest.json").write_text(json.dumps(doc))
    res = R.verify_bundle(tmp_path, trusted_pubkeys=trust)
    assert res.ok and res.version == "v0.1.7"


def test_bundle_catches_tampered_artifact(tmp_path, keys):
    priv, trust = keys
    art = _artifact(tmp_path, "maverick-macos-arm64")
    doc = R.build_manifest("v0.1.7", [art], min_from="v0.1.5", migrations=[],
                           private_key_hex=priv)
    (tmp_path / "manifest.json").write_text(json.dumps(doc))
    (tmp_path / "maverick-macos-arm64").write_bytes(b"swapped-malicious")  # tamper file
    res = R.verify_bundle(tmp_path, trusted_pubkeys=trust)
    assert not res.ok and any("hash mismatch" in i for i in res.issues)


def test_bundle_catches_missing_artifact(tmp_path, keys):
    priv, trust = keys
    art = {"name": "ghost", "sha256": "0" * 64, "size": 1}
    doc = R.build_manifest("v0.1.7", [art], min_from="v0.1.5", migrations=[],
                           private_key_hex=priv)
    (tmp_path / "manifest.json").write_text(json.dumps(doc))
    res = R.verify_bundle(tmp_path, trusted_pubkeys=trust)
    assert not res.ok and any("missing artifact" in i for i in res.issues)


def test_plan_upgrade_paths():
    m = {"version": "v0.1.7", "min_from": "v0.1.5", "migrations": ["m6", "m7"]}
    ok = R.plan_upgrade("v0.1.6", m)
    assert ok.ok and ok.migrations == ["m6", "m7"] and ok.to_version == "v0.1.7"

    assert not R.plan_upgrade("v0.1.7", m).ok            # no-op
    down = R.plan_upgrade("v0.2.0", m)
    assert not down.ok and down.downgrade                # downgrade blocked
    gap = R.plan_upgrade("v0.1.3", m)
    assert not gap.ok and "version gap" in gap.reason    # below min_from
