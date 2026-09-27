"""Production twin: record a goal's tool results, replay them to a fresh goal
with nothing live touched."""
from __future__ import annotations

import asyncio

import pytest
from maverick import config
from maverick.immune import _ledger, twin


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")


@pytest.fixture
def recording_on(monkeypatch):
    monkeypatch.setattr(config, "get_immune",
                        lambda: {"canaries": False, "twin_record": True})


def _live(calls):
    async def run(tool, args):
        calls.append((tool, args))
        return f"{tool}:{args.get('q', '')}:live{len(calls)}"
    return run


def test_recording_is_off_by_default():
    calls: list = []
    asyncio.run(_d(twin, 7, "web_search", {"q": "a"}, _live(calls)))
    assert calls and not twin.recording_path(7).exists()


def test_record_then_replay_serves_production_results_in_order(recording_on):
    calls: list = []
    for q in ("a", "a", "b"):
        asyncio.run(_d(twin, 7, "web_search", {"q": q}, _live(calls)))
    asyncio.run(_d(twin, 7, "spawn_subagent", {"role": "r"}, _live(calls)))
    recorded = twin.load(7)
    assert [r["tool"] for r in recorded] == ["web_search"] * 3  # coordination not recorded

    replay = twin.Replay(recorded)
    live_during_replay: list = []

    async def _twin_run():
        with twin.replaying(replay):
            run = _live(live_during_replay)
            # spawned agents run in their own tasks; the replay must follow them
            return await asyncio.gather(
                _d(twin, 99, "web_search", {"q": "a"}, run),
                _d(twin, 99, "web_search", {"q": "a"}, run),
                _d(twin, 99, "web_search", {"q": "b"}, run),
                _d(twin, 99, "email", {"to": "x"}, run),        # never happened
                _d(twin, 99, "spawn_subagent", {"role": "r"}, run),  # coordination
            )

    out = asyncio.run(_twin_run())
    assert out[:3] == ["web_search:a:live1", "web_search:a:live2", "web_search:b:live3"]
    assert out[3].startswith("ERROR: email failed")
    assert live_during_replay == [("spawn_subagent", {"role": "r"})]  # only coordination ran
    assert replay.report() == {"recorded": 3, "served": 3, "unserved": 0,
                               "unmatched": [{"tool": "email", "args": {"to": "x"}}]}
    assert not twin.recording_path(99).exists()  # a twin is never itself recorded


def test_ask_user_is_replayed_not_sent(recording_on):
    calls: list = []
    asyncio.run(_d(twin, 3, "ask_user", {"q": "dates?"}, _live(calls)))
    replay = twin.Replay(twin.load(3))
    after: list = []

    async def _go():
        with twin.replaying(replay):
            return await _d(twin, 4, "ask_user", {"q": "dates?"}, _live(after))

    assert asyncio.run(_go()) == "ask_user:dates?:live1" and after == []


def test_sealed_recording_hides_output_and_is_bound_to_the_goal(recording_on, monkeypatch):
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    asyncio.run(_d(twin, 5, "sql_query", {"q": "payroll"}, _live([])))
    raw = twin.recording_path(5).read_text()
    assert "payroll" not in raw and "sealed" in raw
    assert twin.load(5)[0]["output"] == "sql_query:payroll:live1"
    # moved to another goal's recording, the blob no longer authenticates
    twin.recording_path(6).write_text(raw)
    with pytest.raises(Exception):  # noqa: B017 -- any auth failure is correct
        twin.load(6)


def test_recording_failure_never_breaks_the_call(recording_on, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(twin, "record", boom)
    assert asyncio.run(_d(twin, 1, "web_search", {"q": "z"}, _live([]))).endswith("live1")


def test_replay_goal_runs_a_faithful_twin_and_ledgers_counts_only(recording_on, tmp_path):
    from maverick.world_model import WorldModel
    world = WorldModel(tmp_path / "world.db")
    gid = world.create_goal("Reconcile March AP", "vendor ledger vs bank", domain="finance")
    asyncio.run(_d(twin, gid, "sql_query", {"q": "ap"}, _live([])))

    def run(twin_gid):
        # the runner executes inside the replay context
        return asyncio.run(_d(twin, twin_gid, "sql_query", {"q": "ap"}, _live([])))

    report = twin.replay_goal(world, gid, run)
    twin_goal = world.get_goal(report["twin_goal"])
    assert (twin_goal.title, twin_goal.description, twin_goal.domain) == (
        "Reconcile March AP", "vendor ledger vs bank", "finance")  # nothing marks it a test
    assert report["twin_status"] == "sql_query:ap:live1" and report["served"] == 1
    assert _ledger.read(twin.replays_path())[0]["unmatched"] == 0
    with pytest.raises(LookupError):
        twin.replay_goal(world, report["twin_goal"], run)  # twins have no recording


@pytest.mark.asyncio
async def test_agent_tool_execution_routes_through_the_twin(recording_on, tmp_path):
    from maverick.agent import Agent
    from maverick.blackboard import Blackboard
    from maverick.budget import Budget
    from maverick.sandbox import LocalBackend
    from maverick.swarm import SwarmContext
    from maverick.tools import Tool
    from maverick.world_model import WorldModel

    world = WorldModel(tmp_path / "world.db")
    ctx = SwarmContext(llm=None, world=world, budget=Budget(max_dollars=1.0),
                       blackboard=Blackboard(), sandbox=LocalBackend(workdir=tmp_path),
                       goal_id=world.create_goal("g", ""), use_skills=False)
    agent = Agent(ctx=ctx, role="coder", brief="b")
    agent.capability = None
    hits: list = []
    agent.tools.register(Tool(
        name="ping", description="ping", input_schema={"type": "object", "properties": {}},
        fn=lambda args: hits.append(1) or f"pong{len(hits)}"))

    assert "pong1" in await agent._run_tool("ping", {})
    with twin.replaying(twin.Replay(twin.load(ctx.goal_id))):
        assert "pong1" in await agent._run_tool("ping", {})
    assert hits == [1]  # the replayed call did not execute the tool


def _d(twin, goal_id, tool, args, run):
    return twin.dispatch(goal_id, tool, args, lambda: run(tool, args))
