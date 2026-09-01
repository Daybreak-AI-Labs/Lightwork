from __future__ import annotations

import copy
import hashlib
import json

import pytest
from maverick import ai_vendor_portal as portal
from maverick import ai_vendor_portal_pilot as pilot
from maverick import ai_vendor_risk as vendor_risk
from maverick.paths import tenant_scope


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    (tmp_path / "config.toml").write_text(
        "[evidence_graph]\n"
        "enable = true\n"
        "\n[model_risk_assurance]\n"
        "enable = true\n"
        "gate_promotions = true\n"
        "\n[ai_vendor_risk]\n"
        "enable = true\n"
        "portal_enable = true\n",
        encoding="utf-8",
    )
    from maverick import audit, config

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *_args, **_kwargs: True)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _declaration(**overrides) -> pilot.PilotDeclaration:
    values = {
        "estate_ref": "estate-consent-001",
        "authorized_by": "user:vendor-risk-owner",
        "consent_record_sha256": hashlib.sha256(b"test-consent").hexdigest(),
        "consent_confirmed": True,
        "synthetic_documents_only": True,
    }
    values.update(overrides)
    return pilot.PilotDeclaration(**values)


def _questionnaire() -> dict:
    with tenant_scope(tenant="tenant-a"):
        return vendor_risk.questionnaire()


def test_consent_and_synthetic_scope_are_mandatory():
    for declaration, message in (
        (_declaration(consent_confirmed=False), "consent"),
        (_declaration(synthetic_documents_only=False), "synthetic"),
        (_declaration(consent_record_sha256="bad"), "digest"),
    ):
        with pytest.raises(pilot.PilotConsentError, match=message):
            pilot.run_local_vendor_portal_pilot(
                declaration=declaration,
                tenant="tenant-a",
                isolation_tenant="tenant-b",
                run_id="VPIL-consent-test",
            )
    with pytest.raises(pilot.PilotConsentError, match="between 1"):
        pilot.run_local_vendor_portal_pilot(
            declaration=_declaration(),
            tenant="tenant-a",
            isolation_tenant="tenant-b",
            cases=(),
            run_id="VPIL-empty-test",
        )


def test_local_pilot_exercises_documents_retry_replay_revocation_and_isolation():
    questionnaire = _questionnaire()
    evidence = pilot.run_local_vendor_portal_pilot(
        declaration=_declaration(),
        tenant="tenant-a",
        isolation_tenant="tenant-b",
        expected_questionnaire_digest=questionnaire["template_digest"],
        run_id="VPIL-full-local-test",
        now=1_800_000_000.0,
    )

    assert evidence["status"] == "passed_local_synthetic"
    assert evidence["scope"]["case_count"] == 2
    assert evidence["contracts"]["questionnaire_pin_state"] == "operator_pinned"
    assert set(evidence["contracts"]["drift_probes"].values()) == {True}
    assert set(evidence["operations"].values()) == {
        "passed",
        "passed_pending_human_decision",
        "passed_same_process",
    }
    assert evidence["acceptance_boundary"] == {
        "notice": pilot.PILOT_BOUNDARY,
        "live_vendor_acceptance": "not_run",
        "live_customer_acceptance": "not_run",
        "live_ecosystem_acceptance": "not_run",
            "independent_document_verification": "not_run",
            "legal_or_certification_outcome": False,
            "evidence_integrity": "unsigned_local_checksum_only",
            "tamper_evident_claimed": False,
        }
    assert evidence["revocation"]["revoked_token_rejected"] is True
    assert evidence["revocation"]["cross_tenant_access_rejected"] is True
    assert pilot.verify_pilot_evidence(evidence)
    for result in evidence["cases"]:
        assert result["exact_submission_retry"] is True
        assert result["import_idempotent_replay"] is True
        assert result["tenant_token_isolation"] is True
        assert result["tenant_record_isolation"] is True
        assert result["human_review_pending"] is True
        assert result["vendor_attestation_only"] is True

    serialized = json.dumps(evidence)
    assert "Synthetic Documented Controls Vendor" not in serialized
    assert "security-office" not in serialized
    assert "tenant-a" not in serialized
    assert "Model provenance is documented" not in serialized
    assert "aivp1." not in serialized

    with tenant_scope(tenant="tenant-a"):
        submissions = portal.list_submissions()
        assessments = vendor_risk.list_vendor_assessments()
        invitations = portal.list_invitations()
    assert len(submissions) == 2
    assert {row["status"] for row in submissions} == {"imported"}
    assert assessments["count"] == 2
    assert len(invitations) == 3
    assert {row["status"] for row in invitations} == {"consumed", "revoked"}


def test_contract_validators_fail_closed_on_shape_digest_and_binding_drift():
    questionnaire = _questionnaire()
    contract = pilot.validate_questionnaire_contract(questionnaire)
    assert contract["template_digest"] == questionnaire["template_digest"]
    with pytest.raises(pilot.PilotContractDrift, match="fields drifted"):
        pilot.validate_questionnaire_contract({**questionnaire, "extra": True})
    with pytest.raises(pilot.PilotContractDrift, match="digest drifted"):
        pilot.validate_questionnaire_contract(
            questionnaire,
            expected_digest="0" * 64,
        )

    case = pilot.representative_synthetic_cases()[0]
    with tenant_scope(tenant="tenant-a"):
        extracted = vendor_risk.extract_answers_from_document(
            case.document,
            case.mime,
            source="synthetic-pilot",
            document_id=case.case_ref,
        )
    document_contract = pilot.validate_document_contract(
        extracted,
        question_ids=frozenset(contract["question_ids"]),
        expected_document_sha256=hashlib.sha256(case.document).hexdigest(),
    )
    assert document_contract["answers"] == dict(case.expected_document_answers)
    changed = copy.deepcopy(extracted)
    first = next(iter(changed["answers"].values()))
    first["citations"][0]["source_binding_sha256"] = "0" * 64
    with pytest.raises(pilot.PilotContractDrift, match="citation binding"):
        pilot.validate_document_contract(
            changed,
            question_ids=frozenset(contract["question_ids"]),
        )


def test_evidence_integrity_rejects_tamper_and_boundary_promotion():
    evidence = pilot.run_local_vendor_portal_pilot(
        declaration=_declaration(),
        tenant="tenant-a",
        isolation_tenant="tenant-b",
        cases=(pilot.representative_synthetic_cases()[0],),
        run_id="VPIL-evidence-test",
        now=1_800_000_001.0,
    )
    tampered = copy.deepcopy(evidence)
    tampered["operations"]["local_ecosystem_workflow"] = "failed"
    assert not pilot.verify_pilot_evidence(tampered)

    promoted = copy.deepcopy(evidence)
    promoted["acceptance_boundary"]["live_vendor_acceptance"] = "passed"
    promoted.pop("evidence_sha256")
    promoted["evidence_sha256"] = pilot._sha256(promoted)
    assert not pilot.verify_pilot_evidence(promoted)


def test_cli_requires_flags_and_writes_exclusive_machine_readable_evidence(
    tmp_path,
):
    output = tmp_path / "vendor-portal-pilot.json"
    digest = _questionnaire()["template_digest"]
    arguments = [
        "--tenant",
        "tenant-a",
        "--isolation-tenant",
        "tenant-b",
        "--estate-ref",
        "estate-consent-001",
        "--authorized-by",
        "user:vendor-risk-owner",
        "--consent-record-sha256",
        hashlib.sha256(b"test-consent").hexdigest(),
        "--expected-questionnaire-digest",
        digest,
        "--run-id",
        "VPIL-cli-test",
        "--consent-confirmed",
        "--synthetic-documents-only",
        "--output",
        str(output),
    ]
    assert pilot.main(arguments) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert pilot.verify_pilot_evidence(evidence)
    assert evidence["acceptance_boundary"]["evidence_integrity"] == (
        "unsigned_local_checksum_only"
    )
    assert evidence["acceptance_boundary"]["tamper_evident_claimed"] is False
    assert pilot.main(arguments) == 2
