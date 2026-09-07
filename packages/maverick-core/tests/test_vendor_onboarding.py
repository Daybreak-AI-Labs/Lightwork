"""Offline acceptance of approval/evidence/effect/customer boundaries."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from maverick import config
from maverick.governed_operations import ServiceContext, digest, operation_binding
from maverick.governed_records import RecordConflict
from maverick.paths import tenant_scope
from maverick.vendor_onboarding import VendorOnboardingService
from maverick.vendor_onboarding_adapters import (
    EvidenceDocument,
    FakeGoogleDriveEvidence,
    FakeJiraTickets,
)

NOW = 1_800_000_000.0
TEXT = b"We do not train models on customer data. Data is encrypted at rest and in transit."
POLICY = Path(__file__).resolve().parents[3] / "scripts/acceptance/fixtures/vendor-documents/northstar-policy.pdf"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    (tmp_path / "config.toml").write_text(
        "[vendor_onboarding]\nenable=true\n[vendor_management]\nenable=true\n"
        "[ai_vendor_risk]\nenable=true\n[model_risk_assurance]\nenable=true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    from maverick import audit

    monkeypatch.setattr(audit, "record", lambda *a, **kw: True)
    monkeypatch.setattr(audit, "record_global", lambda *a, **kw: True)
    ctx = ServiceContext(
        "customer-a", "reviewer@example.test", "procurement",
        "reviewer@example.test", "jira", human=True,
        authorization_role="operator", authorization_policy="operator-policy-v1",
        budget_authority="tenant-budget-v1",
        workspace_authority="workspace-generation-1",
    )
    docs = FakeGoogleDriveEvidence({(ctx.tenant, "security-paper"): EvidenceDocument(
        "security-paper", "1", NOW - 86400, TEXT,
    )})
    tickets = FakeJiraTickets()
    denied = set()
    svc = VendorOnboardingService(docs, tickets, lambda c, cap, res: cap not in denied,
                                  clock=lambda: NOW)
    yield svc, ctx, docs, tickets, denied
    config.reset_config_cache()


def reviewed(svc, ctx):
    row = svc.intake(ctx, "Synthetic Acme AI", "security-paper", "VEND", "Draft support replies")
    row = svc.collect_evidence(ctx, row["id"], row["revision"])
    assert row["evidence"]["status"] == "available"
    row = svc.assess(ctx, row["id"], row["revision"])
    return svc.review(ctx, row["id"], row["revision"], "approved",
                      "Gaps accepted for restricted synthetic pilot", review_seconds=12.5)


def approved(svc, ctx):
    row = reviewed(svc, ctx)
    row = svc.prepare_action(ctx, row["id"], row["revision"])
    return svc.approve_action(ctx, row["id"], row["revision"])


def test_worker_authority_schema_is_explicit_and_closed(setup):
    _, ctx, *_ = setup
    assert ctx.binding()["authority_schema"] == "lightwork.worker-authority.v1"
    with pytest.raises(ValueError, match="unsupported worker authority schema"):
        replace(ctx, authority_schema="lightwork.worker-authority.v2")


@pytest.mark.parametrize("purpose", [None, "", " \t\n", "x" * 4001],
                         ids=["none", "empty", "whitespace", "oversized"])
def test_invalid_intake_purpose_does_not_register_vendor(setup, purpose):
    from maverick import vendor_management

    svc, ctx, _, tickets, _ = setup
    with pytest.raises(ValueError, match="purpose"):
        svc.intake(ctx, "Rejected intake vendor", "security-paper", "VEND", purpose)
    with tenant_scope(tenant=ctx.tenant):
        assert vendor_management.list_vendors() == []
    assert svc.list(ctx) == []
    assert tickets.creates == 0


def test_journey_contract_is_pinned_and_legacy_records_keep_v1(setup, monkeypatch):
    from maverick import vendor_onboarding

    svc, ctx, *_ = setup
    admitted = svc.intake(ctx, "Synthetic pinned contract", "security-paper", "VEND",
                          "Evaluate a limited support assistant")
    contract = admitted["journey_contract"]
    assert contract == vendor_onboarding.CURRENT_JOURNEY_CONTRACT_REF
    assert admitted["work_item"]["process"] == contract
    assert admitted["work_item"]["objective"] == "Evaluate a limited support assistant"
    assert admitted["work_item"]["supported_deployment"]["topology"] == "single_persistent_application_replica"
    assert admitted["work_item"]["technical_effect"] == "not_verified"
    assert admitted["work_item"]["business_outcome"] == "pending"

    legacy = svc.intake(ctx, "Synthetic legacy contract", "security-paper", "VEND", "Legacy request")
    with tenant_scope(tenant=ctx.tenant):
        stored = vendor_onboarding._CASES.update(
            legacy["id"], lambda value: value.pop("journey_contract"),
            expected_revision=legacy["revision"], action="test_legacy_fixture", actor=ctx.actor,
        )

    future = dict(contract, version=contract["version"] + 1, sha256="f" * 64)
    monkeypatch.setattr(vendor_onboarding, "CURRENT_JOURNEY_CONTRACT_REF", future)
    reopened = svc.get(ctx, admitted["id"])
    assert reopened["journey_contract"] == contract
    assert reopened["work_item"]["process"] == contract

    reopened_legacy = svc.get(ctx, legacy["id"])
    assert reopened_legacy["revision"] == stored["revision"]
    assert reopened_legacy["journey_contract"] == vendor_onboarding.JOURNEY_CONTRACT_V1_REF
    assert reopened_legacy["work_item"]["contract_binding"] == "legacy_schema_default"
    assert next(row for row in svc.list(ctx) if row["id"] == legacy["id"])["work_item"] == reopened_legacy["work_item"]


def test_unknown_case_or_journey_contract_cannot_reach_mutation(setup):
    from maverick import vendor_onboarding

    svc, ctx, _, tickets, _ = setup
    row = svc.intake(ctx, "Synthetic unsupported contract", "security-paper", "VEND", "Rejected mutation")
    with tenant_scope(tenant=ctx.tenant):
        unsupported = vendor_onboarding._CASES.update(
            row["id"], lambda value: value.update(schema="lightwork.vendor-onboarding.v999"),
            expected_revision=row["revision"], action="test_unknown_schema_fixture", actor=ctx.actor,
        )
    with pytest.raises(ValueError, match="unsupported vendor onboarding schema"):
        svc.set_documents(ctx, row["id"], unsupported["revision"], [], [])
    with tenant_scope(tenant=ctx.tenant):
        assert vendor_onboarding._CASES.get(row["id"]) == unsupported
    assert tickets.creates == 0

    with tenant_scope(tenant=ctx.tenant):
        supported_schema = vendor_onboarding._CASES.update(
            row["id"], lambda value: value.update(
                schema="lightwork.vendor-onboarding.v1",
                journey_contract={"id": "supplier-evidence-onboarding", "version": 999, "sha256": "0" * 64},
            ), expected_revision=unsupported["revision"], action="test_unknown_contract_fixture", actor=ctx.actor,
        )
    with pytest.raises(ValueError, match="unsupported journey contract"):
        svc.set_documents(ctx, row["id"], supported_schema["revision"], [], [])
    with tenant_scope(tenant=ctx.tenant):
        assert vendor_onboarding._CASES.get(row["id"]) == supported_schema
    assert tickets.creates == 0


@pytest.mark.parametrize(("stage", "lifecycle", "next_action"), [
    ("intake", "Draft", "obtain_evidence"),
    ("evidence_collected", "Ready", "build_assessment"),
    ("pending_review", "Awaiting approval", "review_assessment"),
    ("review_approved", "Ready", "prepare_action"),
    ("review_rejected", "Failed", "correct_or_close"),
    ("action_pending_approval", "Awaiting approval", "approve_action"),
    ("action_approved", "Ready", "execute_action"),
    ("executing", "Running", "wait_or_reconcile"),
    ("reconciliation_required", "Needs reconciliation", "reconcile"),
    ("execution_completed", "Verified", "accept_or_reject"),
    ("accepted", "Accepted", "check_renewal"),
    ("customer_rejected", "Failed", "closed"),
])
def test_every_case_stage_has_an_explicit_work_item_state(setup, stage, lifecycle, next_action):
    svc, ctx, *_ = setup
    row = svc.intake(ctx, "Synthetic state map", "security-paper", "VEND", "Map state")
    operation = {"state": "verified"} if stage in {"execution_completed", "accepted", "customer_rejected"} else None
    outcome = {"accepted": stage == "accepted"} if stage in {"accepted", "customer_rejected"} else None
    projected = svc._present(dict(row, stage=stage, operation=operation, outcome=outcome))
    assert projected["work_item"]["lifecycle"] == lifecycle
    assert projected["work_item"]["next_action"] == next_action


def test_multiple_documents_conflicts_and_secondary_version_guard(setup):
    svc, ctx, docs, tickets, _ = setup
    docs.documents[(ctx.tenant, "policy")] = EvidenceDocument("policy", "1", NOW - 100,
        b"Model provenance is documented with provider and version.")
    docs.documents[(ctx.tenant, "exception")] = EvidenceDocument("exception", "1", NOW - 100,
        b"Model provenance is undisclosed.")
    row = svc.intake(ctx, "Synthetic multiple sources", "", "VEND", "Source comparison",
                     document_ids=["policy", "exception"])
    row = svc.collect_evidence(ctx, row["id"], row["revision"])
    assert row["document_ids"] == ["exception", "policy"]
    assert svc.current_evidence_version(ctx, row) == row["evidence"]["version"]
    reordered = dict(row, evidence=dict(row["evidence"], documents=list(reversed(row["evidence"]["documents"]))))
    assert svc.current_evidence_version(ctx, reordered) == row["evidence"]["version"]
    assert svc._evidence_set(list(reversed(row["evidence"]["documents"]))) == svc._evidence_set(row["evidence"]["documents"])
    row = svc.assess(ctx, row["id"], row["revision"])
    assert row["assessment"]["answers"]["aiv_provenance"]["answer"] == "unknown"
    assert "Conflicting" in row["assessment"]["answers"]["aiv_provenance"]["note"]
    row = svc.review(ctx, row["id"], row["revision"], "approved", "Human accepts documented unresolved conflict")
    row = svc.prepare_action(ctx, row["id"], row["revision"])
    row = svc.approve_action(ctx, row["id"], row["revision"])
    docs.documents[(ctx.tenant, "policy")] = replace(docs.documents[(ctx.tenant, "policy")], version="2")
    with pytest.raises(ValueError) as rejected:
        svc.execute(ctx, row["id"])
    assert rejected.value.code == "approval_preconditions_changed"
    assert tickets.creates == 0


def test_case_scoped_pdf_upload_clears_approval_and_cannot_inherit_new_upload_time(setup):
    svc, ctx, _, tickets, _ = setup
    row = approved(svc, ctx)
    operation = row["operation"]["id"]
    row = svc.upload_document(ctx, row["id"], row["revision"], content=POLICY.read_bytes(),
        name="Synthetic policy.pdf", mime="application/pdf", document_date=NOW - 400 * 86400)
    assert row["assessment"] is None and row["operation"] is None
    assert row["operation_history"][-1]["id"] == operation
    assert row["operation_history"][-1]["state"] == "invalidated"
    uploaded = row["uploads"][0]
    assert uploaded["fresh_until"] < NOW
    assert uploaded["date_basis"] == "uploader_asserted_document_date"
    row = svc.collect_evidence(ctx, row["id"], row["revision"])
    assert row["evidence"]["source"] == "evidence_set"
    row = svc.assess(ctx, row["id"], row["revision"])
    row = svc.review(ctx, row["id"], row["revision"], "approved", "Synthetic expired evidence review")
    with pytest.raises(ValueError, match="freshness expired"):
        svc.prepare_action(ctx, row["id"], row["revision"])
    assert tickets.creates == 0
    other = svc.intake(ctx, "Synthetic other upload owner", "", "VEND", "Cannot reuse another case upload")
    with pytest.raises(ValueError, match="belonging to this case"):
        svc.set_documents(ctx, other["id"], other["revision"], [], [uploaded["document_id"]])


def test_reviewed_upload_is_transient_bound_to_actor_revision_and_explicit_consent(setup, monkeypatch):
    svc, ctx, _, tickets, denied = setup
    row = svc.intake(ctx, "Reviewed upload", "", "VEND", "Review file")
    args = dict(content=TEXT, name="Synthetic.txt", mime="text/plain", document_date=NOW - 100)
    preview = svc.preview_upload(ctx, row["id"], row["revision"], **args)
    assert preview["text"] == TEXT.decode() and preview["saved"] is False
    assert svc.get(ctx, row["id"])["revision"] == row["revision"]
    assert svc.get(ctx, row["id"])["uploads"] == []
    commit = dict(args, expires_at=preview["expires_at"], review_digest=preview["review_digest"], confirm=True)
    with pytest.raises(ValueError, match="account or request changed"):
        svc.upload_reviewed(replace(ctx, actor="different-reviewer"), row["id"], row["revision"], **commit)
    with pytest.raises(ValueError, match="Confirm"):
        svc.upload_reviewed(ctx, row["id"], row["revision"], **dict(commit, confirm=False))
    with pytest.raises(ValueError, match="account or request changed"):
        svc.upload_reviewed(ctx, row["id"], row["revision"], **dict(commit, content=b"Changed text"))
    with pytest.raises(ValueError, match="expired"):
        svc.upload_reviewed(ctx, row["id"], row["revision"], **dict(commit, expires_at=NOW))
    with pytest.raises(KeyError):
        svc.upload_reviewed(replace(ctx, tenant="another-customer"), row["id"], row["revision"], **commit)
    denied.add("evidence.upload")
    with pytest.raises(PermissionError):
        svc.upload_reviewed(ctx, row["id"], row["revision"], **commit)
    denied.clear()
    saved = svc.upload_reviewed(ctx, row["id"], row["revision"], **commit)
    assert saved["uploads"][0]["reviewed_by"] == ctx.actor
    assert saved["assessment"] is None and tickets.creates == 0
    with pytest.raises(RecordConflict):
        svc.upload_reviewed(ctx, row["id"], row["revision"], **commit)
    monkeypatch.setattr(config, "vendor_reviewed_uploads_enabled", lambda: False)
    with pytest.raises(PermissionError, match="disabled"):
        svc.preview_upload(ctx, saved["id"], saved["revision"], **args)


def test_pdf_transcription_keeps_source_bytes_separate_and_never_invents_pdf_locations(setup):
    import hashlib

    svc, ctx, _, _, _ = setup
    row = svc.intake(ctx, "Transcribed upload", "", "VEND", "Review unreadable PDF")
    original = b"%PDF-1.4\nunsupported scanned synthetic fixture"
    args = dict(content=original, name="Scan.pdf", mime="application/pdf", document_date=NOW - 100)
    refused = svc.preview_upload(ctx, row["id"], row["revision"], **args)
    assert refused["status"] == "extraction_failed" and refused["can_transcribe"]
    with pytest.raises(ValueError, match="No supported text"):
        svc.upload_reviewed(ctx, row["id"], row["revision"], **args, confirm=True,
                            review_digest=refused["review_digest"], expires_at=refused["expires_at"])
    args["transcription"] = "Model provenance is documented with provider and version."
    preview = svc.preview_upload(ctx, row["id"], row["revision"], **args)
    saved = svc.upload_reviewed(ctx, row["id"], row["revision"], **args, confirm=True,
                               review_digest=preview["review_digest"], expires_at=preview["expires_at"])
    upload = saved["uploads"][0]
    citation = upload["extracted"]["answers"]["aiv_provenance"]["citations"][0]
    assert citation["document_sha256"] == hashlib.sha256(original).hexdigest()
    assert citation["extracted_text_sha256"] == hashlib.sha256(args["transcription"].encode()).hexdigest()
    assert citation["text_origin"] == "human_transcription" and citation["transcription_paragraph"] == 1
    assert "pages" not in citation
    assert upload["extraction"]["method"] == "human_transcription"


def test_uploaded_only_case_preserves_evidence_for_renewal_and_rechecks_snapshot(setup, monkeypatch):
    from maverick.vendor_evidence_evolution import EvidenceRenewalEvolution

    svc, ctx, _, tickets, _ = setup
    def forbidden_drive(*args, **kwargs):
        raise PermissionError("No Drive connection enrolled for uploaded evidence")
    monkeypatch.setattr(svc.documents, "authority_version", forbidden_drive)
    monkeypatch.setattr(svc.documents, "fetch", forbidden_drive)
    monkeypatch.setattr(svc.documents, "head", forbidden_drive)
    row = svc.intake(ctx, "Synthetic uploaded policy", "", "VEND", "Upload without Drive source")
    row = svc.upload_document(ctx, row["id"], row["revision"], content=POLICY.read_bytes(),
        name="Synthetic policy.pdf", mime="application/pdf", document_date=NOW - 100)
    row = svc.collect_evidence(ctx, row["id"], row["revision"])
    row = svc.assess(ctx, row["id"], row["revision"])
    row = svc.review(ctx, row["id"], row["revision"], "approved", "Synthetic reviewer accepts cited assertions")
    row = svc.prepare_action(ctx, row["id"], row["revision"])
    assert row["operation"]["preconditions"]["document_credential_version"] == "not_applicable:retained_uploads"
    row = svc.approve_action(ctx, row["id"], row["revision"])
    row = svc.execute(ctx, row["id"])
    row = svc.accept(ctx, row["id"], row["revision"], True, "Synthetic acceptance")
    evolution = EvidenceRenewalEvolution(svc.authorize, clock=svc.clock)
    observed = evolution.observe_case(ctx, svc, row["id"], "fresh", rationale="Check uploaded snapshot provenance")
    assert observed["inputs"]["source_version"] == row["evidence"]["version"]
    renewal = svc.intake(ctx, row["vendor_name"], "", "VEND", "Renewal", renewal_of=row["id"])
    renewal = svc.collect_evidence(ctx, renewal["id"], renewal["revision"])
    assert renewal["uploads"][0]["modified_at"] == row["uploads"][0]["modified_at"]
    assert renewal["evidence"]["changes"]["documents"][0]["change"] == "unchanged"
    upload_id = renewal["uploads"][0]["document_id"]
    renewal = svc.set_documents(ctx, renewal["id"], renewal["revision"], [], [])
    renewal = svc.upload_document(ctx, renewal["id"], renewal["revision"],
        content=b"Model provenance is undisclosed.", name="Changed statement.txt", mime="text/plain", document_date=NOW)
    renewal = svc.collect_evidence(ctx, renewal["id"], renewal["revision"])
    changes = renewal["evidence"]["changes"]["documents"]
    assert {item["change"] for item in changes} == {"added", "removed"}
    assert next(item for item in changes if item["document_id"] == upload_id)["before_version"] == "1"
    assert svc.get(ctx, row["id"]) == row
    assert tickets.creates == 1


def test_failed_document_blocks_partial_assessment_and_selection_is_bounded(setup):
    svc, ctx, *_ = setup
    row = svc.intake(ctx, "Synthetic incomplete packet", "", "VEND", "Missing secondary file",
                     document_ids=["security-paper", "missing"])
    row = svc.collect_evidence(ctx, row["id"], row["revision"])
    assert {item["status"] for item in row["evidence"]["documents"]} == {"missing", "available"}
    with pytest.raises(ValueError, match="available"):
        svc.assess(ctx, row["id"], row["revision"])
    with pytest.raises(ValueError, match="once"):
        svc.set_documents(ctx, row["id"], row["revision"], ["duplicate", "duplicate"], [])
    with pytest.raises(ValueError, match="at most 8"):
        svc.set_documents(ctx, row["id"], row["revision"], [str(i) for i in range(9)], [])


def test_complete_workflow_keeps_execution_acceptance_and_release_separate(setup):
    svc, ctx, docs, tickets, _ = setup
    row = approved(svc, ctx)
    row = svc.execute(ctx, row["id"])
    assert row["stage"] == "execution_completed"
    assert row["operation"]["external_confirmation"]["verified"] is True
    assert row["outcome"] is None
    assert row["work_item"]["lifecycle"] == "Verified"
    assert row["work_item"]["technical_effect"] == "verified"
    assert row["work_item"]["business_outcome"] == "pending"
    duplicate = svc.execute(ctx, row["id"])
    assert duplicate["revision"] == row["revision"]
    assert tickets.creates == 1
    row = svc.accept(ctx, row["id"], row["revision"], True, "Accepted by procurement")
    assert row["outcome"]["accepted"] is True
    assert row["outcome"]["released"] is False
    assert row["metrics"]["human_review_seconds"] == 12.5
    assert row["outcome"]["execution_cost_usd"] == 0.0
    assert row["outcome"]["journey_contract"] == row["journey_contract"]
    assert row["work_item"]["lifecycle"] == "Accepted"
    assert row["work_item"]["business_outcome"] == "accepted"
    renewal = svc.intake(ctx, "Synthetic Acme AI", "security-paper", "VEND", "Annual renewal",
                         renewal_of=row["id"])
    assert renewal["renewal_of"] == row["id"]
    assert renewal["vendor_id"] == row["vendor_id"]
    assert renewal["outcome"] is None


def test_model_is_never_human_approval(setup):
    svc, ctx, *_ = setup
    row = svc.intake(ctx, "Synthetic Acme AI", "security-paper", "VEND", "Synthetic request")
    row = svc.collect_evidence(ctx, row["id"], row["revision"])
    row = svc.assess(ctx, row["id"], row["revision"])
    with pytest.raises(PermissionError, match="human"):
        svc.review(replace(ctx, human=False), row["id"], row["revision"], "approved", "Model says approve")
    with pytest.raises(ValueError, match="human-reviewed"):
        svc.prepare_action(ctx, row["id"], row["revision"])


@pytest.mark.parametrize("state", ["missing", "empty", "corrupted", "unavailable"])
def test_evidence_absence_and_failure_are_not_valid_empty_assessments(setup, state):
    svc, ctx, docs, *_ = setup
    if state == "missing":
        docs.documents.clear()
    elif state == "unavailable":
        docs.unavailable = True
    else:
        docs.documents[(ctx.tenant, "security-paper")] = EvidenceDocument(
            "security-paper", "1", NOW - 10, b"" if state == "empty" else b"\xff",
        )
    row = svc.intake(ctx, "Synthetic Acme AI", "security-paper", "VEND", "Synthetic request")
    row = svc.collect_evidence(ctx, row["id"], row["revision"])
    assert row["evidence"]["status"] == state
    with pytest.raises(ValueError, match="available"):
        svc.assess(ctx, row["id"], row["revision"])


@pytest.mark.parametrize(
    "change",
    [
        "source", "credential", "permission", "actor", "owner", "role",
        "role_policy", "budget", "workspace", "expired",
    ],
)
def test_approval_preconditions_and_authority_rechecked_before_effect(setup, change):
    svc, ctx, docs, tickets, denied = setup
    row = approved(svc, ctx)
    if change == "source":
        docs.documents[(ctx.tenant, "security-paper")] = EvidenceDocument("security-paper", "2", NOW, TEXT)
    elif change == "credential":
        tickets.version = "revoked-and-replaced"
    elif change == "permission":
        denied.add("action.execute")
    elif change == "actor":
        ctx = replace(ctx, actor="other-human")
    elif change == "owner":
        ctx = replace(ctx, resource_owner="other-owner")
    elif change == "role":
        ctx = replace(ctx, authorization_role="admin")
    elif change == "role_policy":
        ctx = replace(ctx, authorization_policy="operator-policy-v2")
    elif change == "budget":
        ctx = replace(ctx, budget_authority="tenant-budget-v2")
    elif change == "workspace":
        ctx = replace(ctx, workspace_authority="workspace-generation-2")
    else:
        svc.clock = lambda: NOW + 4000
    with pytest.raises((ValueError, PermissionError, KeyError)):
        svc.execute(ctx, row["id"])
    assert tickets.creates == 0


def test_timeout_after_success_is_reconciled_without_second_create(setup):
    svc, ctx, docs, tickets, _ = setup
    row = approved(svc, ctx)
    tickets.timeout_after_success = True
    row = svc.execute(ctx, row["id"])
    assert row["stage"] == "reconciliation_required"
    assert tickets.creates == 1
    # A new service object sees durable intent and an independent provider state.
    restarted = VendorOnboardingService(docs, tickets, svc.authorize, clock=svc.clock)
    restarted.execute(ctx, row["id"])
    row = restarted.reconcile(ctx, row["id"])
    assert row["operation"]["state"] == "verified"
    assert row["operation"]["external_confirmation"]["reconciled"] is True
    assert tickets.creates == 1


def test_receipt_storage_failure_and_restart_do_not_retry_effect(setup, monkeypatch):
    svc, ctx, docs, tickets, _ = setup
    row = approved(svc, ctx)
    save = svc._save

    def fail_after_effect(*args, **kwargs):
        if tickets.creates:
            raise OSError("synthetic disk outage")
        return save(*args, **kwargs)

    monkeypatch.setattr(svc, "_save", fail_after_effect)
    with pytest.raises(OSError):
        svc.execute(ctx, row["id"])
    assert tickets.creates == 1
    restarted = VendorOnboardingService(docs, tickets, svc.authorize, clock=svc.clock)
    assert restarted.get(ctx, row["id"])["operation"]["state"] == "executing"
    row = restarted.reconcile(ctx, row["id"])
    assert row["operation"]["state"] == "verified"
    assert tickets.creates == 1


def test_unavailable_verifier_and_changed_downstream_state_do_not_accept(setup):
    svc, ctx, docs, tickets, _ = setup
    row = approved(svc, ctx)
    tickets.verify_unavailable = True
    row = svc.execute(ctx, row["id"])
    assert row["operation"]["state"] == "reconciliation_required"
    with pytest.raises(ValueError, match="verified external"):
        svc.accept(ctx, row["id"], row["revision"], True, "Must not accept")
    tickets.verify_unavailable = False
    row = svc.reconcile(ctx, row["id"])
    key = row["operation"]["external_confirmation"]["key"]
    tickets.state[(ctx.tenant, key)]["payload"]["summary"] = "changed by someone else"
    with pytest.raises(ValueError, match="external state changed"):
        svc.accept(ctx, row["id"], row["revision"], True, "Must not accept")


def test_corrections_link_superseded_evidence_and_invalidate_action(setup):
    svc, ctx, docs, tickets, _ = setup
    row = approved(svc, ctx)
    original_id = row["assessment"]["id"]
    qid = next(iter(row["assessment"]["answers"]))
    row = svc.review(ctx, row["id"], row["revision"], "approved", "Corrected reviewer interpretation",
                     corrections={qid: {"answer": "unknown", "note": "Needs confirmation"}})
    assert row["assessment"]["id"] != original_id
    assert row["review_history"][-1]["supersedes"]["id"] == original_id
    assert row["operation"] is None
    assert row["operation_history"][-1]["state"] == "invalidated"
    with pytest.raises(ValueError, match="approved operation"):
        svc.execute(ctx, row["id"])
    assert tickets.creates == 0


def test_tenant_scope_ignores_ambient_and_never_pools_customer_data(setup, monkeypatch):
    from maverick import audit

    svc, ctx, docs, tickets, _ = setup
    events = []
    monkeypatch.setattr(audit, "record", lambda kind, **payload: events.append((kind, payload)) or True)
    with tenant_scope(tenant="ambient-untrusted"):
        row = approved(svc, ctx)
    other = replace(ctx, tenant="customer-b")
    assert svc.list(other) == []
    with pytest.raises(KeyError):
        svc.get(other, row["id"])
    assert tickets.find(other, row["operation"]["payload"]) == []
    with pytest.raises(FileNotFoundError):
        docs.fetch(other, "security-paper")
    assert any(
        kind == audit.EventKind.CAPABILITY_DENIED
        and payload["reason"] == "resource_scope_mismatch"
        and payload["tenant"] == "customer-b"
        and payload["resource_sha256"] == digest(row["id"])
        for kind, payload in events
    )


def test_approval_from_another_intent_cannot_be_replayed(setup):
    from maverick import vendor_onboarding

    svc, ctx, _, tickets, _ = setup
    first = approved(svc, ctx)
    second = approved(svc, ctx)
    replayed = dict(second["operation"], approval=dict(first["operation"]["approval"]))
    with tenant_scope(tenant=ctx.tenant):
        vendor_onboarding._CASES.update(
            second["id"],
            lambda value: value.update(operation=replayed),
            expected_revision=second["revision"],
            action="test_cross_intent_approval_replay",
            actor=ctx.actor,
        )
    with pytest.raises(PermissionError, match="(?i)exact operation"):
        svc.execute(ctx, second["id"])
    assert tickets.creates == 0


def test_optional_shield_absence_keeps_kernel_authority_enforced(setup, monkeypatch):
    import builtins

    svc, ctx, _, tickets, denied = setup
    original_import = builtins.__import__

    def without_shield(name, *args, **kwargs):
        if name == "maverick_shield" or name.startswith("maverick_shield."):
            raise ImportError("optional shield is not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_shield)
    row = approved(svc, ctx)
    row = svc.execute(ctx, row["id"])
    assert row["operation"]["state"] == "verified"
    assert tickets.creates == 1

    blocked = approved(svc, ctx)
    denied.add("action.execute")
    with pytest.raises(PermissionError, match="action.execute"):
        svc.execute(ctx, blocked["id"])
    assert tickets.creates == 1


def test_legacy_approval_without_role_or_budget_binding_requires_new_review(setup):
    from maverick import vendor_onboarding

    svc, ctx, _, tickets, _ = setup
    row = approved(svc, ctx)
    legacy = dict(row["operation"], context=dict(row["operation"]["context"]))
    legacy["context"].pop("authorization_role")
    legacy["context"].pop("authorization_policy")
    legacy["context"].pop("budget_authority")
    legacy["context"].pop("workspace_authority")
    legacy["context"].pop("authority_schema")
    legacy["approval"] = dict(
        legacy["approval"], binding_sha256=operation_binding(legacy),
    )
    with tenant_scope(tenant=ctx.tenant):
        vendor_onboarding._CASES.update(
            row["id"],
            lambda value: value.update(operation=legacy),
            expected_revision=row["revision"],
            action="test_legacy_unbound_operation",
            actor=ctx.actor,
        )
    with pytest.raises(PermissionError, match="(?i)authority changed"):
        svc.execute(ctx, row["id"])
    assert tickets.creates == 0


def test_stale_revision_cannot_overwrite_review(setup):
    svc, ctx, _, tickets, _ = setup
    row = reviewed(svc, ctx)
    assert row["revision"] == 4
    prepared = svc.prepare_action(ctx, row["id"], row["revision"])
    assert prepared["revision"] == 5
    question = next(iter(row["assessment"]["answers"]))
    with pytest.raises(RecordConflict):
        svc.review(ctx, row["id"], row["revision"], "approved", "Conflicting correction",
                   corrections={question: {"answer": "unknown", "note": "Stale writer"}})
    assert svc.get(ctx, row["id"])["revision"] == 5
    assert tickets.creates == 0


@pytest.mark.parametrize("change", ["legacy", "role", "budget", "workspace", "reviewer"])
def test_attempted_effect_can_be_read_back_with_current_authority(setup, change):
    from maverick import vendor_onboarding

    svc, ctx, _, tickets, _ = setup
    row = approved(svc, ctx)
    tickets.timeout_after_success = True
    row = svc.execute(ctx, row["id"])
    operation = row["operation"]
    if change == "legacy":
        operation = dict(operation, context={
            key: value for key, value in operation["context"].items()
            if key not in {"authorization_role", "authorization_policy", "budget_authority",
                           "workspace_authority", "authority_schema"}
        })
        operation["approval"] = dict(operation["approval"],
                                     binding_sha256=operation_binding(operation))
        with tenant_scope(tenant=ctx.tenant):
            vendor_onboarding._CASES.update(
                row["id"], lambda value: value.update(operation=operation),
                expected_revision=row["revision"], action="legacy_effect_fixture", actor=ctx.actor,
            )
    elif change == "role":
        ctx = replace(ctx, authorization_role="admin", authorization_policy="admin-policy")
    elif change == "budget":
        ctx = replace(ctx, budget_authority="revised-budget")
    elif change == "workspace":
        ctx = replace(ctx, workspace_authority="returned-to-workspace")
    else:
        ctx = replace(ctx, actor="second-reviewer@example.test")
    svc.authorize = lambda candidate, *_: candidate.binding() == ctx.binding()
    recovered = svc.reconcile(ctx, row["id"])
    assert recovered["operation"]["state"] == "verified"
    accepted = svc.accept(ctx, row["id"], recovered["revision"], True, "Checked existing result")
    assert accepted["outcome"]["reviewer"] == ctx.actor
    assert accepted["operation"]["context"] == operation["context"]
    assert tickets.creates == 1


@pytest.mark.parametrize("field,value", [
    ("credential_ref", "another-jira"), ("credential_authority", "another-owner"),
])
def test_readback_cannot_change_original_effect_credential_scope(setup, monkeypatch, field, value):
    svc, ctx, _, tickets, _ = setup
    row = svc.execute(ctx, approved(svc, ctx)["id"])
    changed = replace(ctx, **{field: value})
    reads = []
    monkeypatch.setattr(tickets, "find", lambda *a, **kw: reads.append("find") or [])
    monkeypatch.setattr(tickets, "verify", lambda *a, **kw: reads.append("verify") or {})
    with pytest.raises(PermissionError, match="(?i)(credential|scope)"):
        svc.reconcile(changed, row["id"])
    with pytest.raises(PermissionError, match="(?i)(credential|scope)"):
        svc.accept(changed, row["id"], row["revision"], True, "Should be blocked")
    assert reads == []
    assert tickets.creates == 1


def test_stale_prepared_authority_returns_case_to_reviewable_state(setup):
    svc, ctx, _, tickets, _ = setup
    row = reviewed(svc, ctx)
    row = svc.prepare_action(ctx, row["id"], row["revision"])
    changed = replace(ctx, workspace_authority="new-generation")
    svc.authorize = lambda candidate, *_: candidate.binding() == changed.binding()
    with pytest.raises(PermissionError, match="(?i)authority changed"):
        svc.approve_action(changed, row["id"], row["revision"])
    reopened = svc.get(changed, row["id"])
    assert reopened["stage"] == "review_approved"
    assert reopened["operation"] is None
    prepared = svc.prepare_action(changed, row["id"], reopened["revision"])
    assert prepared["operation"]["id"] != row["operation"]["id"]
    assert tickets.creates == 0


def test_changed_source_before_approval_allows_fresh_review_in_app(setup):
    svc, ctx, docs, tickets, _ = setup
    row = reviewed(svc, ctx)
    row = svc.prepare_action(ctx, row["id"], row["revision"])
    docs.documents[(ctx.tenant, "security-paper")] = EvidenceDocument("security-paper", "2", NOW, TEXT)
    with pytest.raises(ValueError):
        svc.approve_action(ctx, row["id"], row["revision"])
    reopened = svc.get(ctx, row["id"])
    assert reopened["operation"] is None
    assert reopened["stage"] == "review_approved"
    assert tickets.creates == 0


def test_reconciliation_rejects_rewritten_historical_approval(setup):
    from maverick import vendor_onboarding

    svc, ctx, _, tickets, _ = setup
    row = svc.execute(ctx, approved(svc, ctx)["id"])
    changed = dict(row["operation"], context=dict(row["operation"]["context"], actor="forged-reviewer"))
    with tenant_scope(tenant=ctx.tenant):
        vendor_onboarding._CASES.update(
            row["id"], lambda value: value.update(operation=changed),
            expected_revision=row["revision"], action="tampered_history_fixture", actor=ctx.actor,
        )
    with pytest.raises(ValueError, match="approval binding"):
        svc.reconcile(ctx, row["id"])
    assert tickets.creates == 1
