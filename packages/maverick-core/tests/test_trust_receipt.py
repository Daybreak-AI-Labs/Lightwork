"""Trust receipts: bearer-verifiable evidence for one audited decision.

Every test here runs the production signer against the standalone verifier,
so a drift in either canonicalisation shows up as a failing test rather than
as a false accusation against an honest customer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from maverick import trust_receipt as tr  # noqa: E402
from maverick import trust_receipt_verify as tv  # noqa: E402

DAY = "2026-01-01"


# ---- fixtures ---------------------------------------------------------------

@pytest.fixture
def audit_dir(tmp_path):
    d = tmp_path / "audit"
    d.mkdir()
    return d


def _write_chain(audit_dir: Path, day: str, rows: list[dict]) -> Path:
    """Write a genuinely signed day-file using the production signer."""
    from maverick.audit.signing import AuditSigner
    path = audit_dir / f"{day}.ndjson"
    signer = AuditSigner(path)
    for row in rows:
        assert signer.write(dict(row))
    return path


def _rows() -> list[dict]:
    return [
        {"ts": 1.0, "kind": "tool_call", "agent": "worker-0-abc123",
         "name": "read_file", "input_summary": "..."},
        {"ts": 2.0, "kind": "governance_denied", "agent": "worker-0-abc123",
         "tool": "wire_transfer", "principal": "agent:finance-1",
         "rule": "deny_actions", "reason": "wire_transfer is denied by policy"},
        {"ts": 3.0, "kind": "tool_result", "agent": "worker-0-abc123",
         "name": "read_file", "status": "ok", "output_summary": "..."},
    ]


def _hashes(path: Path) -> list[str]:
    return [json.loads(ln)["hash"] for ln in path.read_text().splitlines() if ln.strip()]


def _key() -> str:
    from maverick.attestation import publisher_key
    return publisher_key()[1]


def _resign(receipt: dict) -> dict:
    """Re-sign an edited receipt with the real key (an insider edit)."""
    return tr.sign({k: v for k, v in receipt.items() if k != "signature"})


def _claim(result, name: str):
    return next(c for c in result.claims if c.name == name)


@pytest.fixture
def denial_receipt(audit_dir):
    path = _write_chain(audit_dir, DAY, _rows())
    denial = _hashes(path)[1]
    return tr.sign(tr.build(audit_dir=audit_dir, day=DAY, row_hash=denial, now=1767225600.0))


# ---- happy paths ------------------------------------------------------------

def test_a_receipt_verifies_against_the_out_of_band_key(denial_receipt):
    result = tv.verify(denial_receipt, trusted_keys=[_key()])
    assert result.ok, result.reason
    assert _claim(result, "row_integrity").status == tv.HOLDS
    assert _claim(result, "claim_consistency").status == tv.HOLDS
    assert _claim(result, "chain_linkage").status == tv.HOLDS
    # No checkpoint and no anchor ledger: authentic and chained to a tip the
    # issuer computed, but nothing committed to that tip.
    assert _claim(result, "commitment").status == tv.INDETERMINATE
    assert result.depth == tv.DEPTH_SIGNED


def test_the_claim_is_derived_from_the_row(denial_receipt):
    assert denial_receipt["claim"] == {
        "kind": "governance_denied", "decision": "denied", "tool": "wire_transfer",
        "occurred_at": 2.0, "principal": "agent:finance-1",
        "agent": "worker-0-abc123", "rule": "deny_actions",
        "reason": "wire_transfer is denied by policy",
    }
    assert denial_receipt["locator"]["position"] == 2
    assert denial_receipt["chain"]["row_count"] == 3
    assert len(denial_receipt["chain"]["suffix_ndjson"]) == 1


def test_the_row_hash_rule_matches_the_production_signer(audit_dir):
    path = _write_chain(audit_dir, DAY, _rows())
    for line in path.read_text().splitlines():
        row = json.loads(line)
        assert tv.audit_row_hash(row) == row["hash"]
        assert tv.key_id_of(_key()) == row["key_id"]


def test_the_anchor_ledger_commits_a_closed_day(audit_dir):
    from maverick.audit.signing import ensure_anchors
    path = _write_chain(audit_dir, DAY, _rows())
    assert ensure_anchors(audit_dir) >= 1
    receipt = tr.sign(tr.build(audit_dir=audit_dir, day=DAY, row_hash=_hashes(path)[1]))
    assert receipt["commitment"]["source"] == "anchor_ledger"
    result = tv.verify(receipt, trusted_keys=[_key()])
    assert result.ok, result.reason
    # Issuer-signed, co-located: chained, never anchored.
    assert result.depth == tv.DEPTH_CHAINED


def test_a_checkpoint_reaches_anchored_only_with_something_external(audit_dir, tmp_path):
    from maverick.audit.signing import ensure_anchors
    path = _write_chain(audit_dir, DAY, _rows())
    ensure_anchors(audit_dir)
    ckpt = tmp_path / "checkpoints"
    receipt = tr.sign(tr.build(audit_dir=audit_dir, day=DAY, row_hash=_hashes(path)[1],
                               checkpoint_dir=ckpt))
    commitment = receipt["commitment"]
    assert commitment["source"] == "checkpoint"
    assert commitment["record"]["tip_hash"] == receipt["chain"]["tip_hash"]
    assert commitment["record"]["row_count"] == receipt["chain"]["row_count"]

    alone = tv.verify(receipt, trusted_keys=[_key()])
    assert alone.ok and alone.depth == tv.DEPTH_CHAINED

    by_digest = tv.verify(receipt, trusted_keys=[_key()],
                          anchor_digest=commitment["raw_sha256"])
    assert by_digest.ok and by_digest.depth == tv.DEPTH_ANCHORED

    by_file = tv.verify(receipt, trusted_keys=[_key()], checkpoint_dir=ckpt)
    assert by_file.ok and by_file.depth == tv.DEPTH_ANCHORED


def test_a_receipt_without_the_suffix_is_signed_depth_only(audit_dir):
    path = _write_chain(audit_dir, DAY, _rows())
    receipt = tr.sign(tr.build(audit_dir=audit_dir, day=DAY, row_hash=_hashes(path)[1],
                               include_suffix=False))
    result = tv.verify(receipt, trusted_keys=[_key()])
    assert result.ok
    assert _claim(result, "chain_linkage").status == tv.INDETERMINATE
    assert result.depth == tv.DEPTH_SIGNED


# ---- provenance: fail closed ------------------------------------------------

def test_no_trusted_key_fails_closed(denial_receipt):
    result = tv.verify(denial_receipt, trusted_keys=[])
    assert not result.ok
    assert "trusted public key is required" in result.reason


def test_a_wrong_key_fails_provenance(denial_receipt):
    result = tv.verify(denial_receipt, trusted_keys=["ab" * 32])
    assert not result.ok
    assert "not a trusted key" in result.reason


def test_an_attacker_cannot_substitute_their_own_key(denial_receipt):
    """A receipt re-signed by someone else carries a key the verifier does
    not trust, however valid the signature is over the edited contents."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    forged = json.loads(json.dumps(denial_receipt))
    forged["claim"]["decision"] = "executed"
    body = {k: v for k, v in forged.items() if k != "signature"}
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes_raw()
    forged = {**body, "signature": {
        "pubkey": pub.hex(), "key_id": tv.key_id_of(pub.hex()),
        "sig": priv.sign(tv.canonical_bundle_bytes(body)).hex()}}
    result = tv.verify(forged, trusted_keys=[_key()])
    assert not result.ok
    assert "provenance" in result.reason


# ---- tamper variants (insider re-signs with the real key) -------------------

def test_a_drifted_claim_fails_even_when_resigned(denial_receipt):
    edited = json.loads(json.dumps(denial_receipt))
    edited["claim"]["decision"] = "executed"
    result = tv.verify(_resign(edited), trusted_keys=[_key()])
    assert not result.ok
    assert _claim(result, "claim_consistency").status == tv.FAILS


def test_an_edited_row_fails_integrity(denial_receipt):
    edited = json.loads(json.dumps(denial_receipt))
    edited["row"]["reason"] = "approved after all"
    result = tv.verify(_resign(edited), trusted_keys=[_key()])
    assert not result.ok
    assert _claim(result, "row_integrity").status == tv.FAILS
    assert result.depth == tv.DEPTH_NONE


def test_an_edited_following_row_breaks_the_chain(denial_receipt):
    edited = json.loads(json.dumps(denial_receipt))
    nxt = json.loads(edited["chain"]["suffix_ndjson"][0])
    nxt["status"] = "error"
    edited["chain"]["suffix_ndjson"][0] = json.dumps(nxt)
    result = tv.verify(_resign(edited), trusted_keys=[_key()])
    assert not result.ok
    assert _claim(result, "chain_linkage").status == tv.FAILS


def test_a_row_count_that_disagrees_with_the_suffix_fails(denial_receipt):
    edited = json.loads(json.dumps(denial_receipt))
    edited["chain"]["row_count"] += 1
    result = tv.verify(_resign(edited), trusted_keys=[_key()])
    assert not result.ok
    assert _claim(result, "chain_linkage").status == tv.FAILS


def test_a_mismatched_out_of_band_digest_fails(audit_dir, tmp_path):
    from maverick.audit.signing import ensure_anchors
    path = _write_chain(audit_dir, DAY, _rows())
    ensure_anchors(audit_dir)
    receipt = tr.sign(tr.build(audit_dir=audit_dir, day=DAY, row_hash=_hashes(path)[1],
                               checkpoint_dir=tmp_path / "checkpoints"))
    result = tv.verify(receipt, trusted_keys=[_key()], anchor_digest="00" * 32)
    assert not result.ok
    assert _claim(result, "commitment").status == tv.FAILS


# ---- minter honesty ---------------------------------------------------------

def test_minting_refuses_a_broken_chain(audit_dir):
    path = _write_chain(audit_dir, DAY, _rows())
    denial = _hashes(path)[1]
    text = path.read_text()
    path.write_text(text.replace("wire_transfer is denied", "wire_transfer is fine"))
    with pytest.raises(ValueError, match="broken chain"):
        tr.build(audit_dir=audit_dir, day=DAY, row_hash=denial)


def test_minting_never_mutates_the_evidence(audit_dir, tmp_path):
    path = _write_chain(audit_dir, DAY, _rows())
    before = path.read_bytes()
    out = tr.export(tmp_path / "r.json", audit_dir=audit_dir, day=DAY,
                    row_hash=_hashes(path)[1])
    assert out.exists()
    assert path.read_bytes() == before


def test_an_unknown_row_is_a_lookup_error(audit_dir):
    _write_chain(audit_dir, DAY, _rows())
    with pytest.raises(LookupError):
        tr.build(audit_dir=audit_dir, day=DAY, row_hash="00" * 32)
    with pytest.raises(LookupError):
        tr.build(audit_dir=audit_dir, day="2025-12-31", row_hash="00" * 32)


def test_derive_claim_names_the_decision():
    assert tv.derive_claim({"kind": "governance_denied", "tool": "x",
                            "reason": "human approval not granted"})["decision"] \
        == "require_human_not_granted"
    assert tv.derive_claim({"kind": "tool_call", "name": "x"})["decision"] == "executed"
    assert tv.derive_claim({"kind": "capability_denied", "tool": "x"})["decision"] == "denied"
    assert tv.derive_claim({"kind": "autonomy_gated", "tool": "x"})["decision"] == "gated"


# ---- standalone verifier ----------------------------------------------------

def test_the_verifier_source_is_self_contained_and_pinned():
    import hashlib
    text, digest = tr.verifier_source()
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == digest
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "maverick" not in stripped, f"leaked dependency: {stripped}"
        assert not stripped.startswith("from ."), f"relative import: {stripped}"


def test_the_standalone_verifier_runs_as_its_own_program(audit_dir, tmp_path, capsys):
    path = _write_chain(audit_dir, DAY, _rows())
    out = tr.export(tmp_path / "r.json", audit_dir=audit_dir, day=DAY,
                    row_hash=_hashes(path)[1])
    assert tv.main([str(out), "--key", _key()]) == 0
    assert "VERIFIED" in capsys.readouterr().out
    assert tv.main([str(out), "--key", "ab" * 32]) == 1
    assert tv.main([str(tmp_path / "missing.json"), "--key", "ab" * 32]) == 2
    capsys.readouterr()  # drain the text reports before the JSON run
    assert tv.main([str(out), "--key", _key(), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_the_cli_mints_verifies_and_exports(audit_dir, tmp_path):
    from click.testing import CliRunner
    from maverick.cli import main
    path = _write_chain(audit_dir, DAY, _rows())
    out = tmp_path / "r.json"
    runner = CliRunner()
    minted = runner.invoke(main, ["attest", "receipt", "mint", DAY, _hashes(path)[1],
                                  "-o", str(out), "--audit-dir", str(audit_dir)])
    assert minted.exit_code == 0, minted.output
    assert "denied wire_transfer" in minted.output
    verified = runner.invoke(main, ["attest", "receipt", "verify", str(out),
                                    "--key", _key()])
    assert verified.exit_code == 0, verified.output
    assert "VERIFIED" in verified.output
    rejected = runner.invoke(main, ["attest", "receipt", "verify", str(out)])
    assert rejected.exit_code != 0
    exported = runner.invoke(main, ["attest", "receipt", "export-verifier",
                                    str(tmp_path / "verify.py")])
    assert exported.exit_code == 0, exported.output
    assert (tmp_path / "verify.py").read_text() == tr.verifier_source()[0]
