"""Regulatory compiler: obligations become live gates, and the gate proves it."""
from __future__ import annotations

import dataclasses

import pytest
from maverick import regulatory_compiler as rc
from maverick.capability import Capability
from maverick.governance import Decision, Policy, evaluate
from maverick.tools import Tool

ALL = ["gdpr", "eu_ai_act", "hipaa"]


def _activate(monkeypatch, keys):
    monkeypatch.setattr(rc, "active_keys", lambda: list(keys))


# ---- the library ------------------------------------------------------------

def test_every_bound_tool_is_a_classified_tool():
    """A renamed tool must not silently turn an obligation into a no-op."""
    from maverick.safety.tool_risk import tool_risk_is_classified
    for obligations in rc.REGULATIONS.values():
        for ob in obligations:
            for name in ob.actions | ob.deny_tools:
                assert tool_risk_is_classified(name), f"{ob.id} binds unknown tool {name!r}"


def test_the_shipped_library_compiles_to_non_vacuous_gates():
    assert rc.verify(ALL) == []


def test_gdpr_compiles_into_the_chokepoint_policy():
    pol = rc.compile_obligations(["gdpr"]).policy
    assert {"sql_query", "email"} <= pol.require_human_actions
    assert pol.require_human_min_risk == "high"
    assert evaluate("email", policy=pol).decision == Decision.REQUIRE_HUMAN
    assert evaluate("shell", risk="high", policy=pol).decision == Decision.REQUIRE_HUMAN
    assert evaluate("read_file", risk="low", policy=pol).decision == Decision.ALLOW


def test_hipaa_is_an_attenuation_not_a_policy_rule():
    compiled = rc.compile_obligations(["hipaa"])
    assert compiled.policy.is_empty()
    assert "email" in compiled.deny_tools


def test_unknown_keys_are_ignored_and_deduplicated():
    compiled = rc.compile_obligations(["GDPR", "gdpr", "nope"])
    assert [o.regulation for o in compiled.obligations].count("GDPR") == 3


def test_deny_wins_over_require_human_for_the_same_action(monkeypatch):
    deny = rc.Obligation("t-deny", "T", "1", "", rc.EFFECT_DENY, actions=frozenset({"email"}))
    ask = rc.Obligation("t-ask", "T", "2", "", rc.EFFECT_REQUIRE_HUMAN,
                        actions=frozenset({"email"}))
    monkeypatch.setitem(rc.REGULATIONS, "t", (deny, ask))
    pol = rc.compile_obligations(["t"]).policy
    assert "email" in pol.deny_actions
    assert "email" not in pol.require_human_actions
    assert evaluate("email", policy=pol).decision == Decision.DENY


# ---- tagging: proof the gate was live ---------------------------------------

def test_audit_tags_trace_a_verdict_back_to_its_obligation(monkeypatch):
    _activate(monkeypatch, ["gdpr"])
    assert rc.audit_tags("email", "require_human_actions") == {
        "obligation_id": "gdpr-art32-outbound-transfer",
        "regulation": "GDPR", "article": "Art. 32"}
    # A floor-level obligation tags a floor-level rule.
    assert rc.audit_tags("shell", "require_human_min_risk")["obligation_id"] \
        == "gdpr-art22-automated-decision"
    # The rule must belong to the obligation's effect: a capability denial of
    # an outbound tool is not GDPR Art. 32's doing.
    assert rc.audit_tags("email", "capability") == {}
    assert rc.audit_tags("read_file", "require_human_actions") == {}


def test_obligations_checked_lists_every_covering_obligation(monkeypatch):
    _activate(monkeypatch, ALL)
    checked = rc.obligations_checked("email")
    assert "gdpr-art32-outbound-transfer" in checked
    assert "hipaa-164-502b-minimum-necessary" in checked
    assert "gdpr-art17-erasure-governed-path" not in checked
    assert rc.checked_tags("email") == {"obligations_checked": checked}


def test_nothing_active_means_rows_are_untouched(monkeypatch):
    _activate(monkeypatch, [])
    assert rc.audit_tags("email", "require_human_actions") == {}
    assert rc.checked_tags("email") == {}


def test_tagging_never_raises(monkeypatch):
    monkeypatch.setattr(rc, "_active", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rc.audit_tags("email", "deny_actions") == {}
    assert rc.checked_tags("email") == {}


# ---- attenuation: never widens ----------------------------------------------

def test_attenuation_removes_the_tools_and_never_widens(monkeypatch):
    _activate(monkeypatch, ["hipaa"])
    cap = Capability(principal="p", allow_tools=frozenset({"email", "read_file"}),
                     max_risk="high")
    narrowed = rc.attenuate(cap)
    assert not narrowed.permits("email")
    assert narrowed.permits("read_file")
    assert narrowed.allow_tools <= cap.allow_tools
    assert narrowed.deny_tools >= cap.deny_tools
    # A child derived from the narrowed grant cannot get the tool back.
    child = narrowed.attenuate(principal="agent:child", allow={"email", "read_file"})
    assert not child.permits("email")


def test_attenuation_is_identity_when_nothing_is_active(monkeypatch):
    _activate(monkeypatch, [])
    cap = Capability(principal="p")
    assert rc.attenuate(cap) is cap


def test_capability_from_config_applies_the_attenuation(monkeypatch):
    _activate(monkeypatch, ["hipaa"])
    monkeypatch.setattr("maverick.config.load_config", lambda *a, **k: {})
    from maverick.capability import capability_from_config
    cap = capability_from_config("user:local")
    assert not cap.permits("email")
    assert cap.permits("read_file")


# ---- anti-vacuity ------------------------------------------------------------

def test_verify_flags_an_obligation_bound_to_a_tool_that_does_not_exist(monkeypatch):
    """Fault injection: the clean library passes, a corrupted copy is caught."""
    good = rc.REGULATIONS["gdpr"]
    assert rc.verify(["gdpr"]) == []
    bad = tuple(dataclasses.replace(o, actions=frozenset({"tool_that_does_not_exist"}))
                if o.id.endswith("outbound-transfer") else o for o in good)
    monkeypatch.setitem(rc.REGULATIONS, "gdpr", bad)
    problems = rc.verify(["gdpr"])
    assert any("unknown tool 'tool_that_does_not_exist'" in p for p in problems)


def test_verify_flags_a_dead_overlay(monkeypatch):
    """If the chokepoint stopped honouring the compiled policy, the gate says so."""
    from maverick import governance
    from maverick.governance import Verdict
    monkeypatch.setattr(governance, "evaluate",
                        lambda action, **kw: Verdict(Decision.ALLOW, "open", "default"))
    problems = rc.verify(["gdpr"])
    assert any("dead overlay" in p for p in problems)


def test_verify_flags_an_attenuation_that_no_longer_bites(monkeypatch):
    monkeypatch.setattr(rc, "compile_obligations", lambda keys: rc.CompiledObligations(
        Policy(), frozenset(), rc.REGULATIONS["hipaa"]))
    problems = rc.verify(["hipaa"])
    assert any("dead attenuation" in p for p in problems)


def test_verify_flags_an_obligation_with_no_scope(monkeypatch):
    empty = rc.Obligation("t-empty", "T", "1", "", rc.EFFECT_DENY)
    monkeypatch.setitem(rc.REGULATIONS, "t", (empty,))
    assert any("no scope" in p for p in rc.verify(["t"]))


def test_main_exit_codes(monkeypatch, capsys):
    assert rc.main(["--ci", "--profiles", "gdpr,eu_ai_act,hipaa"]) == 0
    assert "non-vacuous" in capsys.readouterr().out
    # Nothing active and nothing requested: a gate that inspected nothing.
    _activate(monkeypatch, [])
    assert rc.main(["--ci"]) == 2
    assert rc.main([]) == 0
    bad = rc.Obligation("t-bad", "T", "1", "", rc.EFFECT_DENY,
                        actions=frozenset({"tool_that_does_not_exist"}))
    monkeypatch.setitem(rc.REGULATIONS, "t", (bad,))
    assert rc.main(["--ci", "--profiles", "t"]) == 1
    capsys.readouterr()  # drain the human-readable output before the JSON run
    assert rc.main(["--json", "--profiles", "gdpr"]) == 0
    import json
    assert json.loads(capsys.readouterr().out)["problems"] == []


# ---- profiles: one activation switch ----------------------------------------

def test_the_profiles_expose_the_compiled_policies():
    from maverick.compliance_profiles import get_profile
    assert get_profile("gdpr").policy == rc.compile_obligations(["gdpr"]).policy
    assert get_profile("eu_ai_act").policy.require_human_min_risk == "high"


def test_configured_profiles_reach_the_live_policy(monkeypatch):
    import maverick.compliance_profiles as cpmod
    monkeypatch.setattr(cpmod, "configured_profiles", lambda: ["gdpr"])
    monkeypatch.setattr("maverick.config.load_config", lambda *a, **k: {})
    monkeypatch.setattr("maverick.config.config_source_errors", dict)
    pol = Policy.from_config()
    assert evaluate("email", policy=pol).decision == Decision.REQUIRE_HUMAN


def test_active_keys_reads_config_and_env(monkeypatch):
    import maverick.compliance_profiles as cpmod
    monkeypatch.setattr(cpmod, "configured_profiles", lambda: ["gdpr", "bogus"])
    monkeypatch.setenv("MAVERICK_COMPLIANCE_PROFILES", "hipaa, eu_ai_act")
    assert rc.active_keys() == ["gdpr", "hipaa", "eu_ai_act"]


# ---- the chokepoint ---------------------------------------------------------

def _agent(tmp_path):
    from maverick.agent import Agent
    from maverick.blackboard import Blackboard
    from maverick.budget import Budget
    from maverick.sandbox import LocalBackend
    from maverick.swarm import SwarmContext
    from maverick.world_model import WorldModel

    world = WorldModel(tmp_path / "world.db")
    goal_id = world.create_goal("g", "")
    ctx = SwarmContext(
        llm=None, world=world, budget=Budget(max_dollars=1.0),
        blackboard=Blackboard(), sandbox=LocalBackend(workdir=tmp_path),
        goal_id=goal_id, use_skills=False,
    )
    agent = Agent(ctx=ctx, role="coder", brief="b")
    agent.capability = None
    agent.tools.register(Tool(
        name="ping", description="ping", fn=lambda args: "pong",
        input_schema={"type": "object", "properties": {}},
    ))
    return agent


def _gov(monkeypatch, policy: dict):
    monkeypatch.setattr("maverick.config.load_config", lambda *a, **k: {"governance": policy})
    monkeypatch.setattr("maverick.config.config_source_errors", dict)


def _capture(monkeypatch):
    import maverick.audit as audit
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda kind, **kw: seen.append((kind, kw)))
    return seen


@pytest.mark.asyncio
async def test_a_denial_at_the_chokepoint_names_the_obligation(monkeypatch, tmp_path):
    deny = rc.Obligation("t-deny-ping", "Test Reg", "s. 1", "", rc.EFFECT_DENY,
                         actions=frozenset({"ping"}))
    monkeypatch.setitem(rc.REGULATIONS, "t", (deny,))
    _activate(monkeypatch, ["t"])
    _gov(monkeypatch, {"deny_actions": ["ping"]})
    seen = _capture(monkeypatch)
    out = await _agent(tmp_path)._run_tool("ping", {})
    assert "DENIED" in out
    row = next(kw for kind, kw in seen if kind == "governance_denied")
    assert row["obligation_id"] == "t-deny-ping"
    assert row["regulation"] == "Test Reg" and row["article"] == "s. 1"


@pytest.mark.asyncio
async def test_a_call_that_ran_records_the_obligations_it_was_checked_against(
        monkeypatch, tmp_path):
    floor = rc.Obligation("t-floor", "Test Reg", "s. 2", "", rc.EFFECT_REQUIRE_HUMAN,
                          min_risk="low")
    monkeypatch.setitem(rc.REGULATIONS, "t", (floor,))
    _activate(monkeypatch, ["t"])
    _gov(monkeypatch, {})
    seen = _capture(monkeypatch)
    out = await _agent(tmp_path)._run_tool("ping", {})
    assert "pong" in out
    row = next(kw for kind, kw in seen if kind == "tool_call")
    assert row["obligations_checked"] == ["t-floor"]


@pytest.mark.asyncio
async def test_no_obligations_leaves_the_rows_exactly_as_before(monkeypatch, tmp_path):
    _activate(monkeypatch, [])
    _gov(monkeypatch, {})
    seen = _capture(monkeypatch)
    await _agent(tmp_path)._run_tool("ping", {})
    row = next(kw for kind, kw in seen if kind == "tool_call")
    assert "obligations_checked" not in row


def test_flow_dispatch_denials_are_tagged_too(monkeypatch):
    deny = rc.Obligation("t-deny-ping", "Test Reg", "s. 1", "", rc.EFFECT_DENY,
                         actions=frozenset({"ping"}))
    monkeypatch.setitem(rc.REGULATIONS, "t", (deny,))
    _activate(monkeypatch, ["t"])
    _gov(monkeypatch, {"deny_actions": ["ping"]})
    # The flow gate classifies and shield-scans before governance; clear both
    # so the test isolates the obligation tag on the governance denial.
    monkeypatch.setattr("maverick.flow.ir.action_tool_policy_error", lambda t: "")
    monkeypatch.setattr("maverick.shield_policy.scan_block", lambda text: None)
    seen = _capture(monkeypatch)
    from maverick.tool_authz import authorize
    err = authorize("ping", {}, origin="flow")
    assert err and "DENIED" in err
    row = next(kw for kind, kw in seen if kind == "governance_denied")
    assert row["obligation_id"] == "t-deny-ping"
