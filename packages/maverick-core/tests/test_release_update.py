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
    assert res.manifest.get("version") == "v0.1.7"   # parsed manifest returned for reuse


def test_bundle_catches_tampered_artifact(tmp_path, keys):
    priv, trust = keys
    art = _artifact(tmp_path, "maverick-macos-arm64")
    doc = R.build_manifest("v0.1.7", [art], min_from="v0.1.5", migrations=[],
                           private_key_hex=priv)
    (tmp_path / "manifest.json").write_text(json.dumps(doc))
    (tmp_path / "maverick-macos-arm64").write_bytes(b"swapped-malicious")  # tamper file
    res = R.verify_bundle(tmp_path, trusted_pubkeys=trust)
    assert not res.ok and any("hash mismatch" in i for i in res.issues)


def test_bundle_rejects_path_traversal_artifact(tmp_path, keys):
    priv, trust = keys
    # A signed manifest whose artifact name escapes the bundle dir must be
    # rejected before we hash/"bless" a file elsewhere on the host (zip-slip).
    art = {"name": "../evil", "sha256": "0" * 64, "size": 1}
    doc = R.build_manifest("v0.1.7", [art], min_from="v0.1.5", migrations=[],
                           private_key_hex=priv)
    (tmp_path / "manifest.json").write_text(json.dumps(doc))
    res = R.verify_bundle(tmp_path, trusted_pubkeys=trust)
    assert not res.ok and any("escapes bundle dir" in i for i in res.issues)


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


def _bundle(tmp_path, priv, *, version="v0.1.7", min_from="v0.1.5", migrations=None):
    art = _artifact(tmp_path, "maverick-macos-arm64")
    doc = R.build_manifest(version, [art], min_from=min_from,
                           migrations=migrations or [], private_key_hex=priv)
    (tmp_path / "manifest.json").write_text(json.dumps(doc))
    return tmp_path


def test_apply_runs_migrations_then_install_in_order(tmp_path, keys):
    priv, trust = keys
    d = _bundle(tmp_path, priv, migrations=["m6", "m7"])
    ran: list[str] = []
    res = R.apply_bundle(d, "v0.1.6", trusted_pubkeys=trust,
                         migrate=ran.append,
                         install=lambda _d, _m: ran.append("install"))
    assert res.ok and res.applied_migrations == ["m6", "m7"]
    assert ran == ["m6", "m7", "install"]                # migrations before install


def test_apply_dry_run_skips_migrations_and_install(tmp_path, keys):
    priv, trust = keys
    d = _bundle(tmp_path, priv, migrations=["m6"])
    touched: list[str] = []
    res = R.apply_bundle(d, "v0.1.6", trusted_pubkeys=trust,
                         migrate=touched.append,
                         install=lambda _d, _m: touched.append("install"),
                         dry_run=True)
    assert res.ok and res.dry_run and res.applied_migrations == ["m6"]
    assert touched == []                                 # nothing actually run


def test_apply_stops_at_first_failing_migration(tmp_path, keys):
    priv, trust = keys
    d = _bundle(tmp_path, priv, migrations=["m6", "boom", "m8"])
    installed: list[str] = []

    def _migrate(mig):
        if mig == "boom":
            raise RuntimeError("migration exploded")

    res = R.apply_bundle(d, "v0.1.6", trusted_pubkeys=trust, migrate=_migrate,
                         install=lambda _d, _m: installed.append("install"))
    assert not res.ok and "boom" in res.reason
    assert res.applied_migrations == ["m6"]              # stopped before m8
    assert installed == []                               # never installed on failure


def test_apply_refuses_a_tampered_bundle(tmp_path, keys):
    priv, trust = keys
    d = _bundle(tmp_path, priv, migrations=["m6"])
    (tmp_path / "maverick-macos-arm64").write_bytes(b"swapped")   # tamper post-manifest
    res = R.apply_bundle(d, "v0.1.6", trusted_pubkeys=trust,
                         migrate=lambda _m: None, install=lambda _d, _m: None)
    assert not res.ok and res.reason.startswith("verify:")


def test_apply_refuses_a_blocked_plan(tmp_path, keys):
    priv, trust = keys
    d = _bundle(tmp_path, priv, migrations=["m6"])
    res = R.apply_bundle(d, "v0.2.0", trusted_pubkeys=trust,   # downgrade
                         migrate=lambda _m: None, install=lambda _d, _m: None)
    assert not res.ok and res.reason.startswith("plan:")
