from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from maverick.ai_benchmark_coordinator import (
    COORDINATOR_STATE_SCHEMA,
    BenchmarkCoordinatorReviewRequired,
    BenchmarkCoordinatorService,
    BenchmarkCoordinatorStateError,
    BenchmarkPrivacyReview,
    InProcessBenchmarkCoordinatorTransport,
    perform_trust_root_rotation,
)
from maverick.ai_benchmark_exchange import (
    METHODOLOGY_NOTICE,
    PRIVACY_NOTICE,
)
from maverick.ai_benchmark_exchange_transport import (
    BenchmarkCoordinatorClient,
    BenchmarkCoordinatorIdempotencyConflict,
    BenchmarkCoordinatorProtocolError,
    Ed25519TrustKey,
    VersionedEd25519TrustRoots,
)

KEY_A = "KEY-" + "a" * 32
KEY_B = "KEY-" + "b" * 32
PERIOD = {"start": "2026-01-01", "end": "2026-06-30"}
COHORT = {
    "industry_code": "technology",
    "geography_code": "north_america",
    "organization_size_code": "enterprise",
    "model_modality_code": "text",
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _export(index: int, *, numerator: int | None = None) -> dict[str, Any]:
    packet = {
        "schema": "lightwork.ai-benchmark-contribution-export.v1",
        "pseudonymous_contribution_id": "BXC-" + f"{index:032x}",
        "version": 1,
        "metric_code": "eval_pass_rate",
        "numerator": 80 + index if numerator is None else numerator,
        "denominator": 100,
        "period": dict(PERIOD),
        "cohort": dict(COHORT),
        "evidence_bindings": [
            {
                "evidence_id": "MRE-" + f"{index + 100:032x}",
                "digest": f"{index + 1:064x}",
            }
        ],
        "content_sha256": f"{index + 200:064x}",
        "authority_sha256": f"{index + 300:064x}",
        "approval": {
            "voluntary_opt_in": True,
            "human_approved": True,
            "four_eyes": True,
            "evidence_current": True,
        },
        "methodology": {
            "aggregation_level": "organization",
            "calculation": "bounded_numerator_divided_by_denominator",
            "sampling_basis": "voluntary_opt_in",
        },
        "privacy": {
            "raw_records_included": False,
            "tenant_identity_included": False,
            "customer_identifiers_included": False,
            "anonymity_claim": False,
            "differential_privacy_claim": False,
            "privacy_budget_accounting": "unsupported",
        },
        "limitations": [METHODOLOGY_NOTICE, PRIVACY_NOTICE],
    }
    return {**packet, "packet_sha256": _sha(packet)}


def _review(package: dict[str, Any], index: int = 1) -> BenchmarkPrivacyReview:
    return BenchmarkPrivacyReview(
        packet_sha256=package["packet_sha256"],
        contribution_consent_evidence_sha256=f"{index + 400:064x}",
        inclusion_evidence_sha256=f"{index + 500:064x}",
        anonymity_evidence_sha256=f"{index + 600:064x}",
        differential_privacy_evidence_sha256=f"{index + 700:064x}",
        privacy_budget_evidence_sha256=f"{index + 800:064x}",
        inclusion_decision="included",
        anonymity_review="reviewed_not_claimed",
        differential_privacy_review="reviewed_not_claimed",
        privacy_budget_review="not_applicable",
        primary_reviewer=f"user:privacy-{index}",
        independent_reviewer=f"user:legal-{index}",
        reviewed_at=1_787_889_500.0 + index,
    )


def _state_with_reviews(count: int) -> dict[str, Any]:
    reviews = {}
    for index in range(1, count + 1):
        package = _export(index, numerator=80)
        reviews[package["packet_sha256"]] = _review(package, index).normalized()
    return {
        "schema": COORDINATOR_STATE_SCHEMA,
        "reviews": reviews,
        "contributions": {},
        "receipts": {},
    }


def _private(seed: int) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)


def _trust_key(
    key_id: str,
    private: Ed25519PrivateKey,
    *,
    introduced_in: int = 1,
) -> Ed25519TrustKey:
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return Ed25519TrustKey(
        key_id=key_id,
        public_key_hex=public.hex(),
        introduced_in=introduced_in,
    )


def _service(
    private: Ed25519PrivateKey,
    *,
    key_id: str = KEY_A,
    path: Path | None = None,
    max_state_bytes: int | None = None,
) -> BenchmarkCoordinatorService:
    def sign(payload: bytes, selected: str) -> bytes:
        assert selected == key_id
        return private.sign(payload)

    kwargs = {}
    if max_state_bytes is not None:
        kwargs["max_state_bytes"] = max_state_bytes
    return BenchmarkCoordinatorService(
        signer=sign,
        signing_key_id=key_id,
        state_path=path,
        clock=lambda: 1_787_889_600.0,
        **kwargs,
    )


def _client(service: BenchmarkCoordinatorService) -> BenchmarkCoordinatorClient:
    return BenchmarkCoordinatorClient(
        coordinator_origin="https://benchmark.example.com",
        enabled=True,
        transport=InProcessBenchmarkCoordinatorTransport(service),
    )


def test_transport_never_substitutes_for_privacy_review() -> None:
    service = _service(_private(1))
    package = _export(1)

    with pytest.raises(BenchmarkCoordinatorProtocolError):
        _client(service).submit(
            package,
            explicit_consent=True,
            idempotency_key="submit-no-review",
        )

    assert service.status()["accepted_contributions"] == 0
    assert service.status()["transport_proves_inclusion"] is False
    assert service.status()["anonymity_verified"] is False
    assert service.status()["differential_privacy_verified"] is False


def test_review_requires_two_people_and_no_inferred_privacy_claims() -> None:
    package = _export(2)
    same_person = copy.copy(_review(package, 2))
    object.__setattr__(same_person, "independent_reviewer", "user:privacy-2")
    with pytest.raises(ValueError, match="two distinct"):
        same_person.normalized()

    false_anonymity = copy.copy(_review(package, 2))
    object.__setattr__(false_anonymity, "anonymity_review", "verified")
    with pytest.raises(ValueError, match="reviewed_not_claimed"):
        false_anonymity.normalized()

    wrong_packet = copy.copy(_review(package, 2))
    object.__setattr__(wrong_packet, "packet_sha256", "f" * 64)
    with pytest.raises(BenchmarkCoordinatorStateError, match="different"):
        _service(_private(1)).admit_review(package, wrong_packet)


def test_reviewed_submission_is_idempotent_and_survives_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "coordinator-state.json"
    private = _private(1)
    service = _service(private, path=state_path)
    package = _export(3)
    review = service.admit_review(package, _review(package, 3))
    assert review["limitations"] == {
        "inclusion_proven_by_transport": False,
        "anonymity_verified": False,
        "differential_privacy_verified": False,
        "privacy_budget_accounted": False,
    }

    accepted = _client(service).submit(
        package,
        explicit_consent=True,
        idempotency_key="submit-reviewed-3",
    )
    assert accepted["status"] == "accepted"
    assert accepted["limitations"]["anonymity_verified"] is False

    restarted = _service(private, path=state_path)
    duplicate = _client(restarted).submit(
        package,
        explicit_consent=True,
        idempotency_key="submit-reviewed-3",
    )
    assert duplicate["status"] == "duplicate"
    assert duplicate["receipt_id"] == accepted["receipt_id"]
    assert restarted.status()["accepted_contributions"] == 1

    changed = _export(3, numerator=70)
    with pytest.raises(BenchmarkCoordinatorIdempotencyConflict):
        _client(restarted).submit(
            changed,
            explicit_consent=True,
            idempotency_key="submit-reviewed-3",
        )


def test_state_byte_limit_is_exact_and_independent_of_request_limit(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "coordinator-state.json"
    payload = _canonical(_state_with_reviews(2))
    assert len(payload) > 2 * 1024
    state_path.write_bytes(payload)

    exact = _service(
        _private(1),
        path=state_path,
        max_state_bytes=len(payload),
    )
    assert exact.status()["reviews"] == 2
    with pytest.raises(BenchmarkCoordinatorStateError, match="byte capacity"):
        _service(
            _private(1),
            path=state_path,
            max_state_bytes=len(payload) - 1,
        )


def test_state_scales_past_request_bound_and_survives_another_commit(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "coordinator-state.json"
    payload = _canonical(_state_with_reviews(420))
    assert len(payload) > 512 * 1024
    state_path.write_bytes(payload)

    service = _service(_private(1), path=state_path)
    assert service.status()["reviews"] == 420
    package = _export(421, numerator=80)
    service.admit_review(package, _review(package, 421))

    restarted = _service(_private(1), path=state_path)
    assert restarted.status()["reviews"] == 421
    assert state_path.stat().st_size > 512 * 1024


def test_capacity_failure_rolls_back_contribution_and_receipt_atomically(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "coordinator-state.json"
    private = _private(1)
    service = _service(private, path=state_path, max_state_bytes=2_048)
    package = _export(11, numerator=80)
    service.admit_review(package, _review(package, 11))
    before = state_path.read_bytes()

    with pytest.raises(BenchmarkCoordinatorProtocolError):
        _client(service).submit(
            package,
            explicit_consent=True,
            idempotency_key="capacity-rollback-11",
        )

    assert state_path.read_bytes() == before
    assert service._state["contributions"] == {}
    assert service._state["receipts"] == {}
    status = _service(
        private,
        path=state_path,
        max_state_bytes=2_048,
    ).status()
    assert status["reviews"] == 1
    assert status["accepted_contributions"] == 0
    assert status["receipts"] == 0


def test_failed_atomic_save_keeps_prior_memory_and_disk_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from maverick import ai_benchmark_coordinator as coordinator

    state_path = tmp_path / "coordinator-state.json"
    private = _private(1)
    service = _service(private, path=state_path)
    package = _export(12, numerator=80)
    service.admit_review(package, _review(package, 12))
    before = state_path.read_bytes()

    def failed_save(*_args, **_kwargs):
        raise OSError("simulated durable write failure")

    monkeypatch.setattr(coordinator, "atomic_write_text", failed_save)
    with pytest.raises(BenchmarkCoordinatorProtocolError):
        _client(service).submit(
            package,
            explicit_consent=True,
            idempotency_key="failed-save-rollback-12",
        )

    assert state_path.read_bytes() == before
    assert service._state["contributions"] == {}
    assert service._state["receipts"] == {}
    restarted = _service(private, path=state_path)
    assert restarted.status()["reviews"] == 1
    assert restarted.status()["accepted_contributions"] == 0
    assert restarted.status()["receipts"] == 0


def test_five_reviewed_contributors_build_a_verifiable_non_dp_report() -> None:
    private = _private(1)
    service = _service(private)
    for index in range(1, 6):
        package = _export(index)
        service.admit_review(package, _review(package, index))
        receipt = _client(service).submit(
            package,
            explicit_consent=True,
            idempotency_key=f"cohort-submit-{index}",
        )
        assert receipt["status"] == "accepted"

    report = service.build_cohort_report(
        metric_code="eval_pass_rate",
        period=PERIOD,
        cohort=COHORT,
    )
    assert report["contributor_count"] == 5
    assert report["numerator"] == sum(80 + index for index in range(1, 6))
    assert report["privacy"] == {
        "state": "not_dp",
        "epsilon": None,
        "delta": None,
        "noise_mechanism": None,
    }
    body = {
        key: value for key, value in report.items() if key not in {"report_sha256", "signature"}
    }
    roots = VersionedEd25519TrustRoots(
        version=1,
        keys=(_trust_key(KEY_A, private),),
    )
    assert report["report_sha256"] == _sha(body)
    assert roots.verify(_canonical(body), report["signature"]) is True


def test_cohort_threshold_and_trust_rotation_ceremony() -> None:
    old_private = _private(1)
    new_private = _private(2)
    old_roots = VersionedEd25519TrustRoots(
        version=1,
        keys=(_trust_key(KEY_A, old_private),),
    )
    rotated, ceremony = perform_trust_root_rotation(
        old_roots,
        version=2,
        additions=(_trust_key(KEY_B, new_private, introduced_in=2),),
        revocations=(KEY_A,),
        primary_operator="user:security-officer",
        independent_operator="user:privacy-officer",
        ceremony_evidence_sha256="9" * 64,
        clock=lambda: 1_787_889_600.0,
    )

    assert rotated.version == 2
    assert ceremony["from_version"] == 1
    assert ceremony["to_version"] == 2
    assert ceremony["private_keys_present"] is False
    payload = _canonical({"test": "rotation"})
    old_signature = {
        "algorithm": "Ed25519",
        "key_id": KEY_A,
        "value": old_private.sign(payload).hex(),
    }
    new_signature = {
        "algorithm": "Ed25519",
        "key_id": KEY_B,
        "value": new_private.sign(payload).hex(),
    }
    assert rotated.verify(payload, old_signature) is False
    assert rotated.verify(payload, new_signature) is True

    service = _service(new_private, key_id=KEY_B)
    for index in range(1, 5):
        package = _export(index)
        service.admit_review(package, _review(package, index))
        _client(service).submit(
            package,
            explicit_consent=True,
            idempotency_key=f"below-threshold-{index}",
        )
    with pytest.raises(BenchmarkCoordinatorReviewRequired, match="threshold"):
        service.build_cohort_report(
            metric_code="eval_pass_rate",
            period=PERIOD,
            cohort=COHORT,
        )
