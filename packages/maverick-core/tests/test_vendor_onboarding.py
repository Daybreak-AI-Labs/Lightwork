"""Offline acceptance of approval/evidence/effect/customer boundaries."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from maverick import config
from maverick.governed_operations import ServiceContext
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
    ctx = ServiceContext("customer-a", "reviewer@example.test", "procurement",
                         "reviewer@example.test", "jira", human=True)
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
    with pytest.raises(ValueError, match="source evidence version changed"):
        svc.execute(ctx, row["id"])
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
    duplicate = svc.execute(ctx, row["id"])
    assert duplicate["revision"] == row["revision"]
    assert tickets.creates == 1
    row = svc.accept(ctx, row["id"], row["revision"], True, "Accepted by procurement")
    assert row["outcome"]["accepted"] is True
    assert row["outcome"]["released"] is False
    assert row["metrics"]["human_review_seconds"] == 12.5
    assert row["outcome"]["execution_cost_usd"] == 0.0
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


@pytest.mark.parametrize("change", ["source", "credential", "permission", "actor", "owner", "expired"])
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


def test_tenant_scope_ignores_ambient_and_never_pools_customer_data(setup):
    svc, ctx, docs, tickets, _ = setup
    with tenant_scope(tenant="ambient-untrusted"):
        row = approved(svc, ctx)
    other = replace(ctx, tenant="customer-b")
    assert svc.list(other) == []
    with pytest.raises(KeyError):
        svc.get(other, row["id"])
    assert tickets.find(other, row["operation"]["payload"]) == []
    with pytest.raises(FileNotFoundError):
        docs.fetch(other, "security-paper")


def test_stale_revision_cannot_overwrite_review(setup):
    svc, ctx, *_ = setup
    row = reviewed(svc, ctx)
    with pytest.raises(RecordConflict):
        svc.prepare_action(ctx, row["id"], row["revision"] - 1)
