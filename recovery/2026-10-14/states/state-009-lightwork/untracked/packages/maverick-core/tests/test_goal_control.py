"""Per-goal cancellation remains precise, typed, and terminal."""
from __future__ import annotations

import pytest


def test_goal_control_refuses_cancelled_goal() -> None:
    from maverick.goal_control import check_goal_control
    from maverick.killswitch import Halted

    class _World:
        def get_goal(self, _goal_id: int):
            return type("Goal", (), {"status": "cancelled"})()

    with pytest.raises(Halted) as exc:
        check_goal_control(_World(), 7)

    assert exc.value.source == "goal-status"


def test_goal_control_loss_is_typed_indeterminate() -> None:
    from maverick.failure_taxonomy import FailureKind, FailureSignal
    from maverick.goal_control import check_goal_control

    class _World:
        def get_goal(self, _goal_id: int):
            raise OSError("database unavailable")

    with pytest.raises(FailureSignal) as exc:
        check_goal_control(_World(), 7)

    assert exc.value.failure_state.kind is FailureKind.INDETERMINATE_EFFECT


def test_terminal_status_fence_preserves_operator_cancellation(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    from maverick.goal_control import set_goal_status_preserving_cancellation

    class _World:
        status = "cancelled"
        writes: list[tuple[str, str | None]] = []

        def get_goal(self, _goal_id: int):
            return type("Goal", (), {"status": self.status})()

        def set_goal_status(
            self,
            _goal_id: int,
            status: str,
            result: str | None = None,
        ) -> None:
            self.writes.append((status, result))
            self.status = status

    world = _World()

    published = set_goal_status_preserving_cancellation(
        world,
        9,
        "done",
        result="too late",
    )

    assert published is False
    assert world.status == "cancelled"
    assert world.writes == []


def test_failure_fence_preserves_operator_cancellation_event(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    from maverick.failure_taxonomy import (
        FailureKind,
        failure_state,
        load_goal_state,
        persist_goal_state,
    )
    from maverick.goal_control import set_goal_failure_preserving_cancellation
    from maverick.world_model import WorldModel

    world = WorldModel(tmp_path / "world.db")
    goal_id = world.create_goal("cancel", "operator state is authoritative")
    cancellation = failure_state(
        FailureKind.CANCELLATION,
        "cancelled by an operator",
        origin="operator",
    )
    world.set_goal_status(goal_id, "cancelled", result="operator cancelled")
    persist_goal_state(world, goal_id, cancellation)

    published = set_goal_failure_preserving_cancellation(
        world,
        goal_id,
        failure_state(
            FailureKind.POLICY_REFUSAL,
            "late policy response",
            origin="policy",
        ),
        "blocked",
        result="late refusal",
    )

    assert published is False
    goal = world.get_goal(goal_id)
    assert goal.status == "cancelled"
    assert goal.result == "operator cancelled"
    assert load_goal_state(world, goal_id) == cancellation


@pytest.mark.asyncio
async def test_agent_polls_goal_cancellation_before_provider_spend(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("MAVERICK_REHEARSAL", "0")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    from maverick.agent import Agent
    from maverick.blackboard import Blackboard
    from maverick.budget import Budget
    from maverick.failure_taxonomy import FailureKind
    from maverick.sandbox import LocalBackend
    from maverick.swarm import SwarmContext
    from maverick.world_model import WorldModel

    class _NeverCalledLLM:
        model = "fake:test"
        calls = 0

        async def complete_async(self, **_kwargs):
            self.calls += 1
            raise AssertionError("cancelled goal reached the provider")

    world = WorldModel(tmp_path / "world.db")
    goal_id = world.create_goal("cancel", "stop before provider")
    world.set_goal_status(goal_id, "cancelled")
    llm = _NeverCalledLLM()
    context = SwarmContext(
        llm=llm,
        world=world,
        budget=Budget(max_dollars=1.0),
        blackboard=Blackboard(),
        sandbox=LocalBackend(workdir=tmp_path),
        goal_id=goal_id,
        max_depth=1,
        use_skills=False,
    )

    result = await Agent(ctx=context, role="researcher", brief="stop").run()

    assert llm.calls == 0
    assert result.failure is not None
    assert result.failure.kind is FailureKind.CANCELLATION
