from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from maverick import config
from maverick.consent_preferences import (
    ConsentConflict,
    ConsentContext,
    ConsentIdempotencyConflict,
    ConsentNotFound,
    ConsentPreferencesConfigError,
    ConsentPreferencesError,
    ConsentPreferenceStore,
    ConsentStalePolicy,
    ConsentValidationError,
    PreferenceChoice,
    ScanAdmissionError,
    TrackerObservation,
    enabled,
    require_enabled,
)

NOW = 1_800_000_000.0
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


class MutableClock:
    def __init__(self, value: float = NOW):
        self.value = value

    def __call__(self) -> float:
        return self.value


class IdFactory:
    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def __call__(self, prefix: str) -> str:
        with self._lock:
            self._value += 1
            return f"{prefix}-{self._value:08d}"


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def store(tmp_path: Path, clock: MutableClock) -> ConsentPreferenceStore:
    return ConsentPreferenceStore(
        tmp_path / "consent.sqlite3",
        clock=clock,
        id_factory=IdFactory(),
    )


def _policy(
    store: ConsentPreferenceStore,
    tenant: str = "tenant-a",
    *,
    policy_id: str = "privacy-notice",
    version: int = 1,
    expected: int = 0,
    purposes: dict[str, str] | None = None,
    effective_at: float = NOW - 10,
) -> dict:
    return store.publish_policy(
        tenant,
        policy_id,
        version,
        purposes or {"analytics": "consent", "marketing": "consent"},
        DIGEST_A,
        effective_at,
        expected_current_version=expected,
    )


def _profile(
    store: ConsentPreferenceStore,
    tenant: str = "tenant-a",
    *,
    profile_id: str = "profile-001",
    identity_kind: str = "known",
    subject_sha256: str = DIGEST_B,
    idem: str = "profile-create-001",
) -> dict:
    return store.create_profile(
        tenant,
        profile_id,
        identity_kind,
        subject_sha256,
        idem,
    )


def _context() -> ConsentContext:
    return ConsentContext(
        jurisdiction="us-state-ca",
        country_code="US",
        region_code="US-CA",
        collection_point="privacy-center",
        asserted_source="client",
    )


def _record(
    store: ConsentPreferenceStore,
    *,
    tenant: str = "tenant-a",
    profile_id: str = "profile-001",
    action: str = "grant",
    purpose: str = "analytics",
    channel: str = "all",
    vendor_id: str = "all",
    expected_revision: int = 0,
    idem: str = "preference-001",
    policy_version: int = 1,
    expires_at: float | None = None,
    retention_until: float | None = None,
) -> dict:
    return store.record_preferences(
        tenant,
        profile_id,
        "privacy-notice",
        policy_version,
        _context(),
        [
            PreferenceChoice(
                purpose_id=purpose,
                action=action,
                channel=channel,
                vendor_id=vendor_id,
                expires_at=expires_at,
            )
        ],
        expected_revision=expected_revision,
        idempotency_key=idem,
        actor_sha256=DIGEST_C,
        retention_until=retention_until,
    )


def test_enabled_is_explicit_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "config_source_errors", list)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: {"consent_preferences": {"enable": True}},
    )
    assert enabled() is True

    monkeypatch.setattr(config, "load_config", lambda: {"consent_preferences": {}})
    assert enabled() is False
    with pytest.raises(ConsentPreferencesConfigError):
        require_enabled()

    monkeypatch.setattr(config, "config_source_errors", lambda: ["bad config"])
    monkeypatch.setattr(config, "load_config", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert enabled() is False


def test_tenants_can_reuse_profile_subject_and_idempotency_namespaces(
    store: ConsentPreferenceStore,
) -> None:
    _profile(store, "tenant-a")
    _profile(store, "tenant-b")

    assert [row["profile_id"] for row in store.list_profiles("tenant-a")["profiles"]] == [
        "profile-001"
    ]
    assert store.summary("tenant-a")["profiles"] == 1
    assert store.summary("tenant-b")["profiles"] == 1
    assert store.summary("tenant-c")["profiles"] == 0


@pytest.mark.parametrize(
    ("profile_id", "subject"),
    [
        ("person@example.com", DIGEST_A),
        ("profile-1", "person@example.com"),
        ("profile/1", DIGEST_A),
    ],
)
def test_profile_contract_rejects_raw_or_unbounded_identity_values(
    store: ConsentPreferenceStore,
    profile_id: str,
    subject: str,
) -> None:
    with pytest.raises(ConsentValidationError):
        store.create_profile("tenant-a", profile_id, "known", subject, "idem-1")


def test_policy_versions_are_append_only_and_compare_and_swap(
    store: ConsentPreferenceStore,
) -> None:
    first = _policy(store)
    assert first["version"] == 1
    with pytest.raises(ConsentConflict):
        _policy(store, version=2, expected=0)
    second = _policy(store, version=2, expected=1, effective_at=NOW + 100)
    assert second["version"] == 2
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE consent_policies SET notice_sha256 = ? WHERE tenant = ?",
                (DIGEST_D, "tenant-a"),
            )


def test_collection_rejects_stale_policy_and_new_policy_invalidates_old_grant(
    store: ConsentPreferenceStore,
) -> None:
    _policy(store)
    _profile(store)
    _record(store)
    _policy(store, version=2, expected=1)

    with pytest.raises(ConsentStalePolicy):
        _record(store, expected_revision=1, idem="old-policy", policy_version=1)
    decision = store.decide(
        "tenant-a",
        "profile-001",
        "analytics",
        policy_id="privacy-notice",
        policy_version=2,
    )
    assert decision.allowed is False
    assert decision.reason == "no_current_explicit_grant"


def test_receipts_are_exactly_bound_chained_and_sql_immutable(
    store: ConsentPreferenceStore,
) -> None:
    policy = _policy(store)
    _profile(store)
    first = _record(store, retention_until=NOW + 1000)
    second = _record(
        store,
        action="deny",
        expected_revision=1,
        idem="preference-002",
        retention_until=NOW + 1000,
    )

    assert first["policy_sha256"] == policy["policy_sha256"]
    assert first["context"]["geolocation"]["asserted"] is True
    assert first["context"]["geolocation"]["verified"] is False
    assert first["context_sha256"] == hashlib.sha256(
        json.dumps(
            first["context"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()
    assert second["previous_receipt_sha256"] == first["receipt_sha256"]
    second_body = dict(second)
    second_sha = second_body.pop("receipt_sha256")
    assert hashlib.sha256(
        json.dumps(
            second_body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest() == second_sha

    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM consent_receipts")


@pytest.mark.parametrize("action", ["deny", "revoke", "withdraw"])
def test_every_negative_action_enforces_denial(
    store: ConsentPreferenceStore,
    action: str,
) -> None:
    _policy(store)
    _profile(store)
    _record(store, action=action)
    decision = store.decide(
        "tenant-a", "profile-001", "analytics", policy_id="privacy-notice"
    )
    assert decision.allowed is False
    assert decision.reason == f"explicit_{action}"


def test_latest_revocation_on_linked_anonymous_profile_overrides_known_grant(
    store: ConsentPreferenceStore,
) -> None:
    _policy(store)
    _profile(store, profile_id="known-001", subject_sha256=DIGEST_A, idem="known")
    _profile(
        store,
        profile_id="anon-001",
        identity_kind="anonymous",
        subject_sha256=DIGEST_B,
        idem="anon",
    )
    store.link_profiles(
        "tenant-a",
        "known-001",
        "anon-001",
        expected_known_revision=0,
        expected_anonymous_revision=0,
        idempotency_key="link-1",
    )
    _record(
        store,
        profile_id="known-001",
        expected_revision=1,
        idem="known-grant",
    )
    _record(
        store,
        profile_id="anon-001",
        action="revoke",
        expected_revision=1,
        idem="anon-revoke",
    )

    for profile in ("known-001", "anon-001"):
        decision = store.decide(
            "tenant-a", profile, "analytics", policy_id="privacy-notice"
        )
        assert decision.allowed is False
        assert decision.reason == "explicit_revoke"


def test_granular_channel_purpose_and_vendor_choices_fail_closed(
    store: ConsentPreferenceStore,
) -> None:
    _policy(store)
    _profile(store)
    _record(store, channel="email", vendor_id="vendor-a")

    allowed = store.decide(
        "tenant-a",
        "profile-001",
        "analytics",
        channel="email",
        vendor_id="vendor-a",
        policy_id="privacy-notice",
    )
    assert allowed.allowed is True
    for purpose, channel, vendor in [
        ("marketing", "email", "vendor-a"),
        ("analytics", "sms", "vendor-a"),
        ("analytics", "email", "vendor-b"),
    ]:
        assert not store.decide(
            "tenant-a",
            "profile-001",
            purpose,
            channel=channel,
            vendor_id=vendor,
            policy_id="privacy-notice",
        ).allowed


def test_more_specific_choice_wins_and_equal_specificity_prefers_denial(
    store: ConsentPreferenceStore,
) -> None:
    _policy(store)
    _profile(store)
    store.record_preferences(
        "tenant-a",
        "profile-001",
        "privacy-notice",
        1,
        _context(),
        [
            PreferenceChoice("analytics", "grant"),
            PreferenceChoice("analytics", "deny", vendor_id="vendor-a"),
        ],
        expected_revision=0,
        idempotency_key="specificity",
        actor_sha256=DIGEST_C,
    )
    assert not store.decide(
        "tenant-a",
        "profile-001",
        "analytics",
        vendor_id="vendor-a",
        policy_id="privacy-notice",
    ).allowed
    assert store.decide(
        "tenant-a",
        "profile-001",
        "analytics",
        vendor_id="vendor-b",
        policy_id="privacy-notice",
    ).allowed


def test_grant_expiry_is_enforced(store: ConsentPreferenceStore, clock: MutableClock) -> None:
    _policy(store)
    _profile(store)
    _record(store, expires_at=NOW + 10, retention_until=NOW + 20)
    assert store.decide(
        "tenant-a", "profile-001", "analytics", policy_id="privacy-notice"
    ).allowed
    clock.value = NOW + 11
    decision = store.decide(
        "tenant-a", "profile-001", "analytics", policy_id="privacy-notice"
    )
    assert decision.allowed is False
    assert decision.reason == "no_current_explicit_grant"


def test_receipt_reactivation_date_is_auditable_and_enforced(
    store: ConsentPreferenceStore,
    clock: MutableClock,
) -> None:
    _policy(store)
    _profile(store)
    receipt = _record(
        store,
        expires_at=NOW + 10,
        retention_until=NOW + 100,
    )
    projected = store.set_receipt_reactivation(
        "tenant-a",
        "profile-001",
        receipt["receipt_id"],
        NOW + 20,
        expected_revision=1,
        idempotency_key="receipt-reactivation-001",
        actor_sha256=DIGEST_D,
    )
    assert projected["reactivation_at"] == NOW + 20
    assert projected["profile_revision"] == 2
    assert store.get_receipt_lifecycle(
        "tenant-a", receipt["receipt_id"]
    ) == {
        key: value for key, value in projected.items() if key != "profile_revision"
    }

    clock.value = NOW + 11
    assert not store.decide(
        "tenant-a", "profile-001", "analytics", policy_id="privacy-notice"
    ).allowed
    clock.value = NOW + 20
    decision = store.decide(
        "tenant-a", "profile-001", "analytics", policy_id="privacy-notice"
    )
    assert decision.allowed is True
    assert decision.receipt_id == receipt["receipt_id"]


def test_clear_expiration_overlay_restores_receipt_without_rewriting_it(
    store: ConsentPreferenceStore,
    clock: MutableClock,
) -> None:
    _policy(store)
    _profile(store)
    receipt = _record(
        store,
        expires_at=NOW + 10,
        retention_until=NOW + 100,
    )
    clock.value = NOW + 11
    assert not store.decide(
        "tenant-a", "profile-001", "analytics", policy_id="privacy-notice"
    ).allowed
    cleared = store.clear_receipt_expiration(
        "tenant-a",
        "profile-001",
        receipt["receipt_id"],
        expected_revision=1,
        idempotency_key="clear-expiration-001",
        actor_sha256=DIGEST_D,
    )
    assert cleared["clear_expiration"] is True
    assert cleared["profile_revision"] == 2
    assert store.decide(
        "tenant-a", "profile-001", "analytics", policy_id="privacy-notice"
    ).allowed
    assert store.get_receipt("tenant-a", receipt["receipt_id"])["choices"][0][
        "expires_at"
    ] == NOW + 10


def test_delete_subject_fails_closed_and_identifier_change_can_restore_authority(
    store: ConsentPreferenceStore,
) -> None:
    _policy(store)
    _profile(store)
    _record(store)
    deleted = store.delete_subject(
        "tenant-a",
        "profile-001",
        expected_revision=1,
        reason_code="verified-erasure",
        idempotency_key="delete-subject-001",
        actor_sha256=DIGEST_D,
    )
    assert deleted["status"] == "deleted"
    assert deleted["tombstone_sha256"] != DIGEST_B
    decision = store.decide(
        "tenant-a", "profile-001", "analytics", policy_id="privacy-notice"
    )
    assert decision.allowed is False
    assert decision.reason == "subject_deleted"
    authority = store.get_profile_authority("tenant-a", "profile-001")
    assert authority["subject_sha256"] == deleted["tombstone_sha256"]
    assert authority["lifecycle"]["status"] == "deleted"
    with pytest.raises(ConsentConflict, match="deleted profile"):
        _record(store, expected_revision=2, idem="preference-after-delete")

    restored = store.change_identifier_type(
        "tenant-a",
        "profile-001",
        "known",
        DIGEST_A,
        expected_revision=2,
        idempotency_key="change-identifier-001",
        actor_sha256=DIGEST_D,
    )
    assert restored["status"] == "active"
    assert restored["revision"] == 3
    assert store.decide(
        "tenant-a", "profile-001", "analytics", policy_id="privacy-notice"
    ).allowed

    with sqlite3.connect(store.path) as connection:
        live_digest = connection.execute(
            "SELECT subject_sha256 FROM consent_profiles "
            "WHERE tenant = ? AND profile_id = ?",
            ("tenant-a", "profile-001"),
        ).fetchone()[0]
    assert live_digest == DIGEST_A


def test_linked_profile_identity_kind_change_is_rejected(
    store: ConsentPreferenceStore,
) -> None:
    _profile(store, profile_id="known", subject_sha256=DIGEST_A, idem="known-profile")
    _profile(
        store,
        profile_id="anonymous",
        identity_kind="anonymous",
        subject_sha256=DIGEST_B,
        idem="anonymous-profile",
    )
    store.link_profiles(
        "tenant-a",
        "known",
        "anonymous",
        expected_known_revision=0,
        expected_anonymous_revision=0,
        idempotency_key="link-profiles-001",
    )
    with pytest.raises(ConsentConflict, match="linked profile"):
        store.change_identifier_type(
            "tenant-a",
            "anonymous",
            "known",
            DIGEST_C,
            expected_revision=1,
            idempotency_key="change-linked-identifier-001",
            actor_sha256=DIGEST_D,
        )


@pytest.mark.parametrize("signal", ["gpc", "dnt"])
def test_privacy_signals_are_asserted_context_and_deny(
    store: ConsentPreferenceStore,
    signal: str,
) -> None:
    _policy(store)
    _profile(store)
    receipt = store.ingest_privacy_signal(
        "tenant-a",
        "profile-001",
        signal,
        ["marketing"],
        "privacy-notice",
        1,
        _context(),
        expected_revision=0,
        idempotency_key=f"signal-{signal}",
        actor_sha256=DIGEST_C,
    )
    assert receipt["context"]["privacy_signal"] == signal
    assert not store.decide(
        "tenant-a", "profile-001", "marketing", policy_id="privacy-notice"
    ).allowed


def test_optimistic_concurrency_allows_exactly_one_writer(tmp_path: Path) -> None:
    clock = MutableClock()
    ids = IdFactory()
    path = tmp_path / "concurrent.sqlite3"
    setup = ConsentPreferenceStore(path, clock=clock, id_factory=ids)
    _policy(setup)
    _profile(setup)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []
    guard = threading.Lock()

    def writer(number: int) -> None:
        local = ConsentPreferenceStore(path, clock=clock, id_factory=ids)
        barrier.wait()
        try:
            _record(local, action="grant", idem=f"writer-{number}")
        except ConsentConflict:
            outcome = "conflict"
        else:
            outcome = "success"
        with guard:
            outcomes.append(outcome)

    threads = [threading.Thread(target=writer, args=(number,)) for number in (1, 2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sorted(outcomes) == ["conflict", "success"]


def test_idempotency_replays_exact_result_and_rejects_rebinding(
    store: ConsentPreferenceStore,
) -> None:
    _policy(store)
    _profile(store)
    first = _record(store, idem="same-operation")
    replay = _record(store, idem="same-operation")
    assert replay == first
    assert store.summary("tenant-a")["receipts"] == 1

    with pytest.raises(ConsentIdempotencyConflict):
        _record(
            store,
            action="deny",
            expected_revision=1,
            idem="same-operation",
        )


def test_outbox_ack_idempotency_and_replay(store: ConsentPreferenceStore) -> None:
    _profile(store)
    page = store.read_outbox("tenant-a", "app-east")
    assert len(page["events"]) == 1
    event = page["events"][0]
    ack = store.ack_outbox("tenant-a", "app-east", event["event_id"], "ack-1")
    assert store.ack_outbox(
        "tenant-a", "app-east", event["event_id"], "ack-1"
    ) == ack
    assert store.read_outbox("tenant-a", "app-east")["events"] == []
    replay = store.read_outbox(
        "tenant-a", "app-east", include_acked=True
    )["events"]
    assert [item["event_id"] for item in replay] == [event["event_id"]]
    assert len(store.read_outbox("tenant-a", "app-west")["events"]) == 1
    assert store.read_outbox("tenant-b", "app-east")["events"] == []


def test_tracker_registry_scan_admission_and_atomic_rejection(
    store: ConsentPreferenceStore,
) -> None:
    tracker = store.register_tracker(
        "tenant-a",
        "tracker-analytics",
        "web_tracker",
        "vendor-a",
        ["analytics"],
        "approved",
        expected_revision=0,
        idempotency_key="tracker-create",
    )
    assert tracker["revision"] == 1
    observation = TrackerObservation(
        "asset-home",
        "tracker-analytics",
        "slot-head",
        DIGEST_B,
    )
    result = store.admit_scan_observations(
        "tenant-a",
        "scan-001",
        [observation],
        NOW - 1,
        DIGEST_C,
        "scan-admit-1",
    )
    assert result["admitted_count"] == 1
    assert len(store.list_scan_observations("tenant-a", "scan-001")["observations"]) == 1

    with pytest.raises(ScanAdmissionError):
        store.admit_scan_observations(
            "tenant-a",
            "scan-002",
            [
                observation,
                TrackerObservation("asset-home", "unknown-tracker", "slot-body", DIGEST_D),
            ],
            NOW - 1,
            DIGEST_C,
            "scan-admit-2",
        )
    assert store.list_scan_observations("tenant-a", "scan-002")["observations"] == []


def test_tracker_enforcement_is_closed_world(store: ConsentPreferenceStore) -> None:
    _policy(store)
    _profile(store)
    _record(store, vendor_id="vendor-a")
    store.register_tracker(
        "tenant-a",
        "tracker-analytics",
        "web_tracker",
        "vendor-a",
        ["analytics"],
        "approved",
        expected_revision=0,
        idempotency_key="tracker-create",
    )
    assert store.decide(
        "tenant-a",
        "profile-001",
        "analytics",
        vendor_id="vendor-a",
        tracker_id="tracker-analytics",
        policy_id="privacy-notice",
    ).allowed
    assert not store.decide(
        "tenant-a",
        "profile-001",
        "analytics",
        vendor_id="vendor-a",
        tracker_id="not-registered",
        policy_id="privacy-notice",
    ).allowed


def test_export_is_bounded_logged_and_database_does_not_retain_idempotency_secret(
    store: ConsentPreferenceStore,
) -> None:
    _policy(store)
    _profile(store, idem="operatorSecretToken")
    _record(store)
    export = store.export_profile(
        "tenant-a",
        "profile-001",
        actor_sha256=DIGEST_D,
        access_purpose="data-subject-access",
        receipt_limit=1,
    )
    assert export["profiles"][0]["subject_sha256"] == DIGEST_B
    log = store.list_access_log("tenant-a", "profile-001")
    assert log["events"][0]["export_sha256"] == export["export_sha256"]
    database = store.path.read_bytes()
    assert b"operatorSecretToken" not in database
    assert b"person@example.com" not in database


def test_receipt_lookup_is_exact_tenant_scoped_and_integrity_checked(
    store: ConsentPreferenceStore,
) -> None:
    _policy(store)
    _profile(store)
    created = _record(store)

    receipt = store.get_receipt("tenant-a", created["receipt_id"])
    assert receipt["receipt_sha256"] == created["receipt_sha256"]
    assert receipt["profile_id"] == "profile-001"

    with pytest.raises(ConsentNotFound):
        store.get_receipt("tenant-b", created["receipt_id"])

    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER consent_receipts_no_update")
        connection.execute(
            "UPDATE consent_receipts SET policy_sha256 = ? WHERE tenant = ? "
            "AND receipt_id = ?",
            (DIGEST_A, "tenant-a", created["receipt_id"]),
        )
    with pytest.raises(ConsentPreferencesError, match="integrity"):
        store.get_receipt("tenant-a", created["receipt_id"])


def test_retention_due_never_deletes_receipts_but_operational_data_is_bounded(
    store: ConsentPreferenceStore,
    clock: MutableClock,
) -> None:
    _policy(store)
    _profile(store)
    _record(store, retention_until=NOW + 10)
    store.export_profile(
        "tenant-a",
        "profile-001",
        actor_sha256=DIGEST_D,
        access_purpose="support-review",
    )
    store.register_tracker(
        "tenant-a",
        "tracker-a",
        "web_tracker",
        "vendor-a",
        ["analytics"],
        "approved",
        expected_revision=0,
        idempotency_key="tracker-a",
    )
    store.admit_scan_observations(
        "tenant-a",
        "scan-a",
        [TrackerObservation("asset-a", "tracker-a", "slot-a", DIGEST_B)],
        NOW,
        DIGEST_C,
        "scan-a",
    )
    clock.value = NOW + 20
    due = store.retention_due("tenant-a", before=clock.value)
    assert len(due["receipts"]) == 1
    assert due["receipts_deleted"] is False
    purged = store.purge_operational_data("tenant-a", before=clock.value)
    assert purged == {
        "scan_observations_deleted": 1,
        "access_events_deleted": 1,
        "receipts_deleted": 0,
    }
    assert store.summary("tenant-a")["receipts"] == 1


def test_bounded_reads_report_truncation_and_reject_unbounded_limits(
    store: ConsentPreferenceStore,
) -> None:
    _profile(store, profile_id="profile-1", subject_sha256=DIGEST_A, idem="profile-1")
    _profile(store, profile_id="profile-2", subject_sha256=DIGEST_B, idem="profile-2")
    page = store.list_profiles("tenant-a", limit=1)
    assert len(page["profiles"]) == 1
    assert page["truncated"] is True
    with pytest.raises(ConsentValidationError):
        store.list_profiles("tenant-a", limit=501)


def test_missing_and_non_consent_authority_fail_closed(
    store: ConsentPreferenceStore,
) -> None:
    assert store.decide("tenant-a", "missing", "analytics").allowed is False
    _policy(store, purposes={"fraud": "legal_obligation"})
    _profile(store)
    decision = store.decide(
        "tenant-a", "profile-001", "fraud", policy_id="privacy-notice"
    )
    assert decision.allowed is False
    assert decision.reason == "external_lawful_basis_required"
    with pytest.raises(ConsentValidationError):
        _record(store, purpose="fraud")


def test_corrupt_authority_fails_closed_instead_of_raising(
    store: ConsentPreferenceStore,
) -> None:
    _policy(store)
    _profile(store)
    with sqlite3.connect(store.path) as conn:
        conn.execute("DROP TABLE consent_profiles")
    decision = store.decide(
        "tenant-a", "profile-001", "analytics", policy_id="privacy-notice"
    )
    assert decision.allowed is False
    assert decision.reason == "authority_unavailable"


def test_identity_link_is_tenant_scoped_and_anonymous_profile_has_one_authority(
    store: ConsentPreferenceStore,
) -> None:
    _profile(store, profile_id="known-1", subject_sha256=DIGEST_A, idem="known-1")
    _profile(store, profile_id="known-2", subject_sha256=DIGEST_B, idem="known-2")
    _profile(
        store,
        profile_id="anon-1",
        identity_kind="anonymous",
        subject_sha256=DIGEST_C,
        idem="anon-1",
    )
    store.link_profiles(
        "tenant-a",
        "known-1",
        "anon-1",
        expected_known_revision=0,
        expected_anonymous_revision=0,
        idempotency_key="link-1",
    )
    with pytest.raises(ConsentConflict):
        store.link_profiles(
            "tenant-a",
            "known-2",
            "anon-1",
            expected_known_revision=0,
            expected_anonymous_revision=1,
            idempotency_key="link-2",
        )
    with pytest.raises(ConsentNotFound):
        store.link_profiles(
            "tenant-b",
            "known-1",
            "anon-1",
            expected_known_revision=0,
            expected_anonymous_revision=0,
            idempotency_key="link-1",
        )
