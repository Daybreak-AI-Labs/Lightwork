"""[tools] exposure: what each model turn is told about, per role and per pack.

Measured defect: every turn carried 237 tool schemas (~188 KB of JSON,
~47k tokens) for orchestrator and worker alike, even with deferred loading
on -- only base_registry's marked SaaS long tail deferred. The tool catalog
was ~95% of the first call's input. ``exposure = "core"`` (default) shows a
role-aware core set (plus a pack's ``allow_tools`` when a pack is selected)
and hides the rest behind ``find_tools``; ``"full"`` restores the previous
catalog. Execution semantics are unchanged: run() executes ANY registered
tool the model names, subject to the same ACL / capability / governance.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from maverick.blackboard import Blackboard
from maverick.budget import Budget
from maverick.sandbox import LocalBackend
from maverick.swarm import SwarmContext
from maverick.world_model import WorldModel


@pytest.fixture
def ctx(tmp_path: Path, fake_llm):
    world = WorldModel(tmp_path / "world.db")
    goal_id = world.create_goal("test goal", "")
    return SwarmContext(
        llm=fake_llm, world=world, budget=Budget(max_dollars=1.0),
        blackboard=Blackboard(), sandbox=LocalBackend(workdir=tmp_path),
        goal_id=goal_id, max_depth=2, use_skills=False,
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("MAVERICK_DEFERRED_TOOLS", raising=False)
    monkeypatch.delenv("MAVERICK_TOOL_EXPOSURE", raising=False)


def _agent(ctx, role="orchestrator", **kw):
    from maverick.agent import Agent
    return Agent(ctx, role=role, brief="b", **kw)


def _offered(agent) -> set[str]:
    return {t["name"] for t in agent.tools.to_anthropic()}


def _config(monkeypatch, tools: dict | None = None):
    import maverick.config as config
    cfg = {"tools": tools} if tools is not None else {}
    monkeypatch.setattr(config, "load_config", lambda *a, **k: cfg)


# ------------------------------------------------------------- config knob

def test_exposure_defaults_to_core(monkeypatch):
    from maverick.config import get_tool_exposure
    _config(monkeypatch)
    assert get_tool_exposure() == {"exposure": "core", "core_extra": []}


def test_exposure_reads_config_and_env(monkeypatch):
    from maverick.config import get_tool_exposure
    _config(monkeypatch, {"exposure": "full", "core_extra": ["arxiv", 3, " wikipedia "]})
    assert get_tool_exposure() == {"exposure": "full", "core_extra": ["arxiv", "wikipedia"]}
    monkeypatch.setenv("MAVERICK_TOOL_EXPOSURE", "core")
    assert get_tool_exposure()["exposure"] == "core"


def test_invalid_exposure_falls_back_to_core(monkeypatch):
    from maverick.config import get_tool_exposure
    _config(monkeypatch, {"exposure": "everything", "core_extra": "arxiv"})
    assert get_tool_exposure() == {"exposure": "core", "core_extra": []}


# --------------------------------------------------------- role core sets

def test_core_tool_names_per_role():
    from maverick.tools import CORE_TOOL_NAMES, core_tool_names
    orch = core_tool_names("orchestrator")
    coder = core_tool_names("coder")
    researcher = core_tool_names("researcher")
    worker = core_tool_names("worker")
    kernel = CORE_TOOL_NAMES - {"apply_patch", "ast_edit", "preview_diff", "repo_map", "dep_graph"}
    for core in (orch, coder, researcher, worker):
        assert kernel <= core
        assert 30 <= len(core) <= 45, sorted(core)
    assert orch >= CORE_TOOL_NAMES and coder >= CORE_TOOL_NAMES
    assert {"spawn_subagent", "spawn_swarm", "spawn_specialist", "list_specialists"} <= orch
    assert {"apply_patch", "repo_map", "dep_graph", "preview_diff"} <= coder
    assert {"arxiv", "wikipedia", "citation_verifier"} <= researcher
    assert "apply_patch" not in worker and "arxiv" not in worker
    assert core_tool_names("worker", extra=["scan_finance_anomalies"]) == worker | {"scan_finance_anomalies"}


# ------------------------------------------------------------ per role

def test_core_exposure_is_role_aware_and_lean(ctx, monkeypatch):
    _config(monkeypatch)
    by_role = {r: _offered(_agent(ctx, r)) for r in ("orchestrator", "worker", "researcher", "coder")}
    for role, names in by_role.items():
        assert len(names) <= 45, (role, sorted(names))
        assert "find_tools" in names
        assert {"read_file", "write_file", "shell", "ask_user", "http_fetch",
                "kv_memory", "spawn_subagent"} <= names, role
        # Niche tools no longer ride every turn.
        for niche in ("scan_finance_anomalies", "embedded_device", "wal_contention",
                      "memleak_quarantine", "chaos_gameday", "voice_cloning_consent"):
            assert niche not in names, (role, niche)
    assert "spawn_swarm" in by_role["orchestrator"]
    assert "apply_patch" in by_role["coder"] and "apply_patch" not in by_role["worker"]
    assert "arxiv" in by_role["researcher"] and "arxiv" not in by_role["coder"]


def test_core_extra_pins_a_tool_visible(ctx, monkeypatch):
    _config(monkeypatch, {"core_extra": ["scan_finance_anomalies"]})
    assert "scan_finance_anomalies" in _offered(_agent(ctx, "worker"))


def test_deferred_tool_is_discoverable_and_executable(ctx, monkeypatch):
    _config(monkeypatch)
    agent = _agent(ctx, "worker")
    registered = {t.name for t in agent.tools.all()}
    assert "chaos_gameday" in registered and "chaos_gameday" not in _offered(agent)
    out = agent.tools.get("find_tools").fn({"query": "chaos gameday fault injection drill"})
    assert "chaos_gameday" in out
    assert "chaos_gameday" in _offered(agent)
    # Executable regardless of exposure -- run() resolves by registry, not catalog.
    assert agent.tools.get("chaos_gameday").name == "chaos_gameday"


def test_full_exposure_restores_previous_catalog(ctx, monkeypatch):
    _config(monkeypatch, {"exposure": "full"})
    agent = _agent(ctx, "worker")
    names = _offered(agent)
    # Previous behaviour: everything except base_registry's marked long tail.
    assert names == {t.name for t in agent.tools.all()} - set(agent.tools.deferrable_names)
    assert len(names) > 200 and "find_tools" in names
    assert "chaos_gameday" in names and "acumatica" not in names


def test_deferred_tools_off_exposes_everything(ctx, monkeypatch):
    _config(monkeypatch)
    monkeypatch.setenv("MAVERICK_DEFERRED_TOOLS", "0")
    agent = _agent(ctx, "worker")
    assert _offered(agent) == {t.name for t in agent.tools.all()}


# ------------------------------------------------------------- per pack

def _pack_agent(ctx, name, monkeypatch):
    # Deployment validation needs the workflow's tools registered; the pack's
    # exposure is what's under test here, so let web_search register.
    import maverick.config as config
    from maverick.domain import agent_from_profile, builtin_domains
    monkeypatch.setattr(config, "get_capabilities", lambda: {"web_search": True, "deferred_tools": True})
    return agent_from_profile(builtin_domains()[name], ctx, "task")


@pytest.mark.parametrize("pack", ["legal_nda_desk", "sec_phishing", "privacy_compliance"])
def test_pack_sees_its_allow_tools_and_nothing_else(ctx, monkeypatch, pack):
    from maverick.capability import COORDINATION_TOOLS
    from maverick.domain import builtin_domains
    _config(monkeypatch)
    profile = builtin_domains()[pack]
    agent = _pack_agent(ctx, pack, monkeypatch)
    names = _offered(agent)
    registered = {t.name for t in agent.tools.all()}
    # allow_tools visible in full (those that registered; the SaaS long tail
    # a suite's data grounding adds to the grant stays deferred) ...
    assert (set(profile.allow_tools) & registered) - agent.tools.deferrable_names <= names
    # ... plus the coordination floor the capability always permits; nothing else.
    extra = names - set(profile.allow_tools) - COORDINATION_TOOLS
    assert not extra, sorted(extra)
    assert len(names) <= 20, sorted(names)
    for denied in profile.deny_tools:
        assert denied not in names
    # A denied tool is not discoverable either.
    assert all(t.name not in profile.deny_tools for t in agent.tools.deferred_tools())
    assert agent.tools.activate(list(profile.deny_tools)) == []
    # Every offered tool is one the pack's capability lets it call.
    assert all(agent.capability.permits(n) for n in names)


def test_pack_without_allow_list_uses_role_core(ctx, monkeypatch):
    from maverick.capability import Capability
    _config(monkeypatch)
    cap = Capability(principal="agent:x", deny_tools=frozenset({"shell", "chaos_gameday"}))
    agent = _agent(ctx, "worker", capability=cap)
    names = _offered(agent)
    assert "read_file" in names and "shell" not in names
    assert "chaos_gameday" not in {t.name for t in agent.tools.deferred_tools()}
    assert agent.tools.activate(["chaos_gameday"]) == []


def test_subagent_inherits_exposure(ctx, monkeypatch):
    from maverick.capability import Capability
    _config(monkeypatch)
    cap = Capability(principal="agent:root", deny_tools=frozenset({"chaos_gameday"}))
    parent = _agent(ctx, "orchestrator", capability=cap)
    from maverick.agent import Agent
    child_cap = cap.attenuate(principal="agent:coder-1")
    child = Agent(ctx, role="coder", brief="c", depth=1, parent=parent, capability=child_cap)
    names = _offered(child)
    assert len(names) <= 45 and "apply_patch" in names and "find_tools" in names
    assert "chaos_gameday" not in {t.name for t in child.tools.deferred_tools()}
    _config(monkeypatch, {"exposure": "full"})
    child_full = Agent(ctx, role="coder", brief="c", depth=1, parent=parent, capability=child_cap)
    assert len(_offered(child_full)) > 200
