"""Cross-replica contracts for tenant lifecycle and spend authority."""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone

import pytest
from maverick import authority_state, paths, quotas
from maverick.privacy_ops import RecordConflict
from maverick.tenant import authority, registry


class _MemoryClaims:
    backend_kind = "postgres"

    def __init__(self, clock: list[float]) -> None:
        self.clock = clock
        self.records: dict[str, dict] = {}
        self.lock = threading.Lock()

    def authoritative_time(self) -> float:
        return self.clock[0]

    def get(self, tenant_id: str):
        key = paths._tenant_namespace_key(tenant_id)
        with self.lock:
            value = self.records.get(key)
            return copy.deepcopy(value) if value is not None else None

    def list(self):
        with self.lock:
            return copy.deepcopy(list(self.records.values()))

    def claim(self, tenant_id: str, *, proposed: dict):
        now = self.clock[0]
        key = paths._tenant_namespace_key(tenant_id)
        with self.lock:
            existing = self.records.get(key)
            if existing is not None:
                return copy.deepcopy(existing), False
            value = {
                "id": authority._claim_record_id(key),
                "schema_version": authority.CLAIM_SCHEMA_VERSION,
                "tenant_id": tenant_id,
                "namespace_key": key,
                "claimed_at": now,
                "state": "provisioning",
                "proposed": copy.deepcopy(proposed),
                "activated_at": None,
                "aborted_at": None,
                "deleted_at": None,
                "revision": 1,
            }
            self.records[key] = value
            return copy.deepcopy(value), True

    def activate(self, tenant_id: str, *, expected_revision: int | None = None) -> bool:
        key = paths._tenant_namespace_key(tenant_id)
        with self.lock:
            value = self.records[key]
            if value["state"] == "active":
                return False
            if (
                expected_revision is not None
                and value["revision"] != expected_revision
            ):
                raise authority.TenantAuthorityError("claim generation changed")
            if value["state"] != "provisioning":
                raise authority.TenantAuthorityError("claim cannot activate")
            value["state"] = "active"
            value["activated_at"] = self.clock[0]
            value["revision"] += 1
            return True

    def begin_abort(self, tenant_id: str):
        key = paths._tenant_namespace_key(tenant_id)
        with self.lock:
            value = self.records[key]
            if value["state"] in {"aborting", "aborted"}:
                return copy.deepcopy(value), False
            if value["state"] != "provisioning":
                raise authority.TenantAuthorityError("claim cannot abort")
            value["state"] = "aborting"
            value["revision"] += 1
            return copy.deepcopy(value), True

    def finish_abort(self, tenant_id: str, *, expected_revision: int) -> bool:
        key = paths._tenant_namespace_key(tenant_id)
        with self.lock:
            value = self.records[key]
            if value["state"] == "aborted":
                return False
            if value["revision"] != expected_revision:
                raise authority.TenantAuthorityError("claim generation changed")
            if value["state"] != "aborting":
                raise authority.TenantAuthorityError("claim cannot finish abort")
            value["state"] = "aborted"
            value["aborted_at"] = self.clock[0]
            value["revision"] += 1
            return True

    def mutation_fence(self, tenant_id: str, *, state: str, revision: int):
        key = paths._tenant_namespace_key(tenant_id)

        def _validate(record: dict, _now: float) -> None:
            if (
                record.get("tenant_id") != tenant_id
                or record.get("namespace_key") != key
                or record.get("state") != state
                or record.get("revision") != revision
            ):
                raise authority.TenantAuthorityError(
                    "tenant control mutation lost its namespace generation fence"
                )

        return authority.GovernedMutationFence(
            namespace="tenant_namespace_claims",
            prefix="TNC",
            record_id=authority._claim_record_id(key),
            authority_scope="deployment_global",
            validate=_validate,
        )

    def tombstone(self, tenant_id: str) -> bool:
        key = paths._tenant_namespace_key(tenant_id)
        with self.lock:
            value = self.records.get(key)
            if value is None:
                return False
            if value["tenant_id"] != tenant_id:
                raise authority.TenantAuthorityError("namespace owner changed")
            if value["state"] == "deleted":
                return False
            if value["state"] != "active":
                raise authority.TenantAuthorityError("claim cannot delete")
            value["state"] = "deleted"
            value["deleted_at"] = self.clock[0]
            value["revision"] += 1
            return True


class _MemoryTenantPlane:
    def __init__(self, clock: list[float]) -> None:
        self.clock = clock
        self.documents: dict[str, dict] = {}
        self.locks: dict[str, threading.RLock] = {}
        self.backend = "postgres"
        self.fail_after_commit_action: str | None = None
        self.retry_once_action: str | None = None
        self.retry_advance = 0.0
        self.claims: _MemoryClaims | None = None

    def store(self, tenant_id: str):
        return _MemoryTenantStore(self, tenant_id)

    def lock(self, tenant_id: str) -> threading.RLock:
        return self.locks.setdefault(tenant_id, threading.RLock())


class _MemoryTenantStore:
    def __init__(self, plane: _MemoryTenantPlane, tenant_id: str) -> None:
        self.plane = plane
        self.tenant_id = tenant_id

    @property
    def backend_kind(self) -> str:
        return self.plane.backend

    def _assert_scope(self) -> None:
        assert paths.current_tenant_id_strict() == self.tenant_id

    def authoritative_time(self) -> float:
        self._assert_scope()
        return self.plane.clock[0]

    def read(self):
        self._assert_scope()
        with self.plane.lock(self.tenant_id):
            return copy.deepcopy(self.plane.documents.get(self.tenant_id, {}))

    def mutate_with_authoritative_time(
        self,
        callback,
        *,
        action: str,
        fence=None,
        **_kwargs,
    ):
        self._assert_scope()
        claim_guard = nullcontext()
        if fence is not None:
            assert self.plane.claims is not None
            claim_guard = self.plane.claims.lock
        with claim_guard:
            if fence is not None:
                record = next(
                    (
                        item
                        for item in self.plane.claims.records.values()
                        if item.get("id") == fence.record_id
                    ),
                    None,
                )
                if record is None:
                    raise authority.TenantAuthorityError(
                        "shared mutation fence authority is unavailable"
                    )
                fence.validate(copy.deepcopy(record), self.plane.clock[0])
            return self._mutate_locked(callback, action=action)

    def _mutate_locked(self, callback, *, action: str):
        with self.plane.lock(self.tenant_id):
            current = copy.deepcopy(self.plane.documents.get(self.tenant_id, {}))
            if self.plane.retry_once_action == action:
                # Model a callback whose optimistic write loses. Its decision is
                # discarded; the next attempt runs after authority time moves.
                callback(copy.deepcopy(current), self.plane.clock[0])
                self.plane.retry_once_action = None
                self.plane.clock[0] += self.plane.retry_advance
            candidate = copy.deepcopy(current)
            result = callback(candidate, self.plane.clock[0])
            self.plane.documents[self.tenant_id] = candidate
            if self.plane.fail_after_commit_action == action:
                self.plane.fail_after_commit_action = None
                raise OSError("response lost after durable commit")
            return result


@contextmanager
def _tenant(tenant_id: str):
    token = paths.set_tenant(tenant_id)
    try:
        yield
    finally:
        paths.reset_tenant(token)


@pytest.fixture
def shared(monkeypatch, tmp_path):
    clock = [datetime(2026, 1, 1, 12, tzinfo=timezone.utc).timestamp()]
    claims = _MemoryClaims(clock)
    plane = _MemoryTenantPlane(clock)
    plane.claims = claims
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setattr(registry, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(quotas, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(authority, "_claim_store", lambda: claims)
    monkeypatch.setattr(authority, "_new_tenant_store", plane.store)
    return clock, claims, plane, tmp_path


def test_namespace_claim_is_digest_addressed_and_alias_creation_conflicts(shared):
    _clock, claims, plane, _home = shared
    created = registry.create_tenant("Acme", max_daily_dollars=10)

    with pytest.raises(ValueError, match="aliases existing tenant"):
        registry.create_tenant("acme", max_daily_dollars=10)

    assert created.id == "Acme"
    assert len(claims.records) == 1
    claim = next(iter(claims.records.values()))
    assert len(claim["id"]) == 56
    assert set(plane.documents) == {"Acme"}


def test_empty_shared_roster_denies_arbitrary_named_tenant(shared):
    assert registry.is_active("unprovisioned") is False
    with pytest.raises(registry.TenantSuspended, match="unknown"):
        registry.assert_tenant_active("unprovisioned")


def test_readiness_marks_only_migrated_tenant_families_shared():
    stores = {
        item.name: item
        for item in authority_state.default_registry({
            "governed_records": {"backend": "postgres"},
            "world_model": {
                "backend": "postgres",
                "dsn": "postgresql://db.internal/lightwork",
            },
        }).stores
    }

    for name in (
        "tenant_registry",
        "tenant_policy_overlays",
        "tenant_wrapped_deks",
        "tenant_usage_ledgers",
        "tenant_spend_reservations",
        "oauth_vaults",
        "browser_auth_vaults",
    ):
        assert stores[name].backend == "shared"
        assert stores[name].service == "postgres"


def test_crash_after_claim_isolated_then_resumable_or_abortable(shared):
    clock, claims, plane, _home = shared
    proposal = {"plan": "free", "display_name": "Acme", "max_daily_dollars": 7.0}
    claims.claim("acme", proposed=proposal)

    assert registry.list_tenants() == []
    assert registry.is_active("acme") is False
    with pytest.raises(registry.TenantSuspended):
        registry.assert_tenant_active("acme")
    assert "acme" not in plane.documents

    recovered = authority.resume_provisioning("acme")
    assert recovered["max_daily_dollars"] == 7
    assert registry.is_active("acme") is True

    claims.claim(
        "beta",
        proposed={"plan": "free", "display_name": "", "max_daily_dollars": 0.0},
    )
    assert authority.abort_provisioning("beta") is True
    assert authority.abort_provisioning("beta") is False
    with pytest.raises(ValueError, match="tombstoned"):
        registry.create_tenant("beta")


def test_abort_racing_resume_cannot_leave_aborted_claim_with_active_control(
    shared,
    monkeypatch,
):
    _clock, claims, plane, _home = shared
    claims.claim(
        "race",
        proposed={"plan": "free", "display_name": "Race", "max_daily_dollars": 3.0},
    )

    abort_read_empty = threading.Event()
    allow_abort = threading.Event()
    resume_ready_to_activate = threading.Event()
    allow_activation = threading.Event()
    abort_thread: list[int] = []
    original_read_control = authority._read_control
    original_activate = claims.activate

    def _barrier_read_control(tenant_id: str):
        result = original_read_control(tenant_id)
        if abort_thread and threading.get_ident() == abort_thread[0]:
            assert result[0] is None
            abort_read_empty.set()
            assert allow_abort.wait(5.0)
        return result

    def _barrier_activate(tenant_id: str, *args, **kwargs):
        resume_ready_to_activate.set()
        assert allow_activation.wait(5.0)
        return original_activate(tenant_id, *args, **kwargs)

    monkeypatch.setattr(authority, "_read_control", _barrier_read_control)
    monkeypatch.setattr(claims, "activate", _barrier_activate)

    def _abort():
        abort_thread.append(threading.get_ident())
        return authority.abort_provisioning("race")

    with ThreadPoolExecutor(max_workers=2) as pool:
        abort_future = pool.submit(_abort)
        assert abort_read_empty.wait(5.0)
        resume_future = pool.submit(authority.resume_provisioning, "race")
        assert resume_ready_to_activate.wait(5.0)
        allow_abort.set()
        try:
            assert abort_future.result(timeout=5.0) is True
        finally:
            allow_activation.set()
        with pytest.raises(authority.TenantAuthorityError):
            resume_future.result(timeout=5.0)

    claim = claims.get("race")
    assert claim is not None and claim["state"] == "aborted"
    assert plane.documents["race"]["tenant"] is None


def test_abort_generation_rejects_resume_before_its_control_write(
    shared,
    monkeypatch,
):
    _clock, claims, plane, _home = shared
    claims.claim(
        "stale",
        proposed={"plan": "free", "display_name": "Stale", "max_daily_dollars": 3.0},
    )

    resume_has_fence = threading.Event()
    allow_resume_write = threading.Event()
    original_mutate_control = authority._mutate_control

    def _barrier_mutate_control(tenant_id: str, callback, *, action: str, **kwargs):
        if action == "tenant.create_recovery":
            resume_has_fence.set()
            assert allow_resume_write.wait(5.0)
        return original_mutate_control(
            tenant_id,
            callback,
            action=action,
            **kwargs,
        )

    monkeypatch.setattr(authority, "_mutate_control", _barrier_mutate_control)
    with ThreadPoolExecutor(max_workers=2) as pool:
        resume_future = pool.submit(authority.resume_provisioning, "stale")
        assert resume_has_fence.wait(5.0)
        assert authority.abort_provisioning("stale") is True
        allow_resume_write.set()
        with pytest.raises(
            authority.TenantAuthorityError,
            match="lost its namespace generation fence",
        ):
            resume_future.result(timeout=5.0)

    claim = claims.get("stale")
    assert claim is not None and claim["state"] == "aborted"
    assert plane.documents["stale"]["tenant"] is None


def test_namespace_transition_reclocks_inside_each_cas_attempt():
    tenant_id = "acme"
    namespace_key = paths._tenant_namespace_key(tenant_id)

    class _Records:
        def __init__(self) -> None:
            self.clock = 100.0
            self.conflict = True
            self.clock_reads: list[float] = []
            self.record = {
                "id": authority._claim_record_id(namespace_key),
                "schema_version": authority.CLAIM_SCHEMA_VERSION,
                "tenant_id": tenant_id,
                "namespace_key": namespace_key,
                "claimed_at": 50.0,
                "state": "provisioning",
                "proposed": {
                    "plan": "free",
                    "display_name": "",
                    "max_daily_dollars": 0.0,
                },
                "activated_at": None,
                "aborted_at": None,
                "deleted_at": None,
                "revision": 1,
            }

        def get(self, _record_id):
            return copy.deepcopy(self.record)

        def authoritative_time(self):
            self.clock_reads.append(self.clock)
            return self.clock

        def update(self, _record_id, mutate, **_kwargs):
            candidate = copy.deepcopy(self.record)
            mutate(candidate)
            if self.conflict:
                self.conflict = False
                self.clock = 200.0
                raise RecordConflict("lost")
            candidate["revision"] += 1
            self.record = candidate
            return copy.deepcopy(candidate)

    records = _Records()
    claims = object.__new__(authority.TenantNamespaceClaimStore)
    claims._records = records

    assert claims.activate(tenant_id) is True
    assert records.clock_reads == [100.0, 200.0]
    assert records.record["activated_at"] == 200.0


def test_exact_tenant_scope_restores_ambient_and_never_recurses_admission(
    monkeypatch,
    shared,
):
    _clock, _claims, plane, _home = shared

    def recursive_failure(*_args, **_kwargs):
        raise AssertionError("tenant authority recursed through admission")

    original_assert = registry.assert_tenant_active
    monkeypatch.setattr(registry, "assert_tenant_active", recursive_failure)
    registry.create_tenant("acme", max_daily_dollars=10)
    with _tenant("beta"):
        assert registry.get_tenant("acme").id == "acme"
        assert registry.suspend_tenant("acme").status == registry.SUSPENDED
        assert paths.current_tenant_id_strict() == "beta"
    monkeypatch.setattr(registry, "assert_tenant_active", original_assert)

    assert plane.documents["acme"]["tenant"]["status"] == registry.SUSPENDED
    assert "beta" not in plane.documents


def test_dual_node_concurrent_reservations_cannot_overspend(shared):
    _clock, _claims, _plane, _home = shared
    registry.create_tenant("acme", max_daily_dollars=10)

    def reserve(index: int) -> float | None:
        return registry.reserve_tenant_budget(
            "acme",
            6,
            600,
            f"run-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        grants = list(pool.map(reserve, range(2)))

    assert sorted(grants) == [4.0, 6.0]
    assert registry.tenant_remaining_today("acme") == 0.0


def test_cap_change_and_reservation_share_one_tenant_cas(shared):
    _clock, _claims, _plane, _home = shared
    registry.create_tenant("acme", max_daily_dollars=10)
    assert registry.reserve_tenant_budget("acme", 6, 600, "first") == 6

    registry.set_quota("acme", 5)

    assert registry.reserve_tenant_budget("acme", 1, 600, "second") == 0
    assert registry.tenant_remaining_today("acme") == 0


def test_settlement_records_and_releases_atomically_and_is_idempotent(shared):
    _clock, _claims, plane, _home = shared
    registry.create_tenant("acme", max_daily_dollars=10)
    assert registry.reserve_tenant_budget("acme", 6, 600, "run-1") == 6

    with _tenant("acme"):
        quotas.record_usage("user:alice", 4, 10, 5, reservation_id="run-1")
        quotas.record_usage("user:alice", 4, 10, 5, reservation_id="run-1")
        assert quotas.UsageLedger().usage("user:alice") == {
            "dollars": 4.0,
            "in_tokens": 10,
            "out_tokens": 5,
        }
        with pytest.raises(quotas.UsageLedgerError, match="settlement"):
            quotas.record_usage("user:alice", 5, 10, 5, reservation_id="run-1")

    document = plane.documents["acme"]
    assert document["reservations"] == {}
    assert set(document["settlements"]) == {"run-1"}
    assert registry.tenant_remaining_today("acme") == 6


def test_settlement_overage_is_metered_released_and_audited_in_receipt(shared):
    _clock, _claims, plane, _home = shared
    registry.create_tenant("acme", max_daily_dollars=10)
    registry.reserve_tenant_budget("acme", 4, 600, "overrun")

    with _tenant("acme"):
        quotas.record_usage("user:alice", 6, 0, 0, reservation_id="overrun")

    receipt = plane.documents["acme"]["settlements"]["overrun"]
    assert receipt["dollars"] == 6
    assert receipt["reserved_dollars"] == 4
    assert receipt["overage_dollars"] == 2
    assert receipt["policy_violation"] == "reservation_overage"
    assert plane.documents["acme"]["reservations"] == {}
    assert registry.tenant_remaining_today("acme") == 4


def test_response_loss_after_commit_retries_without_double_charge(shared):
    _clock, _claims, plane, _home = shared
    registry.create_tenant("acme", max_daily_dollars=10)
    registry.reserve_tenant_budget("acme", 6, 600, "crash-run")
    plane.fail_after_commit_action = "tenant_usage.settle"

    with _tenant("acme"):
        with pytest.raises(quotas.UsageLedgerError, match="settlement failed"):
            quotas.record_usage(
                "user:alice", 4, 10, 5, reservation_id="crash-run"
            )
        quotas.record_usage(
            "user:alice", 4, 10, 5, reservation_id="crash-run"
        )
        assert quotas.UsageLedger().usage("user:alice")["dollars"] == 4


def test_lost_cas_rechecks_authority_day_and_expired_holds(shared):
    clock, _claims, plane, _home = shared
    clock[0] = datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc).timestamp()
    registry.create_tenant("acme", max_daily_dollars=10)
    assert registry.reserve_tenant_budget("acme", 8, 300, "old-day") == 8

    plane.retry_once_action = "tenant_budget.reserve"
    plane.retry_advance = 301
    granted = registry.reserve_tenant_budget("acme", 6, 600, "new-day")

    assert granted == 6
    assert plane.documents["acme"]["reservations"]["new-day"]["day"] == "2026-01-02"


def test_node_clock_skew_cannot_select_usage_day(monkeypatch, shared):
    clock, _claims, _plane, _home = shared
    registry.create_tenant("acme", max_daily_dollars=10)
    registry.reserve_tenant_budget("acme", 5, 600, "run-1")
    monkeypatch.setattr(quotas, "_today", lambda: "1999-12-31")

    with _tenant("acme"):
        quotas.record_usage("user:alice", 3, 0, 0, reservation_id="run-1")
        with pytest.raises(quotas.UsageLedgerError, match="caller-selected"):
            quotas.UsageLedger().record(
                "user:alice", 1, 0, 0, day="1999-12-31"
            )

    assert "2026-01-01" in authority.usage_ledger("acme")["user:alice"]
    clock[0] += 24 * 3600
    assert registry.tenant_remaining_today("acme") == 10


def test_shared_outage_and_backend_drift_never_fall_back_to_files(shared):
    _clock, _claims, plane, home = shared
    registry.create_tenant("acme", max_daily_dollars=10)
    plane.backend = "local"

    with pytest.raises(registry.TenantRegistryError, match="requires governed"):
        registry.set_quota("acme", 5)
    assert not (home / "tenant_registry.json").exists()
    assert not (home / "tenants" / "acme" / "usage" / "ledger.json").exists()


def test_legacy_registry_and_usage_files_block_shared_cutover(shared):
    _clock, _claims, _plane, home = shared
    legacy_registry = home / "tenant_registry.json"
    legacy_registry.write_text('{"tenants": []}', encoding="utf-8")
    with pytest.raises(registry.TenantRegistryError, match="migrate/import"):
        registry.create_tenant("acme")

    legacy_registry.unlink()
    registry.create_tenant("acme")
    ledger = home / "tenants" / "acme" / "usage" / "ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{}", encoding="utf-8")
    with _tenant("acme"):
        with pytest.raises(quotas.UsageLedgerError, match="unavailable"):
            quotas.UsageLedger().usage("user:alice")


def test_suspend_delete_tombstone_and_recreate_rules(shared):
    _clock, claims, plane, _home = shared
    registry.create_tenant("acme", max_daily_dollars=10)
    registry.suspend_tenant("acme")
    with pytest.raises(PermissionError, match="suspended"):
        registry.reserve_tenant_budget("acme", 1, 600, "suspended-run")
    registry.resume_tenant("acme")
    registry.reserve_tenant_budget("acme", 2, 600, "final-run")

    with pytest.raises(registry.TenantRegistryError, match="fleet-wide erasure"):
        registry.delete_tenant("acme", purge=True)
    assert registry.get_tenant("acme") is not None
    assert registry.delete_tenant("acme") is True
    assert registry.get_tenant("acme") is None
    assert plane.documents["acme"]["tenant"]["status"] == authority.DELETED
    assert "final-run" in plane.documents["acme"]["reservations"]
    assert next(iter(claims.records.values()))["deleted_at"] is not None
    with pytest.raises(ValueError, match="tombstoned"):
        registry.create_tenant("acme")


def test_settlement_receipts_prune_only_after_authority_retention_window(shared):
    clock, _claims, plane, _home = shared
    registry.create_tenant("acme", max_daily_dollars=10)
    registry.reserve_tenant_budget("acme", 2, 600, "old-run")
    with _tenant("acme"):
        quotas.record_usage("user:alice", 1, 0, 0, reservation_id="old-run")

    clock[0] += (authority.SETTLEMENT_RETENTION_DAYS + 1) * 24 * 3600
    registry.reserve_tenant_budget("acme", 2, 600, "new-run")
    with _tenant("acme"):
        quotas.record_usage("user:alice", 1, 0, 0, reservation_id="new-run")

    assert set(plane.documents["acme"]["settlements"]) == {"new-run"}
    assert sum(
        cell["dollars"]
        for cell in plane.documents["acme"]["usage"]["user:alice"].values()
    ) == 2


def test_late_old_day_settlement_receipt_retains_from_settlement_time(shared):
    clock, _claims, plane, _home = shared
    registry.create_tenant("acme", max_daily_dollars=10)
    registry.reserve_tenant_budget("acme", 2, 300, "late-run")
    clock[0] += 10 * 24 * 3600

    with _tenant("acme"):
        quotas.record_usage("user:alice", 1, 0, 0, reservation_id="late-run")
    # A mutation immediately after late settlement must not prune a receipt
    # merely because its usage/reservation day is old.
    registry.set_plan("acme", "free")

    assert "late-run" in plane.documents["acme"]["settlements"]


def test_expired_reservations_and_usage_have_bounded_authority_retention(shared):
    clock, _claims, plane, _home = shared
    registry.create_tenant("acme", max_daily_dollars=10)
    registry.reserve_tenant_budget("acme", 2, 300, "abandoned")
    registry.reserve_tenant_budget("acme", 2, 300, "recorded")
    with _tenant("acme"):
        quotas.record_usage("user:alice", 1, 0, 0, reservation_id="recorded")

    clock[0] += (authority.EXPIRED_RESERVATION_RETENTION_DAYS + 2) * 24 * 3600
    registry.set_plan("acme", "free")
    assert "abandoned" not in plane.documents["acme"]["reservations"]

    with _tenant("acme"):
        dry = quotas.UsageLedger().prune(keep_days=1, dry_run=True)
        assert dry["removed_buckets"] == 1
        report = quotas.UsageLedger().prune(keep_days=1)
        assert report["removed_buckets"] == 1
        assert quotas.UsageLedger().usage("user:alice")["dollars"] == 0
        with pytest.raises(quotas.UsageLedgerError, match="authoritative time"):
            quotas.UsageLedger().prune(keep_days=1, now=1.0)
