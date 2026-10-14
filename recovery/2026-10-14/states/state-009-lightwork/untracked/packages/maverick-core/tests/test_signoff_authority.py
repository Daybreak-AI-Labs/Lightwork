"""Adverse-path proof for production deliverable sign-off authority."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from maverick.api_idempotency import request_sha256
from maverick.paths import reset_tenant, set_tenant
from maverick.signoff_authority import (
    SignoffConflict,
    SignoffExpired,
    SignoffIndeterminate,
    policy_binding,
)
from maverick.world_model import MIGRATIONS, SCHEMA_VERSION, WorldModel

_OPERATION = "POST /api/v1/goals/{goal_id}/signoff"


def _done_goal(world: WorldModel, result: str = "reviewed bytes") -> tuple[int, float]:
    goal_id = world.create_goal("deliverable", "", domain="finance_cash13w")
    world.set_goal_status(goal_id, "done", result=result)
    return goal_id, world.get_goal(goal_id).updated_at


def _claim(
    world: WorldModel,
    goal_id: int,
    version: float,
    *,
    decision: str = "approved",
    note: str | None = "reviewed",
    principal: str = "user:alice",
    key: str = "signoff-key-0000000001",
    operation: str = _OPERATION,
    gate: str = "review",
    ttl_seconds: int | None = None,
):
    binding = policy_binding("finance_cash13w", gate)
    normalized = {
        "decision": decision,
        "deliverable_updated_at": float(version),
        "goal_domain": binding.goal_domain,
        "goal_id": int(goal_id),
        "governing_gate": binding.governing_gate,
        "note": note,
        "policy_sha256": binding.policy_sha256,
    }
    return world.decide_signoff_idempotent(
        goal_id,
        decision,
        decided_by=principal,
        note=note,
        expected_updated_at=version,
        goal_domain=binding.goal_domain,
        gate=gate,
        policy_sha256=binding.policy_sha256,
        operation=operation,
        principal_id=principal,
        idempotency_key=key,
        request_sha256=request_sha256(normalized),
        note_sha256=request_sha256({"note": note}),
        ttl_seconds=ttl_seconds,
    )


def test_claim_replay_and_audit_finalization_are_exact_and_ordered(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    goal_id, version = _done_goal(world)

    first = _claim(world, goal_id, version)
    replay_pending = _claim(world, goal_id, version)

    assert first.replayed is False
    assert replay_pending.replayed is True
    assert replay_pending.response == first.response
    assert first.event.state == "PENDING_AUDIT"
    assert world.signoff_for(goal_id) is None
    assert [event.event_id for event in world.pending_signoff_audit_events()] == [
        first.event.event_id
    ]

    assert world.mark_signoff_audit_delivered(first.event.event_id) is True
    effective = world.signoff_for(goal_id)
    assert effective == first.response["signoff"]

    replay_effective = _claim(world, goal_id, version)
    assert replay_effective.replayed is True
    assert replay_effective.response == first.response
    assert replay_effective.event.effective is True
    assert world.mark_signoff_audit_delivered(first.event.event_id) is True


def test_note_or_decision_mismatch_is_conflict_and_never_replaces(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    goal_id, version = _done_goal(world)
    first = _claim(world, goal_id, version)

    with pytest.raises(SignoffConflict, match="different sign-off"):
        _claim(world, goal_id, version, note="different")
    with pytest.raises(SignoffConflict, match="different sign-off"):
        _claim(world, goal_id, version, decision="rejected")

    assert world.mark_signoff_audit_delivered(first.event.event_id) is True
    with pytest.raises(SignoffConflict, match="authoritative sign-off"):
        _claim(
            world,
            goal_id,
            version,
            decision="rejected",
            key="signoff-key-0000000002",
        )
    assert world.signoff_for(goal_id)["decision"] == "approved"


def test_competing_principal_cannot_supersede_pending_decision(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    goal_id, version = _done_goal(world)
    _claim(world, goal_id, version)

    with pytest.raises(SignoffConflict, match="another sign-off"):
        _claim(
            world,
            goal_id,
            version,
            decision="rejected",
            principal="user:bob",
            key="signoff-key-0000000001",
        )
    assert world.signoff_for(goal_id) is None


def test_concurrent_opposite_verdicts_admit_exactly_one(tmp_path):
    path = tmp_path / "world.db"
    creator = WorldModel(path)
    goal_id, version = _done_goal(creator)
    left = WorldModel(path)
    right = WorldModel(path)
    barrier = Barrier(2)

    def _submit(world, decision, principal, key):
        barrier.wait()
        try:
            return _claim(
                world,
                goal_id,
                version,
                decision=decision,
                principal=principal,
                key=key,
            )
        except SignoffConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        approved = pool.submit(
            _submit,
            left,
            "approved",
            "user:alice",
            "signoff-key-0000000001",
        )
        rejected = pool.submit(
            _submit,
            right,
            "rejected",
            "user:bob",
            "signoff-key-0000000002",
        )
        admitted = [result for result in (approved.result(), rejected.result()) if result]

    assert len(admitted) == 1
    assert creator.conn.execute(
        "SELECT COUNT(*) FROM signoff_decision_outbox"
    ).fetchone()[0] == 1
    assert creator.mark_signoff_audit_delivered(admitted[0].event.event_id) is True
    assert creator.signoff_for(goal_id)["decision"] == admitted[0].event.decision
    left.close()
    right.close()
    creator.close()


def test_stale_generation_is_audited_but_never_becomes_effective(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    goal_id, version = _done_goal(world)
    claim = _claim(world, goal_id, version)

    world.set_goal_status(goal_id, "done", result="replacement bytes")
    assert world.mark_signoff_audit_delivered(claim.event.event_id) is False
    assert world.signoff_for(goal_id) is None
    assert world.get_signoff_audit_event(claim.event.event_id).state == "STALE"
    with pytest.raises(SignoffConflict, match="reviewed deliverable changed"):
        _claim(world, goal_id, version)


def test_expired_key_is_retained_as_tombstone(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    goal_id, version = _done_goal(world)
    claim = _claim(world, goal_id, version)
    with world._writing() as conn:
        conn.execute(
            "UPDATE signoff_decision_outbox SET expires_at = 0 WHERE event_id = ?",
            (claim.event.event_id,),
        )

    with pytest.raises(SignoffExpired, match="replay window has expired"):
        _claim(world, goal_id, version)
    assert world.conn.execute(
        "SELECT COUNT(*) FROM signoff_decision_outbox WHERE event_id = ?",
        (claim.event.event_id,),
    ).fetchone()[0] == 1
    assert world.signoff_for(goal_id) is None


def test_failed_generation_cas_rolls_back_claim_and_outbox(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    goal_id, version = _done_goal(world)

    with pytest.raises(SignoffConflict, match="deliverable changed"):
        _claim(world, goal_id, version - 1.0)

    assert world.conn.execute(
        "SELECT COUNT(*) FROM signoff_decision_outbox"
    ).fetchone()[0] == 0
    assert world.signoff_for(goal_id) is None


def test_tenant_route_and_principal_are_part_of_claim_scope(tmp_path):
    token = set_tenant("acme")
    try:
        world = WorldModel(tmp_path / "world.db")
        goal_a, version_a = _done_goal(world, "a")
        goal_b, version_b = _done_goal(world, "b")
        first = _claim(world, goal_a, version_a)
        second = _claim(
            world,
            goal_b,
            version_b,
            principal="user:bob",
            key="signoff-key-0000000001",
            operation="POST /alternate/signoff",
        )
    finally:
        reset_tenant(token)

    rows = world.conn.execute(
        "SELECT tenant_id, operation, principal_id FROM signoff_decision_outbox "
        "ORDER BY created_at"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("acme", _OPERATION, "user:alice"),
        ("acme", "POST /alternate/signoff", "user:bob"),
    ]
    assert first.event.event_id != second.event.event_id


def test_tampered_replay_result_is_indeterminate(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    goal_id, version = _done_goal(world)
    claim = _claim(world, goal_id, version)
    with world._writing() as conn:
        conn.execute(
            "UPDATE signoff_decision_outbox SET result_json = ? WHERE event_id = ?",
            ('{"response":{},"schema":"lightwork.idempotent-signoff-result.v2"}',
             claim.event.event_id),
        )

    with pytest.raises(SignoffIndeterminate, match="does not match"):
        _claim(world, goal_id, version)


def test_domain_transition_advances_revision_and_invalidates_all_prior_authority(
    tmp_path,
):
    world = WorldModel(tmp_path / "world.db")
    pending_goal, pending_version = _done_goal(world, "pending review")
    pending = _claim(world, pending_goal, pending_version)

    world.set_goal_domain(pending_goal, "bank_aml_alerts")
    changed = world.get_goal(pending_goal)
    assert changed.domain == "bank_aml_alerts"
    assert changed.updated_at > pending_version
    assert world.get_signoff_audit_event(pending.event.event_id).state == "STALE"
    assert world.mark_signoff_audit_delivered(pending.event.event_id) is False
    assert world.signoff_for(pending_goal) is None

    effective_goal, effective_version = _done_goal(world, "effective review")
    effective = _claim(
        world,
        effective_goal,
        effective_version,
        key="signoff-key-0000000002",
    )
    assert world.mark_signoff_audit_delivered(effective.event.event_id) is True
    assert world.signoff_for(effective_goal)["decision"] == "approved"

    world.set_goal_domain(effective_goal, "bank_aml_alerts")
    assert world.signoff_for(effective_goal) is None
    assert world.get_signoff_audit_event(effective.event.event_id).state == "STALE"


def test_policy_drift_between_audit_append_and_finalization_fails_closed(
    tmp_path, monkeypatch,
):
    import maverick.signoff_authority as authority

    world = WorldModel(tmp_path / "world.db")
    goal_id, version = _done_goal(world)
    claim = _claim(world, goal_id, version)
    changed = policy_binding("finance_cash13w", "approval")
    monkeypatch.setattr(
        authority,
        "current_signoff_policy_binding",
        lambda _domain: changed,
    )

    assert world.mark_signoff_audit_delivered(claim.event.event_id) is False
    assert world.signoff_for(goal_id) is None
    assert world.get_signoff_audit_event(claim.event.event_id).state == "STALE"


def test_fresh_policy_review_retires_stranded_old_policy_claim(
    tmp_path, monkeypatch,
):
    import maverick.signoff_authority as authority

    world = WorldModel(tmp_path / "world.db")
    goal_id, version = _done_goal(world)
    old_claim = _claim(world, goal_id, version)
    monkeypatch.setattr(
        authority,
        "current_signoff_policy_binding",
        lambda _domain: policy_binding("finance_cash13w", "approval"),
    )

    fresh = _claim(
        world,
        goal_id,
        version,
        gate="approval",
        key="signoff-key-0000000002",
    )

    assert fresh.event.state == "PENDING_AUDIT"
    assert world.get_signoff_audit_event(old_claim.event.event_id).state == "STALE"
    assert world.mark_signoff_audit_delivered(fresh.event.event_id) is True
    assert world.signoff_for(goal_id)["governing_gate"] == "approval"


def test_request_digest_must_include_domain_gate_and_policy_snapshot(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    goal_id, version = _done_goal(world)
    binding = policy_binding("finance_cash13w", "review")
    legacy_digest = request_sha256(
        {
            "decision": "approved",
            "deliverable_updated_at": version,
            "goal_id": goal_id,
            "note": "reviewed",
        }
    )

    with pytest.raises(SignoffIndeterminate, match="policy binding"):
        world.decide_signoff_idempotent(
            goal_id,
            "approved",
            decided_by="user:alice",
            note="reviewed",
            expected_updated_at=version,
            goal_domain=binding.goal_domain,
            gate=binding.governing_gate,
            policy_sha256=binding.policy_sha256,
            operation=_OPERATION,
            principal_id="user:alice",
            idempotency_key="signoff-key-0000000001",
            request_sha256=legacy_digest,
            note_sha256=request_sha256({"note": "reviewed"}),
        )
    assert world.conn.execute(
        "SELECT COUNT(*) FROM signoff_decision_outbox"
    ).fetchone()[0] == 0


def test_exact_unicode_principal_identity_is_not_normalized_or_collapsed(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    canonical = "user:Jos\u00e9"
    decomposed = "user:Jose\u0301"
    first_goal, first_version = _done_goal(world, "first")
    second_goal, second_version = _done_goal(world, "second")

    first = _claim(world, first_goal, first_version, principal=canonical)
    second = _claim(
        world,
        second_goal,
        second_version,
        principal=decomposed,
        key="signoff-key-0000000002",
    )

    assert canonical != decomposed
    assert first.event.decided_by == canonical
    assert second.event.decided_by == decomposed
    assert first.event.event_id != second.event.event_id
    principals = {
        row[0]
        for row in world.conn.execute(
            "SELECT principal_id FROM signoff_decision_outbox"
        ).fetchall()
    }
    assert principals == {canonical, decomposed}


def test_schema_head_contains_signoff_authority(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    assert SCHEMA_VERSION == 42
    assert world.schema_version == SCHEMA_VERSION
    columns = {
        row[1] for row in world.conn.execute("PRAGMA table_info(signoff_decision_outbox)")
    }
    assert {
        "tenant_id",
        "operation",
        "principal_id",
        "idempotency_key",
        "request_sha256",
        "goal_id",
        "deliverable_updated_at",
        "goal_domain",
        "governing_gate",
        "policy_sha256",
        "decision",
        "result_json",
        "state",
        "expires_at",
        "delivered_at",
    } <= columns
    signoff_columns = {
        row[1] for row in world.conn.execute("PRAGMA table_info(signoffs)")
    }
    assert {"goal_domain", "governing_gate", "policy_sha256"} <= signoff_columns
    migration = "\n".join(MIGRATIONS[37])
    assert "DROP " not in migration.upper()
    for table in ("signoffs", "signoff_decision_outbox"):
        for column in ("goal_domain", "governing_gate", "policy_sha256"):
            assert f"ALTER TABLE {table} ADD COLUMN {column}" in migration


@pytest.mark.parametrize("sign", [False, True])
def test_signed_audit_sink_deduplicates_signoff_event_identity(tmp_path, sign):
    from maverick.audit.events import AuditEvent, EventKind
    from maverick.audit.writer import AuditLog

    audit_dir = tmp_path / ("signed" if sign else "unsigned")
    log = AuditLog(audit_dir, sign=sign)
    payload = {
        "signoff_event_id": "deliverable-signoff-v1-stable",
        "tenant_id": "acme",
        "deliverable_updated_at": 123.5,
        "goal_domain": "finance_cash13w",
        "governing_gate": "review",
        "policy_sha256": policy_binding(
            "finance_cash13w", "review"
        ).policy_sha256,
        "decision": "approved",
        "decided_by": "user:alice",
        "request_sha256": "a" * 64,
        "note_sha256": "b" * 64,
        "occurred_at": 124.0,
    }
    for transport_ts in (125.0, 126.0):
        assert log.record(
            AuditEvent(
                ts=transport_ts,
                kind=EventKind.DELIVERABLE_SIGNOFF,
                goal_id=7,
                payload=payload,
            )
        )

    path = next(audit_dir.glob("*.ndjson"))
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert [
        row["signoff_event_id"]
        for row in rows
        if row["kind"] == EventKind.DELIVERABLE_SIGNOFF
    ] == ["deliverable-signoff-v1-stable"]

    conflicting = dict(payload, decision="rejected")
    assert not log.record(
        AuditEvent(
            ts=127.0,
            kind=EventKind.DELIVERABLE_SIGNOFF,
            goal_id=7,
            payload=conflicting,
        )
    )
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
