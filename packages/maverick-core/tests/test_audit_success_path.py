"""Success-path audit: a completed run records GOAL_START/GOAL_END (and tool
calls record TOOL_CALL/TOOL_RESULT) on the audit chain -- not just the denial
events. Regression guard for the "tamper-evident log only captured denials" gap.
"""
from __future__ import annotations

import pytest
from maverick.budget import Budget
from maverick.orchestrator import run_goal
from maverick.sandbox import LocalBackend
from maverick.world_model import WorldModel


@pytest.fixture
def _audit_home(monkeypatch, tmp_path):
    # Point the default audit log at a temp home and drop the cached singleton so
    # record() rebuilds it against tmp instead of the real ~/.maverick.
    monkeypatch.setattr("maverick.paths.maverick_home", lambda: tmp_path / "home")
    monkeypatch.setattr("maverick.audit.writer._default", None, raising=False)
    return tmp_path


@pytest.mark.asyncio
async def test_run_goal_emits_goal_start_and_end(
    _audit_home, tmp_path, fake_llm, make_llm_response,
):
    fake_llm.scripted = [
        make_llm_response(text="FINAL: the answer is 42"),
        make_llm_response(
            text='{"confidence": 0.95, "accepts": true, "critique": "ok", "issues": []}',
        ),
        make_llm_response(text="FINAL: (no skill)"),
    ]
    world = WorldModel(path=tmp_path / "world.db")
    gid = world.create_goal("compute the answer", "trivial")

    await run_goal(
        llm=fake_llm, world=world, budget=Budget(max_dollars=1.0),
        goal_id=gid, sandbox=LocalBackend(workdir=tmp_path), max_depth=1,
    )

    from maverick.audit.reader import iter_events

    events = [e for e in iter_events(all_days=True) if e.get("goal_id") == gid]
    kinds = {e["kind"] for e in events}
    assert "goal_start" in kinds
    assert "goal_end" in kinds
    end = next(e for e in events if e["kind"] == "goal_end")
    assert end["status"] == "succeeded"


def _events_for_goal(goal_id: int) -> list[dict]:
    from maverick.audit.reader import iter_events

    return [e for e in iter_events(all_days=True) if e.get("goal_id") == goal_id]


@pytest.mark.asyncio
async def test_goal_success_audit_rows_are_subject_scrubbable(
    _audit_home, tmp_path, fake_llm, make_llm_response,
):
    fake_llm.scripted = [
        make_llm_response(text="FINAL: PII_MARKER_ALICE_123 result"),
        make_llm_response(
            text='{"confidence": 0.95, "accepts": true, "critique": "ok", "issues": []}',
        ),
        make_llm_response(text="FINAL: (no skill)"),
    ]
    world = WorldModel(path=tmp_path / "world.db")
    gid = world.create_goal("PII_MARKER_ALICE_123 title", "PII_MARKER_ALICE_123 desc")

    await run_goal(
        llm=fake_llm, world=world, budget=Budget(max_dollars=1.0),
        goal_id=gid, sandbox=LocalBackend(workdir=tmp_path), max_depth=1,
        channel="slack", user_id="u123",
    )

    before = [e for e in _events_for_goal(gid) if e["kind"] in {"goal_start", "goal_end"}]
    assert {e["kind"] for e in before} == {"goal_start", "goal_end"}
    assert all(e["channel"] == "slack" and e["user_id"] == "u123" for e in before)

    from maverick.audit.erase import scrub_user

    matched, _scanned = scrub_user("slack", "u123")

    assert matched >= 2
    after = [e for e in _events_for_goal(gid) if e["kind"] in {"goal_start", "goal_end"}]
    if not after:
        from maverick.audit.reader import iter_events
        after = [e for e in iter_events(all_days=True) if e["kind"] in {"goal_start", "goal_end"}]
    assert len(after) == 2
    assert all(e["user_id"] == "[REDACTED]" for e in after)
    assert not any("PII_MARKER_ALICE_123" in str(e) for e in after)


def test_tool_success_audit_rows_are_subject_scrubbable(_audit_home, tmp_path, fake_llm):
    from maverick.agent import Agent
    from maverick.audit.erase import scrub_user
    from maverick.blackboard import Blackboard
    from maverick.sandbox import LocalBackend
    from maverick.swarm import SwarmContext

    world = WorldModel(path=tmp_path / "world.db")
    gid = world.create_goal("tool audit", "subject fields")
    ctx = SwarmContext(
        llm=fake_llm,
        world=world,
        budget=Budget(max_dollars=1.0),
        blackboard=Blackboard(),
        sandbox=LocalBackend(workdir=tmp_path),
        goal_id=gid,
        channel="slack",
        user_id="u123",
    )
    agent = Agent(ctx, role="worker", brief="test")

    agent._audit_tool_event(
        "tool_call", name="demo", input_summary="PII_MARKER_ALICE_123 input",
    )
    agent._audit_tool_event(
        "tool_result", name="demo", status="ok",
        output_summary="PII_MARKER_ALICE_123 output",
    )

    before = [e for e in _events_for_goal(gid) if e["kind"] in {"tool_call", "tool_result"}]
    assert {e["kind"] for e in before} == {"tool_call", "tool_result"}
    assert all(e["channel"] == "slack" and e["user_id"] == "u123" for e in before)

    matched, _scanned = scrub_user("slack", "u123")

    assert matched == 2
    after = [e for e in _events_for_goal(gid) if e["kind"] in {"tool_call", "tool_result"}]
    if not after:
        from maverick.audit.reader import iter_events
        after = [e for e in iter_events(all_days=True) if e["kind"] in {"tool_call", "tool_result"}]
    assert len(after) == 2
    assert all(e["user_id"] == "[REDACTED]" for e in after)
    assert not any("PII_MARKER_ALICE_123" in str(e) for e in after)
