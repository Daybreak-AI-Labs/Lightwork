"""Companion authority regression cases. No model or connector calls are used."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from maverick import crypto_at_rest, employee_companion, file_lock
from maverick.employee_companion import (
    CompanionConflictError,
    CompanionError,
    CompanionPolicyDenied,
    CompanionValidationError,
    EmployeeCompanionStore,
)
from maverick.paths import tenant_scope

ALICE = "user:alice"
BOB = "user:bob"
ADMIN = "user:admin"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPT_PER_TENANT", "0")
    # A synthetic test-only at-rest key; no deployment credentials are read.
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "27" * 32)
    monkeypatch.setattr(crypto_at_rest, "_KEY_PATH", tmp_path / "unused.key")
    with tenant_scope(tenant="companion-acme"):
        yield EmployeeCompanionStore()


def _enable(store, owner=ALICE, **overrides):
    values = {
        "actor": ADMIN, "expected_revision": store.policy(owner)["revision"],
        "enabled": True, "allowed_sources": ["own_tasks"],
        "allowed_tools": ["task_status"], "retention_days": 1,
    }
    values.update(overrides)
    return store.update_policy(owner, **values)


def _reply(store, owner=ALICE, **overrides):
    values = {
        "message": "Private employee question", "response": "You have one active task.",
        "source_refs": ["task:17"], "expected_revision": store.policy(owner)["revision"],
    }
    values.update(overrides)
    return store.record_exchange(owner, **values)


def _state(store, owner=ALICE):
    return json.loads(file_lock.atomic_read_text(store._path(owner)))


def _write_state(store, state, owner=ALICE):
    file_lock.atomic_write_text(store._path(owner), json.dumps(state))


def test_default_policy_is_disabled_private_read_only_and_has_no_saved_history(store):
    policy = store.policy(ALICE)
    assert policy["revision"] == 0
    assert policy["enabled"] is False
    assert policy["allowed_sources"] == policy["allowed_tools"] == []
    assert policy["retention_days"] == 0
    assert policy["memory_enabled"] is policy["admin_content_access"] is False
    assert policy["action_mode"] == "read_only"
    assert policy["approvals"] == "writes_blocked"
    assert store.history(ALICE) == store.policies() == store.events() == []
    with pytest.raises(CompanionPolicyDenied):
        _reply(store)


def test_employee_records_and_encrypted_histories_are_separate(store):
    _enable(store)
    _enable(store, BOB)
    alice_reply = _reply(store, message="Alice private planning")
    bob_reply = _reply(store, BOB, message="Bob private planning")
    assert store.history(ALICE) == alice_reply["history"]
    assert store.history(BOB) == bob_reply["history"]
    assert store._path(ALICE) != store._path(BOB)
    for owner, secret in [(ALICE, "Alice private planning"), (BOB, "Bob private planning")]:
        raw = file_lock.atomic_read_text(store._path(owner))
        assert secret not in raw
        assert "You have one active task." not in raw
        assert "task:17" not in raw
        assert owner not in store._path(owner).name


def test_reused_store_refuses_changed_tenant_and_new_tenant_has_no_access(store):
    _enable(store)
    _reply(store)
    with tenant_scope(tenant="companion-beta"):
        other = EmployeeCompanionStore()
        assert other.policy(ALICE)["enabled"] is False
        assert other.history(ALICE) == []
        assert other.policies() == other.events() == []
        operations = [
            lambda: store.policy(ALICE), store.policies, store.events,
            lambda: store.history(ALICE), lambda: _enable(store),
            lambda: _reply(store), lambda: store.clear_history(ALICE, actor=ALICE),
        ]
        for operation in operations:
            with pytest.raises(CompanionError, match="tenant context changed"):
                operation()
    assert len(store.history(ALICE)) == 1


def test_named_tenant_is_required(monkeypatch):
    monkeypatch.setattr(employee_companion, "current_tenant_id_strict", lambda: None)
    with pytest.raises(CompanionError, match="named workspace"):
        EmployeeCompanionStore()


def test_reused_store_refuses_changed_storage_root(store, tmp_path, monkeypatch):
    _enable(store)
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "replacement"))
    with pytest.raises(CompanionError, match="storage root changed"):
        store.history(ALICE)


def test_policy_and_history_files_use_existing_private_permissions(store):
    _enable(store)
    _reply(store)
    assert file_lock.private_path_is_restricted(store.root, 0o700)
    assert file_lock.private_path_is_restricted(store._path(ALICE), 0o600)


@pytest.mark.parametrize("replicas", ["2", "0", "", "garbage"])
def test_multi_replica_or_invalid_topology_is_refused_at_every_operation(store, monkeypatch, replicas):
    _enable(store)
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", replicas)
    with pytest.raises(CompanionError, match="one control-plane replica"):
        store.policy(ALICE)
    with pytest.raises(CompanionError, match="one control-plane replica"):
        EmployeeCompanionStore()


def test_copied_ciphertext_cannot_open_for_another_employee_or_tenant(store):
    _enable(store)
    _enable(store, BOB)
    _reply(store)
    ciphertext = _state(store)["history"]
    bob = _state(store, BOB)
    bob["history"] = ciphertext
    _write_state(store, bob, BOB)
    with pytest.raises(CompanionError):
        store.history(BOB)
    with tenant_scope(tenant="companion-beta"):
        other = EmployeeCompanionStore()
        _enable(other)
        state = _state(other)
        state["history"] = ciphertext
        _write_state(other, state)
        with pytest.raises(CompanionError):
            other.history(ALICE)


def test_policy_compare_and_swap_and_reply_revision_fence(store):
    first = _enable(store)
    second = _enable(store, enabled=False)
    before = file_lock.atomic_read_text(store._path(ALICE))
    with pytest.raises(CompanionConflictError):
        _enable(store, expected_revision=first["revision"])
    with pytest.raises(CompanionConflictError):
        _reply(store, expected_revision=first["revision"])
    with pytest.raises(CompanionPolicyDenied):
        _reply(store, expected_revision=second["revision"])
    assert file_lock.atomic_read_text(store._path(ALICE)) == before


def test_independent_store_writers_cannot_both_win_same_policy_revision(store):
    policy = _enable(store)
    barrier = Barrier(2)

    def update(retention_days):
        with tenant_scope(tenant="companion-acme"):
            independent = EmployeeCompanionStore()
            barrier.wait(timeout=30)
            try:
                return _enable(independent, expected_revision=policy["revision"],
                               retention_days=retention_days)
            except CompanionConflictError:
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, [7, 30]))
    assert sum(result is not None for result in results) == 1
    assert store.policy(ALICE)["revision"] == policy["revision"] + 1
    assert len(store.events()) == 2


@pytest.mark.parametrize("override", [
    {"allowed_sources": ["all_company_documents"]}, {"allowed_tools": ["send_email"]},
    {"allowed_sources": ["own_tasks", "own_tasks"]}, {"allowed_tools": "task_status"},
    {"retention_days": 365}, {"retention_days": True}, {"enabled": "true"},
    {"expected_revision": True},
])
def test_unsupported_or_ambiguous_policy_values_are_rejected(store, override):
    with pytest.raises(CompanionValidationError):
        _enable(store, **override)
    assert store.policies() == []


@pytest.mark.parametrize("field,value", [
    ("memory_enabled", True), ("admin_content_access", True),
    ("action_mode", "write"), ("approvals", "skip"),
])
def test_capability_expanding_fields_cannot_be_passed_to_policy_update(store, field, value):
    with pytest.raises(TypeError):
        _enable(store, **{field: value})
    assert store.policies() == []


def test_task_source_references_require_both_policy_grants(store):
    _enable(store, allowed_tools=[])
    with pytest.raises(CompanionPolicyDenied):
        _reply(store)
    with pytest.raises(CompanionPolicyDenied):
        _reply(store, source_refs=[])
    _enable(store, allowed_sources=[])
    with pytest.raises(CompanionPolicyDenied):
        _reply(store)


def test_zero_retention_saves_only_event_metadata_without_encryption_call(store, monkeypatch):
    _enable(store, retention_days=0)

    def refuse_seal(*args, **kwargs):
        raise AssertionError("zero retention must not seal or persist conversations")

    monkeypatch.setattr(crypto_at_rest, "seal_bound", refuse_seal)
    reply = _reply(store)
    assert reply["history"] == []
    assert reply["trace"]["reason"] == "owner_and_policy"
    assert _state(store)["history"] is None
    assert "Private employee question" not in file_lock.atomic_read_text(store._path(ALICE))


def test_admin_policy_and_events_never_decrypt_or_return_private_content(store, monkeypatch):
    _enable(store)
    _reply(store, message="Sensitive personnel matter")

    def refuse_open(*args, **kwargs):
        raise AssertionError("administrative metadata must not decrypt history")

    monkeypatch.setattr(crypto_at_rest, "unseal_bound", refuse_open)
    metadata = {"policies": store.policies(), "events": store.events(), "policy": store.policy(ALICE)}
    rendered = json.dumps(metadata)
    assert "Sensitive personnel matter" not in rendered
    assert "You have one active task." not in rendered
    assert "task:17" not in rendered
    assert "history" not in metadata["policies"][0]
    assert {event["action"] for event in metadata["events"]} == {"policy_updated", "reply"}


@pytest.mark.parametrize("operation", ["policy", "reply", "delete"])
def test_required_audit_and_mutation_fail_together_on_publication_failure(store, monkeypatch, operation):
    _enable(store)
    _reply(store)
    before = file_lock.atomic_read_text(store._path(ALICE))

    def failed_write(*args, **kwargs):
        raise OSError("simulated durable write failure")

    monkeypatch.setattr(file_lock, "atomic_write_text", failed_write)
    with pytest.raises(CompanionError, match="storage is unavailable"):
        if operation == "policy":
            _enable(store, retention_days=0)
        elif operation == "reply":
            _reply(store, message="This must not be saved")
        else:
            store.clear_history(ALICE, actor=ALICE)
    assert file_lock.atomic_read_text(store._path(ALICE)) == before


def test_encryption_failure_never_falls_back_to_plaintext_or_records_success(store, monkeypatch):
    _enable(store)
    before = file_lock.atomic_read_text(store._path(ALICE))

    def failed_seal(*args, **kwargs):
        raise crypto_at_rest.EncryptionUnavailable("simulated unavailable encryption")

    monkeypatch.setattr(crypto_at_rest, "seal_bound", failed_seal)
    with pytest.raises(CompanionError):
        _reply(store)
    assert file_lock.atomic_read_text(store._path(ALICE)) == before


def test_history_expiry_removes_ciphertext_and_keeps_only_metadata(store, monkeypatch):
    clock = [1000000.0]
    monkeypatch.setattr(employee_companion.time, "time", lambda: clock[0])
    _enable(store)
    _reply(store)
    clock[0] += 86400
    assert store.history(ALICE) == []
    assert _state(store)["history"] is None
    assert store.events()[-1]["action"] == "history_expired"


def test_shorter_retention_cannot_be_undone_by_later_retention_increase(store, monkeypatch):
    clock = [1000000.0]
    monkeypatch.setattr(employee_companion.time, "time", lambda: clock[0])
    _enable(store, retention_days=30)
    _reply(store)
    _enable(store, retention_days=1)
    assert store.history(ALICE)[0]["expires_at"] == clock[0] + 86400
    _enable(store, retention_days=30)
    clock[0] += 86400
    assert store.history(ALICE) == []


def test_retention_increase_does_not_resurrect_expired_content(store, monkeypatch):
    clock = [1000000.0]
    monkeypatch.setattr(employee_companion.time, "time", lambda: clock[0])
    _enable(store, retention_days=1)
    _reply(store)
    clock[0] += 86400
    _enable(store, retention_days=30)
    assert store.history(ALICE) == []


@pytest.mark.parametrize("override", [{"enabled": False}, {"retention_days": 0}])
def test_disabling_retention_or_companion_erases_saved_history(store, override):
    _enable(store)
    _reply(store)
    _enable(store, **override)
    assert store.history(ALICE) == []
    assert _state(store)["history"] is None


def test_clear_history_is_owner_only_and_does_not_change_another_employee(store, monkeypatch):
    _enable(store)
    _enable(store, BOB)
    _reply(store)
    _reply(store, BOB)
    bob_before = file_lock.atomic_read_text(store._path(BOB))
    with pytest.raises(CompanionPolicyDenied):
        store.clear_history(ALICE, actor=ADMIN)

    def unavailable_key(*args, **kwargs):
        raise crypto_at_rest.EncryptionUnavailable("key unavailable")

    monkeypatch.setattr(crypto_at_rest, "unseal_bound", unavailable_key)
    event = store.clear_history(ALICE, actor=ALICE)
    assert event["action"] == "history_cleared"
    assert store.history(ALICE) == []
    assert _state(store)["history"] is None
    assert file_lock.atomic_read_text(store._path(BOB)) == bob_before


@pytest.mark.parametrize("corruption", ["policy", "identity", "audit", "plaintext", "missing_audit"])
def test_corrupt_authority_is_refused_without_resetting_policy(store, corruption):
    _enable(store)
    state = _state(store)
    if corruption == "policy":
        state["policy"]["admin_content_access"] = True
    elif corruption == "identity":
        state["tenant"] = "some-other-workspace"
    elif corruption == "audit":
        state["events"][0]["message"] = "private content does not belong here"
    elif corruption == "plaintext":
        state["history"] = "unencrypted conversation"
    else:
        state["events"] = []
    _write_state(store, state)
    before = file_lock.atomic_read_text(store._path(ALICE))
    for operation in [lambda: store.policy(ALICE), store.policies, store.events,
                      lambda: store.history(ALICE), lambda: _enable(store)]:
        with pytest.raises(CompanionError):
            operation()
    assert file_lock.atomic_read_text(store._path(ALICE)) == before


def test_duplicate_json_fields_are_refused(store):
    _enable(store)
    raw = file_lock.atomic_read_text(store._path(ALICE))
    file_lock.atomic_write_text(store._path(ALICE), raw.replace('"version":1', '"version":1,"version":1'))
    with pytest.raises(CompanionError):
        store.policy(ALICE)


def test_history_and_metadata_journals_are_bounded(store, monkeypatch):
    monkeypatch.setattr(employee_companion, "MAX_HISTORY", 2)
    monkeypatch.setattr(employee_companion, "MAX_EVENTS", 3)
    _enable(store)
    for index in range(5):
        _reply(store, message=f"Question {index}")
    assert [entry["message"] for entry in store.history(ALICE)] == ["Question 3", "Question 4"]
    assert len(_state(store)["events"]) == len(store.events()) == 3


def test_employee_capacity_blocks_new_records_but_preserves_existing_ones(store, monkeypatch):
    monkeypatch.setattr(employee_companion, "MAX_EMPLOYEES", 1)
    _enable(store)
    with pytest.raises(CompanionError, match="capacity"):
        _enable(store, BOB)
    assert len(store.policies()) == 1
    assert _reply(store)["trace"]["owner"] == ALICE


@pytest.mark.parametrize("owner", ["", "alice", "service:alice", "user:", "user:\n../bob"])
def test_owner_must_be_an_exact_authenticated_employee_identity(store, owner):
    with pytest.raises(CompanionValidationError):
        store.policy(owner)


@pytest.mark.parametrize("override", [
    {"message": "x" * (employee_companion.MAX_MESSAGE + 1)},
    {"response": "x" * (employee_companion.MAX_RESPONSE + 1)},
    {"source_refs": ["https://private-document.invalid"]},
    {"source_refs": ["task:17", "task:17"]}, {"source_refs": ["task:-1"]},
])
def test_exchange_size_and_source_reference_contract(store, override):
    _enable(store)
    before = file_lock.atomic_read_text(store._path(ALICE))
    with pytest.raises(CompanionValidationError):
        _reply(store, **override)
    assert file_lock.atomic_read_text(store._path(ALICE)) == before
