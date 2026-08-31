from __future__ import annotations

import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from maverick.third_party_collaboration import (
    EMAIL_DELIVERY_IMPLEMENTED,
    MODEL_INFERENCE_IMPLEMENTED,
    NETWORK_TRANSPORT_IMPLEMENTED,
    UPLOAD_BYTES_IMPLEMENTED,
    VENDOR_ATTESTATIONS_INDEPENDENTLY_VERIFIED,
    ThirdPartyCollaborationAuthError,
    ThirdPartyCollaborationConfigError,
    ThirdPartyCollaborationConflict,
    ThirdPartyCollaborationIntegrityError,
    ThirdPartyCollaborationPermissionDenied,
    ThirdPartyCollaborationPolicy,
    ThirdPartyCollaborationStore,
    ThirdPartyCollaborationValidationError,
    enabled,
)
from maverick.third_party_lifecycle import ThirdPartyLifecycleStore

DAY = 24 * 60 * 60
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def test_collaboration_gate_requires_exact_parent_lifecycle_and_portal() -> None:
    config = {
        "third_party_management": {
            "enable": True,
            "lifecycle": {"enable": True, "portal": {"enable": True}},
        }
    }
    assert enabled(config)
    assert not enabled({})
    assert not enabled(
        {
            "third_party_management": {
                "enable": True,
                "lifecycle": {"enable": True, "portal": {"enable": 1}},
            }
        }
    )
    assert not enabled(
        {
            "third_party_management": {
                "enable": True,
                "lifecycle": {"enable": True},
            }
        }
    )
    assert issubclass(ThirdPartyCollaborationConfigError, RuntimeError)


class Clock:
    def __init__(self, value: float = 2_000_000_000.0) -> None:
        self.value = value
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.value += seconds


class Principal:
    def __init__(self, value: str = "portal-admin") -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


class Tokens:
    def __init__(self) -> None:
        self.value = 0
        self._lock = threading.Lock()

    def __call__(self, length: int) -> bytes:
        assert length == 32
        with self._lock:
            self.value += 1
            return bytes([self.value]) * 32


def questions() -> list[dict[str, object]]:
    return [
        {
            "question_id": "security_control",
            "domain": "security",
            "prompt_ref": "prompt.security_control",
            "response_type": "boolean",
            "required": True,
            "options": [],
            "condition": None,
            "risk_rules": [
                {
                    "operator": "equals",
                    "value": False,
                    "severity": "high",
                    "reason_code": "security_control_absent",
                }
            ],
        },
        {
            "question_id": "privacy_scope",
            "domain": "privacy",
            "prompt_ref": "prompt.privacy_scope",
            "response_type": "single_choice",
            "required": True,
            "options": ["low", "high"],
            "condition": {
                "question_id": "security_control",
                "operator": "equals",
                "value": True,
            },
            "risk_rules": [
                {
                    "operator": "equals",
                    "value": "high",
                    "severity": "high",
                    "reason_code": "high_privacy_scope",
                }
            ],
        },
    ]


@pytest.fixture
def authority(
    tmp_path: Path,
) -> tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal]:
    clock = Clock()
    principal = Principal("author")
    lifecycle_path = tmp_path / "private" / "lifecycle.sqlite3"
    lifecycle = ThirdPartyLifecycleStore(
        lifecycle_path,
        config_gate=lambda: None,
        principal_resolver=principal,
        clock=clock,
    )
    lifecycle.create_template_version(
        "tenant-a",
        "standard",
        title_ref="title.standard",
        questions=questions(),
        mitigation_rules=[],
        expected_latest_version=0,
        idempotency_key="template-create",
    )
    principal.value = "reviewer"
    lifecycle.approve_template_version(
        "tenant-a",
        "standard",
        1,
        expected_revision=1,
        idempotency_key="template-approve",
    )
    principal.value = "creator"
    lifecycle.create_assessment(
        "tenant-a",
        "assessment-1",
        vendor_ref="vendor-1",
        template_id="standard",
        template_version=1,
        owner_ref="owner",
        respondent_assignments={
            "security_control": "vendor-respondent",
            "privacy_scope": "vendor-respondent",
        },
        retention_seconds=DAY * 10,
        idempotency_key="assessment-create",
    )
    principal.value = "portal-admin"
    collaboration = ThirdPartyCollaborationStore(
        tmp_path / "private" / "collaboration.sqlite3",
        lifecycle_path=lifecycle_path,
        config_gate=lambda: None,
        principal_resolver=principal,
        clock=clock,
        token_factory=Tokens(),
    )
    return collaboration, lifecycle, clock, principal


def policy() -> dict[str, object]:
    return {
        "allowed_kinds": ["audit_report", "policy"],
        "maximum_artifacts": 5,
        "maximum_total_bytes": 10_000,
    }


def issue(
    store: ThirdPartyCollaborationStore,
    invitation_id: str = "invite-1",
    idempotency_key: str = "invite-issue",
) -> tuple[str, dict[str, object]]:
    result = store.issue_invitation(
        "tenant-a",
        invitation_id,
        assessment_id="assessment-1",
        respondent_ref="vendor-respondent",
        allowed_question_ids=["security_control", "privacy_scope"],
        notice_ref="notice.vendor-assessment.v1",
        notice_sha256=DIGEST_A,
        artifact_policy=policy(),
        expires_in_seconds=DAY,
        retention_seconds=DAY * 2,
        idempotency_key=idempotency_key,
    )
    assert result.token is not None
    return result.token, dict(result.invitation)


def complete_answers() -> dict[str, dict[str, object]]:
    return {
        "security_control": {"value": True, "artifact_ids": ["report-1"]},
        "privacy_scope": {"value": "high", "artifact_ids": []},
    }


def manifests() -> list[dict[str, object]]:
    return [
        {
            "artifact_id": "report-1",
            "kind": "audit_report",
            "content_sha256": DIGEST_B,
            "size_bytes": 512,
            "media_type_ref": "application.pdf",
        }
    ]


def test_invitation_token_is_returned_once_and_never_persisted(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
) -> None:
    store, _, _, _ = authority
    token, invitation = issue(store)
    assert token.startswith("tpc1.")
    assert len(token.removeprefix("tpc1.")) == 43
    assert "token" not in invitation
    assert "token_sha256" not in invitation
    replay = store.issue_invitation(
        "tenant-a",
        "invite-1",
        assessment_id="assessment-1",
        respondent_ref="vendor-respondent",
        allowed_question_ids=["security_control", "privacy_scope"],
        notice_ref="notice.vendor-assessment.v1",
        notice_sha256=DIGEST_A,
        artifact_policy=policy(),
        expires_in_seconds=DAY,
        retention_seconds=DAY * 2,
        idempotency_key="invite-issue",
    )
    assert replay.token is None
    assert replay.token_disclosed is False
    assert replay.idempotent_replay is True
    assert token.encode() not in store.path.read_bytes()
    with sqlite3.connect(store.path) as connection:
        payload = connection.execute("SELECT payload_json FROM invitations").fetchone()[0]
    assert token not in payload
    assert hashlib.sha256(token.encode()).hexdigest() in payload


def test_internal_invitation_listing_is_bounded_redacted_and_tenant_scoped(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
) -> None:
    store, _, _, _ = authority
    first_token, _ = issue(store, "invite-1", "issue-list-1")
    second_token, _ = issue(store, "invite-2", "issue-list-2")
    first = store.list_invitations("tenant-a", limit=1)
    assert first["count"] == 1
    assert first["truncated"] is True
    assert first["next_after"] == "invite-1"
    assert first["items"][0]["invitation_id"] == "invite-1"
    second = store.list_invitations(
        "tenant-a",
        limit=1,
        after=str(first["next_after"]),
    )
    assert second["count"] == 1
    assert second["truncated"] is False
    rendered = repr((first, second))
    assert first_token not in rendered
    assert second_token not in rendered
    assert "token_sha256" not in rendered
    assert store.list_invitations("tenant-b") == {
        "items": [],
        "count": 0,
        "truncated": False,
        "next_after": None,
    }


def test_vendor_idempotency_keys_are_scoped_to_invitation_and_generation(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
) -> None:
    store, _, _, _ = authority
    first_token, _ = issue(store, "invite-1", "common-client-key")
    second_token, _ = issue(store, "invite-2", "common-client-key")

    first = store.save_draft(
        "tenant-a",
        first_token,
        answers={"security_control": {"value": True, "artifact_ids": []}},
        artifacts=[],
        expected_revision=0,
        idempotency_key="common-client-key",
    )
    second = store.save_draft(
        "tenant-a",
        second_token,
        answers={"security_control": {"value": False, "artifact_ids": []}},
        artifacts=[],
        expected_revision=0,
        idempotency_key="common-client-key",
    )

    assert first.invitation_id == "invite-1"
    assert second.invitation_id == "invite-2"
    assert first.idempotent_replay is False
    assert second.idempotent_replay is False


def test_non_default_portal_limits_are_enforced_by_the_store(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
    tmp_path: Path,
) -> None:
    _store, lifecycle, clock, principal = authority
    limited = ThirdPartyCollaborationStore(
        tmp_path / "private" / "limited-collaboration.sqlite3",
        lifecycle_path=lifecycle.path,
        config_gate=lambda: None,
        principal_resolver=principal,
        clock=clock,
        token_factory=Tokens(),
        policy=ThirdPartyCollaborationPolicy(
            invitation_ttl_seconds=300,
            retention_seconds=3_600,
            max_messages=1,
            max_artifacts=1,
            max_artifact_bytes=1_024,
        ),
    )
    issued = limited.issue_invitation(
        "tenant-a",
        "limited-invite",
        assessment_id="assessment-1",
        respondent_ref="vendor-respondent",
        allowed_question_ids=["security_control", "privacy_scope"],
        notice_ref="notice.vendor-assessment.v1",
        notice_sha256=DIGEST_A,
        artifact_policy={
            "allowed_kinds": ["audit_report"],
            "maximum_artifacts": 1,
            "maximum_total_bytes": 1_024,
        },
        expires_in_seconds=300,
        retention_seconds=3_600,
        idempotency_key="limited-issue",
    )
    assert issued.token is not None

    with pytest.raises(ThirdPartyCollaborationValidationError, match="portal TTL"):
        limited.issue_invitation(
            "tenant-a",
            "too-long",
            assessment_id="assessment-1",
            respondent_ref="vendor-respondent",
            allowed_question_ids=["security_control", "privacy_scope"],
            notice_ref="notice.vendor-assessment.v1",
            notice_sha256=DIGEST_A,
            artifact_policy=policy(),
            expires_in_seconds=301,
            retention_seconds=3_600,
            idempotency_key="too-long",
        )

    with pytest.raises(ThirdPartyCollaborationValidationError, match="artifact policy"):
        limited.issue_invitation(
            "tenant-a",
            "too-many-artifacts",
            assessment_id="assessment-1",
            respondent_ref="vendor-respondent",
            allowed_question_ids=["security_control", "privacy_scope"],
            notice_ref="notice.vendor-assessment.v1",
            notice_sha256=DIGEST_A,
            artifact_policy=policy(),
            expires_in_seconds=300,
            retention_seconds=3_600,
            idempotency_key="too-many-artifacts",
        )


def test_contract_is_safe_and_visibility_is_computed_server_side(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
) -> None:
    store, _, _, _ = authority
    token, _ = issue(store)
    contract = store.contract_for_token("tenant-a", token)
    assert [item["question_id"] for item in contract["questions"]] == ["security_control"]
    assert set(contract["questions"][0]) == {
        "question_id",
        "domain",
        "prompt_ref",
        "response_type",
        "required",
        "options",
    }
    serialized = repr(contract)
    for forbidden in (
        "risk_rules",
        "condition",
        "owner_ref",
        "assignments",
        "highest_severity",
        "reason_codes",
        "recommendations",
    ):
        assert forbidden not in serialized
    receipt = store.save_draft(
        "tenant-a",
        token,
        answers={"security_control": {"value": True, "artifact_ids": []}},
        artifacts=[],
        expected_revision=0,
        idempotency_key="draft-visible",
    )
    assert receipt.active_question_ids == ("security_control", "privacy_scope")
    contract = store.contract_for_token("tenant-a", token)
    assert [item["question_id"] for item in contract["questions"]] == [
        "security_control",
        "privacy_scope",
    ]


def test_token_failures_are_non_enumerating_across_tenant_expiry_rotation_and_revoke(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
) -> None:
    store, _, clock, _ = authority
    token, invitation = issue(store)
    wrong = "tpc1." + "A" * 43
    messages: list[str] = []
    for tenant, credential in (("tenant-b", token), ("tenant-a", wrong), ("tenant-a", "bad")):
        with pytest.raises(ThirdPartyCollaborationAuthError) as caught:
            store.contract_for_token(tenant, credential)
        messages.append(str(caught.value))
    with pytest.raises(ThirdPartyCollaborationAuthError, match="invalid invitation"):
        store.save_draft(
            "tenant-a",
            wrong,
            answers={},
            artifacts=[],
            expected_revision=-1,
            idempotency_key="malformed-invalid-token-draft",
        )
    with pytest.raises(ThirdPartyCollaborationAuthError, match="invalid invitation"):
        store.finalize_submission(
            "tenant-a",
            wrong,
            answers={},
            artifacts=[],
            attested=False,
            expected_draft_revision=-1,
            idempotency_key="malformed-invalid-token-final",
        )
    rotated = store.rotate_invitation(
        "tenant-a",
        "invite-1",
        expires_in_seconds=DAY,
        expected_revision=int(invitation["revision"]),
        idempotency_key="rotate",
    )
    assert rotated.token is not None
    rotate_replay = store.rotate_invitation(
        "tenant-a",
        "invite-1",
        expires_in_seconds=DAY,
        expected_revision=int(invitation["revision"]),
        idempotency_key="rotate",
    )
    assert rotate_replay.token is None
    assert rotate_replay.idempotent_replay is True
    with pytest.raises(ThirdPartyCollaborationAuthError) as caught:
        store.contract_for_token("tenant-a", token)
    messages.append(str(caught.value))
    revoked = store.revoke_invitation(
        "tenant-a",
        "invite-1",
        reason_code="request_withdrawn",
        expected_revision=int(rotated.invitation["revision"]),
        idempotency_key="revoke",
    )
    with pytest.raises(ThirdPartyCollaborationAuthError) as caught:
        store.contract_for_token("tenant-a", rotated.token)
    messages.append(str(caught.value))
    assert revoked["status"] == "revoked"
    expiry_token, _ = issue(store, "invite-expiry", "invite-expiry-issue")
    clock.advance(DAY + 1)
    with pytest.raises(ThirdPartyCollaborationAuthError) as caught:
        store.contract_for_token("tenant-a", expiry_token)
    messages.append(str(caught.value))
    assert set(messages) == {"invalid invitation"}


def test_draft_is_atomic_and_rejects_hidden_answers_and_idempotency_conflicts(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
) -> None:
    store, _, _, _ = authority
    token, _ = issue(store)
    with pytest.raises(ThirdPartyCollaborationValidationError, match="schema is closed"):
        store.save_draft(
            "tenant-a",
            token,
            answers={"security_control": {"value": True, "artifact_ids": ["report-1"]}},
            artifacts=[{**manifests()[0], "content": "raw-upload-bytes"}],
            expected_revision=0,
            idempotency_key="upload-bytes-rejected",
        )
    with pytest.raises(ThirdPartyCollaborationConflict, match="not currently visible"):
        store.save_draft(
            "tenant-a",
            token,
            answers={
                "security_control": {"value": False, "artifact_ids": []},
                "privacy_scope": {"value": "high", "artifact_ids": []},
            },
            artifacts=[],
            expected_revision=0,
            idempotency_key="hidden-draft",
        )
    assert store.contract_for_token("tenant-a", token)["draft_revision"] == 0
    first = store.save_draft(
        "tenant-a",
        token,
        answers={"security_control": {"value": True, "artifact_ids": []}},
        artifacts=[],
        expected_revision=0,
        idempotency_key="draft-one",
    )
    replay = store.save_draft(
        "tenant-a",
        token,
        answers={"security_control": {"value": True, "artifact_ids": []}},
        artifacts=[],
        expected_revision=0,
        idempotency_key="draft-one",
    )
    assert replay.revision == first.revision
    assert replay.idempotent_replay is True
    with pytest.raises(ThirdPartyCollaborationConflict, match="different canonical content"):
        store.save_draft(
            "tenant-a",
            token,
            answers={"security_control": {"value": False, "artifact_ids": []}},
            artifacts=[],
            expected_revision=0,
            idempotency_key="draft-one",
        )


def test_final_submission_is_attestation_only_and_lost_response_replays(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, lifecycle, clock, _ = authority
    token, _ = issue(store)
    original = store._complete_finalization  # noqa: SLF001

    def lose_response(*args: object, **kwargs: object) -> object:
        raise RuntimeError("response lost after lifecycle commit")

    monkeypatch.setattr(store, "_complete_finalization", lose_response)
    with pytest.raises(RuntimeError, match="response lost"):
        store.finalize_submission(
            "tenant-a",
            token,
            answers=complete_answers(),
            artifacts=manifests(),
            attested=True,
            expected_draft_revision=0,
            idempotency_key="final-one",
        )
    after_commit = lifecycle.get_assessment("tenant-a", "assessment-1")
    assert after_commit.revision == 2
    clock.advance(DAY + 1)
    monkeypatch.setattr(store, "_complete_finalization", original)
    receipt = store.finalize_submission(
        "tenant-a",
        token,
        answers=complete_answers(),
        artifacts=manifests(),
        attested=True,
        expected_draft_revision=0,
        idempotency_key="final-one",
    )
    assert receipt.evidence_status == "respondent_attestation_unverified"
    assert receipt.assessment_revision == 2
    assert receipt.idempotent_replay is True
    assert lifecycle.get_assessment("tenant-a", "assessment-1").revision == 2
    replay = store.finalize_submission(
        "tenant-a",
        token,
        answers=complete_answers(),
        artifacts=manifests(),
        attested=True,
        expected_draft_revision=0,
        idempotency_key="final-one",
    )
    assert replay.idempotent_replay is True
    assessment = lifecycle.get_assessment("tenant-a", "assessment-1")
    assert assessment.route == "not_evaluated"
    assert assessment.highest_severity is None
    assert assessment.responses["security_control"]["evidence_status"] == (
        "respondent_attestation_unverified"
    )
    assert "content" not in repr(store.contract_for_token)  # manifests only boundary


def test_final_rejects_authority_drift_and_explicit_attestation_is_mandatory(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
) -> None:
    store, lifecycle, _, principal = authority
    token, _ = issue(store)
    with pytest.raises(ThirdPartyCollaborationPermissionDenied, match="attestation"):
        store.finalize_submission(
            "tenant-a",
            token,
            answers=complete_answers(),
            artifacts=manifests(),
            attested=False,
            expected_draft_revision=0,
            idempotency_key="unattested",
        )
    assessment = lifecycle.get_assessment("tenant-a", "assessment-1")
    principal.value = "vendor-respondent"
    lifecycle.record_response(
        "tenant-a",
        "assessment-1",
        "security_control",
        value=True,
        evidence_sha256=DIGEST_A,
        evidence_refs=["external.change"],
        expected_revision=assessment.revision,
        idempotency_key="external-change",
    )
    principal.value = "portal-admin"
    with pytest.raises(ThirdPartyCollaborationConflict, match="authority changed"):
        store.finalize_submission(
            "tenant-a",
            token,
            answers=complete_answers(),
            artifacts=manifests(),
            attested=True,
            expected_draft_revision=0,
            idempotency_key="drifted-final",
        )
    assert store.get_invitation("tenant-a", "invite-1")["status"] == "active"


def test_follow_up_requires_internal_transition_and_reopens_with_new_pins(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
) -> None:
    store, _, _, _ = authority
    token, _ = issue(store)
    store.finalize_submission(
        "tenant-a",
        token,
        answers=complete_answers(),
        artifacts=manifests(),
        attested=True,
        expected_draft_revision=0,
        idempotency_key="final-follow-up",
    )
    finalized = store.get_invitation("tenant-a", "invite-1")
    requested = store.request_follow_up(
        "tenant-a",
        "invite-1",
        question_ids=["privacy_scope"],
        reason_code="clarification_required",
        expected_revision=int(finalized["revision"]),
        idempotency_key="follow-up-request",
    )
    with pytest.raises(ThirdPartyCollaborationAuthError):
        store.contract_for_token("tenant-a", token)
    reopened = store.reopen_follow_up(
        "tenant-a",
        "invite-1",
        expires_in_seconds=DAY,
        expected_revision=int(requested["revision"]),
        idempotency_key="follow-up-reopen",
    )
    assert reopened.token is not None and reopened.token != token
    assert reopened.invitation["assessment_revision"] == 2
    contract = store.contract_for_token("tenant-a", reopened.token)
    assert [item["question_id"] for item in contract["questions"]] == ["privacy_scope"]


def test_concurrent_revocation_and_finalization_fail_closed(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
) -> None:
    store, _, _, _ = authority
    token, invitation = issue(store)

    def finalize() -> str:
        try:
            store.finalize_submission(
                "tenant-a",
                token,
                answers=complete_answers(),
                artifacts=manifests(),
                attested=True,
                expected_draft_revision=0,
                idempotency_key="concurrent-final",
            )
            return "finalized"
        except (ThirdPartyCollaborationAuthError, ThirdPartyCollaborationConflict):
            return "closed"

    def revoke() -> str:
        try:
            store.revoke_invitation(
                "tenant-a",
                "invite-1",
                reason_code="security_event",
                expected_revision=int(invitation["revision"]),
                idempotency_key="concurrent-revoke",
            )
            return "revoked"
        except ThirdPartyCollaborationConflict:
            return "closed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda function: function(), (finalize, revoke)))
    assert outcomes.count("closed") == 1
    assert len(set(outcomes) & {"finalized", "revoked"}) == 1
    assert store.get_invitation("tenant-a", "invite-1")["status"] in {
        "finalized",
        "revoked",
    }


def test_retention_legal_hold_history_and_integrity_tamper_detection(
    authority: tuple[ThirdPartyCollaborationStore, ThirdPartyLifecycleStore, Clock, Principal],
) -> None:
    store, _, clock, _ = authority
    _, invitation = issue(store)
    held = store.set_legal_hold(
        "tenant-a",
        "invite-1",
        active=True,
        evidence_ref="legal.case-1",
        expected_revision=int(invitation["revision"]),
        idempotency_key="hold-on",
    )
    clock.advance(DAY * 3)
    assert store.purge_expired("tenant-a", before=clock(), idempotency_key="purge-held") == {
        "invitations": 0,
        "drafts": 0,
    }
    released = store.set_legal_hold(
        "tenant-a",
        "invite-1",
        active=False,
        evidence_ref="legal.release-1",
        expected_revision=int(held["revision"]),
        idempotency_key="hold-off",
    )
    assert released["legal_hold"] is False
    counts = store.purge_expired("tenant-a", before=clock(), idempotency_key="purge-released")
    assert counts == {"invitations": 1, "drafts": 0}
    verification = store.verify_integrity("tenant-a")
    assert verification.history_events >= 5
    assert verification.terminal_history_sha256 != "0" * 64
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE history SET payload_json='{}' WHERE tenant_id='tenant-a' AND sequence=1"
        )
    with pytest.raises(ThirdPartyCollaborationIntegrityError):
        store.verify_integrity("tenant-a")


def test_capability_boundaries_are_explicitly_false() -> None:
    assert NETWORK_TRANSPORT_IMPLEMENTED is False
    assert EMAIL_DELIVERY_IMPLEMENTED is False
    assert MODEL_INFERENCE_IMPLEMENTED is False
    assert UPLOAD_BYTES_IMPLEMENTED is False
    assert VENDOR_ATTESTATIONS_INDEPENDENTLY_VERIFIED is False
