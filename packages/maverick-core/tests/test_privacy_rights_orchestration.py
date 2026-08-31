from __future__ import annotations

import hashlib
import inspect
import sqlite3
from pathlib import Path

import pytest
from maverick import privacy_rights_orchestration as rights


class MutableClock:
    def __init__(self, value: float = 2_000_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def authority(tmp_path: Path):
    actor = {"value": "operator"}
    clock = MutableClock()
    token_counter = {"value": 0}

    def token() -> str:
        token_counter["value"] += 1
        return f"lease-token-{token_counter['value']}"

    store = rights.PrivacyRightsOrchestrationStore(
        tmp_path / "privacy-rights.sqlite3",
        config_gate=lambda: None,
        principal_resolver=lambda: actor["value"],
        digest_key_resolver=lambda _tenant: b"k" * 32,
        clock=clock,
        token_factory=token,
    )
    return store, actor, clock


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def enroll_all(
    store: rights.PrivacyRightsOrchestrationStore,
    tenant: str,
    source: str,
    *,
    key: str,
) -> rights.SourceEnrollment:
    return store.enroll_source(
        tenant,
        source,
        adapter_ref="provider-adapter-v1",
        credential_ref="env:PROVIDER_RIGHTS_TOKEN",
        capabilities=sorted(rights.TASK_PHASES),
        expected_revision=0,
        idempotency_key=key,
    )


def open_verified(
    store: rights.PrivacyRightsOrchestrationStore,
    clock: MutableClock,
    tenant: str,
    case_id: str,
    requested_rights: list[str],
) -> rights.RightsCase:
    case = store.open_case(
        tenant,
        case_id,
        request_version=1,
        external_request_ref=f"external request for {case_id}",
        subject_reference=f"person@example.test/{case_id}",
        rights=requested_rights,
        retained_until=clock() + 600,
        idempotency_key=f"open-{case_id}",
    )
    return store.set_identity_verification(
        tenant,
        case_id,
        status="verified",
        evidence_sha256=digest(f"identity-{case_id}"),
        evidence_ref=f"identity:{case_id}",
        expected_revision=case.revision,
        idempotency_key=f"verify-{case_id}",
    )


def task_by_phase(tasks: tuple[rights.SourceTask, ...], phase: str) -> rights.SourceTask:
    return next(task for task in tasks if task.phase == phase)


def complete_callback_phase(
    store: rights.PrivacyRightsOrchestrationStore,
    tenant: str,
    case_id: str,
    source_id: str,
    task: rights.SourceTask,
    *,
    suffix: str,
) -> rights.SourceEvidenceReceipt:
    claimed = store.claim_callbacks(
        tenant,
        limit=1,
        lease_seconds=30,
        idempotency_key=f"claim-{suffix}",
    )
    assert len(claimed) == 1
    callback = claimed[0]
    assert callback.phase == task.phase
    acknowledged = store.ack_callback(
        tenant,
        callback.callback_id,
        lease_token=callback.lease_token or "",
        acknowledgement_sha256=digest(f"ack-{suffix}"),
        acknowledgement_ref=f"ack:{suffix}",
        expected_revision=callback.revision,
        idempotency_key=f"ack-{suffix}",
    )
    assert acknowledged.state == "acked"
    return store.record_source_evidence(
        tenant,
        case_id,
        source_id,
        task.phase,
        evidence_id=f"evidence:{suffix}",
        status="complete",
        provider_evidence_sha256=digest(f"evidence-{suffix}"),
        record_count=2,
        opaque_ref=f"provider-receipt:{suffix}",
        expected_task_revision=task.revision,
        idempotency_key=f"record-{suffix}",
    )


def complete_local_phase(
    store: rights.PrivacyRightsOrchestrationStore,
    tenant: str,
    case_id: str,
    source_id: str,
    task: rights.SourceTask,
    *,
    suffix: str,
) -> rights.SourceEvidenceReceipt:
    return store.record_source_evidence(
        tenant,
        case_id,
        source_id,
        task.phase,
        evidence_id=f"evidence:{suffix}",
        status="complete",
        provider_evidence_sha256=digest(f"evidence-{suffix}"),
        record_count=2,
        opaque_ref=f"local-receipt:{suffix}",
        expected_task_revision=task.revision,
        idempotency_key=f"record-{suffix}",
    )


def test_two_level_config_gate_is_strict_default_off(monkeypatch, tmp_path: Path) -> None:
    from maverick import config

    monkeypatch.setattr(config, "config_source_errors", list)
    for configured in (
        {},
        {"privacy_automation": {"enable": True}},
        {
            "privacy_automation": {
                "enable": False,
                "rights_orchestration": {"enable": True},
            }
        },
        {
            "privacy_automation": {
                "enable": True,
                "rights_orchestration": {"enable": False},
            }
        },
    ):
        monkeypatch.setattr(config, "load_config", lambda value=configured: value)
        assert rights.enabled() is False
        with pytest.raises(rights.PrivacyRightsConfigError):
            rights.PrivacyRightsOrchestrationStore(
                tmp_path / f"off-{len(configured)}.sqlite3",
                principal_resolver=lambda: "operator",
            )

    monkeypatch.setattr(
        config,
        "load_config",
        lambda: {
            "privacy_automation": {
                "enable": True,
                "rights_orchestration": {"enable": True},
            }
        },
    )
    assert rights.enabled() is True


def test_tenant_canonicalization_isolation_and_opaque_not_found(authority) -> None:
    store, _actor, _clock = authority
    composed = "Caf\u00e9"
    decomposed = "Cafe\u0301"
    source = enroll_all(store, decomposed, "directory", key="source-canonical")
    assert source.tenant_id == composed
    assert store.get_source(composed, "directory") == source

    enroll_all(store, "tenant-b", "directory", key="source-b")
    assert len(store.list_sources(composed)) == 1
    assert len(store.list_sources("tenant-b")) == 1
    with pytest.raises(rights.PrivacyRightsNotFound):
        store.get_source("tenant-c", "directory")


def test_public_operations_cannot_accept_spoofed_actor_and_require_principal(
    tmp_path: Path,
) -> None:
    for name, method in inspect.getmembers(
        rights.PrivacyRightsOrchestrationStore, predicate=inspect.isfunction
    ):
        if name.startswith("_") or name == "product_boundaries":
            continue
        parameters = inspect.signature(method).parameters
        assert "actor" not in parameters
        assert "actor_ref" not in parameters

    store = rights.PrivacyRightsOrchestrationStore(
        tmp_path / "no-principal.sqlite3",
        config_gate=lambda: None,
        principal_resolver=lambda: None,
        digest_key_resolver=lambda _tenant: b"k" * 32,
    )
    with pytest.raises(rights.PrivacyRightsPermissionDenied):
        store.list_sources("tenant-a")


def test_source_enrollment_idempotency_conflict_and_reference_only_schema(
    authority,
) -> None:
    store, _actor, _clock = authority
    first = enroll_all(store, "tenant-a", "crm", key="enroll-crm")
    replay = enroll_all(store, "tenant-a", "crm", key="enroll-crm")
    assert replay == first

    with pytest.raises(rights.PrivacyRightsConflict):
        store.enroll_source(
            "tenant-a",
            "crm",
            adapter_ref="provider-adapter-v1",
            credential_ref="env:PROVIDER_RIGHTS_TOKEN",
            capabilities=["discover"],
            expected_revision=0,
            idempotency_key="enroll-crm",
        )
    with pytest.raises(rights.PrivacyRightsValidationError):
        store.enroll_source(
            "tenant-a",
            "bad-origin",
            adapter_ref="https://provider.example",
            credential_ref="literal-provider-secret",
            capabilities=["discover"],
            expected_revision=0,
            idempotency_key="bad-source",
        )


def test_raw_subject_request_and_provider_secrets_never_enter_sqlite(authority) -> None:
    store, _actor, clock = authority
    raw_subject = "very.private.person@example.test"
    raw_request = "provider-ticket-PII-12345"
    literal_secret = "top-secret-provider-password"
    store.enroll_source(
        "tenant-a",
        "crm",
        adapter_ref="adapter-ref-v1",
        credential_ref="vault:rights-token-ref",
        capabilities=["discover", "retrieve", "redact"],
        expected_revision=0,
        idempotency_key="source",
    )
    case = store.open_case(
        "tenant-a",
        "case-private",
        request_version=2,
        external_request_ref=raw_request,
        subject_reference=raw_subject,
        rights=["access"],
        retained_until=clock() + 600,
        idempotency_key="open-private",
    )
    assert case.subject_sha256 != digest(raw_subject)
    assert case.external_request_sha256 != digest(raw_request)
    assert raw_subject not in repr(case)
    assert raw_request not in repr(case)

    database_bytes = store.path.read_bytes()
    wal_path = Path(f"{store.path}-wal")
    if wal_path.exists():
        database_bytes += wal_path.read_bytes()
    for forbidden in (raw_subject, raw_request, literal_secret, "https://provider.example"):
        assert forbidden.encode() not in database_bytes


def test_identity_evidence_gate_and_access_vs_deletion_plans(authority) -> None:
    store, _actor, clock = authority
    enroll_all(store, "tenant-a", "crm", key="source")
    case = store.open_case(
        "tenant-a",
        "case-access",
        request_version=1,
        external_request_ref="request-access",
        subject_reference="subject-access",
        rights=["access"],
        retained_until=clock() + 600,
        idempotency_key="open-access",
    )
    with pytest.raises(rights.PrivacyRightsConflict, match="identity"):
        store.plan_source_tasks(
            "tenant-a",
            case.case_id,
            "crm",
            expected_case_revision=case.revision,
            idempotency_key="plan-before-identity",
        )
    with pytest.raises(rights.PrivacyRightsValidationError):
        store.set_identity_verification(
            "tenant-a",
            case.case_id,
            status="verified",
            evidence_sha256=None,
            evidence_ref=None,
            expected_revision=case.revision,
            idempotency_key="bad-identity",
        )
    verified = store.set_identity_verification(
        "tenant-a",
        case.case_id,
        status="verified",
        evidence_sha256=digest("identity"),
        evidence_ref="identity:receipt",
        expected_revision=case.revision,
        idempotency_key="identity",
    )
    access_tasks = store.plan_source_tasks(
        "tenant-a",
        case.case_id,
        "crm",
        expected_case_revision=verified.revision,
        idempotency_key="plan-access",
    )
    assert {task.phase for task in access_tasks} == {"discover", "retrieve", "redact"}
    assert not any(task.destructive for task in access_tasks)

    deletion = open_verified(
        store, clock, "tenant-a", "case-delete", ["erasure"]
    )
    deletion_tasks = store.plan_source_tasks(
        "tenant-a",
        deletion.case_id,
        "crm",
        expected_case_revision=deletion.revision,
        idempotency_key="plan-delete",
    )
    assert {task.phase for task in deletion_tasks} == {
        "discover",
        "delete",
        "verify_delete",
    }
    assert task_by_phase(deletion_tasks, "delete").destructive is True
    with pytest.raises(rights.PrivacyRightsConflict):
        store.create_response_package(
            "tenant-a",
            deletion.case_id,
            "package-delete",
            object_ref="object:delete",
            package_sha256=digest("package"),
            encryption_key_ref="vault:package-key",
            expires_at=clock() + 60,
            expected_case_revision=deletion.revision + 1,
            idempotency_key="package-delete",
        )


def test_legal_hold_retention_and_four_eyes_block_destructive_dispatch(
    authority,
) -> None:
    store, actor, clock = authority
    enroll_all(store, "tenant-a", "crm", key="source")
    case = open_verified(store, clock, "tenant-a", "case-delete", ["erasure"])
    tasks = store.plan_source_tasks(
        "tenant-a",
        case.case_id,
        "crm",
        expected_case_revision=case.revision,
        idempotency_key="plan",
    )
    delete_task = task_by_phase(tasks, "delete")

    with pytest.raises(rights.PrivacyRightsPermissionDenied, match="distinct"):
        store.approve_destructive_work(
            "tenant-a",
            delete_task.task_id,
            expected_revision=delete_task.revision,
            idempotency_key="self-approve",
        )

    case = store.set_legal_hold(
        "tenant-a",
        case.case_id,
        active=True,
        evidence_sha256=digest("hold"),
        evidence_ref="hold:1",
        expected_revision=case.revision + 1,
        idempotency_key="hold-on",
    )
    actor["value"] = "approver"
    with pytest.raises(rights.PrivacyRightsConflict, match="legal hold"):
        store.approve_destructive_work(
            "tenant-a",
            delete_task.task_id,
            expected_revision=delete_task.revision,
            idempotency_key="approve-on-hold",
        )
    case = store.set_legal_hold(
        "tenant-a",
        case.case_id,
        active=False,
        evidence_sha256=digest("hold-release"),
        evidence_ref="hold:release",
        expected_revision=case.revision,
        idempotency_key="hold-off",
    )
    case = store.set_retention_gate(
        "tenant-a",
        case.case_id,
        blocks_deletion=True,
        rule_ref="rule:retain",
        evidence_sha256=digest("retention"),
        expected_revision=case.revision,
        idempotency_key="retain-on",
    )
    with pytest.raises(rights.PrivacyRightsConflict, match="retention"):
        store.approve_destructive_work(
            "tenant-a",
            delete_task.task_id,
            expected_revision=delete_task.revision,
            idempotency_key="approve-retained",
        )
    case = store.set_retention_gate(
        "tenant-a",
        case.case_id,
        blocks_deletion=False,
        rule_ref="rule:released",
        evidence_sha256=digest("retention-release"),
        expected_revision=case.revision,
        idempotency_key="retain-off",
    )
    approved = store.approve_destructive_work(
        "tenant-a",
        delete_task.task_id,
        expected_revision=delete_task.revision,
        idempotency_key="approve-delete",
    )
    assert approved.approved_by == "approver"

    case = store.set_legal_hold(
        "tenant-a",
        case.case_id,
        active=True,
        evidence_sha256=digest("late-hold"),
        evidence_ref="hold:late",
        expected_revision=case.revision,
        idempotency_key="late-hold",
    )
    discover = task_by_phase(tasks, "discover")
    complete_callback_phase(
        store,
        "tenant-a",
        case.case_id,
        "crm",
        discover,
        suffix="delete-discover",
    )
    assert (
        store.claim_callbacks(
            "tenant-a",
            limit=10,
            lease_seconds=30,
            idempotency_key="claim-held-delete",
        )
        == ()
    )
    case = store.set_legal_hold(
        "tenant-a",
        case.case_id,
        active=False,
        evidence_sha256=digest("late-release"),
        evidence_ref="hold:late-release",
        expected_revision=case.revision,
        idempotency_key="late-release",
    )
    claimed = store.claim_callbacks(
        "tenant-a",
        limit=1,
        lease_seconds=30,
        idempotency_key="claim-unheld-delete",
    )
    assert claimed[0].phase == "delete"


def test_default_lease_token_accepts_urlsafe_leading_symbols(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rights.secrets, "token_urlsafe", lambda _size: "_leading-token")
    actor = {"value": "operator"}
    clock = MutableClock()
    store = rights.PrivacyRightsOrchestrationStore(
        tmp_path / "default-token.sqlite3",
        config_gate=lambda: None,
        principal_resolver=lambda: actor["value"],
        digest_key_resolver=lambda _tenant: b"k" * 32,
        clock=clock,
    )
    enroll_all(store, "tenant-a", "crm", key="source-default-token")
    case = open_verified(store, clock, "tenant-a", "case-default-token", ["access"])
    store.plan_source_tasks(
        "tenant-a",
        case.case_id,
        "crm",
        phases=["discover"],
        max_attempts=2,
        expected_case_revision=case.revision,
        idempotency_key="plan-default-token",
    )

    claimed = store.claim_callbacks(
        "tenant-a",
        limit=1,
        lease_seconds=10,
        idempotency_key="claim-default-token",
    )

    assert claimed[0].lease_token == "lease:_leading-token"


def test_callback_lease_recovery_dead_letter_requeue_and_ack(authority) -> None:
    store, _actor, clock = authority
    enroll_all(store, "tenant-a", "crm", key="source")
    case = open_verified(store, clock, "tenant-a", "case-retry", ["access"])
    tasks = store.plan_source_tasks(
        "tenant-a",
        case.case_id,
        "crm",
        phases=["discover"],
        max_attempts=2,
        expected_case_revision=case.revision,
        idempotency_key="plan",
    )
    first = store.claim_callbacks(
        "tenant-a",
        limit=1,
        lease_seconds=10,
        idempotency_key="claim-1",
    )[0]
    assert first.state == "leased"
    exported_while_leased = store.export_case(
        "tenant-a",
        case.case_id,
        "export:no-lease-token",
        retained_until=clock() + 120,
        idempotency_key="export-no-lease-token",
    )
    assert first.lease_token not in repr(exported_while_leased)
    assert "callbacks" not in exported_while_leased.body
    assert store.claim_callbacks(
        "tenant-a",
        limit=1,
        lease_seconds=10,
        idempotency_key="claim-1",
    ) == (first,)

    clock.advance(11)
    recovered = store.claim_callbacks(
        "tenant-a",
        limit=1,
        lease_seconds=10,
        idempotency_key="claim-recovery",
    )[0]
    assert recovered.recovery_count == 1
    assert recovered.lease_token != first.lease_token
    failed = store.fail_callback(
        "tenant-a",
        recovered.callback_id,
        lease_token=recovered.lease_token or "",
        error_code="provider_unavailable",
        retry_delay_seconds=0,
        expected_revision=recovered.revision,
        idempotency_key="fail-1",
    )
    assert failed.state == "pending"
    second = store.claim_callbacks(
        "tenant-a",
        limit=1,
        lease_seconds=10,
        idempotency_key="claim-2",
    )[0]
    dead = store.fail_callback(
        "tenant-a",
        second.callback_id,
        lease_token=second.lease_token or "",
        error_code="provider_unavailable",
        retry_delay_seconds=0,
        expected_revision=second.revision,
        idempotency_key="fail-2",
    )
    assert dead.state == "dead_letter"
    requeued = store.requeue_callback(
        "tenant-a",
        dead.callback_id,
        expected_revision=dead.revision,
        idempotency_key="requeue",
    )
    assert requeued.state == "pending"
    assert requeued.attempts == 0
    final_claim = store.claim_callbacks(
        "tenant-a",
        limit=1,
        lease_seconds=10,
        idempotency_key="claim-final",
    )[0]
    acked = store.ack_callback(
        "tenant-a",
        final_claim.callback_id,
        lease_token=final_claim.lease_token or "",
        acknowledgement_sha256=digest("ack"),
        acknowledgement_ref="ack:final",
        expected_revision=final_claim.revision,
        idempotency_key="ack-final",
    )
    assert acked.state == "acked"
    receipt = store.record_source_evidence(
        "tenant-a",
        case.case_id,
        "crm",
        "discover",
        evidence_id="evidence:final",
        status="complete",
        provider_evidence_sha256=digest("provider-receipt"),
        record_count=1,
        opaque_ref="provider:final",
        expected_task_revision=tasks[0].revision,
        idempotency_key="evidence-final",
    )
    assert receipt.provider_evidence_sha256 == digest("provider-receipt")


def test_completeness_manifest_names_enrolled_covered_and_uncovered(authority) -> None:
    store, _actor, clock = authority
    enroll_all(store, "tenant-a", "crm", key="crm")
    enroll_all(store, "tenant-a", "archive", key="archive")
    case = open_verified(store, clock, "tenant-a", "case-coverage", ["access"])
    tasks = store.plan_source_tasks(
        "tenant-a",
        case.case_id,
        "crm",
        phases=["discover"],
        expected_case_revision=case.revision,
        idempotency_key="plan-crm",
    )
    complete_callback_phase(
        store,
        "tenant-a",
        case.case_id,
        "crm",
        tasks[0],
        suffix="coverage",
    )
    manifest = store.build_completeness_manifest(
        "tenant-a",
        case.case_id,
        "manifest:coverage",
        expected_case_revision=case.revision + 1,
        idempotency_key="manifest",
    )
    assert manifest.enrolled_source_ids == ("archive", "crm")
    assert manifest.covered_source_ids == ("crm",)
    assert manifest.uncovered_sources == (
        {"source_id": "archive", "reason": "not_planned"},
    )
    assert manifest.estate_completeness_claimed is False
    assert "does not discover or infer" in manifest.limitation


def test_access_package_closure_four_eyes_export_integrity_and_purge(authority) -> None:
    store, actor, clock = authority
    enroll_all(store, "tenant-a", "crm", key="source")
    case = open_verified(store, clock, "tenant-a", "case-complete", ["access"])
    tasks = store.plan_source_tasks(
        "tenant-a",
        case.case_id,
        "crm",
        expected_case_revision=case.revision,
        idempotency_key="plan",
    )
    complete_callback_phase(
        store,
        "tenant-a",
        case.case_id,
        "crm",
        task_by_phase(tasks, "discover"),
        suffix="complete-discover",
    )
    complete_callback_phase(
        store,
        "tenant-a",
        case.case_id,
        "crm",
        task_by_phase(tasks, "retrieve"),
        suffix="complete-retrieve",
    )
    complete_local_phase(
        store,
        "tenant-a",
        case.case_id,
        "crm",
        task_by_phase(tasks, "redact"),
        suffix="complete-redact",
    )
    manifest = store.build_completeness_manifest(
        "tenant-a",
        case.case_id,
        "manifest:complete",
        expected_case_revision=case.revision + 1,
        idempotency_key="manifest",
    )
    assert manifest.covered_source_ids == ("crm",)
    package = store.create_response_package(
        "tenant-a",
        case.case_id,
        "package:complete",
        object_ref="encrypted-object:complete",
        package_sha256=digest("package"),
        encryption_key_ref="vault:response-key",
        expires_at=clock() + 60,
        expected_case_revision=case.revision + 2,
        idempotency_key="package",
    )
    assert package.delivery_performed is False
    assert store.get_response_package("tenant-a", package.package_id) == package
    exported = store.export_case(
        "tenant-a",
        case.case_id,
        "export:complete",
        retained_until=clock() + 90,
        idempotency_key="export",
    )
    assert exported.body["limitations"]["provider_operations_performed"] is False

    actor["value"] = "operator"
    with pytest.raises(rights.PrivacyRightsPermissionDenied, match="distinct"):
        store.approve_case_closure(
            "tenant-a",
            case.case_id,
            expected_revision=case.revision + 3,
            idempotency_key="self-closure-approval",
        )
    actor["value"] = "closure-approver"
    approved = store.approve_case_closure(
        "tenant-a",
        case.case_id,
        expected_revision=case.revision + 3,
        idempotency_key="closure-approval",
    )
    actor["value"] = "operator"
    closed = store.close_case(
        "tenant-a",
        case.case_id,
        expected_revision=approved.revision,
        idempotency_key="close",
    )
    assert closed.status == "closed"
    verification = store.verify_integrity("tenant-a")
    assert verification.verified_records > 0
    assert verification.audit_events > 0

    clock.advance(601)
    purged = store.purge_expired(
        "tenant-a", cutoff=clock(), idempotency_key="purge"
    )
    assert purged == {"packages": 1, "exports": 1, "cases": 1}
    with pytest.raises(rights.PrivacyRightsNotFound):
        store.get_case("tenant-a", case.case_id)
    store.verify_integrity("tenant-a")


def test_integrity_verification_detects_tampering(authority) -> None:
    store, _actor, _clock = authority
    enroll_all(store, "tenant-a", "crm", key="source")
    store.verify_integrity("tenant-a")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE rights_sources SET payload_json=? "
            "WHERE tenant_id=? AND source_id=?",
            ('{"tenant_id":"tenant-a"}', "tenant-a", "crm"),
        )
    with pytest.raises(rights.PrivacyRightsIntegrityError):
        store.verify_integrity("tenant-a")


def test_integrity_verification_is_strictly_tenant_scoped(authority) -> None:
    store, _actor, _clock = authority
    enroll_all(store, "tenant-a", "crm-a", key="source-a")
    enroll_all(store, "tenant-b", "crm-b", key="source-b")
    before = store.verify_integrity("tenant-a")
    tenant_b_before = store.verify_integrity("tenant-b")
    assert before.verified_records == tenant_b_before.verified_records

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE rights_sources SET payload_json=? "
            "WHERE tenant_id=? AND source_id=?",
            ('{"tenant_id":"tenant-b"}', "tenant-b", "crm-b"),
        )

    after = store.verify_integrity("tenant-a")
    assert after == before
    with pytest.raises(rights.PrivacyRightsIntegrityError):
        store.verify_integrity("tenant-b")


def test_product_boundaries_make_no_provider_or_completeness_claim() -> None:
    boundaries = rights.PrivacyRightsOrchestrationStore.product_boundaries()
    assert boundaries == {
        "provider_neutral": True,
        "network_io": False,
        "provider_writes": False,
        "identity_verification_performed": False,
        "response_delivery_performed": False,
        "raw_subject_persisted": False,
        "raw_external_request_ref_persisted": False,
        "credential_values_persisted": False,
        "provider_origins_persisted": False,
        "estate_completeness_claimed": False,
        "live_provider_acceptance_verified": False,
    }
