"""Adversarial coverage for the shared artifact release authority."""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from maverick.artifact_release_authority import (
    RELEASE_SCHEMA,
    ArtifactReleaseAuthority,
    ArtifactReleaseConflict,
    ArtifactReleaseStateError,
    ReconciliationAction,
)
from maverick.governed_records import GovernedRecordBackendError
from maverick.paths import tenant_scope

_DIGEST_A = "11" * 32
_DIGEST_B = "22" * 32
_DIGEST_C = "33" * 32


@pytest.fixture(autouse=True)
def _isolated_local_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_GOVERNED_RECORDS_BACKEND", "local")
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.setenv("MAVERICK_WORLD_BACKEND", "sqlite")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr("maverick.audit.record", lambda *_args, **_kwargs: True)


def _publish(
    authority: ArtifactReleaseAuthority,
    digest: str = _DIGEST_A,
    *,
    expected_revision: int = 0,
    transaction: str = "promotion-txn-1",
    record: str = "promotion-record-1",
    deployment: str = "risk/verifier",
):
    return authority.publish_desired(
        deployment,
        artifact_kind="verifier",
        desired_digest=digest,
        promotion_record_id=record,
        promotion_transaction_id=transaction,
        expected_revision=expected_revision,
        actor="release-controller",
    )


def test_only_exact_healthy_admitted_observation_is_serving_ready():
    authority = ArtifactReleaseAuthority()

    absent = authority.reconciliation_decision("risk/verifier", "node-a")
    assert absent.action is ReconciliationAction.WAIT_FOR_DESIRED
    assert absent.ready is False

    desired = _publish(authority)
    assert desired.generation == 1
    assert desired.previous_digest is None
    assert desired.rollout_state == "pending"
    assert authority.reconciliation_decision(
        "risk/verifier", "node-a"
    ).action is ReconciliationAction.APPLY_DESIRED

    staging = authority.report_observation(
        "risk/verifier",
        "node-a",
        observed_generation=1,
        observed_digest=_DIGEST_A,
        health="staging",
        expected_revision=0,
        actor="node/node-a",
    )
    decision = authority.reconciliation_decision("risk/verifier", "node-a")
    assert decision.action is ReconciliationAction.VERIFY_HEALTH
    assert decision.ready is False

    healthy = authority.report_observation(
        "risk/verifier",
        "node-a",
        observed_generation=1,
        observed_digest=_DIGEST_A,
        health="healthy",
        expected_revision=staging.revision,
        actor="node/node-a",
    )
    pending = authority.reconciliation_decision("risk/verifier", "node-a")
    assert pending.action is ReconciliationAction.AWAIT_ROLLOUT
    assert authority.serving_ready("risk/verifier", "node-a") is False

    admitted = authority.transition_rollout(
        "risk/verifier",
        "canary",
        expected_revision=desired.revision,
        actor="release-controller",
    )
    with pytest.raises(ArtifactReleaseConflict, match="expected revision"):
        authority.transition_rollout(
            "risk/verifier",
            "canary",
            expected_revision=desired.revision,
            actor="stale-release-controller",
        )
    ready = authority.reconciliation_decision("risk/verifier", "node-a")
    assert admitted.rollout_state == "canary"
    assert ready.action is ReconciliationAction.SERVE
    assert ready.ready is True
    assert authority.serving_ready("risk/verifier", "node-a") is True

    authority.report_observation(
        "risk/verifier",
        "node-a",
        observed_generation=1,
        observed_digest=_DIGEST_B,
        health="healthy",
        expected_revision=healthy.revision,
        actor="node/node-a",
    )
    mismatch = authority.reconciliation_decision("risk/verifier", "node-a")
    assert mismatch.action is ReconciliationAction.APPLY_DESIRED
    assert mismatch.ready is False


def test_publish_is_idempotent_and_preserves_cas_lineage_and_rollback():
    authority = ArtifactReleaseAuthority()
    first = _publish(authority)

    retry = _publish(authority, expected_revision=0)
    assert retry == first

    with pytest.raises(ArtifactReleaseConflict, match="reused"):
        _publish(authority, _DIGEST_B, expected_revision=first.revision)

    second = _publish(
        authority,
        _DIGEST_B,
        expected_revision=first.revision,
        transaction="promotion-txn-2",
        record="promotion-record-2",
    )
    assert second.generation == 2
    assert second.previous_digest == _DIGEST_A
    assert second.rollout_state == "pending"

    with pytest.raises(ArtifactReleaseConflict, match="expected revision"):
        _publish(
            authority,
            _DIGEST_C,
            expected_revision=first.revision,
            transaction="promotion-txn-stale",
            record="promotion-record-stale",
        )

    rollback = _publish(
        authority,
        _DIGEST_A,
        expected_revision=second.revision,
        transaction="promotion-txn-3",
        record="promotion-record-3",
    )
    assert rollback.generation == 3
    assert rollback.previous_digest == _DIGEST_B
    assert rollback.rollout_state == "rollback"


def test_concurrent_desired_publish_has_exactly_one_cas_winner():
    authority = ArtifactReleaseAuthority()
    first = _publish(authority)
    contenders = 16
    barrier = Barrier(contenders)

    def publish(index: int):
        digest = hashlib.sha256(f"candidate-{index}".encode()).hexdigest()
        barrier.wait()
        try:
            return _publish(
                authority,
                digest,
                expected_revision=first.revision,
                transaction=f"promotion-txn-race-{index}",
                record=f"promotion-record-race-{index}",
            )
        except ArtifactReleaseConflict:
            return None

    with ThreadPoolExecutor(max_workers=contenders) as pool:
        outcomes = list(pool.map(publish, range(contenders)))

    winners = [item for item in outcomes if item is not None]
    assert len(winners) == 1
    assert winners[0].generation == 2
    assert winners[0].previous_digest == _DIGEST_A
    assert authority.get_desired("risk/verifier") == winners[0]


def test_node_rows_scale_independently_and_same_node_updates_use_cas():
    authority = ArtifactReleaseAuthority()
    desired = _publish(authority)
    authority.transition_rollout(
        "risk/verifier",
        "rolling",
        expected_revision=desired.revision,
        actor="release-controller",
    )
    nodes = [f"node-{index:02d}" for index in range(16)]
    barrier = Barrier(len(nodes))

    def report(node: str):
        barrier.wait()
        return authority.report_observation(
            "risk/verifier",
            node,
            observed_generation=1,
            observed_digest=_DIGEST_A,
            health="healthy",
            expected_revision=0,
            actor=f"node/{node}",
        )

    with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
        observations = list(pool.map(report, nodes))

    assert {item.node_id for item in observations} == set(nodes)
    fleet = authority.fleet_readiness("risk/verifier", set(reversed(nodes)))
    assert fleet.consistent_snapshot is True
    assert fleet.ready is True
    assert [item.node_id for item in fleet.decisions] == sorted(nodes)
    assert all(item.action is ReconciliationAction.SERVE for item in fleet.decisions)

    target = observations[0]
    update_barrier = Barrier(2)

    def update(health: str):
        update_barrier.wait()
        try:
            return authority.report_observation(
                "risk/verifier",
                target.node_id,
                observed_generation=1,
                observed_digest=_DIGEST_A,
                health=health,
                expected_revision=target.revision,
                actor=f"node/{target.node_id}",
            )
        except ArtifactReleaseConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        raced = list(pool.map(update, ("staging", "unhealthy")))

    assert sum(item is not None for item in raced) == 1
    assert authority.get_observation(
        "risk/verifier", target.node_id
    ).revision == target.revision + 1
    assert authority.fleet_readiness("risk/verifier", nodes).ready is False


def test_release_and_observations_are_tenant_scoped():
    authority = ArtifactReleaseAuthority()

    with tenant_scope(tenant="alpha"):
        alpha = _publish(authority, _DIGEST_A)
        authority.report_observation(
            "risk/verifier",
            "node-a",
            observed_generation=1,
            observed_digest=_DIGEST_A,
            health="healthy",
            expected_revision=0,
            actor="node/node-a",
        )

    with tenant_scope(tenant="beta"):
        beta = _publish(authority, _DIGEST_B)
        assert authority.get_observation("risk/verifier", "node-a") is None

    assert alpha.desired_digest == _DIGEST_A
    assert beta.desired_digest == _DIGEST_B
    with tenant_scope(tenant="alpha"):
        assert authority.get_desired("risk/verifier") == alpha
        assert authority.get_observation(
            "risk/verifier", "node-a"
        ).observed_digest == _DIGEST_A
    with tenant_scope(tenant="beta"):
        assert authority.get_desired("risk/verifier") == beta


def test_untrusted_inputs_fail_closed_and_diagnostics_are_redacted():
    authority = ArtifactReleaseAuthority()

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        _publish(authority, ("ab" * 32).upper())
    with pytest.raises(ValueError, match="expected_revision"):
        _publish(authority, expected_revision=True)

    desired = _publish(authority)
    with pytest.raises(ValueError, match="node_id"):
        authority.report_observation(
            "risk/verifier",
            "../node/a",
            observed_generation=1,
            observed_digest=_DIGEST_A,
            health="healthy",
            expected_revision=0,
            actor="node",
        )
    with pytest.raises(ArtifactReleaseStateError, match="future"):
        authority.report_observation(
            "risk/verifier",
            "node-a",
            observed_generation=desired.generation + 1,
            observed_digest=_DIGEST_B,
            health="staging",
            expected_revision=0,
            actor="node/node-a",
        )
    with pytest.raises(ValueError, match="cannot carry"):
        authority.report_observation(
            "risk/verifier",
            "node-a",
            observed_generation=1,
            observed_digest=_DIGEST_A,
            health="healthy",
            last_error="contradictory",
            expected_revision=0,
            actor="node/node-a",
        )
    with pytest.raises(ValueError, match="requires an artifact"):
        authority.report_observation(
            "risk/verifier",
            "node-a",
            observed_generation=0,
            observed_digest=None,
            health="healthy",
            expected_revision=0,
            actor="node/node-a",
        )
    with pytest.raises(ValueError, match="observed_at"):
        authority.report_observation(
            "risk/verifier",
            "node-a",
            observed_generation=1,
            observed_digest=_DIGEST_A,
            health="staging",
            observed_at=float("nan"),
            expected_revision=0,
            actor="node/node-a",
        )
    with pytest.raises(ValueError, match="far in the future"):
        authority.report_observation(
            "risk/verifier",
            "node-a",
            observed_generation=1,
            observed_digest=_DIGEST_A,
            health="staging",
            observed_at=time.time() + 301,
            expected_revision=0,
            actor="node/node-a",
        )

    secret = "sk-" + "proj-" + "a" * 28  # pragma: allowlist secret
    observation = authority.report_observation(
        "risk/verifier",
        "node-a",
        observed_generation=1,
        observed_digest=_DIGEST_A,
        health="unhealthy",
        last_error=f"provider rejected {secret}",
        expected_revision=0,
        actor="node/node-a",
    )
    assert secret not in observation.last_error
    assert "REDACTED" in observation.last_error
    with pytest.raises(ArtifactReleaseConflict, match="cannot move backwards"):
        authority.report_observation(
            "risk/verifier",
            "node-a",
            observed_generation=1,
            observed_digest=_DIGEST_A,
            health="staging",
            observed_at=observation.observed_at - 1,
            expected_revision=observation.revision,
            actor="node/node-a",
        )


def test_multi_replica_local_selection_is_rejected_by_shared_plane(monkeypatch):
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "2")
    authority = ArtifactReleaseAuthority()

    with pytest.raises(GovernedRecordBackendError, match="require Postgres"):
        _ = authority.backend_kind


def test_reconciliation_detects_an_authority_change_during_snapshot(monkeypatch):
    authority = ArtifactReleaseAuthority()
    first = _publish(authority)
    second = _publish(
        authority,
        _DIGEST_B,
        expected_revision=first.revision,
        transaction="promotion-txn-2",
        record="promotion-record-2",
    )
    reads = iter((first, second))
    monkeypatch.setattr(authority, "get_desired", lambda _deployment: next(reads))
    monkeypatch.setattr(
        authority,
        "get_observation",
        lambda _deployment, _node: None,
    )

    decision = authority.reconciliation_decision("risk/verifier", "node-a")
    assert decision.action is ReconciliationAction.AUTHORITY_CHANGED
    assert decision.desired_generation == 2
    assert decision.ready is False


class _MalformedReadStore:
    backend_kind = "local"

    def __init__(self, raw: dict):
        self.raw = raw

    def get(self, _record_id: str):
        return dict(self.raw)


def test_persisted_unknown_fields_fail_closed_at_the_public_read_boundary():
    raw = {
        "id": "wrong-id",
        "schema": RELEASE_SCHEMA,
        "record_kind": "desired",
        "deployment_id": "risk/verifier",
        "artifact_kind": "verifier",
        "generation": 1,
        "desired_digest": _DIGEST_A,
        "previous_digest": None,
        "promotion_record_id": "promotion-record-1",
        "promotion_transaction_id": "promotion-txn-1",
        "rollout_state": "pending",
        "status": "pending",
        "revision": 1,
        "created_at": 1.0,
        "updated_at": 1.0,
        "attacker_controlled": True,
    }
    malformed = _MalformedReadStore(raw)
    authority = ArtifactReleaseAuthority(
        release_store=malformed,  # type: ignore[arg-type]
        observation_store=malformed,  # type: ignore[arg-type]
    )

    with pytest.raises(ArtifactReleaseStateError, match="unknown fields"):
        authority.get_desired("risk/verifier")
