"""Adversarial contracts for the bounded benchmark exchange core."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from maverick import ai_benchmark_exchange as exchange
from maverick.governed_records import GovernedRecordStore
from maverick.paths import tenant_scope
from maverick.privacy_ops import RecordConflict

NOW = 1_800_000_000.0
CONTRIBUTION = "BCI-0123456789abcdef0123456789abcdef"
EVIDENCE = "MRE-0123456789abcdef0123456789abcdef"
REPORT = "BXR-0123456789abcdef0123456789abcdef"
KEY_ID = "KEY-0123456789abcdef0123456789abcdef"
SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(
    hashlib.sha256(b"external-benchmark-exchange-test-key").digest()
)
VERIFYING_KEY = SIGNING_KEY.public_key()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class Evidence:
    def __init__(self) -> None:
        self.digest = hashlib.sha256(b"evidence-v1").hexdigest()
        self.status = "approved"
        self.current = True

    def __call__(self, evidence_id: str):
        if evidence_id != EVIDENCE:
            return None
        return {
            "evidence_id": EVIDENCE,
            "digest": self.digest,
            "status": self.status,
            "current": self.current,
        }


class Verifier:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, payload: bytes, signature):
        self.calls += 1
        if signature["algorithm"] != "Ed25519" or signature["key_id"] != KEY_ID:
            return False
        try:
            VERIFYING_KEY.verify(bytes.fromhex(signature["value"]), payload)
        except (InvalidSignature, ValueError):
            return False
        return True


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.setenv("MAVERICK_GOVERNED_RECORDS_BACKEND", "local")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    (tmp_path / "config.toml").write_text(
        "[ai_benchmark_exchange]\nenable = true\n", encoding="utf-8"
    )
    from maverick import audit, config

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *_args, **_kwargs: True)
    config.reset_config_cache()
    yield tmp_path
    config.reset_config_cache()


def _store(namespace: str, prefix: str, record_type: str) -> GovernedRecordStore:
    return GovernedRecordStore(namespace, prefix, record_type)


def _service(label: str = "main", *, verifier=True):
    evidence = Evidence()
    signature_verifier = Verifier() if verifier else None
    clock = Clock()

    def pseudonymizer(tenant_ref: str, contribution_id: str) -> str:
        material = f"{tenant_ref}\0{contribution_id}".encode()
        return "BXC-" + hmac.new(b"pseudonym-key", material, hashlib.sha256).hexdigest()[:32]

    service = exchange.AIBenchmarkExchangeService(
        contributions=_store(
            f"test_ai_benchmark_contributions_{label}",
            "BXC",
            "test_ai_benchmark_contribution",
        ),
        reports=_store(
            f"test_ai_benchmark_reports_{label}",
            "BXR",
            "test_ai_benchmark_report",
        ),
        evidence_resolver=evidence,
        signature_verifier=signature_verifier,
        pseudonymizer=pseudonymizer,
        clock=clock,
    )
    return service, service._contributions, service._reports, evidence, signature_verifier, clock


def _period():
    return {"start": "2026-01-01", "end": "2026-12-31"}


def _cohort():
    return {
        "industry_code": "technology",
        "geography_code": "north_america",
        "organization_size_code": "enterprise",
        "model_modality_code": "text",
    }


def _bindings(evidence: Evidence):
    return [{"evidence_id": EVIDENCE, "digest": evidence.digest}]


def _create(service, evidence, *, numerator=91, operation="prepare-001"):
    return service.create_contribution(
        CONTRIBUTION,
        metric_code="eval_pass_rate",
        numerator=numerator,
        denominator=100,
        period=_period(),
        cohort=_cohort(),
        evidence_bindings=_bindings(evidence),
        voluntary_opt_in=True,
        idempotency_key=operation,
        actor="user:preparer",
    )


def _report(*, privacy=None, contributor_count=12, k_min=5, report_id=REPORT):
    body = {
        "schema": exchange.COHORT_REPORT_SCHEMA,
        "report_id": report_id,
        "issued_at": NOW,
        "metric_code": "eval_pass_rate",
        "numerator": 920,
        "denominator": 1000,
        "period": _period(),
        "cohort": _cohort(),
        "contributor_count": contributor_count,
        "k_anonymity_min": k_min,
        "aggregation_method": "pooled_ratio",
        "privacy": privacy
        or {
            "state": "not_dp",
            "epsilon": None,
            "delta": None,
            "noise_mechanism": None,
        },
        "methodology_version": exchange.METHODOLOGY_VERSION,
    }
    signature = SIGNING_KEY.sign(_canonical(body)).hex()
    return {
        **body,
        "report_sha256": _sha(body),
        "signature": {
            "algorithm": "Ed25519",
            "key_id": KEY_ID,
            "value": signature,
        },
    }


def test_feature_is_default_off_and_explicit_tenant_is_required(_isolate):
    from maverick import config

    (_isolate / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert exchange.enabled() is False
    service, *_ = _service("disabled")
    with tenant_scope(tenant="acme"):
        with pytest.raises(exchange.AIBenchmarkExchangeConfigError, match="disabled"):
            service.list_contributions()

    (_isolate / "config.toml").write_text(
        "[ai_benchmark_exchange]\nenable = true\n", encoding="utf-8"
    )
    config.reset_config_cache()
    assert exchange.enabled() is True
    with pytest.raises(exchange.AIBenchmarkExchangeStateError, match="explicit tenant"):
        service.list_contributions()


def test_create_requires_voluntary_human_opt_in_and_current_exact_evidence():
    service, _, _, evidence, _, _ = _service("consent")
    with tenant_scope(tenant="acme"):
        kwargs = {
            "metric_code": "eval_pass_rate",
            "numerator": 9,
            "denominator": 10,
            "period": _period(),
            "cohort": _cohort(),
            "evidence_bindings": _bindings(evidence),
            "idempotency_key": "prepare-001",
            "actor": "user:preparer",
        }
        with pytest.raises(exchange.AIBenchmarkExchangeConsentError, match="voluntary"):
            service.create_contribution(
                CONTRIBUTION, voluntary_opt_in=False, **kwargs
            )
        with pytest.raises(ValueError, match="authenticated user"):
            service.create_contribution(
                CONTRIBUTION, voluntary_opt_in=True, **{**kwargs, "actor": "agent:auto"}
            )
        evidence.current = False
        with pytest.raises(exchange.AIBenchmarkExchangeEvidenceError, match="current"):
            service.create_contribution(
                CONTRIBUTION, voluntary_opt_in=True, **kwargs
            )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"metric_code": "customer_acme_score"}, "closed taxonomy"),
        ({"numerator": 101}, "must not exceed"),
        ({"denominator": 0}, "between 1"),
        ({"period": {"start": "2026-12-31", "end": "2026-01-01"}}, "must not follow"),
        (
            {
                "cohort": {
                    **_cohort(),
                    "industry_code": "acme-customer",
                }
            },
            "closed taxonomy",
        ),
    ],
)
def test_closed_aggregate_schema_rejects_identifying_or_unbounded_values(overrides, message):
    service, _, _, evidence, _, _ = _service(f"closed-{message[:4]}")
    values = {
        "metric_code": "eval_pass_rate",
        "numerator": 91,
        "denominator": 100,
        "period": _period(),
        "cohort": _cohort(),
    }
    values.update(overrides)
    with tenant_scope(tenant="acme"):
        with pytest.raises(ValueError, match=message):
            service.create_contribution(
                CONTRIBUTION,
                **values,
                evidence_bindings=_bindings(evidence),
                voluntary_opt_in=True,
                idempotency_key="prepare-001",
                actor="user:preparer",
            )


def test_create_is_idempotent_and_conflicting_reuse_fails_closed():
    service, _, _, evidence, _, _ = _service("idempotent")
    with tenant_scope(tenant="acme"):
        first = _create(service, evidence)
        assert _create(service, evidence) == first
        with pytest.raises(exchange.AIBenchmarkExchangeIdempotencyConflict):
            _create(service, evidence, numerator=90)


def test_four_eyes_approval_and_export_strip_identity_and_are_deterministic():
    service, _, _, evidence, _, _ = _service("export")
    with tenant_scope(tenant="acme-customer-name"):
        draft = _create(service, evidence)
        assert draft["status"] == "draft"
        with pytest.raises(exchange.AIBenchmarkExchangeConsentError, match="four-eyes"):
            service.approve_contribution(
                CONTRIBUTION,
                1,
                expected_revision=draft["revision"],
                actor="user:preparer",
            )
        with pytest.raises(exchange.AIBenchmarkExchangeConsentError, match="lacks"):
            service.export_contribution(CONTRIBUTION)
        approved = service.approve_contribution(
            CONTRIBUTION,
            1,
            expected_revision=draft["revision"],
            actor="user:approver",
        )
        packet = service.export_contribution(CONTRIBUTION)
        assert service.export_contribution(CONTRIBUTION) == packet
        serialized = json.dumps(packet, sort_keys=True)
        assert approved["status"] == "approved"
        assert packet["schema"] == exchange.CONTRIBUTION_EXPORT_SCHEMA
        assert packet["pseudonymous_contribution_id"].startswith("BXC-")
        assert CONTRIBUTION not in serialized
        assert "acme-customer-name" not in serialized
        assert "tenant_ref" not in serialized
        assert packet["privacy"] == {
            "raw_records_included": False,
            "tenant_identity_included": False,
            "customer_identifiers_included": False,
            "anonymity_claim": False,
            "differential_privacy_claim": False,
            "privacy_budget_accounting": "unsupported",
        }
        body = dict(packet)
        packet_sha = body.pop("packet_sha256")
        assert packet_sha == _sha(body)


def test_add_version_uses_cas_resets_approval_and_binds_operation():
    service, _, _, evidence, _, _ = _service("version")
    with tenant_scope(tenant="acme"):
        draft = _create(service, evidence)
        approved = service.approve_contribution(
            CONTRIBUTION,
            1,
            expected_revision=draft["revision"],
            actor="user:approver",
        )
        kwargs = {
            "metric_code": "eval_pass_rate",
            "numerator": 93,
            "denominator": 100,
            "period": _period(),
            "cohort": _cohort(),
            "evidence_bindings": _bindings(evidence),
            "voluntary_opt_in": True,
            "expected_revision": approved["revision"],
            "idempotency_key": "prepare-002",
            "actor": "user:preparer-2",
        }
        updated = service.add_version(CONTRIBUTION, **kwargs)
        assert updated["status"] == "draft"
        assert len(updated["versions"]) == 2
        assert service.add_version(CONTRIBUTION, **kwargs) == updated
        with pytest.raises(exchange.AIBenchmarkExchangeIdempotencyConflict):
            service.add_version(CONTRIBUTION, **{**kwargs, "numerator": 92})
        with pytest.raises(RecordConflict):
            service.add_version(
                CONTRIBUTION,
                **{
                    **kwargs,
                    "idempotency_key": "prepare-003",
                    "expected_revision": approved["revision"],
                },
            )


def test_evidence_staleness_invalidates_an_approved_export():
    service, _, _, evidence, _, _ = _service("stale")
    with tenant_scope(tenant="acme"):
        draft = _create(service, evidence)
        service.approve_contribution(
            CONTRIBUTION,
            1,
            expected_revision=draft["revision"],
            actor="user:approver",
        )
        evidence.digest = hashlib.sha256(b"changed").hexdigest()
        with pytest.raises(exchange.AIBenchmarkExchangeStaleError, match="stale"):
            service.export_contribution(CONTRIBUTION)
        projection = service.get_contribution(CONTRIBUTION)
        assert projection is not None
        assert projection["status"] == "stale"
        assert projection["versions"][0]["exportable"] is False


def test_contribution_tenant_isolation_and_tamper_detection():
    service, store, _, evidence, _, _ = _service("tenant-tamper")
    with tenant_scope(tenant="tenant-a"):
        created = _create(service, evidence)
        record_id = service._contribution_record_id(
            "TEN-" + hashlib.sha256(b"tenant-a").hexdigest()[:24], CONTRIBUTION
        )

        def tamper(row):
            row["versions"][0]["content"]["numerator"] = 1

        store.update(
            record_id,
            tamper,
            expected_revision=created["revision"],
            action="test_tamper",
            actor="user:test",
        )
        with pytest.raises(exchange.AIBenchmarkExchangeStateError, match="malformed"):
            service.get_contribution(CONTRIBUTION)
    with tenant_scope(tenant="tenant-b"):
        assert service.get_contribution(CONTRIBUTION) is None
        assert service.list_contributions() == []


def test_signed_not_dp_report_ingest_is_idempotent_and_honest():
    service, _, _, _, verifier, _ = _service("report")
    with tenant_scope(tenant="acme"):
        report = _report()
        result = service.ingest_cohort_report(report, actor="user:analyst")
        assert result == service.ingest_cohort_report(report, actor="user:analyst")
        assert verifier.calls == 2
        assert result["verification"]["signature_verified"] is True
        assert result["privacy_assessment"] == {
            "k_threshold_structurally_satisfied": True,
            "anonymity_verified": False,
            "differential_privacy_parameters_complete": False,
            "differential_privacy_verified": False,
            "privacy_budget_accounting": "unsupported",
        }
        assert result["privacy"]["state"] == "not_dp"
        assert service.get_cohort_report(REPORT) == result
        assert service.list_cohort_reports() == [result]
        assert KEY_ID not in json.dumps(result, sort_keys=True)


def test_report_requires_real_injected_verifier_and_exact_digest():
    service, _, _, _, _, _ = _service("no-verifier", verifier=False)
    with tenant_scope(tenant="acme"):
        with pytest.raises(exchange.AIBenchmarkExchangeVerificationError, match="not configured"):
            service.ingest_cohort_report(_report(), actor="user:analyst")

    service, _, _, _, _, _ = _service("bad-report")
    with tenant_scope(tenant="acme"):
        bad_digest = _report()
        bad_digest["numerator"] = 919
        with pytest.raises(exchange.AIBenchmarkExchangeVerificationError, match="digest"):
            service.ingest_cohort_report(bad_digest, actor="user:analyst")
        bad_signature = _report()
        bad_signature["signature"]["value"] = "0" * 64
        with pytest.raises(exchange.AIBenchmarkExchangeVerificationError, match="not valid"):
            service.ingest_cohort_report(bad_signature, actor="user:analyst")


@pytest.mark.parametrize(
    ("report", "message"),
    [
        (_report(k_min=4), "k_anonymity_min"),
        (_report(contributor_count=5, k_min=6), "contributor_count"),
        (
            _report(
                privacy={
                    "state": "not_dp",
                    "epsilon": 1.0,
                    "delta": None,
                    "noise_mechanism": None,
                }
            ),
            "must not provide",
        ),
        (
            _report(
                privacy={
                    "state": "differential_privacy",
                    "epsilon": None,
                    "delta": 1e-6,
                    "noise_mechanism": "gaussian",
                }
            ),
            "must be a number",
        ),
    ],
)
def test_report_privacy_schema_enforces_k_minimum_and_explicit_dp_state(report, message):
    service, _, _, _, _, _ = _service(f"privacy-{message[:4]}")
    with tenant_scope(tenant="acme"):
        with pytest.raises(ValueError, match=message):
            service.ingest_cohort_report(report, actor="user:analyst")


def test_complete_dp_parameters_are_reported_but_never_claimed_verified():
    service, _, _, _, _, _ = _service("dp")
    report = _report(
        privacy={
            "state": "differential_privacy",
            "epsilon": 1.0,
            "delta": 1e-6,
            "noise_mechanism": "gaussian",
        }
    )
    with tenant_scope(tenant="acme"):
        result = service.ingest_cohort_report(report, actor="user:analyst")
    assert result["privacy_assessment"]["differential_privacy_parameters_complete"] is True
    assert result["privacy_assessment"]["differential_privacy_verified"] is False
    assert result["privacy_assessment"]["anonymity_verified"] is False


def test_report_identity_conflict_tenant_isolation_and_stored_tamper_detection():
    service, _, reports, _, _, _ = _service("report-tamper")
    with tenant_scope(tenant="tenant-a"):
        created = service.ingest_cohort_report(_report(), actor="user:analyst")
        changed = _report()
        changed["numerator"] = 900
        body = {key: changed[key] for key in exchange._REPORT_BODY_FIELDS}
        changed["report_sha256"] = _sha(body)
        changed["signature"]["value"] = SIGNING_KEY.sign(_canonical(body)).hex()
        with pytest.raises(exchange.AIBenchmarkExchangeIdempotencyConflict):
            service.ingest_cohort_report(changed, actor="user:analyst")

        tenant_ref = "TEN-" + hashlib.sha256(b"tenant-a").hexdigest()[:24]
        record_id = service._report_record_id(tenant_ref, REPORT)

        def tamper(row):
            row["body"]["numerator"] = 1

        reports.update(
            record_id,
            tamper,
            expected_revision=1,
            action="test_tamper",
            actor="user:test",
        )
        with pytest.raises(
            (exchange.AIBenchmarkExchangeStateError, exchange.AIBenchmarkExchangeVerificationError)
        ):
            service.get_cohort_report(REPORT)
        assert created["report_id"] == REPORT
    with tenant_scope(tenant="tenant-b"):
        assert service.get_cohort_report(REPORT) is None
        assert service.list_cohort_reports() == []


def test_no_raw_fields_or_cross_tenant_pooling_operations_exist():
    public_methods = {
        name
        for name in dir(exchange.AIBenchmarkExchangeService)
        if not name.startswith("_")
    }
    assert "pool" not in " ".join(public_methods)
    assert "aggregate" not in " ".join(public_methods)
    assert {
        "create_contribution",
        "add_version",
        "approve_contribution",
        "export_contribution",
        "ingest_cohort_report",
        "get_cohort_report",
        "list_cohort_reports",
    }.issubset(public_methods)

    serialized_fields = " ".join(
        sorted(
            exchange._CONTENT_FIELDS
            | exchange._REPORT_BODY_FIELDS
            | exchange._COHORT_FIELDS
        )
    )
    for forbidden in ("prompt", "output", "example", "customer_id", "tenant_id"):
        assert forbidden not in serialized_fields
