from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import maverick.audit_evidence_verify as evidence_verifier
import pytest
from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from maverick.audit.events import AuditEvent, EventKind
from maverick.audit.evidence import AuditEvidenceError, build_evidence_bundle
from maverick.audit.writer import AuditLog
from maverick.audit_evidence_verify import EvidenceVerificationError, verify
from maverick.cli import main
from maverick.paths import reset_tenant, set_tenant


def _packet(*, day: str = "2099-01-01") -> tuple[dict, str, Ed25519PrivateKey]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_hex = public.hex()
    key_id = hashlib.sha256(public).hexdigest()[:16]
    rows = []
    previous = ""
    for event in (
        {"kind": "goal_start", "goal_id": 7, "event_id": "evt-1"},
        {"kind": "goal_end", "goal_id": 8, "event_id": "evt-2"},
    ):
        payload = {**event, "prev_hash": previous, "key_id": key_id}
        row_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        row = {
            **payload,
            "hash": row_hash,
            "sig": private.sign(bytes.fromhex(row_hash)).hex(),
        }
        rows.append(row)
        previous = row_hash
    segment = {
        "day": day,
        "row_count": len(rows),
        "tip_hash": rows[-1]["hash"],
        "content_sha256": hashlib.sha256(
            json.dumps(
                rows, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        ).hexdigest(),
        "rows": rows,
    }
    packet = {
        "schema": "lightwork.audit-evidence.v1",
        "generated_at": "2098-12-31T12:00:00Z",
        "tenant": "tenant-a",
        "selection": {
            "day": day,
            "all_days": False,
            "since": None,
            "until": None,
            "goal_id": 7,
            "incident_id": None,
            "action_id": None,
        },
        "segments": [segment],
        "anchors": {"status": "current_day_only", "rows": []},
        "public_keys": {key_id: public_hex},
        "matches": [{"segment": 0, "row": 0, "hash": rows[0]["hash"]}],
        "summary": {"segment_count": 1, "source_row_count": 2, "match_count": 1},
        "limitations": ["fixture"],
    }
    canonical = json.dumps(
        packet, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    packet["signature"] = {
        "pubkey": public_hex,
        "key_id": key_id,
        "sig": private.sign(canonical).hex(),
    }
    return packet, public_hex, private


def _resign(packet: dict, private: Ed25519PrivateKey) -> None:
    packet.pop("signature", None)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    packet["signature"] = {
        "pubkey": public.hex(),
        "key_id": hashlib.sha256(public).hexdigest()[:16],
        "sig": private.sign(
            json.dumps(
                packet, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        ).hex(),
    }


def test_standalone_verifier_accepts_complete_exact_selection():
    packet, key, _private = _packet()
    assert verify(packet, trusted_pubkey_hex=key) == {
        "schema": "lightwork.audit-evidence.v1",
        "segments": 1,
        "source_rows": 2,
        "matches": 1,
    }


@pytest.mark.parametrize("mutation", ["row", "publisher"])
def test_standalone_verifier_refuses_tamper_and_self_asserted_identity(mutation):
    packet, key, _private = _packet()
    if mutation == "row":
        packet["segments"][0]["rows"][0]["goal_id"] = 999
    else:
        key = "00" * 32
    with pytest.raises(EvidenceVerificationError):
        verify(packet, trusted_pubkey_hex=key)


@pytest.mark.parametrize("mutation", ["matches", "row", "selection"])
def test_trusted_publisher_cannot_bypass_inner_evidence_checks(mutation):
    packet, key, private = _packet()
    if mutation == "matches":
        packet["matches"] = []
    elif mutation == "row":
        packet["segments"][0]["rows"][0]["goal_id"] = 999
    else:
        packet["selection"]["all_days"] = True
    _resign(packet, private)
    with pytest.raises(EvidenceVerificationError):
        verify(packet, trusted_pubkey_hex=key)


def test_standalone_verifier_requires_anchor_for_completed_segment():
    packet, key, _private = _packet(day="2098-12-30")
    with pytest.raises(EvidenceVerificationError, match="lacks an anchor"):
        verify(packet, trusted_pubkey_hex=key)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({}, "exactly one evidence range"),
        ({"day": "2026-02-30"}, "real YYYY-MM-DD"),
        ({"day": "2026-01-01", "incident_id": "bad/id"}, "incident_id"),
        ({"day": "2026-01-01", "goal_id": 0}, "goal_id"),
    ],
)
def test_builder_refuses_ambiguous_or_unbounded_selection(kwargs, message):
    with pytest.raises(AuditEvidenceError, match=message):
        build_evidence_bundle(tenant="tenant-a", **kwargs)


def test_builder_refuses_tenant_other_than_active(monkeypatch):
    monkeypatch.setattr(
        "maverick.paths.current_tenant_id_strict", lambda: "tenant-a"
    )
    with pytest.raises(AuditEvidenceError, match="must equal the active tenant"):
        build_evidence_bundle(tenant="tenant-b", day="2099-01-01")


def test_builder_and_standalone_verifier_roundtrip_real_signed_chain(
    local_audit_key_custody,
):
    token = set_tenant("tenant-a")
    try:
        log = AuditLog(sign=True)
        assert log.record(
            AuditEvent(
                ts=1_000_000.0,
                kind=EventKind.GOAL_START,
                agent="test",
                goal_id=7,
                payload={"title": "bounded evidence", "description": None},
            )
        )
        day = datetime.now(timezone.utc).date().isoformat()
        packet = build_evidence_bundle(tenant="tenant-a", day=day, goal_id=7)
    finally:
        reset_tenant(token)
    publisher_key = packet["signature"]["pubkey"]
    assert verify(packet, trusted_pubkey_hex=publisher_key)["matches"] == 1


def test_evidence_verify_cli_uses_out_of_band_key(tmp_path):
    packet, key, _private = _packet()
    bundle = tmp_path / "evidence.json"
    bundle.write_text(json.dumps(packet), encoding="utf-8")
    result = CliRunner().invoke(
        main,
        ["audit", "evidence-verify", str(bundle), "--key", key],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["matches"] == 1


def test_standalone_loader_refuses_oversized_packet(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_verifier, "_MAX_BUNDLE_BYTES", 8)
    path = tmp_path / "large.json"
    path.write_bytes(b"{" + b" " * 16 + b"}")
    with pytest.raises(EvidenceVerificationError, match="byte bound"):
        evidence_verifier.load_bundle(path)


def test_evidence_issue_cli_audits_before_and_after_private_publish(
    tmp_path, monkeypatch
):
    from maverick import audit, billing, connections, paths
    from maverick.audit import evidence, export

    calls = []
    monkeypatch.setattr(paths, "current_tenant_id_strict", lambda: "tenant-a")
    monkeypatch.setattr(billing, "feature_allowed", lambda *args, **kwargs: True)
    monkeypatch.setattr(connections, "current_principal", lambda: "user:operator")
    monkeypatch.setattr(export, "audit_event_paths", lambda **kwargs: [])
    monkeypatch.setattr(
        audit,
        "required_audit_event",
        lambda kind, **payload: calls.append((kind, payload)) or True,
    )
    monkeypatch.setattr(
        evidence,
        "build_evidence_bundle",
        lambda **kwargs: {
            "schema": "lightwork.audit-evidence.v1",
            "summary": {"segment_count": 1, "source_row_count": 2, "match_count": 1},
            "signature": {"fixture": True},
        },
    )
    output = tmp_path / "issued.json"
    result = CliRunner().invoke(
        main,
        [
            "audit", "evidence", "--day", "2099-01-01", "--goal-id", "7",
            "--output", str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert [kind for kind, _ in calls] == [
        "audit_evidence_export_authorized",
        "audit_evidence_export_completed",
    ]
    assert json.loads(output.read_text(encoding="utf-8"))["signature"] == {
        "fixture": True
    }
