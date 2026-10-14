"""Adverse coverage for durable child-agent execution (RUN-07)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from maverick.agent import Agent, AgentResult
from maverick.blackboard import Blackboard
from maverick.budget import Budget
from maverick.capability import Capability
from maverick.child_checkpoint import (
    ChildCheckpointStore,
    ChildIndeterminateError,
    DurableSpawnCapReached,
    bind_tool_invocation,
    descriptor_for,
    prepare_spawn_operation,
    validate_replayed_operations,
)
from maverick.llm import LLMResponse, ToolCall
from maverick.sandbox import LocalBackend
from maverick.swarm import SwarmContext
from maverick.tools.spawn import spawn_subagent_tool, spawn_swarm_tool
from maverick.world_model import WorldModel


class _FinalLLM:
    model = "fake:test"

    def __init__(self, text: str = "FINAL: done"):
        self.text = text
        self.calls = 0

    async def complete_async(self, **_kwargs) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text=self.text,
            thinking=None,
            tool_calls=[],
            stop_reason="end_turn",
        )


def _ctx(tmp_path: Path, *, max_spawns: int = 8) -> SwarmContext:
    world = WorldModel(tmp_path / "world.db")
    goal_id = world.create_goal("durable child", "")
    return SwarmContext(
        llm=_FinalLLM(),
        world=world,
        budget=Budget(max_dollars=10.0),
        blackboard=Blackboard(),
        sandbox=LocalBackend(workdir=tmp_path),
        goal_id=goal_id,
        episode_id=17,
        max_depth=3,
        max_total_spawns=max_spawns,
        use_skills=False,
        tenant_id="tenant-a",
    )


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAVERICK_DURABLE", "1")
    monkeypatch.setenv("MAVERICK_REQUIRE_DURABLE_CHECKPOINTS", "1")


def _fake_runtime_agent(child_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        checkpoint_id=child_id,
        role="researcher",
        depth=1,
        model="fake:test",
        max_steps=8,
        system="stable child system",
        capability=None,
    )


def _process_ctx(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        world=WorldModel(Path(db_path)),
        budget=Budget(max_dollars=10.0),
        goal_id=91,
        episode_id=4,
        tenant_id="tenant-process",
        _spawns_used=0,
        max_total_spawns=4,
    )


def _crash_writer(db_path: str, pending: bool) -> None:
    os.environ["MAVERICK_DURABLE"] = "1"
    os.environ["MAVERICK_REQUIRE_DURABLE_CHECKPOINTS"] = "1"
    ctx = _process_ctx(db_path)
    parent = SimpleNamespace(ctx=ctx, checkpoint_id="orchestrator-0")
    args = {"role": "researcher", "task": "resume after host death"}
    with bind_tool_invocation(parent, 3, 0):
        operation = prepare_spawn_operation(
            parent, "spawn_subagent", args, "subagent"
        )
    assert operation is not None
    descriptor = descriptor_for(
        operation,
        0,
        role="researcher",
        depth=1,
        task=args["task"],
        domain=None,
        parent_capability=None,
        child_capability=None,
    )
    operation.claim([descriptor])
    runtime = operation.bind(0, _fake_runtime_agent(descriptor.child_id))
    messages = [{"role": "user", "content": "committed child history"}]
    if pending:
        runtime.begin_turn(3, messages, ctx.budget)
    else:
        runtime.checkpoint_turn(3, messages, ctx.budget)
    # Model a SIGKILL/OOM: no finally blocks or graceful WorldModel close.
    os._exit(23)


def _recovery_reader(db_path: str, queue, expect_pending: bool) -> None:
    os.environ["MAVERICK_DURABLE"] = "1"
    os.environ["MAVERICK_REQUIRE_DURABLE_CHECKPOINTS"] = "1"
    try:
        ctx = _process_ctx(db_path)
        parent = SimpleNamespace(ctx=ctx, checkpoint_id="orchestrator-0")
        args = {"role": "researcher", "task": "resume after host death"}
        with bind_tool_invocation(parent, 3, 0):
            operation = prepare_spawn_operation(
                parent, "spawn_subagent", args, "subagent"
            )
        assert operation is not None
        descriptor = descriptor_for(
            operation,
            0,
            role="researcher",
            depth=1,
            task=args["task"],
            domain=None,
            parent_capability=None,
            child_capability=None,
        )
        operation.claim([descriptor])
        try:
            operation.bind(0, _fake_runtime_agent(descriptor.child_id))
        except ChildIndeterminateError:
            queue.put(("indeterminate", ctx._spawns_used))
            return
        if expect_pending:
            queue.put(("unexpected_resume", ctx._spawns_used))
            return
        queue.put(
            (
                "resumed",
                descriptor.child_id,
                ctx._spawns_used,
            )
        )
    except BaseException as exc:  # pragma: no cover - diagnostic from child process
        queue.put(("error", type(exc).__name__, str(exc)))


@pytest.mark.asyncio
async def test_completed_spawn_replays_without_rerunning_child(
    tmp_path, monkeypatch,
):
    _enable(monkeypatch)
    ctx = _ctx(tmp_path)
    parent = Agent(ctx=ctx, role="orchestrator", brief="root")
    calls: list[str] = []

    async def fake_run(child):
        calls.append(child.name)
        child._durable_child_messages = [
            {"role": "user", "content": "child committed"}
        ]
        return AgentResult(final="durable answer", role=child.role, name=child.name)

    monkeypatch.setattr(Agent, "run", fake_run)
    args = {"role": "researcher", "task": "one exact task"}
    tool = spawn_subagent_tool(parent)
    with bind_tool_invocation(parent, 2, 0):
        first = await tool.fn(args)
    with bind_tool_invocation(parent, 2, 0):
        replay = await tool.fn(args)

    assert first == replay == "durable answer"
    assert len(calls) == 1
    assert calls[0].startswith("child-")
    assert ctx._spawns_used == 1


@pytest.mark.asyncio
async def test_real_child_loop_uses_child_checkpoint_in_required_mode(
    tmp_path, monkeypatch,
):
    _enable(monkeypatch)
    ctx = _ctx(tmp_path)
    parent = Agent(ctx=ctx, role="orchestrator", brief="root", max_steps=2)
    args = {"role": "researcher", "task": "finish once"}

    with bind_tool_invocation(parent, 0, 0):
        output = await spawn_subagent_tool(parent).fn(args)
    assert output == "done"
    assert ctx.llm.calls == 1

    # The same root step/tool call replays the committed operation result and
    # does not send a second provider request.
    with bind_tool_invocation(parent, 0, 0):
        replay = await spawn_subagent_tool(parent).fn(args)
    assert replay == "done"
    assert ctx.llm.calls == 1


@pytest.mark.asyncio
async def test_real_swarm_commits_children_and_join_result(tmp_path, monkeypatch):
    _enable(monkeypatch)
    ctx = _ctx(tmp_path)
    parent = Agent(ctx=ctx, role="orchestrator", brief="root", max_steps=2)
    args = {
        "agents": [
            {"role": "researcher", "task": "a"},
            {"role": "analyst", "task": "b"},
        ]
    }
    with bind_tool_invocation(parent, 0, 0):
        output = await spawn_swarm_tool(parent).fn(args)
    assert "done" in output
    assert ctx.llm.calls == 2
    assert ctx._spawns_used == 2

    with bind_tool_invocation(parent, 0, 0):
        replay = await spawn_swarm_tool(parent).fn(args)
    assert replay == output
    assert ctx.llm.calls == 2


def test_authority_drift_refuses_child_reconstruction(tmp_path, monkeypatch):
    _enable(monkeypatch)
    ctx = _ctx(tmp_path)
    parent = SimpleNamespace(ctx=ctx, checkpoint_id="orchestrator-0")
    args = {"role": "researcher", "task": "authority-bound"}
    with bind_tool_invocation(parent, 1, 0):
        first = prepare_spawn_operation(parent, "spawn_subagent", args, "subagent")
    assert first is not None
    descriptor = descriptor_for(
        first,
        0,
        role="researcher",
        depth=1,
        task=args["task"],
        domain=None,
        parent_capability=None,
        child_capability=None,
    )
    first.claim([descriptor])

    changed = Capability(principal=descriptor.child_id, deny_tools=frozenset({"shell"}))
    drifted = descriptor_for(
        first,
        0,
        role="researcher",
        depth=1,
        task=args["task"],
        domain=None,
        parent_capability=None,
        child_capability=changed,
    )
    with pytest.raises(Exception, match="authority changed"):
        first.claim([drifted])


def test_durable_reservation_survives_fresh_context(tmp_path, monkeypatch):
    _enable(monkeypatch)
    ctx = _ctx(tmp_path, max_spawns=1)
    parent = SimpleNamespace(ctx=ctx, checkpoint_id="orchestrator-0")
    args = {"role": "researcher", "task": "first"}
    with bind_tool_invocation(parent, 0, 0):
        operation = prepare_spawn_operation(
            parent, "spawn_subagent", args, "subagent"
        )
    assert operation is not None
    descriptor = descriptor_for(
        operation,
        0,
        role="researcher",
        depth=1,
        task="first",
        domain=None,
        parent_capability=None,
        child_capability=None,
    )
    operation.claim([descriptor])

    fresh = _ctx(tmp_path / "other", max_spawns=1)
    fresh.world = WorldModel(tmp_path / "world.db")
    fresh.goal_id = ctx.goal_id
    fresh.episode_id = ctx.episode_id
    fresh.tenant_id = ctx.tenant_id
    fresh._spawns_used = 0
    assert ChildCheckpointStore(fresh).restore_reservation_floor() == 1
    assert fresh._spawns_used == 1

    parent2 = SimpleNamespace(ctx=fresh, checkpoint_id="orchestrator-0")
    args2 = {"role": "researcher", "task": "second"}
    with bind_tool_invocation(parent2, 1, 0):
        second = prepare_spawn_operation(
            parent2, "spawn_subagent", args2, "subagent"
        )
    assert second is not None
    second_descriptor = descriptor_for(
        second,
        0,
        role="researcher",
        depth=1,
        task="second",
        domain=None,
        parent_capability=None,
        child_capability=None,
    )
    with pytest.raises(DurableSpawnCapReached):
        second.claim([second_descriptor])


@pytest.mark.asyncio
async def test_parent_replay_cannot_omit_outstanding_child(tmp_path, monkeypatch):
    _enable(monkeypatch)
    ctx = _ctx(tmp_path)
    parent = Agent(ctx=ctx, role="orchestrator", brief="root", max_steps=1)
    args = {"role": "researcher", "task": "must not disappear"}
    with bind_tool_invocation(parent, 0, 0):
        operation = prepare_spawn_operation(
            parent, "spawn_subagent", args, "subagent"
        )
    assert operation is not None
    descriptor = descriptor_for(
        operation,
        0,
        role="researcher",
        depth=1,
        task=args["task"],
        domain=None,
        parent_capability=None,
        child_capability=None,
    )
    operation.claim([descriptor])

    result = await parent.run()
    assert result.final is None
    assert result.error is not None
    assert result.error.startswith("durability_failed:")
    assert "omitted durable child work" in result.error


def test_exact_replayed_spawn_is_accepted(tmp_path, monkeypatch):
    _enable(monkeypatch)
    ctx = _ctx(tmp_path)
    parent = Agent(ctx=ctx, role="orchestrator", brief="root")
    args = {"role": "researcher", "task": "same"}
    with bind_tool_invocation(parent, 4, 1):
        operation = prepare_spawn_operation(
            parent, "spawn_subagent", args, "subagent"
        )
    assert operation is not None
    descriptor = descriptor_for(
        operation,
        0,
        role="researcher",
        depth=1,
        task="same",
        domain=None,
        parent_capability=None,
        child_capability=None,
    )
    operation.claim([descriptor])
    calls = [
        ToolCall(id="read", name="read_file", input={"path": "x"}),
        ToolCall(id="spawn", name="spawn_subagent", input=args),
    ]
    validate_replayed_operations(parent, 4, calls)


@pytest.mark.parametrize("pending", [False, True])
def test_spawned_process_crash_recovery_is_resume_or_indeterminate(
    tmp_path, pending,
):
    db_path = str(tmp_path / ("pending.db" if pending else "clean.db"))
    env = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (package_root, env.get("PYTHONPATH", "")) if value
    )
    writer = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "crash-writer",
            db_path,
            "1" if pending else "0",
        ],
        env=env,
        check=False,
        timeout=20,
    )
    assert writer.returncode == 23

    reader = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "recovery-reader",
            db_path,
            "1" if pending else "0",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert reader.returncode == 0, reader.stderr
    observed = tuple(json.loads(reader.stdout.strip().splitlines()[-1]))
    if pending:
        assert observed == ("indeterminate", 1)
    else:
        assert observed[0] == "resumed"
        assert observed[1].startswith("child-")
        assert observed[2] == 1


def test_validate_replay_rejects_changed_args(tmp_path, monkeypatch):
    _enable(monkeypatch)
    ctx = _ctx(tmp_path)
    parent = Agent(ctx=ctx, role="orchestrator", brief="root")
    args = {"role": "researcher", "task": "original"}
    with bind_tool_invocation(parent, 6, 0):
        operation = prepare_spawn_operation(
            parent, "spawn_subagent", args, "subagent"
        )
    assert operation is not None
    descriptor = descriptor_for(
        operation,
        0,
        role="researcher",
        depth=1,
        task="original",
        domain=None,
        parent_capability=None,
        child_capability=None,
    )
    operation.claim([descriptor])
    changed = [
        ToolCall(
            id="spawn",
            name="spawn_subagent",
            input={"role": "researcher", "task": "changed"},
        )
    ]
    with pytest.raises(ChildIndeterminateError, match="changed or omitted"):
        validate_replayed_operations(parent, 6, changed)


def test_spawned_process_helpers_are_executable():
    # Keep the adverse helpers at module scope so this file can be launched by
    # a fresh Windows process, not only inherited through POSIX fork state.
    assert _crash_writer.__qualname__ == "_crash_writer"
    assert _recovery_reader.__qualname__ == "_recovery_reader"


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests
    action, database, pending_flag = sys.argv[1:4]
    is_pending = pending_flag == "1"
    if action == "crash-writer":
        _crash_writer(database, is_pending)
    elif action == "recovery-reader":
        class _StdoutQueue:
            @staticmethod
            def put(value) -> None:
                print(json.dumps(value), flush=True)

        _recovery_reader(database, _StdoutQueue(), is_pending)
    else:
        raise SystemExit(2)
