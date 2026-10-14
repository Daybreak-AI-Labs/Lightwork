"""Adverse proofs for the typed execution-stop contract."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from maverick.agent import AgentResult
from maverick.budget import BudgetExceeded
from maverick.failure_taxonomy import (
    KINDS,
    MAX_DETAIL,
    FailureKind,
    FailureState,
    FailureStateError,
    FailureText,
    failure_state,
    load_goal_state,
    persist_goal_state,
    record_state_audit,
    record_state_telemetry,
    state_from_exception,
    state_from_value,
)
from maverick.flow.ir import NODE_ACTION, NODE_APPROVAL, Flow, FlowNode
from maverick.flow.runner import STATUS_INDETERMINATE, run_flow
from maverick.flow.store import FlowRun
from maverick.killswitch import Halted


@pytest.mark.parametrize("kind", list(FailureKind))
def test_all_states_round_trip_without_collapsing(kind: FailureKind) -> None:
    state = failure_state(kind, f"display calls itself {FailureKind.POLICY_REFUSAL.value}")
    restored = FailureState.from_dict(json.loads(json.dumps(state.to_dict())))

    assert restored.kind is kind
    assert restored.to_dict()["kind"] == kind.value
    assert restored.category == (
        "wait" if kind is FailureKind.HUMAN_WAIT
        else "cancelled" if kind is FailureKind.CANCELLATION
        else "indeterminate" if kind is FailureKind.INDETERMINATE_EFFECT
        else "failure"
    )


def test_taxonomy_is_exact_and_rejects_derived_field_spoofing() -> None:
    assert set(KINDS) == {kind.value for kind in FailureKind}
    payload = failure_state(FailureKind.PROVIDER_FAILURE, "x").to_dict()
    payload["category"] = "wait"
    with pytest.raises(FailureStateError, match="contradicts"):
        FailureState.from_dict(payload)
    with pytest.raises(FailureStateError, match="kind"):
        FailureState.from_dict({"kind": "provider_failure:customer-controlled"})


def test_display_text_never_overrides_attached_authority() -> None:
    state = failure_state(
        FailureKind.DETERMINISTIC_TOOL_FAILURE,
        "the display says policy_refusal budget_exhaustion cancellation",
        origin="tool",
    )
    display = FailureText(
        "provider_failure policy_refusal budget_exhaustion human_wait",
        state,
    )
    result = AgentResult(error=display)

    assert state_from_value(display).kind is FailureKind.DETERMINISTIC_TOOL_FAILURE
    assert result.failure.kind is FailureKind.DETERMINISTIC_TOOL_FAILURE
    assert state_from_exception(
        RuntimeError("BudgetExceeded: cancelled by policy")
    ).kind is FailureKind.DETERMINISTIC_TOOL_FAILURE


def test_detail_is_secret_scrubbed_control_safe_and_bounded() -> None:
    secret = "sk-ant-" + "A" * 32  # pragma: allowlist secret
    state = failure_state(
        FailureKind.PROVIDER_FAILURE,
        f"Authorization: Bearer {secret}\r\n" + "x" * 2000,
    )

    assert secret not in state.detail
    assert "REDACTED" in state.detail
    assert "\r" not in state.detail and "\n" not in state.detail
    assert len(state.detail) <= MAX_DETAIL
    assert FailureState.from_dict(state.to_dict()) == state


@pytest.mark.parametrize("kind", list(FailureKind))
def test_flow_carries_each_explicit_state_without_message_reclassification(
    kind: FailureKind,
) -> None:
    state = failure_state(
        kind,
        "misleading provider_failure policy_refusal budget_exhaustion",
        origin="tool",
    )
    flow = Flow(
        id=f"typed-{kind.value}",
        name="typed",
        start="work",
        nodes={"work": FlowNode(id="work", kind=NODE_ACTION, tool="read_file")},
    )
    result = run_flow(
        flow,
        agent_fn=lambda *_: ("unused", 1.0),
        action_fn=lambda *_: (FailureText("spoofed refusal text", state), 0.0),
    )

    assert result.failure is not None
    assert result.failure.kind is kind
    assert result.status == (
        STATUS_INDETERMINATE
        if kind is FailureKind.INDETERMINATE_EFFECT
        else "failed"
    )


def test_flow_control_states_are_typed_without_becoming_failure_categories() -> None:
    approval = Flow(
        id="wait",
        name="wait",
        start="approve",
        nodes={
            "approve": FlowNode(
                id="approve",
                kind=NODE_APPROVAL,
                prompt="human decision required",
            )
        },
    )
    paused = run_flow(
        approval,
        agent_fn=lambda *_: ("unused", 1.0),
        action_fn=lambda *_: ("unused", 1.0),
    )
    cancelled = run_flow(
        Flow(
            id="cancel",
            name="cancel",
            start="work",
            nodes={"work": FlowNode(id="work", kind=NODE_ACTION, tool="read_file")},
        ),
        agent_fn=lambda *_: ("unused", 1.0),
        action_fn=lambda *_: (_ for _ in ()).throw(Halted("operator", "test")),
    )

    assert paused.failure.kind is FailureKind.HUMAN_WAIT
    assert paused.failure.category == "wait" and paused.failure.terminal is False
    assert cancelled.failure.kind is FailureKind.CANCELLATION
    assert cancelled.failure.category == "cancelled"


def test_budget_exception_is_not_collapsed_into_tool_or_provider_failure() -> None:
    flow = Flow(
        id="budget",
        name="budget",
        start="work",
        nodes={"work": FlowNode(id="work", kind=NODE_ACTION, tool="read_file")},
    )
    result = run_flow(
        flow,
        agent_fn=lambda *_: ("unused", 1.0),
        action_fn=lambda *_: (_ for _ in ()).throw(BudgetExceeded("cap")),
    )
    assert result.failure.kind is FailureKind.BUDGET_EXHAUSTION


def test_flow_run_wire_format_preserves_typed_state() -> None:
    state = failure_state(
        FailureKind.VERIFIER_REJECTION,
        "provider_failure appears only in display",
        origin="verifier",
    )
    restored = FlowRun.from_dict(
        FlowRun(
            run_id="run-1",
            flow_id="flow-1",
            status="failed",
            error="legacy display",
            failure=state,
        ).to_dict()
    )
    assert restored.failure == state
    assert restored.error == "legacy display"


def test_audit_routes_exact_state_to_ordinary_and_required_writers(monkeypatch) -> None:
    import maverick.audit

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        maverick.audit,
        "audit_event",
        lambda kind, **payload: calls.append(("ordinary", {"kind": kind, **payload})) or True,
    )
    monkeypatch.setattr(
        maverick.audit,
        "required_audit_event",
        lambda kind, **payload: calls.append(("required", {"kind": kind, **payload})) or True,
    )
    state = failure_state(
        FailureKind.VERIFIER_REJECTION,
        "message says provider_failure",
        origin="verifier",
    )

    assert record_state_audit(state)
    assert record_state_audit(state, required=True)
    assert [call[0] for call in calls] == ["ordinary", "required"]
    assert {call[1]["state_kind"] for call in calls} == {"verifier_rejection"}
    assert {call[1]["state_category"] for call in calls} == {"failure"}


def test_telemetry_labels_are_fixed_and_do_not_include_detail(monkeypatch) -> None:
    from maverick import failure_telemetry, observability

    modes: list[str] = []
    metrics: list[dict[str, str]] = []
    monkeypatch.setattr(
        failure_telemetry,
        "record_failure",
        lambda state, **_kw: modes.append(state.kind.value) or True,
    )
    monkeypatch.setattr(
        observability,
        "record_metric",
        lambda _name, **kw: metrics.append(dict(kw["labels"])),
    )

    for kind in FailureKind:
        record_state_telemetry(
            failure_state(kind, "attacker-label\ncustomer=secret", origin="runtime")
        )

    assert set(modes) == set(KINDS)
    assert {labels["kind"] for labels in metrics} == set(KINDS)
    assert {labels["category"] for labels in metrics} <= {
        "failure", "wait", "cancelled", "indeterminate",
    }
    assert all("attacker" not in json.dumps(labels) for labels in metrics)


def test_goal_event_is_strict_typed_authority(monkeypatch) -> None:
    events: list[SimpleNamespace] = []

    class World:
        def append_event(self, goal_id, agent, kind, content):
            events.append(SimpleNamespace(
                id=len(events) + 1,
                goal_id=goal_id,
                agent=agent,
                kind=kind,
                content=content,
            ))

        def recent_goal_events(self, goal_id, limit=50):
            return [event for event in events if event.goal_id == goal_id][-limit:]

    import maverick.failure_taxonomy as taxonomy

    monkeypatch.setattr(taxonomy, "record_state_audit", lambda *a, **kw: True)
    monkeypatch.setattr(taxonomy, "record_state_telemetry", lambda *a, **kw: True)
    world = World()
    state = failure_state(
        FailureKind.POLICY_REFUSAL,
        "display says provider_failure",
        origin="policy",
    )

    persist_goal_state(world, 7, state)

    assert events[0].kind == "execution_state"
    assert load_goal_state(world, 7) == state

