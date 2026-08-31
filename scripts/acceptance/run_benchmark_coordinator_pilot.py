"""Operate the benchmark coordinator in a consent-gated synthetic pilot.

This runner proves coordinator/restart/trust-rotation mechanics only.  It never
upgrades transport, pseudonymization, cohort size, or reviewer attestations into
an anonymity or differential-privacy claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from maverick.ai_benchmark_coordinator import (
    BenchmarkCoordinatorService,
    BenchmarkPrivacyReview,
    InProcessBenchmarkCoordinatorTransport,
    perform_trust_root_rotation,
)
from maverick.ai_benchmark_exchange import METHODOLOGY_NOTICE, PRIVACY_NOTICE
from maverick.ai_benchmark_exchange_transport import (
    BenchmarkCoordinatorClient,
    Ed25519TrustKey,
    VersionedEd25519TrustRoots,
)
from maverick.file_lock import atomic_write_text

SCHEMA = "lightwork.ai-benchmark-coordinator-pilot.v1"
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


def _digest(value: str, label: str) -> str:
    text = str(value or "").strip().lower().removeprefix("sha256:")
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{label} must be a SHA-256 digest")
    return text


def _package(index: int) -> dict[str, Any]:
    packet = {
        "schema": "lightwork.ai-benchmark-contribution-export.v1",
        "pseudonymous_contribution_id": "BXC-" + f"{index:032x}",
        "version": 1,
        "metric_code": "eval_pass_rate",
        "numerator": 80 + index,
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


def _trust_key(
    key_id: str, private_key: Ed25519PrivateKey, *, introduced_in: int
) -> Ed25519TrustKey:
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return Ed25519TrustKey(
        key_id=key_id,
        public_key_hex=public.hex(),
        introduced_in=introduced_in,
    )


def run(
    *,
    state_path: Path,
    primary_reviewer: str,
    independent_reviewer: str,
    rotation_primary: str,
    rotation_independent: str,
    consent_evidence_sha256: str,
    inclusion_evidence_sha256: str,
    anonymity_review_sha256: str,
    differential_privacy_review_sha256: str,
    privacy_budget_review_sha256: str,
    rotation_evidence_sha256: str,
    synthetic_review_confirmed: bool,
) -> dict[str, Any]:
    if synthetic_review_confirmed is not True:
        raise ValueError("the synthetic contribution/review boundary must be acknowledged")
    evidence = {
        "consent": _digest(consent_evidence_sha256, "consent evidence"),
        "inclusion": _digest(inclusion_evidence_sha256, "inclusion evidence"),
        "anonymity": _digest(anonymity_review_sha256, "anonymity review"),
        "differential_privacy": _digest(
            differential_privacy_review_sha256,
            "differential-privacy review",
        ),
        "privacy_budget": _digest(
            privacy_budget_review_sha256,
            "privacy-budget review",
        ),
    }
    old_private = Ed25519PrivateKey.generate()
    active_private = Ed25519PrivateKey.generate()
    roots = VersionedEd25519TrustRoots(
        version=1,
        keys=(_trust_key(KEY_A, old_private, introduced_in=1),),
    )
    rotated, ceremony = perform_trust_root_rotation(
        roots,
        version=2,
        additions=(_trust_key(KEY_B, active_private, introduced_in=2),),
        revocations=(KEY_A,),
        primary_operator=rotation_primary,
        independent_operator=rotation_independent,
        ceremony_evidence_sha256=_digest(
            rotation_evidence_sha256, "rotation evidence"
        ),
    )

    def signer(payload: bytes, key_id: str) -> bytes:
        if key_id != KEY_B:
            raise ValueError("inactive coordinator signing key")
        return active_private.sign(payload)

    service = BenchmarkCoordinatorService(
        signer=signer,
        signing_key_id=KEY_B,
        state_path=state_path,
    )
    client = BenchmarkCoordinatorClient(
        coordinator_origin="https://benchmark.example.com",
        enabled=True,
        transport=InProcessBenchmarkCoordinatorTransport(service),
    )
    receipt_digests: list[str] = []
    for index in range(1, 6):
        package = _package(index)
        service.admit_review(
            package,
            BenchmarkPrivacyReview(
                packet_sha256=package["packet_sha256"],
                contribution_consent_evidence_sha256=evidence["consent"],
                inclusion_evidence_sha256=evidence["inclusion"],
                anonymity_evidence_sha256=evidence["anonymity"],
                differential_privacy_evidence_sha256=evidence[
                    "differential_privacy"
                ],
                privacy_budget_evidence_sha256=evidence["privacy_budget"],
                inclusion_decision="included",
                anonymity_review="reviewed_not_claimed",
                differential_privacy_review="reviewed_not_claimed",
                privacy_budget_review="not_applicable",
                primary_reviewer=primary_reviewer,
                independent_reviewer=independent_reviewer,
                reviewed_at=time.time(),
            ),
        )
        receipt = client.submit(
            package,
            explicit_consent=True,
            idempotency_key=f"synthetic-pilot-{index}",
        )
        receipt_digests.append(_sha(receipt))
    report = service.build_cohort_report(
        metric_code="eval_pass_rate",
        period=PERIOD,
        cohort=COHORT,
    )
    body = {
        key: value
        for key, value in report.items()
        if key not in {"report_sha256", "signature"}
    }
    if not rotated.verify(_canonical(body), report["signature"]):
        raise RuntimeError("rotated trust roots did not verify the cohort report")

    restarted = BenchmarkCoordinatorService(
        signer=signer,
        signing_key_id=KEY_B,
        state_path=state_path,
    )
    duplicate = BenchmarkCoordinatorClient(
        coordinator_origin="https://benchmark.example.com",
        enabled=True,
        transport=InProcessBenchmarkCoordinatorTransport(restarted),
    ).submit(
        _package(1),
        explicit_consent=True,
        idempotency_key="synthetic-pilot-1",
    )
    result = {
        "schema": SCHEMA,
        "status": "passed_local_synthetic",
        "mode": "in_process_synthetic_contributors",
        "contributor_count": report["contributor_count"],
        "receipt_sha256": receipt_digests,
        "cohort_report_sha256": report["report_sha256"],
        "cohort_signature_verified_after_rotation": True,
        "restart_duplicate_recovered": duplicate["status"] == "duplicate",
        "coordinator_status": restarted.status(),
        "trust_rotation_ceremony_sha256": ceremony["ceremony_sha256"],
        "privacy": {
            "contribution_consent_reviewed": True,
            "inclusion_decision": "included",
            "anonymity_review": "reviewed_not_claimed",
            "differential_privacy_review": "reviewed_not_claimed",
            "privacy_budget_review": "not_applicable",
            "transport_proves_any_property": False,
        },
        "limitations": [
            "All five contributors and review references are synthetic pilot fixtures.",
            "No anonymity or differential-privacy property is claimed or verified.",
            "No live participant, cross-tenant network, or production custody was accepted.",
        ],
    }
    result["evidence_sha256"] = _sha(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--primary-reviewer", required=True)
    parser.add_argument("--independent-reviewer", required=True)
    parser.add_argument("--rotation-primary", required=True)
    parser.add_argument("--rotation-independent", required=True)
    parser.add_argument("--consent-evidence-sha256", required=True)
    parser.add_argument("--inclusion-evidence-sha256", required=True)
    parser.add_argument("--anonymity-review-sha256", required=True)
    parser.add_argument("--differential-privacy-review-sha256", required=True)
    parser.add_argument("--privacy-budget-review-sha256", required=True)
    parser.add_argument("--rotation-evidence-sha256", required=True)
    parser.add_argument("--synthetic-review-confirmed", action="store_true")
    args = parser.parse_args(argv)
    report = run(
        state_path=args.state,
        primary_reviewer=args.primary_reviewer,
        independent_reviewer=args.independent_reviewer,
        rotation_primary=args.rotation_primary,
        rotation_independent=args.rotation_independent,
        consent_evidence_sha256=args.consent_evidence_sha256,
        inclusion_evidence_sha256=args.inclusion_evidence_sha256,
        anonymity_review_sha256=args.anonymity_review_sha256,
        differential_privacy_review_sha256=(
            args.differential_privacy_review_sha256
        ),
        privacy_budget_review_sha256=args.privacy_budget_review_sha256,
        rotation_evidence_sha256=args.rotation_evidence_sha256,
        synthetic_review_confirmed=args.synthetic_review_confirmed,
    )
    atomic_write_text(
        args.output,
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        mode=0o600,
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
