"""HTTP contract for the audit-only, digest-only Evolution Ledger API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from maverick.evolution_ledger import EvolutionLedgerError, build_promotion_receipt
from maverick.self_improvement import PromotionLedger, PromotionRecord
from maverick_dashboard import evolution_ledger_api as api

BASE = "/audit/evolution-ledger"


def _record(**overrides: object) -> PromotionRecord:
    values: dict[str, object] = {
        "id": "private-candidate-identifier",
        "rung": "code",
        "summary": "private promotion summary must not reach the dashboard",
        "baseline_score": 0.1,
        "candidate_score": 0.9,
        "promoted_at": 100.0,
        "approver_id": "a" * 16,
        "payload_sha256": "b" * 64,
        "approval_signature": "c" * 128,
        "model_risk_payload_sha256": "d" * 64,
        "samples": 8,
        "effect_ci_low": 0.1,
        "provenance": {
            "workspace_snapshot_sha256": "e" * 64,
            "raw_locator": "C:/private/path/never-expose",
        },
        "audit_payload": {"private_note": "never expose journal content"},
    }
    values.update(overrides)
    return PromotionRecord(**values)


def _client(monkeypatch: pytest.MonkeyPatch, *, permitted: bool = True) -> TestClient:
    def require(_request, permission: str) -> None:
        assert permission == "audit"
        if not permitted:
            raise HTTPException(status_code=403, detail="insufficient role")

    monkeypatch.setattr(api, "require_permission", require)
    app = FastAPI()
    app.include_router(api.create_evolution_ledger_router())
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_snapshot_is_digest_only_bounded_and_does_not_repair_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_path = tmp_path / "self_improvement.json"
    ledger = PromotionLedger(path=ledger_path)
    record = _record()
    ledger.add(record)
    assert ledger.mark_rolled_back(record.id, at=120.0)
    journal_path = Path(f"{ledger_path}.journal")
    before = {
        ledger_path: ledger_path.read_bytes(),
        journal_path: journal_path.read_bytes(),
    }
    lock_path = Path(f"{journal_path}.lock")
    lock_path.unlink(missing_ok=True)
    monkeypatch.setattr(api, "_promotion_ledger_source", lambda: (ledger, "available"))

    response = _client(monkeypatch).get(f"{BASE}/snapshot")

    assert response.status_code == 200, response.text
    assert {path: path.read_bytes() for path in before} == before
    assert not lock_path.exists()
    payload = response.json()
    assert payload["schema"] == api.SNAPSHOT_SCHEMA
    assert payload["sources"] == {
        "promotion_ledger": "available",
        "dreaming": "not_exposed",
        "hindsight": "not_exposed",
        "proof": "not_exposed",
    }
    projection = payload["projection"]
    assert projection["schema"] == "lightwork.evolution-ledger-projection.v1"
    assert projection["entry_count"] == 2
    assert {entry["kind"] for entry in projection["entries"]} == {
        "promotion",
        "rollback",
    }
    rendered = json.dumps(payload, sort_keys=True)
    for private in (
        record.id,
        record.summary,
        record.approval_signature,
        "C:/private/path/never-expose",
        "never expose journal content",
    ):
        assert private not in rendered
    assert "e" * 64 in rendered


def test_snapshot_names_an_absent_source_without_claiming_no_learning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "_promotion_ledger_source", lambda: (None, "not_present"))

    response = _client(monkeypatch).get(f"{BASE}/snapshot")

    assert response.status_code == 200
    projection = response.json()["projection"]
    assert projection["entries"] == []
    assert "promotion_ledger_not_present" in projection["limitations"]
    assert (
        "dreaming_hindsight_and_runtime_proof_sources_are_not_exposed_here"
        in projection["limitations"]
    )


def test_absent_named_tenant_source_does_not_admit_or_create_a_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening an audit view cannot create the tenant claim ``data_dir`` owns."""

    from maverick import paths

    home = tmp_path / "new-home"
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    token = paths.set_tenant("fresh-audit-tenant")
    try:
        source, state = api._promotion_ledger_source()
    finally:
        paths.reset_tenant(token)

    assert source is None and state == "not_present"
    assert not home.exists()


def test_snapshot_fails_closed_without_leaking_source_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable():
        raise EvolutionLedgerError("C:/private/ledger-path")

    monkeypatch.setattr(api, "_promotion_ledger_source", unavailable)

    response = _client(monkeypatch).get(f"{BASE}/snapshot")

    assert response.status_code == 503
    assert response.json()["detail"] == "evolution ledger is unavailable or did not verify"
    assert "private" not in response.text


def test_receipt_verification_is_digest_only_and_never_echoes_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = build_promotion_receipt(_record())
    client = _client(monkeypatch)

    verified = client.post(
        f"{BASE}/verify-receipt",
        json={"receipt": json.dumps(receipt)},
    )
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert body["schema"] == api.RECEIPT_VERIFICATION_SCHEMA
    assert body["receipt_valid"] is True
    assert body["receipt_sha256"] == receipt["receipt_sha256"]
    assert body["approval_status"] in {
        "not_recorded",
        "not_verifiable_without_private_binding",
    }
    assert "private-candidate-identifier" not in verified.text

    unsafe = dict(receipt)
    unsafe["raw_journal_content"] = "must never be returned"
    rejected = client.post(
        f"{BASE}/verify-receipt",
        json={"receipt": json.dumps(unsafe)},
    )
    assert rejected.status_code == 200
    assert rejected.json()["receipt_valid"] is False
    assert "must never be returned" not in rejected.text


def test_receipt_input_is_strict_bounded_and_all_routes_are_audit_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    permitted = _client(monkeypatch)
    duplicate = '{"schema":"x","schema":"y"}'
    assert permitted.post(f"{BASE}/verify-receipt", json={"receipt": duplicate}).status_code == 422
    assert (
        permitted.post(
            f"{BASE}/verify-receipt",
            json={"receipt": "{}", "approval_signature": "not-accepted"},
        ).status_code
        == 422
    )
    assert permitted.get(f"{BASE}/snapshot?debug=1").status_code == 404

    denied = _client(monkeypatch, permitted=False)
    assert denied.get(f"{BASE}/snapshot").status_code == 403
    assert denied.post(f"{BASE}/verify-receipt", json={"receipt": "{}"}).status_code == 403
