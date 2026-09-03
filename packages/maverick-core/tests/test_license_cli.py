"""`maverick license` — the operator surface over signed-license entitlements.

The Click group wraps ``maverick.entitlements`` primitives (build_license_payload
+ sign_license + resolve). The publisher PRIVATE key is a secret sourced from
MAVERICK_LICENSE_PRIVATE_KEY (env/secret provider), never config and never the
repo, and ``issue`` must never echo it. Re-issuing with a different ``--suite``
(the ``set`` alias) is how a deployment's features change, so it must change the
resolved entitlement.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from maverick.cli import main


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    for var in (
        "MAVERICK_TENANT",
        "MAVERICK_LICENSE_PRIVATE_KEY",
        "MAVERICK_LICENSE_PUBKEYS",
        "MAVERICK_LICENSE_ENFORCE",
    ):
        monkeypatch.delenv(var, raising=False)


def _keypair():
    from maverick.entitlements import new_keypair
    return new_keypair()


def _run(*args):
    return CliRunner().invoke(main, ["license", *args])


def test_keygen_prints_keypair_once_with_secret_warning():
    res = _run("keygen")
    assert res.exit_code == 0, res.output
    assert "private key" in res.output.lower()
    assert "public key" in res.output.lower()
    # The whole point: tell the operator to vault the private key.
    assert "secret manager" in res.output.lower()
    assert "MAVERICK_LICENSE_PRIVATE_KEY" in res.output


def test_issue_without_signing_key_errors(monkeypatch):
    # No MAVERICK_LICENSE_PRIVATE_KEY -> actionable error, not a stack trace.
    res = _run("issue", "--customer", "acme")
    assert res.exit_code == 2
    assert "MAVERICK_LICENSE_PRIVATE_KEY" in res.output


def test_issue_writes_a_verifiable_signed_license(monkeypatch, tmp_path):
    priv, pub = _keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_PRIVATE_KEY", priv)
    out = tmp_path / "license.json"
    res = _run("issue", "--customer", "acme", "--tier", "gold",
               "--suite", "fleet", "--out", str(out))
    assert res.exit_code == 0, res.output
    assert out.exists()

    doc = json.loads(out.read_text("utf-8"))
    from maverick.entitlements import verify_license
    ok, why = verify_license(doc, [pub])
    assert ok, why
    assert doc["customer"] == "acme"
    assert doc["tier"] == "gold"
    assert doc["suites"] == ["fleet"]
    # The signing key is a secret: it must never appear in the CLI output.
    assert priv not in res.output


def test_issue_never_prints_the_private_key_even_to_stdout(monkeypatch):
    priv, _pub = _keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_PRIVATE_KEY", priv)
    res = _run("issue", "--customer", "acme", "--stdout")
    assert res.exit_code == 0, res.output
    # The signed doc is printed, but never the private key material.
    assert priv not in res.output
    assert '"sig"' in res.output


def test_set_alias_changes_the_resolved_entitlement(monkeypatch, tmp_path):
    # "set" is issue under another verb: re-issuing with an add-on suite must
    # flip the resolved entitlement (the go-live "control that tenant's features"
    # requirement).
    priv, pub = _keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_PRIVATE_KEY", priv)
    out = tmp_path / "license.json"

    from maverick.entitlements import load_entitlements

    res = _run("issue", "--customer", "acme", "--tier", "gold", "--out", str(out))
    assert res.exit_code == 0, res.output
    before = load_entitlements(str(out), trusted_pubkeys=[pub])
    assert before.suite_enabled("fleet") is False

    res = _run("set", "--customer", "acme", "--tier", "gold",
               "--suite", "fleet", "--out", str(out))
    assert res.exit_code == 0, res.output
    after = load_entitlements(str(out), trusted_pubkeys=[pub])
    assert after.suite_enabled("fleet") is True


def test_show_reports_the_resolved_entitlement(monkeypatch, tmp_path):
    priv, pub = _keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_PRIVATE_KEY", priv)
    monkeypatch.setenv("MAVERICK_LICENSE_PUBKEYS", pub)  # trust anchor for show
    out = tmp_path / "license.json"
    assert _run("issue", "--customer", "acme", "--tier", "gold",
                "--out", str(out)).exit_code == 0

    res = _run("show", "--file", str(out))
    assert res.exit_code == 0, res.output
    assert "acme" in res.output
    assert "gold" in res.output
    # Gold unlocks external_agents; platinum-only multi_tenant stays off.
    assert "external_agents" in res.output


def test_show_exits_nonzero_when_untrusted(monkeypatch, tmp_path):
    # A license signed by a key the deployment does not trust grants nothing.
    priv, _pub = _keypair()
    monkeypatch.setenv("MAVERICK_LICENSE_PRIVATE_KEY", priv)
    out = tmp_path / "license.json"
    assert _run("issue", "--customer", "acme", "--out", str(out)).exit_code == 0
    # No trust anchor configured, and an explicit bogus pubkey -> not licensed.
    res = _run("show", "--file", str(out), "--pubkey", "bb" * 32)
    assert res.exit_code == 1, res.output
