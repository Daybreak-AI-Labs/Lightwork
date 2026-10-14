"""Governed Cyber Resilience Act product-security operations."""

from __future__ import annotations

import calendar
import hashlib
import json
import time
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from maverick import config as maverick_config
from maverick import cra_ops, evidence_graph
from maverick.privacy_ops import RecordConflict


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[cra_ops]\nenable = true\n\n[evidence_graph]\nenable = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    maverick_config.reset_config_cache()
    import maverick.audit as audit

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    yield config_path
    maverick_config.reset_config_cache()


def _product_and_release(*, with_sbom: bool = True):
    product = cra_ops.create_product(
        name="Lightwork",
        description="Governed enterprise agent platform.",
        manufacturer="Day AI Labs",
        product_kind="software",
        support_until=1_900_000_000.0,
        actor="product-security@example.com",
    )
    product = cra_ops.decide_product_applicability(
        product["id"],
        decision="applicable",
        rationale="Human counsel determined this EU release is in scope.",
        reviewer="counsel@example.com",
        expected_revision=product["revision"],
    )
    assert product is not None
    release = cra_ops.create_release(
        product_id=product["id"],
        version="1.2.3",
        artifact_sha256="a" * 64,
        released_at=1_750_000_000.0,
        actor="release-manager@example.com",
    )
    if with_sbom:
        release = cra_ops.attach_sbom(
            release["id"],
            _cyclonedx(),
            source="release-pipeline",
            actor="release-manager@example.com",
            expected_revision=release["revision"],
        )
        assert release is not None
    return product, release


def _cyclonedx():
    return json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "components": [
                {
                    "bom-ref": "pkg:pypi/httpx@0.27.0",
                    "type": "library",
                    "name": "httpx",
                    "version": "0.27.0",
                    "purl": "pkg:pypi/httpx@0.27.0",
                }
            ],
            "dependencies": [],
        }
    )


def _counsel_override(case: dict) -> dict:
    overridden = cra_ops.override_case_effective_date(
        case["id"],
        rationale="Counsel explicitly directed pre-effective operational clocking.",
        counsel="counsel@example.com",
        expected_revision=case["revision"],
    )
    assert overridden is not None
    return overridden


class _ReceiptSigner:
    def __init__(self, private_key: ed25519.Ed25519PrivateKey):
        self._private_key = private_key
        public = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self.public_key = public.hex()
        self.key_id = hashlib.sha256(public).hexdigest()[:16]

    def sign(self, message: bytes) -> str:
        return self._private_key.sign(message).hex()


def _configure_receipt_signer(config_path) -> _ReceiptSigner:
    signer = _ReceiptSigner(ed25519.Ed25519PrivateKey.generate())
    config_path.write_text(
        "[cra_ops]\n"
        "enable = true\n"
        f'filing_receipt_trusted_public_keys = ["{signer.public_key}"]\n\n'
        "[evidence_graph]\n"
        "enable = true\n",
        encoding="utf-8",
    )
    maverick_config.reset_config_cache()
    return signer


def _approved_case_evidence(
    case: dict,
    *,
    valid_until: float | None = None,
) -> dict:
    node = evidence_graph.ingest(
        source="cra-test",
        source_id=f"case:{case['id']}",
        evidence_type="incident_observation",
        title="Bound incident observation",
        summary="Reviewed evidence for this exact CRA case.",
        controls=["CRA:ARTICLE-14"],
        attributes={"case_id": case["id"]},
        links=[{
            "relation": "supports",
            "target_type": "cra_case",
            "target_id": case["id"],
        }],
        actor="collector",
        valid_until=valid_until,
    )
    approved = evidence_graph.decide(
        node["id"],
        decision="approved",
        rationale="Human reviewer approved the exact evidence payload.",
        reviewer="evidence-reviewer",
        expected_revision=node["revision"],
    )
    assert approved is not None
    return approved


def _approved_case_report(
    *,
    valid_until: float | None = None,
) -> tuple[dict, dict, dict, dict, dict]:
    product, release = _product_and_release()
    case = cra_ops.open_case(
        release_id=release["id"],
        case_type="severe_incident",
        title="Approved report pack authority",
        summary="The product pack must revalidate this authority at render time.",
        awareness_at=1_760_000_000.0,
        external_id="INC-PACK-AUTHORITY",
        actor="analyst",
    )
    case = cra_ops.decide_case_reportability(
        case["id"],
        decision="reportable",
        rationale="Human reviewers confirmed this incident is reportable.",
        reviewer="counsel",
        expected_revision=case["revision"],
    )
    assert case is not None
    evidence = _approved_case_evidence(case, valid_until=valid_until)
    report = cra_ops.draft_report(
        case_id=case["id"],
        stage="early_warning",
        content={"summary": "Approved exact report packet."},
        evidence_node_ids=[evidence["id"]],
        actor="analyst",
    )
    report = cra_ops.approve_report(
        report["id"],
        packet_sha256=report["packet_sha256"],
        rationale="Human reviewer approved the exact packet.",
        reviewer="counsel",
        expected_revision=report["revision"],
    )
    assert report is not None
    return product, release, case, evidence, report


def test_product_applicability_is_human_decided_and_revision_checked():
    product = cra_ops.create_product(
        name="Device agent",
        description="An update agent sold with an appliance.",
        manufacturer="Example Corp",
        product_kind="software",
        support_until=1_900_000_000.0,
        actor="product-owner",
    )
    assert product["applicability"]["status"] == "undetermined"

    decided = cra_ops.decide_product_applicability(
        product["id"],
        decision="not_applicable",
        rationale="Counsel documented the specific exclusion and evidence.",
        reviewer="legal-reviewer",
        expected_revision=product["revision"],
    )
    assert decided is not None
    assert decided["applicability"]["decided_by"] == "legal-reviewer"

    with pytest.raises(RecordConflict):
        cra_ops.decide_product_applicability(
            product["id"],
            decision="applicable",
            rationale="Stale browser update.",
            reviewer="other-reviewer",
            expected_revision=product["revision"],
        )


def test_release_binds_artifact_and_normalised_sbom():
    _, release = _product_and_release(with_sbom=False)

    attached = cra_ops.attach_sbom(
        release["id"],
        _cyclonedx(),
        source="release-pipeline",
        actor="release-manager",
        expected_revision=release["revision"],
    )

    assert attached is not None
    assert attached["artifact_sha256"] == "a" * 64
    assert attached["sbom"]["format"] == "cyclonedx"
    assert attached["sbom"]["component_count"] == 1
    assert attached["release_binding_sha256"]

    with pytest.raises(RecordConflict):
        cra_ops.create_release(
            product_id=attached["product_id"],
            version=attached["version"],
            artifact_sha256="b" * 64,
            released_at=1_750_000_100.0,
            actor="racing-release-manager",
        )


def test_product_pack_refuses_missing_or_tampered_sbom_authority():
    product, release = _product_and_release(with_sbom=False)
    with pytest.raises(ValueError, match="immutable SBOM"):
        cra_ops.render_product_security_pack(product["id"])

    release = cra_ops.attach_sbom(
        release["id"],
        _cyclonedx(),
        source="release-pipeline",
        actor="release-manager",
        expected_revision=release["revision"],
    )
    assert release is not None
    assert len(cra_ops.render_product_security_pack(product["id"])["releases"]) == 1

    changed = cra_ops._RELEASES.update(  # noqa: SLF001 - corruption regression
        release["id"],
        lambda record: record["sbom"]["components"].append({"id": "tampered"}),
        expected_revision=release["revision"],
        action="test_tamper_normalized_sbom",
        actor="test",
    )
    assert changed is not None
    with pytest.raises(ValueError, match="normalized SBOM binding"):
        cra_ops.render_product_security_pack(product["id"])


def test_case_clock_starts_only_after_human_reportability_decision():
    _, release = _product_and_release()
    awareness = 1_760_000_000.0
    case = cra_ops.open_case(
        release_id=release["id"],
        case_type="actively_exploited_vulnerability",
        title="Dependency vulnerability observed in exploitation feed",
        summary="A bounded observation requires human legal and technical review.",
        awareness_at=awareness,
        external_id="CVE-2026-12345",
        actor="psirt-analyst",
    )

    pending = cra_ops.case_status(case["id"], now=awareness + 100_000)
    assert pending["reportability"]["status"] == "undetermined"
    assert pending["clocks"] == []

    decided = cra_ops.decide_case_reportability(
        case["id"],
        decision="reportable",
        rationale="PSIRT and counsel confirmed active exploitation and scope.",
        reviewer="psirt-counsel",
        expected_revision=case["revision"],
    )
    assert decided is not None
    assert cra_ops.case_status(case["id"], now=awareness + 25 * 3600)["clocks"] == []
    decided = _counsel_override(decided)
    status = cra_ops.case_status(case["id"], now=awareness + 25 * 3600)
    assert status["clocks"][0]["stage"] == "early_warning"
    assert status["clocks"][0]["deadline_at"] == awareness + 24 * 3600
    assert status["clocks"][0]["state"] == "overdue"
    assert status["clocks"][1]["deadline_at"] == awareness + 72 * 3600
    assert status["clocks"][2]["state"] == "awaiting_anchor"


@pytest.mark.parametrize("applicability", [None, "not_applicable"])
def test_case_open_requires_current_human_applicable_product(applicability):
    product = cra_ops.create_product(
        name="Unresolved device agent",
        description="Applicability has not conferred CRA case authority.",
        manufacturer="Example Corp",
        product_kind="software",
        support_until=1_900_000_000.0,
        actor="product-owner",
    )
    if applicability is not None:
        product = cra_ops.decide_product_applicability(
            product["id"],
            decision=applicability,
            rationale="Counsel documented why this product is outside scope.",
            reviewer="legal-reviewer",
            expected_revision=product["revision"],
        )
        assert product is not None
    release = cra_ops.create_release(
        product_id=product["id"],
        version="1.0.0",
        artifact_sha256="b" * 64,
        released_at=1_750_000_000.0,
        actor="release-manager",
    )
    release = cra_ops.attach_sbom(
        release["id"],
        _cyclonedx(),
        source="release-pipeline",
        actor="release-manager",
        expected_revision=release["revision"],
    )
    assert release is not None

    with pytest.raises(ValueError, match="human-determined applicable"):
        cra_ops.open_case(
            release_id=release["id"],
            case_type="actively_exploited_vulnerability",
            title="Case without applicability authority",
            summary="The case must remain gated on the product decision.",
            awareness_at=1_760_000_000.0,
            external_id="CVE-2026-UNRESOLVED",
            actor="psirt-analyst",
        )


def test_reportability_rechecks_current_product_applicability_authority():
    product, release = _product_and_release()
    case = cra_ops.open_case(
        release_id=release["id"],
        case_type="actively_exploited_vulnerability",
        title="Applicability changed underneath the case",
        summary="A corrupted current product decision must not activate clocks.",
        awareness_at=1_760_000_000.0,
        external_id="CVE-2026-STALE-APPLICABILITY",
        actor="psirt-analyst",
    )

    def _corrupt_applicability(record):
        record["applicability"]["status"] = "not_applicable"

    changed = cra_ops._PRODUCTS.update(  # noqa: SLF001 - corrupt-state regression
        product["id"],
        _corrupt_applicability,
        expected_revision=product["revision"],
        action="test_corrupt_applicability",
        actor="test",
    )
    assert changed is not None
    with pytest.raises(ValueError, match="human-determined applicable"):
        cra_ops.decide_case_reportability(
            case["id"],
            decision="reportable",
            rationale="This must not override the product applicability gate.",
            reviewer="psirt-counsel",
            expected_revision=case["revision"],
        )


def test_reportable_case_emits_no_clocks_after_product_binding_goes_stale():
    product, release = _product_and_release()
    awareness = 1_760_000_000.0
    case = cra_ops.open_case(
        release_id=release["id"],
        case_type="actively_exploited_vulnerability",
        title="Bound case",
        summary="The clock remains contingent on the exact product authority.",
        awareness_at=awareness,
        external_id="CVE-2026-PRODUCT-REVISION",
        actor="psirt-analyst",
    )
    case = cra_ops.decide_case_reportability(
        case["id"],
        decision="reportable",
        rationale="PSIRT and counsel confirmed scope and reportability.",
        reviewer="psirt-counsel",
        expected_revision=case["revision"],
    )
    assert case is not None
    case = _counsel_override(case)
    assert cra_ops.case_status(case["id"], now=awareness + 25 * 3600)["clocks"]

    def _advance_product_revision(record):
        record["description"] = f"{record['description']} Reviewed."

    changed = cra_ops._PRODUCTS.update(  # noqa: SLF001 - stale-authority regression
        product["id"],
        _advance_product_revision,
        expected_revision=product["revision"],
        action="test_advance_product_revision",
        actor="test",
    )
    assert changed is not None
    with pytest.raises(ValueError, match="stale for the current product applicability"):
        cra_ops.case_status(case["id"], now=awareness + 25 * 3600)


def test_vulnerability_final_report_is_anchored_to_corrective_measure():
    _, release = _product_and_release()
    awareness = 1_760_000_000.0
    case = cra_ops.open_case(
        release_id=release["id"],
        case_type="actively_exploited_vulnerability",
        title="Actively exploited issue",
        summary="Human review is required.",
        awareness_at=awareness,
        external_id="CVE-2026-9999",
        actor="analyst",
    )
    case = cra_ops.decide_case_reportability(
        case["id"],
        decision="reportable",
        rationale="Confirmed by PSIRT and legal.",
        reviewer="reviewer",
        expected_revision=case["revision"],
    )
    assert case is not None
    case = _counsel_override(case)
    measure_at = awareness + 5 * 86400
    case = cra_ops.record_case_anchor(
        case["id"],
        anchor="corrective_measure_available",
        occurred_at=measure_at,
        evidence="Release 1.2.4 publication receipt.",
        actor="release-manager",
        expected_revision=case["revision"],
    )
    status = cra_ops.case_status(case["id"], now=measure_at)
    final = next(clock for clock in status["clocks"] if clock["stage"] == "final_report")
    assert final["deadline_at"] == measure_at + 14 * 86400


def test_severe_incident_final_deadline_is_one_calendar_month_after_notification(
    _isolate_state,
):
    signer = _configure_receipt_signer(_isolate_state)
    _, release = _product_and_release()
    awareness = datetime(2026, 1, 30, 12, tzinfo=timezone.utc).timestamp()
    case = cra_ops.open_case(
        release_id=release["id"],
        case_type="severe_incident",
        title="Severe product-security incident",
        summary="Availability and integrity impact under human review.",
        awareness_at=awareness,
        external_id="INC-42",
        actor="incident-commander",
    )
    case = cra_ops.decide_case_reportability(
        case["id"],
        decision="reportable",
        rationale="Human reviewers determined the Article 14 severity test is met.",
        reviewer="counsel",
        expected_revision=case["revision"],
    )
    assert case is not None
    case = _counsel_override(case)

    packet = cra_ops.draft_report(
        case_id=case["id"],
        stage="full_notification",
        content={
            "nature": "Integrity impact",
            "initial_assessment": "Bounded investigation summary.",
            "measures": "Revoked affected credentials and issued guidance.",
        },
        evidence_node_ids=[],
        actor="incident-commander",
    )
    packet = cra_ops.approve_report(
        packet["id"],
        packet_sha256=packet["packet_sha256"],
        rationale="Content and sensitivity marking reviewed.",
        reviewer="counsel",
        expected_revision=packet["revision"],
    )
    assert packet is not None
    submitted = time.time()
    envelope = cra_ops.build_filing_receipt_envelope(
        report_id=packet["id"],
        packet_sha256=packet["packet_sha256"],
        stage=packet["stage"],
        case_id=packet["case_id"],
        submitted_at=submitted,
        channel="CRA Single Reporting Platform",
        receipt_id="SRP-123",
        signer=signer,
    )
    packet = cra_ops.record_verified_filing_receipt(
        packet["id"],
        envelope=envelope,
        actor="authorised-filer",
        expected_revision=packet["revision"],
    )
    assert packet is not None
    assert packet["submission"]["packet_sha256"] == packet["packet_sha256"]

    status = cra_ops.case_status(case["id"], now=submitted)
    final = next(clock for clock in status["clocks"] if clock["stage"] == "final_report")
    submitted_dt = datetime.fromtimestamp(submitted, timezone.utc)
    expected_year = submitted_dt.year + (1 if submitted_dt.month == 12 else 0)
    expected_month = 1 if submitted_dt.month == 12 else submitted_dt.month + 1
    expected_day = min(
        submitted_dt.day,
        calendar.monthrange(expected_year, expected_month)[1],
    )
    due = datetime.fromtimestamp(final["deadline_at"], timezone.utc)
    assert (due.year, due.month, due.day) == (
        expected_year,
        expected_month,
        expected_day,
    )

    corrupted = cra_ops._REPORTS.update(  # noqa: SLF001 - corruption regression
        packet["id"],
        lambda record: record["submission"].update({"signature": "0" * 128}),
        expected_revision=packet["revision"],
        action="test_corrupt_filing_receipt",
        actor="test",
    )
    assert corrupted is not None
    with pytest.raises(ValueError, match="signature does not verify"):
        cra_ops.case_status(case["id"], now=submitted)


def test_report_requires_human_approval_and_receipt_binds_exact_packet(
    _isolate_state,
):
    signer = _configure_receipt_signer(_isolate_state)
    _, release = _product_and_release()
    case = cra_ops.open_case(
        release_id=release["id"],
        case_type="severe_incident",
        title="Incident",
        summary="Potential severe incident.",
        awareness_at=1_760_000_000.0,
        external_id="INC-43",
        actor="incident-commander",
    )
    case = cra_ops.decide_case_reportability(
        case["id"],
        decision="reportable",
        rationale="Article 14 severity was confirmed by human review.",
        reviewer="counsel",
        expected_revision=case["revision"],
    )
    assert case is not None
    evidence = _approved_case_evidence(case)
    packet = cra_ops.draft_report(
        case_id=case["id"],
        stage="early_warning",
        content={"summary": "Early warning", "suspected_malicious": True},
        evidence_node_ids=[evidence["id"]],
        actor="incident-commander",
    )
    with pytest.raises(ValueError, match="human-approved"):
        cra_ops.record_submission_receipt(
            packet["id"],
            submitted_at=time.time(),
            channel="SRP",
            receipt_id="receipt",
            actor="filer",
            expected_revision=packet["revision"],
        )

    approved = cra_ops.approve_report(
        packet["id"],
        packet_sha256=packet["packet_sha256"],
        rationale="Reviewed and approved for filing by an authorised human.",
        reviewer="counsel",
        expected_revision=packet["revision"],
    )
    assert approved is not None
    attested = cra_ops.record_submission_receipt(
        approved["id"],
        submitted_at=time.time(),
        channel="SRP",
        receipt_id="local-attestation",
        actor="filer",
        expected_revision=approved["revision"],
    )
    assert attested is not None
    assert attested["status"] == "approved"
    assert attested["submission"] is None
    assert (
        attested["local_submission_attestation"]["verification_status"]
        == "unverified_local_attestation"
    )

    envelope = cra_ops.build_filing_receipt_envelope(
        report_id=approved["id"],
        packet_sha256=approved["packet_sha256"],
        stage=approved["stage"],
        case_id=approved["case_id"],
        submitted_at=time.time(),
        channel="SRP",
        receipt_id="verified-receipt",
        signer=signer,
    )
    receipt = cra_ops.record_verified_filing_receipt(
        approved["id"],
        envelope=envelope,
        actor="filer",
        expected_revision=attested["revision"],
    )
    assert receipt is not None
    assert receipt["submission"]["packet_sha256"] == approved["packet_sha256"]
    assert receipt["submission"]["verification_status"] == "verified_external_receipt"


def test_report_approval_requires_reviewer_digest_and_intact_packet():
    _, release = _product_and_release()
    case = cra_ops.open_case(
        release_id=release["id"],
        case_type="severe_incident",
        title="Exact approval packet",
        summary="The approving human must name the packet digest they reviewed.",
        awareness_at=1_760_000_000.0,
        external_id="INC-EXACT-APPROVAL",
        actor="incident-commander",
    )
    case = cra_ops.decide_case_reportability(
        case["id"],
        decision="reportable",
        rationale="Human reviewers confirmed this incident is reportable.",
        reviewer="counsel",
        expected_revision=case["revision"],
    )
    assert case is not None
    report = cra_ops.draft_report(
        case_id=case["id"],
        stage="early_warning",
        content={"summary": "Exact packet content."},
        evidence_node_ids=[],
        actor="incident-commander",
    )

    with pytest.raises(ValueError, match="exact current report packet"):
        cra_ops.approve_report(
            report["id"],
            packet_sha256="0" * 64,
            rationale="This digest was not the packet shown to the reviewer.",
            reviewer="counsel",
            expected_revision=report["revision"],
        )

    tampered = cra_ops._REPORTS.update(  # noqa: SLF001 - corruption regression
        report["id"],
        lambda record: record.update({"content": {"summary": "Tampered."}}),
        expected_revision=report["revision"],
        action="test_tamper_report_packet",
        actor="test",
    )
    assert tampered is not None
    with pytest.raises(ValueError, match="packet binding no longer verifies"):
        cra_ops.approve_report(
            report["id"],
            packet_sha256=report["packet_sha256"],
            rationale="A stale digest must not approve modified content.",
            reviewer="counsel",
            expected_revision=tampered["revision"],
        )


def test_case_requires_sbom_and_reportability_rechecks_exact_release_binding():
    _, release = _product_and_release(with_sbom=False)
    case_args = {
        "release_id": release["id"],
        "case_type": "severe_incident",
        "title": "Potential severe incident",
        "summary": "Exact release binding is required.",
        "awareness_at": 1_760_000_000.0,
        "external_id": "INC-SBOM",
        "actor": "analyst",
    }
    with pytest.raises(ValueError, match="SBOM"):
        cra_ops.open_case(**case_args)

    release = cra_ops.attach_sbom(
        release["id"],
        _cyclonedx(),
        source="release-pipeline",
        actor="release-manager",
        expected_revision=release["revision"],
    )
    assert release is not None
    case = cra_ops.open_case(**case_args)

    stale = cra_ops._CASES.update(  # noqa: SLF001 - legacy-record regression seam
        case["id"],
        lambda record: record.update({"release_binding_sha256": "0" * 64}),
        expected_revision=case["revision"],
        action="test_legacy_binding",
        actor="test",
    )
    assert stale is not None
    with pytest.raises(ValueError, match="stale"):
        cra_ops.decide_case_reportability(
            case["id"],
            decision="reportable",
            rationale="Human decision must not bless a stale pre-SBOM case.",
            reviewer="counsel",
            expected_revision=stale["revision"],
        )


def test_report_evidence_is_current_approved_attributed_and_revision_bound():
    _, release = _product_and_release()
    case = cra_ops.open_case(
        release_id=release["id"],
        case_type="severe_incident",
        title="Incident with graph evidence",
        summary="Evidence must be case-scoped and review-gated.",
        awareness_at=1_760_000_000.0,
        external_id="INC-EVIDENCE",
        actor="analyst",
    )
    case = cra_ops.decide_case_reportability(
        case["id"],
        decision="reportable",
        rationale="Human reviewers confirmed scope.",
        reviewer="counsel",
        expected_revision=case["revision"],
    )
    assert case is not None

    pending = evidence_graph.ingest(
        source="cra-test",
        source_id="pending-node",
        evidence_type="incident_observation",
        title="Pending evidence",
        summary="This has not been reviewed.",
        controls=["CRA:ARTICLE-14"],
        attributes={"case_id": case["id"]},
        actor="collector",
    )
    with pytest.raises(ValueError, match="approved and current"):
        cra_ops.draft_report(
            case_id=case["id"],
            stage="early_warning",
            content={"summary": "Bound notification"},
            evidence_node_ids=[pending["id"]],
            actor="analyst",
        )

    wrong = evidence_graph.ingest(
        source="cra-test",
        source_id="wrong-case",
        evidence_type="incident_observation",
        title="Wrong-case evidence",
        summary="Approved but attributed elsewhere.",
        controls=["CRA:ARTICLE-14"],
        attributes={"case_id": "CRC-another-case"},
        actor="collector",
    )
    wrong = evidence_graph.decide(
        wrong["id"],
        decision="approved",
        rationale="Payload was reviewed, but attribution is still wrong.",
        reviewer="reviewer",
        expected_revision=wrong["revision"],
    )
    assert wrong is not None
    with pytest.raises(ValueError, match="different CRA authority"):
        cra_ops.draft_report(
            case_id=case["id"],
            stage="early_warning",
            content={"summary": "Bound notification"},
            evidence_node_ids=[wrong["id"]],
            actor="analyst",
        )

    approved = _approved_case_evidence(case)
    report = cra_ops.draft_report(
        case_id=case["id"],
        stage="early_warning",
        content={"summary": "Bound notification"},
        evidence_node_ids=[approved["id"]],
        actor="analyst",
    )
    assert report["evidence_bindings"] == [{
        "id": approved["id"],
        "revision": approved["revision"],
        "payload_sha256": approved["payload_sha256"],
        "review_sha256": cra_ops._digest(approved["review"]),  # noqa: SLF001
    }]

    revoked = evidence_graph.decide(
        approved["id"],
        decision="revoked",
        rationale="Underlying observation was superseded.",
        reviewer="reviewer",
        expected_revision=approved["revision"],
    )
    assert revoked is not None
    with pytest.raises(ValueError, match="approved and current"):
        cra_ops.approve_report(
            report["id"],
            packet_sha256=report["packet_sha256"],
            rationale="This must not approve revoked evidence.",
            reviewer="counsel",
            expected_revision=report["revision"],
        )


def test_product_pack_refuses_report_after_approved_evidence_is_revoked():
    product, _, _, evidence, _ = _approved_case_report()
    pack = cra_ops.render_product_security_pack(product["id"])
    assert len(pack["approved_reports"]) == 1

    revoked = evidence_graph.decide(
        evidence["id"],
        decision="revoked",
        rationale="The previously approved observation was superseded.",
        reviewer="evidence-reviewer",
        expected_revision=evidence["revision"],
    )
    assert revoked is not None
    with pytest.raises(ValueError, match="approved and current"):
        cra_ops.render_product_security_pack(product["id"])


def test_product_pack_refuses_report_after_approved_evidence_expires(monkeypatch):
    expires_at = time.time() + 60
    product, _, _, _, _ = _approved_case_report(valid_until=expires_at)
    pack = cra_ops.render_product_security_pack(product["id"])
    assert len(pack["approved_reports"]) == 1

    monkeypatch.setattr(evidence_graph.time, "time", lambda: expires_at + 1)
    with pytest.raises(ValueError, match="approved and current"):
        cra_ops.render_product_security_pack(product["id"])


@pytest.mark.parametrize("authority", ["packet", "approval"])
def test_product_pack_refuses_tampered_packet_or_human_approval(authority):
    product, _, _, _, report = _approved_case_report()
    pack = cra_ops.render_product_security_pack(product["id"])
    assert len(pack["approved_reports"]) == 1

    def _tamper(record):
        if authority == "packet":
            record["content"] = {"summary": "Content changed after approval."}
        else:
            record["review"]["approved_packet_sha256"] = "0" * 64

    changed = cra_ops._REPORTS.update(  # noqa: SLF001 - corruption regression
        report["id"],
        _tamper,
        expected_revision=report["revision"],
        action=f"test_{authority}_tamper",
        actor="test",
    )
    assert changed is not None
    with pytest.raises(ValueError, match="packet binding|exact current packet"):
        cra_ops.render_product_security_pack(product["id"])


@pytest.mark.parametrize("authority", ["applicability", "release"])
def test_product_pack_refuses_report_after_bound_authority_drifts(authority):
    product, release, _, _, _ = _approved_case_report()
    pack = cra_ops.render_product_security_pack(product["id"])
    assert len(pack["approved_reports"]) == 1

    if authority == "applicability":
        changed = cra_ops._PRODUCTS.update(  # noqa: SLF001 - regression seam
            product["id"],
            lambda record: record.update({"description": "Changed scope."}),
            expected_revision=product["revision"],
            action="test_applicability_drift",
            actor="test",
        )
    else:
        changed = cra_ops._RELEASES.update(  # noqa: SLF001 - regression seam
            release["id"],
            lambda record: record.update({"artifact_sha256": "b" * 64}),
            expected_revision=release["revision"],
            action="test_release_drift",
            actor="test",
        )
    assert changed is not None
    with pytest.raises(ValueError, match="stale|binding"):
        cra_ops.render_product_security_pack(product["id"])


def test_temporal_anchors_fail_closed_on_future_and_out_of_order_values():
    _, release = _product_and_release()
    with pytest.raises(ValueError, match="future"):
        cra_ops.open_case(
            release_id=release["id"],
            case_type="actively_exploited_vulnerability",
            title="Future observation",
            summary="A future clock cannot be asserted.",
            awareness_at=time.time() + 301,
            external_id="CVE-FUTURE",
            actor="analyst",
        )

    awareness = time.time() - 3600
    case = cra_ops.open_case(
        release_id=release["id"],
        case_type="actively_exploited_vulnerability",
        title="Chronology test",
        summary="Anchor ordering is checked.",
        awareness_at=awareness,
        external_id="CVE-CHRONOLOGY",
        actor="analyst",
    )
    case = cra_ops.decide_case_reportability(
        case["id"],
        decision="reportable",
        rationale="Human reviewers confirmed reportability.",
        reviewer="counsel",
        expected_revision=case["revision"],
    )
    assert case is not None
    with pytest.raises(ValueError, match="precede awareness"):
        cra_ops.record_case_anchor(
            case["id"],
            anchor="corrective_measure_available",
            occurred_at=awareness - 1,
            evidence="Invalid historical ordering.",
            actor="release-manager",
            expected_revision=case["revision"],
        )
    with pytest.raises(ValueError, match="future"):
        cra_ops.record_case_anchor(
            case["id"],
            anchor="corrective_measure_available",
            occurred_at=time.time() + 301,
            evidence="Invalid future anchor.",
            actor="release-manager",
            expected_revision=case["revision"],
        )


def test_disabled_and_malformed_global_config_deny_all_authority_mutations(
    _isolate_state,
):
    _isolate_state.write_text("[cra_ops]\nenable = false\n", encoding="utf-8")
    maverick_config.reset_config_cache()

    class _DummySigner:
        key_id = "0" * 16

        def sign(self, _message: bytes) -> str:
            return "0" * 128

    operations = [
        lambda: cra_ops.create_product(
            name="x", description="x", manufacturer="x", product_kind="x",
            support_until=1, actor="x",
        ),
        lambda: cra_ops.decide_product_applicability(
            "x", decision="applicable", rationale="x", reviewer="x",
            expected_revision=1,
        ),
        lambda: cra_ops.create_release(
            product_id="x", version="x", artifact_sha256="0" * 64,
            released_at=1, actor="x",
        ),
        lambda: cra_ops.attach_sbom(
            "x", {}, source="x", actor="x", expected_revision=1,
        ),
        lambda: cra_ops.open_case(
            release_id="x", case_type="severe_incident", title="x", summary="x",
            awareness_at=1, external_id="x", actor="x",
        ),
        lambda: cra_ops.decide_case_reportability(
            "x", decision="reportable", rationale="x", reviewer="x",
            expected_revision=1,
        ),
        lambda: cra_ops.override_case_effective_date(
            "x", rationale="x", counsel="x", expected_revision=1,
        ),
        lambda: cra_ops.record_case_anchor(
            "x", anchor="corrective_measure_available", occurred_at=1,
            evidence="x", actor="x", expected_revision=1,
        ),
        lambda: cra_ops.draft_report(
            case_id="x", stage="early_warning", content={"summary": "x"},
            evidence_node_ids=[], actor="x",
        ),
        lambda: cra_ops.approve_report(
            "x", packet_sha256="0" * 64, rationale="x", reviewer="x",
            expected_revision=1,
        ),
        lambda: cra_ops.record_submission_receipt(
            "x", submitted_at=1, channel="x", receipt_id="x", actor="x",
            expected_revision=1,
        ),
        lambda: cra_ops.record_verified_filing_receipt(
            "x", envelope={}, actor="x", expected_revision=1,
        ),
        lambda: cra_ops.create_vex(
            release_id="x", vulnerability_id="x", status="affected",
            justification="x", impact_statement="x", actor="x",
        ),
        lambda: cra_ops.decide_vex(
            "x", decision="approved", rationale="x", reviewer="x",
            expected_revision=1,
        ),
        lambda: cra_ops.case_status("x"),
        lambda: cra_ops.render_product_security_pack("x"),
        lambda: cra_ops.collect_evidence(actor="x"),
        lambda: cra_ops.build_filing_receipt_envelope(
            report_id="x", packet_sha256="0" * 64, stage="early_warning",
            case_id="x", submitted_at=1, channel="x", receipt_id="x",
            signer=_DummySigner(),
        ),
    ]
    for operation in operations:
        with pytest.raises(RuntimeError, match="disabled by deployment policy"):
            operation()

    _isolate_state.write_text("[cra_ops\nenable = true", encoding="utf-8")
    maverick_config.reset_config_cache()
    with pytest.raises(RuntimeError, match="disabled by deployment policy"):
        cra_ops.create_product(
            name="x",
            description="x",
            manufacturer="x",
            product_kind="x",
            support_until=1,
            actor="x",
        )


def test_vex_is_review_gated_and_product_security_pack_is_signed():
    product, release = _product_and_release()
    vex = cra_ops.create_vex(
        release_id=release["id"],
        vulnerability_id="CVE-2026-12345",
        status="not_affected",
        justification="vulnerable_code_not_present",
        impact_statement="The affected optional parser is not included.",
        actor="psirt-analyst",
    )
    assert vex["review"]["status"] == "pending"
    vex = cra_ops.decide_vex(
        vex["id"],
        decision="approved",
        rationale="Reproduced against the exact release artifact and SBOM.",
        reviewer="product-security-reviewer",
        expected_revision=vex["revision"],
    )
    assert vex is not None

    pack = cra_ops.render_product_security_pack(product["id"])
    assert pack["kind"] == "lightwork-cra-product-security-pack"
    assert pack["product"]["id"] == product["id"]
    assert pack["pack_sha256"]
    assert "signature" in pack
    assert pack["legal_notice"].startswith("Human legal determination")

    projected = cra_ops.collect_evidence(actor="assurance-collector")
    assert len(projected) == 2
    assert {row["status"] for row in projected} == {"approved"}
    assert {
        row["review"]["inherited_from"] for row in projected
    } == {product["id"], vex["id"]}
    assert all(len(row["review"]["authority_sha256"]) == 64 for row in projected)
    assert evidence_graph.coverage_report([
        "CRA:PRODUCT-SCOPE",
        "CRA:VULNERABILITY-HANDLING",
    ])["coverage_percent"] == 100.0


def test_projected_vex_fails_closed_after_release_authority_drifts():
    _, release = _product_and_release()
    vex = cra_ops.create_vex(
        release_id=release["id"],
        vulnerability_id="CVE-2026-STALE-GRAPH",
        status="affected",
        justification="component_present",
        impact_statement="The reviewed release contains the affected component.",
        actor="psirt-analyst",
    )
    vex = cra_ops.decide_vex(
        vex["id"],
        decision="approved",
        rationale="The human reviewer approved this exact VEX authority.",
        reviewer="product-security-reviewer",
        expected_revision=vex["revision"],
    )
    assert vex is not None
    projected = cra_ops.collect_evidence(actor="assurance-collector")
    vex_node = next(
        node for node in projected if node["evidence_type"] == "approved_vex"
    )
    assert evidence_graph.get(vex_node["id"])["status"] == "approved"

    changed = cra_ops._RELEASES.update(  # noqa: SLF001 - authority drift regression
        release["id"],
        lambda record: record.update({"artifact_sha256": "b" * 64}),
        expected_revision=release["revision"],
        action="test_release_drift_after_graph_projection",
        actor="test",
    )
    assert changed is not None

    current = evidence_graph.get(vex_node["id"])
    assert current is not None
    assert current["status"] == "authority_stale"
    assert current["freshness"] == "stale"
    assert current["authority_validation"]["reason"] == "source_authority_invalid"
    coverage = evidence_graph.coverage_report([
        "CRA:PRODUCT-SCOPE",
        "CRA:VULNERABILITY-HANDLING",
    ])
    assert coverage["covered_controls"] == ["CRA:PRODUCT-SCOPE"]
    assert coverage["missing_controls"] == ["CRA:VULNERABILITY-HANDLING"]
    assert coverage["coverage_percent"] == 50.0

    pack = evidence_graph.render_pack(required_controls=[
        "CRA:PRODUCT-SCOPE",
        "CRA:VULNERABILITY-HANDLING",
    ])
    packed_vex = next(node for node in pack["nodes"] if node["id"] == vex_node["id"])
    assert packed_vex["status"] == "authority_stale"
    assert pack["coverage"] == coverage


def test_product_pack_refuses_tampered_approved_vex():
    product, release = _product_and_release()
    vex = cra_ops.create_vex(
        release_id=release["id"],
        vulnerability_id="CVE-2026-EXACT-VEX",
        status="affected",
        justification="component_present",
        impact_statement="The exact release contains the affected component.",
        actor="psirt-analyst",
    )
    vex = cra_ops.decide_vex(
        vex["id"],
        decision="approved",
        rationale="The human reviewer approved this exact VEX authority.",
        reviewer="product-security-reviewer",
        expected_revision=vex["revision"],
    )
    assert vex is not None
    assert len(cra_ops.render_product_security_pack(product["id"])["approved_vex"]) == 1

    changed = cra_ops._VEX.update(  # noqa: SLF001 - corruption regression
        vex["id"],
        lambda record: record.update({"impact_statement": "Tampered after review."}),
        expected_revision=vex["revision"],
        action="test_tamper_approved_vex",
        actor="test",
    )
    assert changed is not None
    with pytest.raises(ValueError, match="VEX authority binding"):
        cra_ops.render_product_security_pack(product["id"])
    with pytest.raises(ValueError, match="VEX authority binding"):
        cra_ops.collect_evidence(actor="assurance-collector")
    assert not any(
        node.get("evidence_type") == "approved_vex"
        for node in evidence_graph.list_nodes()
    )


@pytest.mark.parametrize("authority", ["release", "sbom"])
def test_cra_evidence_projection_refuses_vex_after_release_authority_drift(
    authority,
):
    _, release = _product_and_release()
    vex = cra_ops.create_vex(
        release_id=release["id"],
        vulnerability_id=f"CVE-2026-PROJECTION-{authority.upper()}",
        status="affected",
        justification="component_present",
        impact_statement="The reviewed release contains the affected component.",
        actor="psirt-analyst",
    )
    vex = cra_ops.decide_vex(
        vex["id"],
        decision="approved",
        rationale="The human reviewer approved the exact VEX authority.",
        reviewer="product-security-reviewer",
        expected_revision=vex["revision"],
    )
    assert vex is not None

    def _corrupt(record):
        if authority == "release":
            record["artifact_sha256"] = "b" * 64
        else:
            record["sbom"]["components"][0]["name"] = "tampered-after-review"

    changed = cra_ops._RELEASES.update(  # noqa: SLF001 - corruption regression
        release["id"],
        _corrupt,
        expected_revision=release["revision"],
        action=f"test_{authority}_drift_after_vex_review",
        actor="test",
    )
    assert changed is not None
    with pytest.raises(ValueError, match="release/SBOM|normalized SBOM"):
        cra_ops.collect_evidence(actor="assurance-collector")
    assert not any(
        node.get("evidence_type") == "approved_vex"
        for node in evidence_graph.list_nodes()
    )
