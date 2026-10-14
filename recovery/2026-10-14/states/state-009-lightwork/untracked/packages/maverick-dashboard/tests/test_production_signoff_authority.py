"""Production HTTP boundary for audited deliverable sign-off."""
from __future__ import annotations

import asyncio
import contextlib

import pytest
from fastapi import HTTPException, Response
from maverick_dashboard.api_schemas import SignoffIn
from pydantic import ValidationError
from starlette.requests import Request

_PRINCIPAL = "user:alice"


@pytest.mark.parametrize("version", [float("inf"), float("-inf"), float("nan")])
def test_signoff_schema_rejects_non_finite_deliverable_versions(version):
    with pytest.raises(ValidationError):
        SignoffIn(decision="approved", expected_updated_at=version)
_KEY = "production-signoff-key-0001"


def _request(goal_id: int, key: str | None = _KEY) -> Request:
    headers = [(b"origin", b"http://testserver")]
    if key is not None:
        headers.append((b"idempotency-key", key.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/goals/{goal_id}/signoff",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


@pytest.fixture
def production_world(tmp_path, monkeypatch):
    from maverick import production_scope
    from maverick.paths import reset_tenant, set_tenant
    from maverick.world_model import WorldModel
    from maverick_dashboard import api

    token = set_tenant("acme")
    world = WorldModel(tmp_path / "world.db")
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    monkeypatch.setattr(api, "_world", lambda: world)
    monkeypatch.setattr(api, "require_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "assert_goal_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "caller_principal", lambda _request: _PRINCIPAL)
    try:
        yield api, world
    finally:
        world.close()
        reset_tenant(token)


def _goal(world):
    goal_id = world.create_goal(
        "review forecast", "", owner=_PRINCIPAL, domain="finance_cash13w"
    )
    world.set_goal_status(goal_id, "done", result="reviewed deliverable")
    return goal_id, world.get_goal(goal_id).updated_at


def _payload(version: float, *, decision="approved", note="ties out") -> SignoffIn:
    return SignoffIn(
        decision=decision,
        expected_updated_at=version,
        note=note,
    )


def _post(api, goal_id: int, payload: SignoffIn, *, key: str | None = _KEY):
    response = Response()
    result = asyncio.run(
        api.post_signoff(_request(goal_id, key), goal_id, payload, response)
    )
    return result, response


def test_production_exact_replay_is_audited_once_and_has_no_side_channels(
    production_world, monkeypatch,
):
    api, world = production_world
    goal_id, version = _goal(world)
    audit_calls = []
    from maverick import audit

    monkeypatch.setattr(
        audit,
        "required_audit_event",
        lambda kind, **payload: audit_calls.append((kind, payload)) or True,
    )
    monkeypatch.setattr(
        api,
        "_record_signoff_outcome",
        lambda *_args, **_kwargs: pytest.fail("production learning side channel ran"),
    )
    monkeypatch.setattr(
        api,
        "_handoff_approved_deliverable",
        lambda *_args, **_kwargs: pytest.fail("production handoff ran"),
    )

    first, first_response = _post(api, goal_id, _payload(version))
    replay, replay_response = _post(api, goal_id, _payload(version))

    assert replay == first
    assert first_response.headers["Idempotency-Replayed"] == "false"
    assert replay_response.headers["Idempotency-Replayed"] == "true"
    assert first_response.headers["X-Lightwork-Signoff-Audit"] == "effective"
    assert replay_response.headers["X-Lightwork-Decision-Id"] == first["decision_id"]
    assert len(audit_calls) == 1
    _kind, audited = audit_calls[0]
    assert audited["signoff_event_id"] == first["decision_id"]
    assert audited["tenant_id"] == "acme"
    assert audited["goal_id"] == goal_id
    assert audited["deliverable_updated_at"] == version
    assert audited["goal_domain"] == "finance_cash13w"
    assert audited["governing_gate"] == "review"
    assert audited["decision"] == "approved"
    assert audited["decided_by"] == _PRINCIPAL
    assert len(audited["request_sha256"]) == len(audited["note_sha256"]) == 64
    assert len(audited["policy_sha256"]) == 64
    assert first["signoff"]["policy_sha256"] == audited["policy_sha256"]
    assert world.signoff_for(goal_id) == first["signoff"]


def test_production_requires_attributable_principal_and_idempotency_key(
    production_world, monkeypatch,
):
    api, world = production_world
    goal_id, version = _goal(world)

    with pytest.raises(HTTPException) as missing_key:
        _post(api, goal_id, _payload(version), key=None)
    assert missing_key.value.status_code == 428
    assert world.signoff_for(goal_id) is None

    monkeypatch.setattr(api, "caller_principal", lambda _request: None)
    with pytest.raises(HTTPException) as anonymous:
        _post(api, goal_id, _payload(version))
    assert anonymous.value.status_code == 401
    assert world.signoff_for(goal_id) is None


def test_note_or_verdict_reuse_conflicts_before_second_audit(
    production_world, monkeypatch,
):
    api, world = production_world
    goal_id, version = _goal(world)
    audit_calls = []
    from maverick import audit

    monkeypatch.setattr(
        audit,
        "required_audit_event",
        lambda *args, **kwargs: audit_calls.append((args, kwargs)) or True,
    )
    _post(api, goal_id, _payload(version))

    for payload in (
        _payload(version, note="different note"),
        _payload(version, decision="rejected"),
    ):
        with pytest.raises(HTTPException) as conflict:
            _post(api, goal_id, payload)
        assert conflict.value.status_code == 409
    assert len(audit_calls) == 1
    assert world.signoff_for(goal_id)["decision"] == "approved"


def test_audit_refusal_keeps_approval_non_effective_and_same_key_repairs(
    production_world, monkeypatch,
):
    api, world = production_world
    goal_id, version = _goal(world)
    from maverick import audit
    from maverick.audit import AuditRequiredWriteFailed

    def _refuse(*_args, **_kwargs):
        raise AuditRequiredWriteFailed("required audit unavailable")

    monkeypatch.setattr(audit, "required_audit_event", _refuse)
    with pytest.raises(HTTPException) as pending:
        _post(api, goal_id, _payload(version))
    assert pending.value.status_code == 503
    assert pending.value.headers["X-Lightwork-Signoff-Audit"] == "pending"
    assert world.signoff_for(goal_id) is None
    assert len(world.pending_signoff_audit_events()) == 1

    monkeypatch.setattr(audit, "required_audit_event", lambda *_a, **_kw: True)
    repaired, response = _post(api, goal_id, _payload(version))
    assert response.headers["Idempotency-Replayed"] == "true"
    assert repaired["signoff"]["decision"] == "approved"
    assert world.signoff_for(goal_id) == repaired["signoff"]


def test_race_after_signed_append_cannot_approve_replacement_generation(
    production_world, monkeypatch,
):
    api, world = production_world
    goal_id, version = _goal(world)
    from maverick import audit

    def _replace_after_audit(*_args, **_kwargs):
        world.set_goal_status(goal_id, "done", result="new unreviewed generation")
        return True

    monkeypatch.setattr(audit, "required_audit_event", _replace_after_audit)
    with pytest.raises(HTTPException) as stale:
        _post(api, goal_id, _payload(version))
    assert stale.value.status_code == 409
    assert stale.value.headers["X-Lightwork-Signoff-Audit"] == "delivered_stale"
    assert world.signoff_for(goal_id) is None


def test_gate_policy_drift_after_signed_append_cannot_become_effective(
    production_world, monkeypatch,
):
    api, world = production_world
    goal_id, version = _goal(world)
    import maverick.signoff_authority as authority
    from maverick import audit

    def _change_policy_after_audit(*_args, **_kwargs):
        monkeypatch.setattr(
            authority,
            "current_signoff_policy_binding",
            lambda _domain: authority.policy_binding(
                "finance_cash13w", "approval"
            ),
        )
        return True

    monkeypatch.setattr(audit, "required_audit_event", _change_policy_after_audit)
    with pytest.raises(HTTPException) as stale:
        _post(api, goal_id, _payload(version))
    assert stale.value.status_code == 409
    assert stale.value.headers["X-Lightwork-Signoff-Audit"] == "delivered_stale"
    assert world.signoff_for(goal_id) is None


def test_lost_claim_commit_ack_reconciles_without_second_decision(
    production_world, monkeypatch,
):
    api, world = production_world
    goal_id, version = _goal(world)
    from maverick import audit

    monkeypatch.setattr(audit, "required_audit_event", lambda *_a, **_kw: True)
    original_writing = world._writing
    lose_ack = True

    @contextlib.contextmanager
    def _uncertain_commit():
        nonlocal lose_ack
        with original_writing() as conn:
            yield conn
        if lose_ack:
            lose_ack = False
            raise RuntimeError("commit acknowledgement lost")

    monkeypatch.setattr(world, "_writing", _uncertain_commit)
    result, response = _post(api, goal_id, _payload(version))

    assert response.headers["Idempotency-Replayed"] == "true"
    assert world.signoff_for(goal_id) == result["signoff"]
    assert world.conn.execute(
        "SELECT COUNT(*) FROM signoff_decision_outbox"
    ).fetchone()[0] == 1


def test_lost_audit_finalization_ack_reconciles_effective_state(
    production_world, monkeypatch,
):
    api, world = production_world
    goal_id, version = _goal(world)
    from maverick import audit

    monkeypatch.setattr(audit, "required_audit_event", lambda *_a, **_kw: True)
    original_writing = world._writing
    commits = 0

    @contextlib.contextmanager
    def _lose_second_ack():
        nonlocal commits
        with original_writing() as conn:
            yield conn
        commits += 1
        if commits == 2:
            raise RuntimeError("audit finalization acknowledgement lost")

    monkeypatch.setattr(world, "_writing", _lose_second_ack)
    result, response = _post(api, goal_id, _payload(version))

    assert response.headers["X-Lightwork-Signoff-Audit"] == "effective"
    assert world.signoff_for(goal_id) == result["signoff"]


def test_production_handoff_gate_returns_before_connector_io(
    production_world, monkeypatch,
):
    api, world = production_world
    goal_id, _version = _goal(world)
    goal = world.get_goal(goal_id)
    import maverick.webhooks as webhooks

    monkeypatch.setattr(
        webhooks,
        "fire_deliverable_handoff",
        lambda *_args, **_kwargs: pytest.fail("connector/network path was reached"),
    )
    api._handoff_approved_deliverable(goal, _PRINCIPAL)
