from __future__ import annotations

import hashlib
import json

import pytest
from maverick.soc2_certification_evidence import (
    CERTIFICATION_ASSERTED,
    LIVE_AUDIT_ACCEPTANCE,
    NETWORK_IO,
    RAW_SOURCE_CONTENT_PERSISTED,
    SOC2_ACCESS_REVIEW_CONTROL,
    SOC2_AUDIT_RETENTION_CONTROL,
    SOC2_CERTIFICATION_COLLECTOR_ID,
    SOC2_CHANGE_MANAGEMENT_CONTROL,
    AccessReviewState,
    AuditLogRetentionPosture,
    GitChangeManagementSnapshot,
    Soc2CertificationEvidenceCollector,
    Soc2CertificationEvidenceRunState,
    Soc2CertificationEvidenceSchedule,
    audit_retention_posture_from_soc2_snapshot,
    capture_git_change_management_snapshot,
    collect_soc2_certification_evidence,
    select_due_soc2_certification_runs,
    suite_audit_capture_reference,
)
from maverick.suite_audit_capture import build_suite_audit_capture
from maverick.trc_evidence_collectors import (
    CollectionRequest,
    EvidenceCollectorValidationError,
    validate_page,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _capture_reference() -> str:
    capture = build_suite_audit_capture(
        tenant_id="tenant-a",
        source_id="tech_risk_compliance",
        source_revision=1,
        source_schema_version=1,
        observed_at=1_800_000_000.0,
        records=(),
        complete=False,
        retention_complete=False,
        integrity_verified=True,
        limitations=("source_history_incomplete",),
    )
    return suite_audit_capture_reference(capture)


def _bundle():
    capture_reference = _capture_reference()
    return collect_soc2_certification_evidence(
        access_review=AccessReviewState(
            source_revision_sha256=_sha("access-review-revision-v1"),
            review_completed=True,
            reviewed_principal_count=3,
            reviewed_principals_sha256=_sha("reviewed-principals-v1"),
            approval_sha256=_sha("access-review-approval-v1"),
            coverage_complete=False,
            limitations=("access_review_population_partial",),
            suite_capture_sha256=capture_reference,
        ),
        change_management=GitChangeManagementSnapshot(
            source_revision_sha256=_sha("git-source-revision-v1"),
            available=True,
            repository_sha256=_sha("repository-reference-v1"),
            head_commit_sha256=_sha("commit-reference-v1"),
            worktree_clean=True,
            history_complete=False,
            coverage_complete=False,
            limitations=(
                "git_change_approval_unverified",
                "git_history_scope_unverified",
            ),
            suite_capture_sha256=capture_reference,
        ),
        audit_retention=AuditLogRetentionPosture(
            source_revision_sha256=_sha("audit-retention-revision-v1"),
            audit_chain_status="ok",
            policy_configured=True,
            audit_days=365,
            anchors_checked=True,
            retention_markers_verified=None,
            coverage_complete=False,
            limitations=("audit_retention_marker_unverified",),
            suite_capture_sha256=capture_reference,
        ),
    )


def _request(*, controls: tuple[str, ...] | None = None) -> CollectionRequest:
    return CollectionRequest(
        tenant_sha256=_sha("tenant"),
        definition_id="soc2-definition",
        definition_version=1,
        definition_integrity_sha256=_sha("definition"),
        collector_id=SOC2_CERTIFICATION_COLLECTOR_ID,
        collector_schema_version=1,
        collector_key_id="soc2-key-v1",
        capability_revision=1,
        enrollment_id="soc2-enrollment",
        enrollment_revision=1,
        enrollment_integrity_sha256=_sha("enrollment"),
        asset_scope_sha256=_sha("asset-scope"),
        control_refs=controls
        or (
            SOC2_ACCESS_REVIEW_CONTROL,
            SOC2_AUDIT_RETENTION_CONTROL,
            SOC2_CHANGE_MANAGEMENT_CONTROL,
        ),
        window_started_at=1.0,
        window_ended_at=2.0,
    )


def _schedule(*, schedule_id: str = "schedule-a", next_due_at: float = 100.0):
    return Soc2CertificationEvidenceSchedule(
        schedule_id=schedule_id,
        scope_sha256=_sha(f"scope-{schedule_id}"),
        state="active",
        interval_seconds=60,
        next_due_at=next_due_at,
        revision=1,
        max_attempts=3,
    )


def test_bundle_is_deterministic_digest_only_and_non_certifying() -> None:
    first = _bundle()
    second = _bundle()

    assert first == second
    assert first.evidence_sha256 == second.evidence_sha256
    assert [item.control_ref for item in first.controls] == [
        SOC2_ACCESS_REVIEW_CONTROL,
        SOC2_AUDIT_RETENTION_CONTROL,
        SOC2_CHANGE_MANAGEMENT_CONTROL,
    ]
    assert [item.status for item in first.controls] == ["observed", "observed", "observed"]
    assert [item.source_id for item in first.coverage] == [
        "access_review",
        "audit_retention",
        "change_management",
    ]
    by_control = {item.control_ref: item for item in first.controls}
    assert {
        row.evidence_ref_sha256 for row in first.coverage
    } == {item.evidence_sha256 for item in first.controls}
    assert by_control[SOC2_ACCESS_REVIEW_CONTROL].source_revision_sha256 == _sha(
        "access-review-revision-v1"
    )
    assert by_control[SOC2_CHANGE_MANAGEMENT_CONTROL].source_revision_sha256 == _sha(
        "git-source-revision-v1"
    )
    assert by_control[SOC2_AUDIT_RETENTION_CONTROL].source_revision_sha256 == _sha(
        "audit-retention-revision-v1"
    )
    assert "access_review_population_partial" in first.limitations
    assert "git_change_approval_unverified" in first.limitations
    assert "audit_retention_marker_unverified" in first.limitations
    assert first.legal_certification is False
    assert "does not establish SOC 2 compliance" in first.notice
    rendered = json.dumps(first.public_metadata(), sort_keys=True)
    assert "reviewed-principals-v1" not in rendered
    assert "commit-reference-v1" not in rendered
    assert "access-review-approval-v1" not in rendered
    assert len(first.evidence_sha256) == 64
    assert NETWORK_IO is False
    assert LIVE_AUDIT_ACCEPTANCE is False
    assert CERTIFICATION_ASSERTED is False
    assert RAW_SOURCE_CONTENT_PERSISTED is False


def test_git_and_soc2_normalizers_exclude_raw_source_data(tmp_path) -> None:
    private_path = tmp_path / "private-worktree-name"
    private_commit = "PRIVATE-COMMIT-DO-NOT-EXPORT"
    private_branch = "private/customer-branch"
    snapshot = capture_git_change_management_snapshot(
        private_path,
        state_reader=lambda _root: {
            "commit": private_commit,
            "branch": private_branch,
            "dirty": False,
        },
    )
    assert snapshot.available is True
    assert snapshot.worktree_clean is True
    assert snapshot.history_complete is False
    assert snapshot.limitations == (
        "git_change_approval_unverified",
        "git_history_scope_unverified",
    )
    assert private_path.name not in repr(snapshot)
    assert private_commit not in repr(snapshot)
    assert private_branch not in repr(snapshot)

    secret = "private-audit-error-and-path"
    posture = audit_retention_posture_from_soc2_snapshot(
        soc2_snapshot={
            "audit_log": {
                "status": "ok",
                "anchors_checked": True,
                "error": secret,
                "audit_dir": secret,
            }
        },
        retention_config={"audit_days": 365, "secret": secret},
    )
    assert posture.audit_chain_status == "ok"
    assert posture.audit_days == 365
    assert posture.retention_markers_verified is None
    assert posture.limitations == ("audit_retention_marker_unverified",)
    assert secret not in repr(posture)


def test_trc_collector_is_closed_single_page_and_rejects_unsupported_control() -> None:
    collector = Soc2CertificationEvidenceCollector(_bundle)
    request = _request()
    page = collector.fetch(request, None)

    assert validate_page(page, expected_controls=request.control_refs) is page
    assert page.terminal is True
    assert page.complete is True
    assert page.next_cursor is None
    assert [item.control_ref for item in page.items] == list(request.control_refs)
    assert "reviewed-principals-v1" not in repr(page)
    with pytest.raises(EvidenceCollectorValidationError):
        collector.fetch(_request(controls=("not-supported",)), None)
    with pytest.raises(EvidenceCollectorValidationError):
        collector.fetch(request, "second-page")


def test_reader_failure_is_opaque_and_preserves_terminal_technical_collection() -> None:
    secret = "private-source-error"

    def failed_reader():
        raise RuntimeError(secret)

    page = Soc2CertificationEvidenceCollector(failed_reader).fetch(_request(), None)
    assert page.terminal is True
    assert page.complete is True
    assert {item.status for item in page.items} == {"unknown"}
    assert secret not in repr(page)


def test_due_selector_is_deterministic_retry_safe_and_advances_terminal_runs() -> None:
    schedule_a = _schedule(schedule_id="schedule-a")
    schedule_b = _schedule(schedule_id="schedule-b")
    initial = select_due_soc2_certification_runs(
        (schedule_b, schedule_a),
        (),
        now=130.0,
    )
    repeated = select_due_soc2_certification_runs(
        (schedule_a, schedule_b),
        (),
        now=130.0,
    )
    assert initial == repeated
    assert [run.schedule_id for run in initial.runs] == ["schedule-a", "schedule-b"]
    assert {run.mode for run in initial.runs} == {"new_due"}
    first_run = initial.runs[0]

    retry = select_due_soc2_certification_runs(
        (schedule_a,),
        (
            Soc2CertificationEvidenceRunState(
                run_id=first_run.run_id,
                schedule_id=schedule_a.schedule_id,
                schedule_revision=schedule_a.revision,
                scheduled_for=schedule_a.next_due_at,
                attempt=1,
                state="retry_wait",
                not_before=120.0,
            ),
        ),
        now=130.0,
    )
    assert len(retry.runs) == 1
    assert retry.runs[0].run_id == first_run.run_id
    assert retry.runs[0].mode == "retry_due"
    assert retry.runs[0].attempt == 2
    assert retry.runs[0].next_schedule_due_at == 160.0
    assert retry == select_due_soc2_certification_runs(
        (schedule_a,),
        (
            Soc2CertificationEvidenceRunState(
                run_id=first_run.run_id,
                schedule_id=schedule_a.schedule_id,
                schedule_revision=schedule_a.revision,
                scheduled_for=schedule_a.next_due_at,
                attempt=1,
                state="retry_wait",
                not_before=120.0,
            ),
        ),
        now=130.0,
    )

    terminal = select_due_soc2_certification_runs(
        (schedule_a,),
        (
            Soc2CertificationEvidenceRunState(
                run_id=first_run.run_id,
                schedule_id=schedule_a.schedule_id,
                schedule_revision=schedule_a.revision,
                scheduled_for=schedule_a.next_due_at,
                attempt=1,
                state="completed",
                not_before=100.0,
            ),
        ),
        now=130.0,
    )
    assert terminal.runs == ()
    assert len(terminal.terminal_advances) == 1
    assert terminal.terminal_advances[0].terminal_run_id == first_run.run_id
    assert terminal.terminal_advances[0].next_due_at == 160.0


def test_leased_run_does_not_duplicate_until_lease_expiry() -> None:
    schedule = _schedule()
    initial = select_due_soc2_certification_runs((schedule,), (), now=100.0)
    run = initial.runs[0]
    leased = Soc2CertificationEvidenceRunState(
        run_id=run.run_id,
        schedule_id=schedule.schedule_id,
        schedule_revision=schedule.revision,
        scheduled_for=schedule.next_due_at,
        attempt=1,
        state="leased",
        not_before=100.0,
        lease_expires_at=200.0,
    )
    assert select_due_soc2_certification_runs((schedule,), (leased,), now=130.0).runs == ()
    recovered = select_due_soc2_certification_runs((schedule,), (leased,), now=201.0)
    assert len(recovered.runs) == 1
    assert recovered.runs[0].run_id == run.run_id
    assert recovered.runs[0].mode == "lease_recovered"
    assert recovered.runs[0].attempt == 2
