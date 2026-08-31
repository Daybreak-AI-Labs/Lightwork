from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from maverick import privacy_data_discovery as discovery
from maverick_dashboard import privacy_data_discovery_api

NOW = 1_800_000_000.0
API = "/api/v1/privacy/data-discovery"


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class Rows:
    def __init__(self) -> None:
        self.rows: list[discovery.StructuredRecord] = []
        self.calls: list[tuple[str, str, float]] = []

    def iter_rows(self, *, source, credential_env, limits, deadline):
        del limits
        self.calls.append((source.origin, credential_env, deadline))
        yield from tuple(self.rows)


class Documents:
    def __init__(self) -> None:
        self.documents: list[discovery.UnstructuredDocument] = []

    def iter_documents(self, *, source, credential_env, limits, deadline):
        del source, credential_env, limits, deadline
        yield from tuple(self.documents)


@pytest.fixture()
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from maverick import config

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[privacy_automation]\n"
        "enable = true\n"
        "[privacy_automation.data_discovery]\n"
        "enable = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    for name in (
        "MAVERICK_DASHBOARD_ADMINS",
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_PROXY_AUTH",
        "MAVERICK_TENANT_BY_USER",
    ):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()

    clock = Clock()
    rows = Rows()
    documents = Documents()
    callbacks: list[discovery.ReviewedClassification] = []
    database = tmp_path / "privacy-discovery.sqlite3"

    def store_factory(_ops, tenant_id: str, request: Request):
        return discovery.PrivacyDataDiscoveryStore(
            database,
            config_gate=lambda: None,
            clock=clock,
            evidence_key_resolver=lambda _name: b"test-evidence-key-material-32-bytes",
            review_callback=privacy_data_discovery_api._callback_authority(
                request,
                tenant_id,
            ),
        )

    monkeypatch.setattr(privacy_data_discovery_api, "_store", store_factory)
    monkeypatch.setattr(
        privacy_data_discovery_api,
        "require_global_permission",
        lambda _request, _permission: None,
    )
    monkeypatch.setattr(
        privacy_data_discovery_api,
        "require_permission",
        lambda _request, _permission: None,
    )

    app = FastAPI()

    @app.middleware("http")
    async def test_principal(request: Request, call_next):
        request.state.principal = SimpleNamespace(
            sub=request.headers.get("X-Test-Subject", "operator-a")
        )
        return await call_next(request)

    app.state.privacy_data_discovery_structured_sources = {"fixture": rows}
    app.state.privacy_data_discovery_unstructured_sources = {"fixture": documents}
    app.state.privacy_data_discovery_credential_ref_present = lambda _name: True
    app.state.privacy_data_discovery_review_callback = callbacks.append
    app.include_router(privacy_data_discovery_api.router, prefix="/api/v1")
    client = TestClient(
        app,
        headers={"Origin": "http://testserver", "X-Test-Subject": "operator-a"},
    )
    yield client, database, rows, documents, callbacks, clock, config_path, app
    config.reset_config_cache()


def _ok(response, expected: int = 200):
    assert response.status_code == expected, response.text
    return response.json()


def _source_payload(*, kind: str = "structured") -> dict[str, object]:
    return {
        "kind": kind,
        "provider": "fixture",
        "origin": "https://data.example.com",
        "resource_ref": "customer-records",
        "credential_env": "PRIVACY_SOURCE_TOKEN",
        "evidence_key_env": "PRIVACY_EVIDENCE_HMAC_KEY",
        "expected_revision": 0,
        "idempotency_key": f"enroll-{kind}-v1",
    }


def _enroll(client: TestClient, source_id: str = "customers") -> dict[str, object]:
    return _ok(client.put(f"{API}/sources/{source_id}", json=_source_payload()))


def _manual_payload(
    operation: str,
    *,
    max_attempts: int = 3,
    retention_seconds: int = 3_600,
) -> dict[str, object]:
    return {
        "limits": {
            "max_records": 100,
            "max_files": 10,
            "max_bytes": 1_000_000,
            "max_seconds": 30.0,
            "max_findings": 100,
            "max_fields_per_record": 20,
            "max_record_bytes": 100_000,
            "max_document_bytes": 100_000,
        },
        "max_attempts": max_attempts,
        "retention_seconds": retention_seconds,
        "idempotency_key": operation,
    }


def _run_manual(
    client: TestClient,
    rows: Rows,
    *,
    operation: str,
    values: list[discovery.StructuredRecord],
    retention_seconds: int = 3_600,
) -> dict[str, object]:
    rows.rows = values
    _ok(
        client.post(
            f"{API}/sources/customers/jobs",
            json=_manual_payload(operation, retention_seconds=retention_seconds),
        )
    )
    claim = _ok(
        client.post(
            f"{API}/jobs/claim",
            json={"limit": 1, "lease_seconds": 60.0},
        )
    )["items"][0]
    return _ok(client.post(f"{API}/jobs/{claim['job_id']}/execute"))


def test_source_contract_is_strict_tenant_bound_and_default_closed(environment) -> None:
    client, _database, _rows, _documents, _callbacks, _clock, config_path, _app = (
        environment
    )
    enrolled = _enroll(client)
    assert enrolled["tenant_id"] == "tenant-a"
    assert enrolled["credential_value_persisted"] is False
    assert enrolled["external_side_effect"] is False
    assert _ok(client.get(f"{API}/sources"))["items"][0]["source_id"] == "customers"

    forged = _source_payload()
    forged["tenant_id"] = "tenant-b"
    response = client.put(f"{API}/sources/forged", json=forged)
    assert response.status_code == 422

    raw_secret = _source_payload()
    raw_secret["credential_env"] = "sk-live-secret-value"
    response = client.put(f"{API}/sources/raw-secret", json=raw_secret)
    assert response.status_code == 422

    config_path.write_text(
        "[privacy_automation]\n"
        "enable = true\n"
        "[privacy_automation.data_discovery]\n"
        "enable = false\n",
        encoding="utf-8",
    )
    from maverick import config

    config.reset_config_cache()
    assert client.get(f"{API}/status").status_code == 403


def test_schedule_claim_execute_reports_inventory_and_digest_only_diff(environment) -> None:
    client, database, rows, _documents, _callbacks, _clock, _config, _app = environment
    _enroll(client)
    schedule = _ok(
        client.put(
            f"{API}/schedules/hourly",
            json={
                "source_id": "customers",
                "interval_seconds": 3_600,
                "limits": _manual_payload("unused")["limits"],
                "max_attempts": 3,
                "retention_seconds": 3_600,
                "active": True,
                "expected_revision": 0,
                "idempotency_key": "schedule-hourly-v1",
                "next_run_at": NOW,
            },
        )
    )
    assert schedule["schedule_id"] == "hourly"
    assert _ok(client.get(f"{API}/schedules"))["items"][0]["active"] is True
    assert _ok(
        client.post(f"{API}/schedules/enqueue-due", json={"limit": 10})
    )["enqueued"] == 1

    secret = "alice.private@example.com"
    rows.rows = [
        discovery.StructuredRecord(
            "row-1",
            {"email": secret, "phone": "+1 212-555-0199"},
        )
    ]
    claim = _ok(
        client.post(
            f"{API}/jobs/claim",
            json={"limit": 1, "lease_seconds": 60.0},
        )
    )
    assert claim["lease_owner_bound_to_request_principal"] is True
    job = claim["items"][0]
    completed = _ok(client.post(f"{API}/jobs/{job['job_id']}/execute"))
    assert completed["external_side_effect"] is True
    assert completed["provider_network_io_possible"] is True
    assert completed["raw_data_returned"] is False
    assert completed["live_provider_acceptance_verified"] is False
    assert completed["report"]["absence_is_proven"] is False
    assert completed["report"]["completeness"] == "complete_within_declared_bounds"
    report_a = completed["report"]
    assert rows.calls[0][0:2] == (
        "https://data.example.com",
        "PRIVACY_SOURCE_TOKEN",
    )
    assert secret not in json.dumps(completed)
    assert secret.encode() not in database.read_bytes()

    findings = _ok(
        client.get(f"{API}/reports/{report_a['report_id']}/findings")
    )
    assert findings["digest_only_evidence"] is True
    assert findings["raw_values_returned"] is False
    assert all(len(item["evidence_sha256"]) == 64 for item in findings["items"])
    inventory = _ok(client.get(f"{API}/inventory"))
    assert inventory["absence_is_proven"] is False
    assert inventory["approved_findings_only_are_authoritative_updates"] is True

    report_b = _run_manual(
        client,
        rows,
        operation="manual-second",
        values=[
            discovery.StructuredRecord(
                "row-1",
                {"email": secret, "phone": "+1 212-555-0199", "dob": "1990-01-01"},
            )
        ],
    )["report"]
    diff = _ok(
        client.get(
            f"{API}/reports/diff",
            params={
                "base_report_id": report_a["report_id"],
                "target_report_id": report_b["report_id"],
            },
        )
    )
    assert diff["digest_only"] is True
    assert diff["absence_is_proven"] is False
    assert diff["added_finding_sha256"]
    assert _ok(client.get(f"{API}/reports"))["items"]


def test_fail_dead_letter_replay_and_revoked_source_are_controlled(environment) -> None:
    client, _database, _rows, _documents, _callbacks, _clock, _config, _app = environment
    source = _enroll(client)
    job = _ok(
        client.post(
            f"{API}/sources/customers/jobs",
            json=_manual_payload("manual-dead-letter", max_attempts=1),
        )
    )
    claim = _ok(
        client.post(
            f"{API}/jobs/claim",
            json={"limit": 1, "lease_seconds": 60.0},
        )
    )["items"][0]
    assert claim["job_id"] == job["job_id"]
    failed = _ok(
        client.post(
            f"{API}/jobs/{job['job_id']}/fail",
            json={"error_code": "adapter_error", "base_backoff_seconds": 1.0},
        )
    )
    assert failed["state"] == "dead_letter"
    replayed = _ok(
        client.post(
            f"{API}/jobs/{job['job_id']}/replay",
            json={
                "expected_revision": failed["revision"],
                "idempotency_key": "replay-manual-v1",
            },
        )
    )
    assert replayed["state"] == "queued"

    unsafe_failure = client.post(
        f"{API}/jobs/{job['job_id']}/fail",
        json={
            "error_code": "alice_private_value",
            "base_backoff_seconds": 1.0,
        },
    )
    assert unsafe_failure.status_code == 422

    revoked = _ok(
        client.post(
            f"{API}/sources/customers/state",
            json={
                "state": "revoked",
                "expected_revision": source["revision"],
                "idempotency_key": "revoke-customers-v1",
            },
        )
    )
    assert revoked["state"] == "revoked"
    assert (
        client.post(
            f"{API}/sources/customers/jobs",
            json=_manual_payload("blocked-after-revocation"),
        ).status_code
        == 409
    )


def test_four_eyes_review_uses_server_evidence_and_approved_only_callback(
    environment,
) -> None:
    client, _database, rows, _documents, callbacks, _clock, _config, _app = environment
    _enroll(client)
    report = _run_manual(
        client,
        rows,
        operation="review-scan",
        values=[
            discovery.StructuredRecord("row-1", {"email": "review@example.com"})
        ],
    )["report"]
    finding = _ok(
        client.get(f"{API}/reports/{report['report_id']}/findings")
    )["items"][0]

    forged = client.post(
        f"{API}/findings/{finding['finding_id']}/reviews",
        json={
            "decision": "confirmed",
            "effective_category": finding["category"],
            "freshness_seconds": 300,
            "expected_finding_revision": finding["revision"],
            "idempotency_key": "review-forged-evidence",
            "evidence_sha256": "0" * 64,
        },
    )
    assert forged.status_code == 422

    proposal = _ok(
        client.post(
            f"{API}/findings/{finding['finding_id']}/reviews",
            json={
                "decision": "confirmed",
                "effective_category": finding["category"],
                "freshness_seconds": 300,
                "expected_finding_revision": finding["revision"],
                "idempotency_key": "review-proposal-v1",
            },
        )
    )
    assert proposal["evidence_sha256"] == finding["evidence_sha256"]
    assert proposal["evidence_bound_from_authoritative_finding"] is True
    assert proposal["data_map_callback_invoked"] is False
    assert callbacks == []

    same_reviewer = client.post(
        f"{API}/reviews/{proposal['review_id']}/approve",
        json={
            "expected_version": proposal["version"],
            "expected_integrity_sha256": proposal["integrity_sha256"],
            "idempotency_key": "review-self-approval",
        },
    )
    assert same_reviewer.status_code == 409

    approved = _ok(
        client.post(
            f"{API}/reviews/{proposal['review_id']}/approve",
            headers={"X-Test-Subject": "operator-b"},
            json={
                "expected_version": proposal["version"],
                "expected_integrity_sha256": proposal["integrity_sha256"],
                "idempotency_key": "review-approval-v1",
            },
        )
    )
    assert approved["four_eyes_enforced"] is True
    assert approved["approved_only_callback"] is True
    assert approved["callback_attempted_this_request"] is True
    assert approved["callback_acknowledged"] is True
    assert approved["callback_durable_outbox"] is False
    assert len(callbacks) == 1
    assert callbacks[0].finding_id == finding["finding_id"]
    assert not hasattr(callbacks[0], "raw_value")
    replayed = _ok(
        client.post(
            f"{API}/reviews/{proposal['review_id']}/approve",
            headers={"X-Test-Subject": "operator-b"},
            json={
                "expected_version": proposal["version"],
                "expected_integrity_sha256": proposal["integrity_sha256"],
                "idempotency_key": "review-approval-v1",
            },
        )
    )
    assert replayed["callback_attempted_this_request"] is False
    assert replayed["callback_acknowledged"] is None
    assert len(callbacks) == 1
    reviews = _ok(client.get(f"{API}/findings/{finding['finding_id']}/reviews"))
    assert reviews["items"][0]["status"] == "approved"


def test_callback_failure_is_generic_after_durable_approval(environment) -> None:
    client, database, rows, _documents, _callbacks, _clock, _config, app = environment
    _enroll(client)
    report = _run_manual(
        client,
        rows,
        operation="callback-failure-scan",
        values=[discovery.StructuredRecord("row", {"phone": "212-555-0199"})],
    )["report"]
    finding = _ok(
        client.get(f"{API}/reports/{report['report_id']}/findings")
    )["items"][0]
    proposal = _ok(
        client.post(
            f"{API}/findings/{finding['finding_id']}/reviews",
            json={
                "decision": "confirmed",
                "effective_category": finding["category"],
                "freshness_seconds": 300,
                "expected_finding_revision": finding["revision"],
                "idempotency_key": "callback-failure-proposal",
            },
        )
    )

    def broken_callback(_update):
        raise RuntimeError("private callback payload alice@example.com")

    app.state.privacy_data_discovery_review_callback = broken_callback
    response = client.post(
        f"{API}/reviews/{proposal['review_id']}/approve",
        headers={"X-Test-Subject": "operator-b"},
        json={
            "expected_version": proposal["version"],
            "expected_integrity_sha256": proposal["integrity_sha256"],
            "idempotency_key": "callback-failure-approval",
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "privacy data discovery external adapter or callback failed"
    )
    assert b"alice@example.com" not in database.read_bytes()
    reviews = _ok(client.get(f"{API}/findings/{finding['finding_id']}/reviews"))
    assert reviews["items"][0]["status"] == "approved"
    assert _ok(client.get(f"{API}/audit"))["items"][-1]["action"] == (
        "review_callback_failed"
    )


def test_posture_audit_chain_and_retention_are_explicit(environment) -> None:
    client, _database, rows, _documents, _callbacks, clock, _config, _app = environment
    _enroll(client)
    _run_manual(
        client,
        rows,
        operation="short-retention-scan",
        values=[],
        retention_seconds=10,
    )
    status = _ok(client.get(f"{API}/status"))
    assert status["live_provider_acceptance_verified"] is False
    assert status["completeness_claim"] is False
    assert status["callback_delivery"]["durable_outbox"] is False
    posture = _ok(client.get(f"{API}/posture"))
    assert posture["raw_values_persisted"] is False
    assert posture["document_text_persisted"] is False
    assert posture["callback_outbox_available"] is False
    assert _ok(client.get(f"{API}/audit"))["items"]
    assert _ok(client.get(f"{API}/audit/verify"))["verified"] is True

    clock.advance(11)
    purged = _ok(
        client.post(
            f"{API}/retention/purge",
            json={"idempotency_key": "retention-window-v1"},
        )
    )
    assert purged["purged_reports"] == 1
    assert purged["raw_data_purged"] is False
    assert purged["digest_only_audit_tombstones_retained"] is True


def test_unstructured_unsupported_format_is_reported_not_claimed_complete(
    environment,
) -> None:
    client, _database, _rows, documents, _callbacks, _clock, _config, _app = environment
    payload = _source_payload(kind="unstructured")
    _ok(client.put(f"{API}/sources/documents", json=payload))
    documents.documents = [
        discovery.UnstructuredDocument(
            "doc-1",
            "application/pdf",
            b"raw unsupported pdf with alice@example.com",
        )
    ]
    _ok(
        client.post(
            f"{API}/sources/documents/jobs",
            json=_manual_payload("unstructured-unsupported"),
        )
    )
    job = _ok(
        client.post(
            f"{API}/jobs/claim",
            json={"limit": 1, "lease_seconds": 60.0},
        )
    )["items"][0]
    completed = _ok(client.post(f"{API}/jobs/{job['job_id']}/execute"))
    assert "unsupported_media_type" in completed["report"]["error_codes"]
    assert completed["report"]["completeness"] == "bounded_partial"
    assert completed["report"]["absence_is_proven"] is False
