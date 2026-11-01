"""#612 agent-loop audit: mid-dispatch budget trips must not orphan a
tool_use, and reserved spawn slots must be released when a child fails.

A `tool_use` block in an assistant message MUST be answered by a matching
`tool_result` in the immediately following user message, or Anthropic 400s
the next request. `budget.record_tool_call()` calls `check()` and can raise
`BudgetExceeded` *after* the assistant turn (with its tool_use blocks) is
already in the message history -- so the loop has to answer every pending
tool_use before it unwinds.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from maverick.agent import Agent
from maverick.blackboard import Blackboard
from maverick.budget import Budget, BudgetExceeded
from maverick.llm import LLMResponse, ToolCall
from maverick.sandbox import LocalBackend
from maverick.swarm import SwarmContext
from maverick.world_model import WorldModel


def _assert_no_orphan_tool_use(messages: list[dict]) -> None:
    """Every tool_use id in an assistant message has a tool_result right after."""
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        tool_use_ids = [
            b["id"] for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        if not tool_use_ids:
            continue
        # The next message must answer all of them.
        assert i + 1 < len(messages), (
            f"assistant tool_use {tool_use_ids} has no following message"
        )
        nxt = messages[i + 1]
        assert nxt.get("role") == "user", f"tool_use {tool_use_ids} not answered"
        answered = {
            b["tool_use_id"] for b in nxt["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result"
        }
        assert set(tool_use_ids) <= answered, (
            f"orphaned tool_use ids: {set(tool_use_ids) - answered}"
        )


class _CapturingLLM:
    """Records the (live) messages list it is handed each turn, so a test can
    inspect the history after the run unwinds."""

    model = "fake:test"

    def __init__(self, scripted: list[LLMResponse]):
        self.scripted = list(scripted)
        self.last_messages: list[dict] | None = None

    async def complete_async(self, *, system, messages, **kwargs) -> LLMResponse:
        self.last_messages = messages  # same object the loop mutates
        if self.scripted:
            return self.scripted.pop(0)
        return LLMResponse(
            text="FINAL: done", thinking=None, tool_calls=[],
            stop_reason="end_turn",
        )


def _ctx(tmp_path: Path, llm, **budget_kw) -> SwarmContext:
    world = WorldModel(tmp_path / "world.db")
    goal_id = world.create_goal("test goal", "")
    return SwarmContext(
        llm=llm,
        world=world,
        budget=Budget(max_dollars=1.0, **budget_kw),
        blackboard=Blackboard(),
        sandbox=LocalBackend(workdir=tmp_path),
        goal_id=goal_id,
        max_depth=2,
        use_skills=False,
    )


class TestBudgetTripDoesNotOrphanToolUse:
    @pytest.mark.asyncio
    async def test_serial_path_budget_trip_answers_pending(self, tmp_path):
        # Two NON-parallel-safe tool calls in one turn -> serial path. Cap
        # tool calls at 1 so the SECOND record_tool_call() raises mid-turn,
        # after the first tool already produced a result.
        llm = _CapturingLLM([
            LLMResponse(
                text="acting",
                thinking=None,
                tool_calls=[
                    ToolCall(id="a", name="shell", input={"cmd": "echo 1"}),
                    ToolCall(id="b", name="shell", input={"cmd": "echo 2"}),
                ],
                stop_reason="tool_use",
            ),
        ])
        ctx = _ctx(tmp_path, llm, max_tool_calls=1)
        agent = Agent(ctx=ctx, role="researcher", brief="...")
        with pytest.raises(BudgetExceeded):
            await agent.run()
        assert llm.last_messages is not None
        _assert_no_orphan_tool_use(llm.last_messages)

    @pytest.mark.asyncio
    async def test_serial_tool_raise_mid_run_answers_pending(self, tmp_path, monkeypatch):
        # A stateful serial tool (e.g. spawn_subagent whose child shares this
        # Budget) can raise BudgetExceeded DURING execution, after its tool_use
        # is already in the turn. The serial path must answer the pending
        # tool_use before unwinding so no orphan reaches a resume/parent.
        llm = _CapturingLLM([
            LLMResponse(
                text="acting",
                thinking=None,
                tool_calls=[ToolCall(id="a", name="shell", input={"cmd": "echo 1"})],
                stop_reason="tool_use",
            ),
        ])
        ctx = _ctx(tmp_path, llm)  # generous tool-call cap; the raise is in _run_tool
        agent = Agent(ctx=ctx, role="researcher", brief="...")

        async def _boom(name, args):
            raise BudgetExceeded("child tripped the shared budget mid-run")

        monkeypatch.setattr(agent, "_run_tool", _boom)
        with pytest.raises(BudgetExceeded):
            await agent.run()
        assert llm.last_messages is not None
        _assert_no_orphan_tool_use(llm.last_messages)

    @pytest.mark.asyncio
    async def test_patch_validated_resets_on_new_final(self, tmp_path):
        # #612: _patch_validated was sticky for the whole run; a new FINAL must
        # reset it so a later genuinely-different FINAL re-validates its patch.
        llm = _CapturingLLM([
            LLMResponse(text="FINAL: done", thinking=None, tool_calls=[],
                        stop_reason="end_turn"),
        ])
        ctx = _ctx(tmp_path, llm)
        agent = Agent(ctx=ctx, role="researcher", brief="...")
        agent._patch_validated = True  # simulate a prior rejected patch
        await agent.run()
        assert agent._patch_validated is False

    @pytest.mark.asyncio
    async def test_apply_check_reject_loop_stops_at_revision_cap(
        self, tmp_path, monkeypatch,
    ):
        # The #612 fix above resets `_patch_validated` on every new
        # FINAL, which also silently defeated the "one retry max" every
        # reject site in the apply-check block was relying on that flag
        # for -- each revised FINAL looked like a first attempt again, so
        # an agent that keeps submitting an unapplyable patch looped until
        # `max_steps` instead of stopping after one retry.
        # `_patch_revision_attempts` (never reset per FINAL) must cap it.
        monkeypatch.setenv("MAVERICK_CODING_MODE", "1")
        monkeypatch.delenv("MAVERICK_FAIL_TO_PASS", raising=False)
        monkeypatch.delenv("MAVERICK_PASS_TO_PASS", raising=False)

        # tmp_path is deliberately NOT a git repo, so `validate_patch`
        # always returns valid=False ("workdir is not a git repository")
        # no matter what the diff says -- a guaranteed, deterministic
        # apply-check rejection on every attempt.
        final_text = (
            "FINAL: fixing it\n"
            "```diff\n"
            "--- a/x\n"
            "+++ b/x\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
            "```\n"
        )
        llm = _CapturingLLM([
            LLMResponse(text=final_text, thinking=None, tool_calls=[],
                        stop_reason="end_turn"),
            LLMResponse(text=final_text, thinking=None, tool_calls=[],
                        stop_reason="end_turn"),
            LLMResponse(text=final_text, thinking=None, tool_calls=[],
                        stop_reason="end_turn"),
        ])
        ctx = _ctx(tmp_path, llm)
        agent = Agent(ctx=ctx, role="researcher", brief="...")

        result = await agent.run()

        assert result.final is not None
        # First FINAL: rejected, one revision requested (attempts -> 1).
        # Second FINAL: the cap is already hit, so the apply-check block
        # is skipped entirely and the FINAL is accepted as-is. The third
        # scripted response must never be consumed.
        assert agent._patch_revision_attempts == 1
        assert len(llm.scripted) == 1

    @pytest.mark.asyncio
    async def test_parallel_path_budget_trip_answers_pending(self, tmp_path):
        # Two parallel-safe reads -> parallel path records both up front; the
        # second record_tool_call() raises before any result is appended.
        f = tmp_path / "f.txt"
        f.write_text("hello")
        llm = _CapturingLLM([
            LLMResponse(
                text="reading",
                thinking=None,
                tool_calls=[
                    ToolCall(id="a", name="read_file", input={"path": "f.txt"}),
                    ToolCall(id="b", name="read_file", input={"path": "f.txt"}),
                ],
                stop_reason="tool_use",
            ),
        ])
        ctx = _ctx(tmp_path, llm, max_tool_calls=1)
        agent = Agent(ctx=ctx, role="researcher", brief="...")
        with pytest.raises(BudgetExceeded):
            await agent.run()
        assert llm.last_messages is not None
        _assert_no_orphan_tool_use(llm.last_messages)


class TestReleaseSpawns:
    def test_release_frees_capacity(self, tmp_path):
        ctx = _ctx(tmp_path, _CapturingLLM([]))
        ctx.max_total_spawns = 2
        assert ctx.try_reserve_spawns(2) is True
        # Cap reached: no more slots.
        assert ctx.try_reserve_spawns(1) is False
        # A child failed -> release its slot -> capacity frees up again.
        ctx.release_spawns(1)
        assert ctx.try_reserve_spawns(1) is True

    def test_release_clamps_at_zero(self, tmp_path):
        ctx = _ctx(tmp_path, _CapturingLLM([]))
        ctx.release_spawns(5)  # nothing reserved
        assert ctx._spawns_used == 0

    @pytest.mark.asyncio
    async def test_spawn_subagent_releases_on_child_raise(self, tmp_path):
        from maverick.tools.spawn import spawn_subagent_tool

        ctx = _ctx(tmp_path, _CapturingLLM([]))
        ctx.max_total_spawns = 1
        parent = Agent(ctx=ctx, role="orchestrator", brief="root")

        # Force the child's run() to raise (a genuine failure, not a return).
        async def _boom(self):
            raise RuntimeError("child blew up")

        from maverick.agent import Agent as _A
        orig_run = _A.run
        _A.run = _boom
        try:
            tool = spawn_subagent_tool(parent)
            with pytest.raises(RuntimeError):
                await tool.fn({"role": "researcher", "task": "do x"})
        finally:
            _A.run = orig_run

        # The failed child's reserved slot was returned.
        assert ctx._spawns_used == 0
        assert ctx.try_reserve_spawns(1) is True

    @pytest.mark.asyncio
    async def test_spawn_swarm_releases_failed_children_only(self, tmp_path):
        from maverick.tools.spawn import spawn_swarm_tool

        ctx = _ctx(tmp_path, _CapturingLLM([]))
        ctx.max_total_spawns = 4
        parent = Agent(ctx=ctx, role="orchestrator", brief="root")

        from maverick.agent import Agent as _A
        from maverick.agent import AgentResult
        calls = {"n": 0}

        async def _mixed(self):
            calls["n"] += 1
            # First child errors (raises), second returns a normal result.
            if calls["n"] == 1:
                raise RuntimeError("child 1 failed")
            return AgentResult(final="ok", role=self.role, name=self.name)

        orig_run = _A.run
        _A.run = _mixed
        try:
            tool = spawn_swarm_tool(parent)
            out = await tool.fn({"agents": [
                {"role": "researcher", "task": "a"},
                {"role": "researcher", "task": "b"},
            ]})
        finally:
            _A.run = orig_run

        assert "EXCEPTION" in out  # the raised child surfaced
        # 2 reserved, 1 failed -> only the failed slot released; the
        # successful child legitimately keeps its slot.
        assert ctx._spawns_used == 1


class TestToolDispatchWallClockGuard:
    @pytest.mark.asyncio
    async def test_hung_tool_is_aborted_at_the_wall_cap(self, tmp_path, monkeypatch):
        # A tool with no timeout of its own (a plugin, an MCP server,
        # a browser/file-watcher wait) previously had no equivalent to
        # _complete_guarded's wall-clock/killswitch polling -- it could hold
        # the goal 'active' and ignore `maverick halt` indefinitely. A never-
        # returning tool under a short wall cap must now abort promptly
        # instead of hanging past it.
        import asyncio
        import time

        from maverick.tools import Tool

        hung = {"cancelled": False}

        async def _never_returns(args: dict) -> str:
            try:
                await asyncio.sleep(3600)
                return "should never get here"
            except asyncio.CancelledError:
                hung["cancelled"] = True
                raise

        llm = _CapturingLLM([
            LLMResponse(
                text="acting", thinking=None,
                tool_calls=[ToolCall(id="a", name="hang", input={})],
                stop_reason="tool_use",
            ),
        ])
        ctx = _ctx(tmp_path, llm, max_wall_seconds=1.0)
        agent = Agent(ctx=ctx, role="researcher", brief="...")
        agent.tools.register(Tool(
            name="hang", description="never returns",
            input_schema={"type": "object", "properties": {}},
            fn=_never_returns,
        ))

        # Exercise expiration during dispatch, independently of cold setup or
        # scheduler delays before it. The actual guard still runs against the
        # event loop's real monotonic deadline and cancels the actual tool.
        armed_at = None
        guarded = agent._await_wall_clock_guarded

        def elapsed():
            return 0.0 if armed_at is None else time.monotonic() - armed_at

        async def arm_at_tool_dispatch(make_coro, *, in_flight_note):
            nonlocal armed_at
            if in_flight_note == "mid-tool-execution":
                armed_at = time.monotonic()
            return await guarded(make_coro, in_flight_note=in_flight_note)

        monkeypatch.setattr(ctx.budget, "elapsed", elapsed)
        monkeypatch.setattr(agent, "_await_wall_clock_guarded", arm_at_tool_dispatch)
        with pytest.raises(BudgetExceeded, match="mid-tool-execution"):
            await agent.run()

        # Aborted near the cap, not left hanging toward the 3600s sleep.
        assert armed_at is not None
        assert time.monotonic() - armed_at < 5.0
        assert hung["cancelled"] is True
        assert llm.last_messages is not None
        _assert_no_orphan_tool_use(llm.last_messages)

    @pytest.mark.asyncio
    async def test_wall_cap_exhausted_during_setup_stops_before_dispatch(self, tmp_path):
        # A startup that consumes the cap has a different, valid outcome from
        # a mid-tool timeout: the turn boundary returns a structured stop.
        llm = _CapturingLLM([])
        ctx = _ctx(tmp_path, llm, max_wall_seconds=1.0)
        agent = Agent(ctx=ctx, role="researcher", brief="...")
        ctx.budget._started_monotonic -= 2.0

        result = await agent.run()

        assert result.stop_kind == "budget"
        assert "wall" in result.stop_detail
        assert llm.last_messages is None
        assert ctx.budget.tool_calls == 0
