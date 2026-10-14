"""Adversarial coverage for the shared tenant quota/backpressure authority."""
from __future__ import annotations

import inspect
import json
import os
import threading
import uuid

import pytest

_LIVE_PG_DSN = os.environ.get("MAVERICK_TEST_PG_DSN") or os.environ.get(
    "MAVERICK_PG_DSN"
)

LIMITS = {
    "concurrent_runs": 1,
    "queued_jobs": 1,
    "daily_tokens": 100,
    "daily_spend_dollars": 10,
    "tool_calls": 20,
    "storage_bytes": 1000,
}


@pytest.fixture(autouse=True)
def _quota_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_ENFORCE", "1")
    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_POLICY_GENERATION", "1")
    monkeypatch.setenv(
        "MAVERICK_TENANT_QUOTA_LIMITS_JSON",
        json.dumps({"default": LIMITS}),
    )
    monkeypatch.delenv("MAVERICK_WORLD_BACKEND", raising=False)
    monkeypatch.delenv("MAVERICK_PG_DSN", raising=False)


@pytest.fixture
def world(tmp_path):
    from maverick.world_model import WorldModel

    instance = WorldModel(tmp_path / "quota-world.db")
    try:
        yield instance
    finally:
        instance.close()


def _tenant(tenant_id: str):
    from maverick.paths import reset_tenant, set_tenant

    class Scope:
        def __enter__(self):
            self.token = set_tenant(tenant_id)
            return self

        def __exit__(self, *_exc):
            reset_tenant(self.token)

    return Scope()


def test_atomic_concurrent_reservation_has_one_winner(world):
    from maverick import tenant_quota_authority as quota

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def contender(request_id: str) -> None:
        with _tenant("acme"):
            barrier.wait(timeout=5)
            try:
                quota.reserve(
                    "acme",
                    f"user:{request_id}",
                    {"concurrent_runs": 1},
                    request_id,
                    60,
                    world=world,
                    now=1000,
                )
                outcomes.append("admitted")
            except quota.TenantQuotaExceeded:
                outcomes.append("refused")

    threads = [threading.Thread(target=contender, args=(f"r{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(outcomes) == ["admitted", "refused"]


def test_exact_replay_and_settlement_are_idempotent_without_double_charge(world):
    from maverick import tenant_quota_authority as quota

    with _tenant("acme"):
        reservation = quota.reserve(
            "acme",
            "user:alice",
            {
                "concurrent_runs": 1,
                "daily_tokens": 10,
                "daily_spend_dollars": 2,
                "tool_calls": 3,
            },
            "request-1",
            60,
            world=world,
            now=1000,
        )
        replay = quota.reserve(
            "acme",
            "user:alice",
            {
                "concurrent_runs": 1,
                "daily_tokens": 10,
                "daily_spend_dollars": 2,
                "tool_calls": 3,
            },
            "request-1",
            60,
            world=world,
            now=1001,
        )
        assert replay.request_id == reservation.request_id
        assert replay.expires_at == 1061
        actual = {
            "daily_tokens": 7,
            "daily_spend_dollars": 1.25,
            "tool_calls": 2,
        }
        assert quota.settle(reservation, actual, world=world, now=1002)["state"] == "SETTLED"
        assert quota.settle(reservation, actual, world=world, now=1003)["idempotent"] is True
        standing = quota.status("acme", world=world, now=1003)
    assert standing["used"]["daily_tokens"] == 7
    assert standing["used"]["daily_spend_dollars"] == 1.25
    assert standing["used"]["tool_calls"] == 2
    assert standing["reserved"]["concurrent_runs"] == 0


def test_settlement_cannot_introduce_or_exceed_a_reserved_dimension(world):
    from maverick import tenant_quota_authority as quota

    with _tenant("acme"):
        reservation = quota.reserve(
            "acme",
            "user:alice",
            {"daily_tokens": 10},
            "bounded-settlement",
            60,
            world=world,
            now=1000,
        )
        with pytest.raises(quota.TenantQuotaPolicyError, match="daily_spend"):
            quota.settle(
                reservation,
                {"daily_spend_dollars": 0.01},
                world=world,
                now=1001,
            )
        with pytest.raises(quota.TenantQuotaPolicyError, match="daily_tokens"):
            quota.settle(
                reservation,
                {"daily_tokens": 11},
                world=world,
                now=1001,
            )
        assert quota.status("acme", world=world, now=1001)["used"][
            "daily_tokens"
        ] == 0
        quota.settle(
            reservation,
            {"daily_tokens": 10},
            world=world,
            now=1002,
        )


def test_request_identity_cannot_change_principal_or_vector(world):
    from maverick import tenant_quota_authority as quota

    with _tenant("acme"):
        quota.reserve(
            "acme", "user:alice", {"queued_jobs": 1}, "same", 60,
            world=world, now=1000,
        )
        with pytest.raises(quota.TenantQuotaAuthorityUnavailable):
            quota.reserve(
                "acme", "user:bob", {"queued_jobs": 1}, "same", 60,
                world=world, now=1001,
            )
        with pytest.raises(quota.TenantQuotaAuthorityUnavailable):
            quota.reserve(
                "acme", "user:alice", {"queued_jobs": 0}, "same", 60,
                world=world, now=1001,
            )


def test_expiry_reclaims_crashed_hold_and_preserves_lifecycle(world):
    from maverick import tenant_quota_authority as quota

    with _tenant("acme"):
        projected = {
            "concurrent_runs": 1,
            "daily_tokens": 10,
            "daily_spend_dollars": 2,
            "tool_calls": 3,
            "storage_bytes": 4,
        }
        expired = quota.reserve(
            "acme", "user:alice", projected, "crashed", 10,
            world=world, now=1000,
        )
        assert quota.reconcile("acme", world=world, now=1011) == 1
        assert quota.reconcile("acme", world=world, now=1012) == 0
        standing = quota.status("acme", world=world, now=1011)
        assert standing["reserved"]["concurrent_runs"] == 0
        assert standing["used"]["daily_tokens"] == 10
        assert standing["used"]["daily_spend_dollars"] == 2
        assert standing["used"]["tool_calls"] == 3
        assert standing["used"]["storage_bytes"] == 4
        replay = quota.reserve(
            "acme", "user:alice", projected, "crashed", 10,
            world=world, now=1012,
        )
        assert replay.state == "EXPIRED"
        with pytest.raises(quota.TenantQuotaAuthorityUnavailable):
            quota.settle(expired, {}, world=world, now=1012)


def test_active_daily_projection_survives_utc_day_rollover(world):
    from maverick import tenant_quota_authority as quota

    with _tenant("acme"):
        crossing = quota.reserve(
            "acme",
            "user:alice",
            {"daily_tokens": 100},
            "cross-midnight",
            60,
            world=world,
            now=86390,
        )
        after_midnight = quota.status("acme", world=world, now=86401)
        assert after_midnight["used"]["daily_tokens"] == 0
        assert after_midnight["reserved"]["daily_tokens"] == 100
        with pytest.raises(quota.TenantQuotaExceeded):
            quota.reserve(
                "acme",
                "user:bob",
                {"daily_tokens": 1},
                "new-day-run",
                60,
                world=world,
                now=86401,
            )
        quota.settle(
            crossing,
            {"daily_tokens": 10},
            world=world,
            now=86402,
        )
        settled = quota.status("acme", world=world, now=86402)
    assert settled["used"]["daily_tokens"] == 10
    assert settled["reserved"]["daily_tokens"] == 0


def test_noisy_neighbor_and_mutable_policy_are_tenant_isolated(world, monkeypatch):
    from maverick import tenant_quota_authority as quota

    with _tenant("acme"):
        first = quota.reserve(
            "acme", "user:alice", {"queued_jobs": 1}, "acme-1", 60,
            world=world, now=1000,
        )
    with _tenant("beta"):
        beta = quota.reserve(
            "beta", "user:bob", {"queued_jobs": 1}, "beta-1", 60,
            world=world, now=1000,
        )
        assert beta.active
    lowered = dict(LIMITS)
    lowered["queued_jobs"] = 0
    monkeypatch.setenv(
        "MAVERICK_TENANT_QUOTA_LIMITS_JSON",
        json.dumps({"default": LIMITS, "tenants": {"acme": lowered}}),
    )
    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_POLICY_GENERATION", "2")
    with _tenant("acme"):
        replay = quota.reserve(
            "acme", "user:alice", {"queued_jobs": 1}, "acme-1", 60,
            world=world, now=1001,
        )
        assert replay.request_id == first.request_id
        with pytest.raises(quota.TenantQuotaExceeded):
            quota.reserve(
                "acme", "user:carol", {"queued_jobs": 1}, "acme-2", 60,
                world=world, now=1001,
            )


def test_shared_policy_generation_fences_replica_drift(world, monkeypatch):
    from maverick import tenant_quota_authority as quota

    with _tenant("acme"):
        quota.reserve(
            "acme",
            "user:alice",
            {"queued_jobs": 1},
            "generation-one",
            60,
            world=world,
            now=1000,
        )
        changed = dict(LIMITS)
        changed["queued_jobs"] = 2
        monkeypatch.setenv(
            "MAVERICK_TENANT_QUOTA_LIMITS_JSON",
            json.dumps({"default": changed}),
        )
        with pytest.raises(quota.TenantQuotaPolicyError, match="conflicts"):
            quota.status("acme", world=world, now=1001)

        monkeypatch.setenv("MAVERICK_TENANT_QUOTA_POLICY_GENERATION", "2")
        advanced = quota.status("acme", world=world, now=1002)
        assert advanced["authority_policy_generation"] == 2
        assert advanced["authority_policy_sha256"] == advanced["policy_sha256"]
        assert advanced["limits"]["queued_jobs"] == 2

        monkeypatch.setenv("MAVERICK_TENANT_QUOTA_POLICY_GENERATION", "1")
        with pytest.raises(quota.TenantQuotaPolicyError, match="stale"):
            quota.status("acme", world=world, now=1003)


def test_shared_policy_requires_explicit_generation(monkeypatch):
    from maverick import tenant_quota_authority as quota

    monkeypatch.delenv("MAVERICK_TENANT_QUOTA_POLICY_GENERATION", raising=False)
    monkeypatch.setattr(quota, "_postgres_configured", lambda: True)
    with pytest.raises(quota.TenantQuotaPolicyError, match="generation"):
        quota.policy_for_tenant("acme")


def test_shared_authority_requires_the_complete_six_resource_vector(
    world, monkeypatch,
):
    from maverick import tenant_quota_authority as quota

    monkeypatch.setattr(quota, "_postgres_configured", lambda: True)
    monkeypatch.setenv(
        "MAVERICK_TENANT_QUOTA_LIMITS_JSON",
        json.dumps({"default": {"queued_jobs": 1}}),
    )
    with _tenant("acme"):
        with pytest.raises(quota.TenantQuotaPolicyError, match="ceiling unavailable"):
            quota.reserve(
                "acme",
                "user:alice",
                {"queued_jobs": 1},
                "incomplete-shared-vector",
                60,
                world=world,
            )


@pytest.mark.parametrize(
    "value",
    [2**63, 2**63 + 1],
)
def test_integer_resources_reject_values_outside_durable_range(value):
    from maverick import tenant_quota_authority as quota

    with pytest.raises(quota.TenantQuotaPolicyError, match="durable integer range"):
        quota.normalize_vector({"daily_tokens": value})


def test_large_integer_resource_is_not_rounded_through_float():
    from maverick import tenant_quota_authority as quota

    exact = 2**53 + 1
    assert quota.normalize_vector({"storage_bytes": exact})["storage_bytes"] == exact
    with pytest.raises(quota.TenantQuotaPolicyError, match="exact integer literal"):
        quota.normalize_vector({"storage_bytes": float(2**53 + 2)})
    with pytest.raises(quota.TenantQuotaPolicyError, match="finite"):
        quota.normalize_vector({"daily_spend_dollars": 10**10_000})


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ('{"default":{},"default":{}}', "duplicate key"),
        ('{"default":{},"surprise":1}', "unknown fields"),
        ("[]", "must be an object"),
        ('{"default":{},"tenants":[]}', "tenants must be an object"),
        ('{"default":{"daily_tokens":NaN}}', "non-finite number"),
    ],
)
def test_policy_json_fails_closed_on_ambiguous_shapes(
    monkeypatch, document, message,
):
    from maverick import tenant_quota_authority as quota

    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_LIMITS_JSON", document)
    with pytest.raises(quota.TenantQuotaPolicyError, match=message):
        quota.policy_for_tenant("acme")


def test_quota_identity_and_vector_keys_are_exact_wire_strings(world):
    from maverick import tenant_quota_authority as quota

    with pytest.raises(quota.TenantQuotaPolicyError, match="keys must be strings"):
        quota.normalize_vector({1: 1})
    with pytest.raises(quota.TenantQuotaAuthorityUnavailable, match="must be a string"):
        quota.reserve(
            b"acme",
            "user:alice",
            {"queued_jobs": 1},
            "typed-identity",
            60,
            world=world,
        )


@pytest.mark.parametrize("generation", ["01", "+1", "1.0", "-1", str(2**63)])
def test_policy_generation_requires_canonical_durable_integer(
    monkeypatch, generation,
):
    from maverick import tenant_quota_authority as quota

    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_POLICY_GENERATION", generation)
    with pytest.raises(quota.TenantQuotaPolicyError, match="generation"):
        quota.policy_for_tenant("acme")


def test_missing_dimension_and_authority_outage_fail_closed(world, monkeypatch):
    from maverick import tenant_quota_authority as quota

    monkeypatch.setenv(
        "MAVERICK_TENANT_QUOTA_LIMITS_JSON",
        json.dumps({"default": {"concurrent_runs": 1}}),
    )
    with _tenant("acme"):
        with pytest.raises(quota.TenantQuotaPolicyError, match="daily_tokens"):
            quota.reserve(
                "acme", "user:alice", {"daily_tokens": 1}, "missing", 60,
                world=world, now=1000,
            )

        class BrokenWorld:
            def reserve_tenant_quota(self, *_args, **_kwargs):
                raise ConnectionError("secret host detail")

        with pytest.raises(quota.TenantQuotaAuthorityUnavailable):
            quota.reserve(
                "acme", "user:alice", {"concurrent_runs": 1}, "outage", 60,
                world=BrokenWorld(), now=1000,
            )


def test_production_postgres_authority_cannot_be_disabled(monkeypatch):
    from maverick import tenant_quota_authority as quota

    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_ENFORCE", "0")
    monkeypatch.setattr(quota, "_production_required", lambda: True)
    monkeypatch.setattr(quota, "_postgres_configured", lambda: True)

    class OfflinePostgres:
        def reserve_tenant_quota(self, *_args, **_kwargs):
            raise ConnectionError("internal database endpoint")

    assert quota.authority_required() is True
    with _tenant("acme"):
        with pytest.raises(quota.TenantQuotaAuthorityUnavailable) as refused:
            quota.reserve(
                "acme",
                "user:alice",
                {"concurrent_runs": 1},
                "production-outage",
                60,
                world=OfflinePostgres(),
            )
    assert "endpoint" not in str(refused.value)


def test_storage_reservation_settles_cumulatively_and_blocks_overage(world):
    from maverick import tenant_quota_authority as quota

    with _tenant("acme"):
        first = quota.reserve(
            "acme",
            "user:alice",
            {"storage_bytes": 600},
            "storage-one",
            60,
            world=world,
            now=1000,
        )
        quota.settle(
            first,
            {"storage_bytes": 600},
            world=world,
            now=1001,
        )
        with pytest.raises(quota.TenantQuotaExceeded):
            quota.reserve(
                "acme",
                "user:alice",
                {"storage_bytes": 401},
                "storage-two",
                60,
                world=world,
                now=1002,
            )
        second = quota.reserve(
            "acme",
            "user:alice",
            {"storage_bytes": 400},
            "storage-three",
            60,
            world=world,
            now=1002,
        )
        quota.settle(
            second,
            {"storage_bytes": 400},
            world=world,
            now=1003,
        )
        standing = quota.status("acme", world=world, now=1003)
    assert standing["used"]["storage_bytes"] == 1000


def test_job_queue_applies_and_releases_tenant_backpressure(tmp_path):
    from maverick.job_queue import JobQueue

    queue = JobQueue(tmp_path / "jobs.db")
    with _tenant("acme"):
        first = queue.enqueue(
            "run_goal", {"tenant": "acme", "principal_id": "user:alice"}
        )
        with pytest.raises(Exception, match="quota exceeded"):
            queue.enqueue(
                "run_goal", {"tenant": "acme", "principal_id": "user:bob"}
            )
        assert queue.cancel(first) is True
        assert queue.enqueue(
            "run_goal", {"tenant": "acme", "principal_id": "user:bob"}
        ) > first


def test_job_queue_dedup_replay_owns_one_hold_and_terminal_releases_it(tmp_path):
    from maverick import tenant_quota_authority as quota
    from maverick.job_queue import JobQueue

    queue = JobQueue(tmp_path / "jobs.db")
    payload = {"tenant": "acme", "principal_id": "user:alice"}
    with _tenant("acme"):
        first = queue.enqueue("run_goal", payload, dedup_key="same-delivery")
        assert queue.enqueue(
            "run_goal", payload, dedup_key="same-delivery"
        ) == first
        assert quota.status("acme")["reserved"]["queued_jobs"] == 1
        claimed = queue.claim()
        assert claimed is not None
        assert queue.complete(
            claimed.id,
            expected_attempts=claimed.attempts,
        ) is True
        assert quota.status("acme")["reserved"]["queued_jobs"] == 0


def test_job_queue_claim_outage_leaves_durable_job_pending(tmp_path, monkeypatch):
    from maverick import tenant_quota_authority as quota
    from maverick.job_queue import JobQueue

    queue = JobQueue(tmp_path / "jobs.db")
    with _tenant("acme"):
        job_id = queue.enqueue(
            "run_goal",
            {"tenant": "acme", "principal_id": "user:alice"},
        )

        def unavailable(*_args, **_kwargs):
            raise quota.TenantQuotaAuthorityUnavailable("authority unavailable")

        monkeypatch.setattr(quota, "reserve", unavailable)
        with pytest.raises(quota.TenantQuotaAuthorityUnavailable):
            queue.claim()
        job = queue.get(job_id)
    assert job is not None
    assert job.status == "pending"
    assert job.attempts == 0


def test_queue_hold_renews_after_generation_boundary(tmp_path, monkeypatch):
    from maverick import job_queue as queue_module
    from maverick import tenant_quota_authority as quota
    from maverick.job_queue import JobQueue

    clock = [1000.0]
    monkeypatch.setattr(queue_module.time, "time", lambda: clock[0])
    queue = JobQueue(tmp_path / "jobs.db")
    with _tenant("acme"):
        job_id = queue.enqueue(
            "run_goal",
            {"tenant": "acme", "principal_id": "user:alice"},
        )
        original = queue.get(job_id)
        assert original is not None and original.quota_request_id
        assert quota.status("acme", now=clock[0])["reserved"]["queued_jobs"] == 1
        clock[0] = original.quota_expires_at + 1
        claimed = queue.claim(now=clock[0], ready_at=clock[0])
        assert claimed is not None and claimed.id == job_id
        assert claimed.quota_request_id != original.quota_request_id
        standing = quota.status("acme", now=clock[0])
        assert standing["reserved"]["queued_jobs"] == 1
        assert standing["active_reservations"] == 1
        assert queue.complete(
            claimed.id,
            expected_attempts=claimed.attempts,
        ) is True
        assert quota.status("acme", now=clock[0])["reserved"]["queued_jobs"] == 0


def test_queue_claim_skips_cap_blocked_tenant_without_head_of_line_stall(
    tmp_path, monkeypatch,
):
    from maverick.job_queue import JobQueue

    queue = JobQueue(tmp_path / "jobs.db")
    with _tenant("acme"):
        running_id = queue.enqueue(
            "run_goal", {"tenant": "acme", "principal_id": "user:alice"}
        )
    running = queue.claim()
    assert running is not None and running.id == running_id

    # Simulate a legacy/recovered row that exists without an admitted hold.
    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_ENFORCE", "0")
    with _tenant("acme"):
        blocked_id = queue.enqueue(
            "run_goal", {"tenant": "acme", "principal_id": "user:bob"}
        )
    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_ENFORCE", "1")
    with _tenant("beta"):
        beta_id = queue.enqueue(
            "run_goal", {"tenant": "beta", "principal_id": "user:beta"}
        )

    claimed = queue.claim()
    assert claimed is not None and claimed.id == beta_id
    assert queue.get(blocked_id).status == "pending"
    assert queue.complete(claimed.id, expected_attempts=claimed.attempts) is True
    assert queue.complete(running.id, expected_attempts=running.attempts) is True


def test_terminal_job_cannot_be_resurrected_after_quota_release(tmp_path):
    from maverick import tenant_quota_authority as quota
    from maverick.job_queue import JobQueue

    queue = JobQueue(tmp_path / "jobs.db")
    with _tenant("acme"):
        job_id = queue.enqueue(
            "run_goal", {"tenant": "acme", "principal_id": "user:alice"}
        )
        claimed = queue.claim()
        assert claimed is not None and claimed.id == job_id
        assert queue.complete(
            job_id, expected_attempts=claimed.attempts
        ) is True
        assert queue.fail(
            job_id,
            "stale worker",
            retry_after=0,
            expected_attempts=claimed.attempts,
        ) is False
        assert queue.get(job_id).status == "done"
        assert quota.status("acme")["reserved"]["queued_jobs"] == 0


def test_orchestrator_admission_settles_actual_run_usage_once(world):
    from maverick import tenant_quota_authority as quota
    from maverick.budget import Budget
    from maverick.orchestrator import _QuotaUsageSettlement

    budget = Budget(
        max_input_tokens=60,
        max_output_tokens=40,
        max_dollars=5,
        max_tool_calls=10,
        max_wall_seconds=60,
    )
    budget.input_tokens = 11
    budget.output_tokens = 7
    budget.cache_read_tokens = 3
    budget.cache_write_tokens = 2
    budget.dollars = 1.5
    budget.tool_calls = 4
    with _tenant("acme"):
        settlement = _QuotaUsageSettlement(
            principal="user:alice",
            budget=budget,
            domain=None,
            world=world,
            goal_id=7,
        )
        settlement.admit()
        standing = quota.status("acme", world=world)
        assert standing["reserved"]["concurrent_runs"] == 1
        assert standing["reserved"]["daily_tokens"] == 100
        settlement.record()
        settlement.record()
        standing = quota.status("acme", world=world)
    assert standing["reserved"]["concurrent_runs"] == 0
    # daily_tokens is the hard Budget-backed uncached input + output vector;
    # cache buckets remain cost-accounted but do not have a predictable token
    # maximum that can be reserved before dispatch.
    assert standing["used"]["daily_tokens"] == 18
    assert standing["used"]["daily_spend_dollars"] == 1.5
    assert standing["used"]["tool_calls"] == 4


def test_orchestrator_clamps_run_budget_to_shared_daily_remainder(world):
    from maverick import tenant_quota_authority as quota
    from maverick.budget import Budget
    from maverick.orchestrator import _QuotaUsageSettlement

    with _tenant("acme"):
        prior = quota.reserve(
            "acme",
            "user:prior",
            {"daily_tokens": 100, "daily_spend_dollars": 10, "tool_calls": 20},
            "prior-run",
            60,
            world=world,
        )
        quota.settle(
            prior,
            {"daily_tokens": 23, "daily_spend_dollars": 7, "tool_calls": 16},
            world=world,
        )
        budget = Budget(
            max_input_tokens=60,
            max_output_tokens=40,
            max_dollars=5,
            max_tool_calls=10,
            max_wall_seconds=60,
        )
        settlement = _QuotaUsageSettlement(
            principal="user:alice",
            budget=budget,
            domain=None,
            world=world,
            goal_id=8,
        )
        settlement.admit()
        standing = quota.status("acme", world=world)
        assert budget.max_input_tokens + budget.max_output_tokens == 77
        assert budget.max_dollars == 3
        assert budget.max_tool_calls == 4
        assert standing["reserved"]["daily_tokens"] == 77
        assert standing["reserved"]["daily_spend_dollars"] == 3
        assert standing["reserved"]["tool_calls"] == 4
        settlement.record()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_input_tokens", -1),
        ("max_output_tokens", True),
        ("max_tool_calls", 1.5),
        ("max_dollars", float("nan")),
        ("max_wall_seconds", float("inf")),
        ("max_wall_seconds", 7 * 86400),
    ],
)
def test_orchestrator_rejects_malformed_mutable_budget_before_reserve(
    world, field, value,
):
    from maverick import tenant_quota_authority as quota
    from maverick.budget import Budget
    from maverick.orchestrator import _QuotaUsageSettlement

    budget = Budget(
        max_input_tokens=60,
        max_output_tokens=40,
        max_dollars=5,
        max_tool_calls=10,
        max_wall_seconds=60,
    )
    # Deliberately mutate after Budget.__post_init__; alternate callers and
    # hot configuration can bypass constructor-time normalization.
    setattr(budget, field, value)
    with _tenant("acme"):
        settlement = _QuotaUsageSettlement(
            principal="user:alice",
            budget=budget,
            domain=None,
            world=world,
            goal_id=9,
        )
        with pytest.raises(quota.TenantQuotaPolicyError):
            settlement.admit()
        assert quota.status("acme", world=world)["active_reservations"] == 0


def test_postgres_shape_locks_tenant_row_before_admission():
    from maverick.world_model_backends import postgres

    migration = "\n".join(dict(postgres.MIGRATIONS)[41])
    assert "tenant_quota_usage" in migration
    assert "tenant_quota_reservations" in migration
    assert "PRIMARY KEY (tenant_id, request_id)" in migration
    assert {"tenant_quota_usage", "tenant_quota_reservations"}.issubset(
        postgres._RLS_TABLES
    )
    source = inspect.getsource(postgres.PostgresWorldModel.reserve_tenant_quota)
    assert "FOR UPDATE" in source
    assert "_required_quota_tenant" in source


@pytest.mark.skipif(
    not _LIVE_PG_DSN,
    reason="MAVERICK_PG_DSN not set (live Postgres quota proof unavailable)",
)
def test_live_postgres_reservation_round_trip(monkeypatch):
    from maverick import tenant_quota_authority as quota
    from maverick.paths import reset_tenant, set_tenant
    from maverick.world_model_backends.postgres import PostgresWorldModel

    # The module fixture hides accidental developer DSNs; an explicit test DSN
    # must be restored here to make this guarded proof runnable on demand.
    dsn = _LIVE_PG_DSN
    assert dsn is not None
    monkeypatch.setenv("MAVERICK_PG_DSN", dsn)
    tenant = f"quota-test-{uuid.uuid4().hex[:12]}"
    token = set_tenant(tenant)
    instance = PostgresWorldModel(dsn=dsn)
    try:
        reservation = quota.reserve(
            tenant, "user:live", {"concurrent_runs": 1}, uuid.uuid4().hex, 60,
            world=instance,
        )
        assert reservation.active
        assert quota.release(reservation, world=instance)["state"] == "RELEASED"
    finally:
        instance.close()
        reset_tenant(token)
