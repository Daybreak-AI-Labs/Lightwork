"""Earned Autonomy (moonshot Bet 5): consequence cards, the predicted-vs-actual
join, evidence-driven graduation/demotion, the saga, and the agent wiring."""
from __future__ import annotations

import json

import pytest
from maverick import earned_autonomy as ea


def _engine(tmp_path, *, policy=None, frozen=False, grants=None, revokes=None,
            audits=None, now=(lambda: 100.0)):
    """A deterministic engine: injected clock, spies for grant/revoke/audit."""
    grants = grants if grants is not None else []
    revokes = revokes if revokes is not None else []
    audits = audits if audits is not None else []
    return ea.EarnedAutonomyEngine(
        cards=ea.CardStore(path=tmp_path / "cards.ndjson"),
        ledger=ea.TrustLedger(path=tmp_path / "trust.ndjson"),
        policy=policy or ea.GraduationPolicy(
            min_streak=3, min_samples=3, min_accuracy=0.9, tolerance=0.25,
            max_auto_risk="high", require_reversible=True, armed=True),
        frozen_fn=lambda: frozen,
        audit_fn=lambda kind, **p: audits.append((kind, p)),
        grant_fn=grants.append,
        revoke_fn=revokes.append,
        now=now,
    )


def _card(engine, *, episode_id, predicted=0.9, reversible=True,
          action="wire_transfer", principal="cfo-bot", risk="high"):
    return engine.record_card(
        principal=principal, action=action, risk=risk,
        predicted_outcome=predicted, goal_id=1, episode_id=episode_id,
        reversible=reversible, source="rehearsal", ts=float(episode_id))


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    """These are engine-behavior tests; the enable gate is tested explicitly."""
    monkeypatch.setenv("MAVERICK_EARNED_AUTONOMY", "1")


# -- stores: chained, persistent, tamper-evident ---------------------------


def test_card_store_roundtrip_and_chain(tmp_path):
    store = ea.CardStore(path=tmp_path / "c.ndjson")
    card = ea.ConsequenceCard(
        principal="p", action="deploy", risk="high", predicted_outcome=0.8,
        goal_id=1, episode_id=7, reversible=True, ts=1.0)
    assert store.append(card) is True
    assert store.verify().startswith("VALID: 1 link(s)")
    again = ea.CardStore(path=tmp_path / "c.ndjson")
    rows = again.cards()
    assert len(rows) == 1 and rows[0]["action"] == "deploy"
    assert again.verify().startswith("VALID: 1")


def test_tampered_store_is_detected(tmp_path):
    store = ea.CardStore(path=tmp_path / "c.ndjson")
    for ep in (1, 2):
        store.append(ea.ConsequenceCard(
            principal="p", action="send", risk="high", predicted_outcome=0.5,
            goal_id=1, episode_id=ep, ts=float(ep)))
    lines = (tmp_path / "c.ndjson").read_text().splitlines()
    row = json.loads(lines[0])
    row["predicted_outcome"] = 1.0  # rewrite history
    lines[0] = json.dumps(row, sort_keys=True)
    (tmp_path / "c.ndjson").write_text("\n".join(lines) + "\n")
    assert ea.CardStore(path=tmp_path / "c.ndjson").verify().startswith("BROKEN")


def test_trust_ledger_reduction(tmp_path):
    ledger = ea.TrustLedger(path=tmp_path / "t.ndjson")
    for kind, cid in (("hit", "a"), ("hit", "b"), ("miss", "c"), ("hit", "d")):
        assert ledger.record_event(kind, "p", "deploy", card_id=cid, ts=1.0)
    s = ledger.state("p", "deploy")
    assert (s.hits, s.misses, s.streak) == (3, 1, 1)
    assert s.samples == 4 and s.accuracy == 0.75 and s.graduated is False
    assert ledger.scored_ids() == frozenset({"a", "b", "c", "d"})
    assert ledger.record_event("bogus", "p", "deploy") is False


# -- the enable gate -------------------------------------------------------


def test_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MAVERICK_EARNED_AUTONOMY", raising=False)
    monkeypatch.setattr("maverick.config.get_earned_autonomy",
                        lambda: {"enable": False})
    assert ea.enabled() is False
    engine = _engine(tmp_path)
    assert _card(engine, episode_id=1) is None
    assert engine.reconcile() == ea.ReconcileReport()


def test_env_master_switch_overrides_config(monkeypatch):
    monkeypatch.setattr("maverick.config.get_earned_autonomy",
                        lambda: {"enable": True})
    monkeypatch.setenv("MAVERICK_EARNED_AUTONOMY", "0")
    assert ea.enabled() is False
    monkeypatch.setenv("MAVERICK_EARNED_AUTONOMY", "1")
    assert ea.enabled() is True


def test_record_blocked_by_learning_guard(tmp_path, monkeypatch):
    monkeypatch.setattr("maverick.learning_guard.learning_write_allowed",
                        lambda *a, **k: False)
    engine = _engine(tmp_path)
    assert _card(engine, episode_id=1) is None
    assert engine.cards.cards() == []


# -- the predicted-vs-actual join ------------------------------------------


def test_reconcile_scores_hits_and_misses_idempotently(tmp_path):
    engine = _engine(tmp_path)
    _card(engine, episode_id=1, predicted=0.9)   # actual 1.0 -> hit (|0.1|<=0.25)
    _card(engine, episode_id=2, predicted=0.9)   # actual 0.0 -> miss
    _card(engine, episode_id=3, predicted=0.9)   # no actual yet -> unscored
    actuals = {(1, 1): 1.0, (1, 2): 0.0}
    report = engine.reconcile(resolve=lambda g, e: actuals.get((g, e)))
    assert (report.scored, report.hits, report.misses) == (2, 1, 1)
    s = engine.ledger.state("cfo-bot", "wire_transfer")
    assert (s.hits, s.misses, s.streak) == (1, 1, 0)
    # Idempotent: nothing is double-counted on a second pass.
    again = engine.reconcile(resolve=lambda g, e: actuals.get((g, e)))
    assert again.scored == 0
    assert engine.ledger.state("cfo-bot", "wire_transfer").samples == 2


def test_reconcile_skips_cards_without_a_join_key(tmp_path):
    engine = _engine(tmp_path)
    _card(engine, episode_id=0)  # no episode -> nothing to join against
    report = engine.reconcile(resolve=lambda g, e: 1.0)
    assert report.scored == 0


# -- graduation: the earned dial -------------------------------------------


def _graduate(engine, *, n=3):
    for ep in range(1, n + 1):
        _card(engine, episode_id=ep, predicted=0.9)
    return engine.reconcile(resolve=lambda g, e: 1.0)


def test_streak_graduates_and_grants(tmp_path):
    grants, audits = [], []
    engine = _engine(tmp_path, grants=grants, audits=audits)
    report = _graduate(engine)
    assert report.graduated == ("wire_transfer",)
    assert grants == ["wire_transfer"]
    assert engine.ledger.state("cfo-bot", "wire_transfer").graduated is True
    assert engine.decide("cfo-bot", "wire_transfer", risk="high").auto is True
    decisions = [p.get("decision") for k, p in audits
                 if k == "autonomy_graduation"]
    assert decisions == ["graduate"]


def test_one_miss_demotes_instantly_and_revokes(tmp_path):
    grants, revokes = [], []
    engine = _engine(tmp_path, grants=grants, revokes=revokes)
    _graduate(engine)
    _card(engine, episode_id=9, predicted=0.9)
    report = engine.reconcile(resolve=lambda g, e: 0.0 if e == 9 else 1.0)
    assert report.demoted == ("wire_transfer",)
    assert revokes == ["wire_transfer"]
    s = engine.ledger.state("cfo-bot", "wire_transfer")
    assert s.graduated is False and s.streak == 0
    assert engine.decide("cfo-bot", "wire_transfer", risk="high").auto is False


def test_unarmed_policy_never_graduates(tmp_path):
    grants = []
    engine = _engine(tmp_path, grants=grants, policy=ea.GraduationPolicy(
        min_streak=3, min_samples=3, max_auto_risk="high", armed=False))
    report = _graduate(engine)
    assert report.graduated == () and grants == []


def test_risk_ceiling_blocks_high_risk_by_default(tmp_path):
    engine = _engine(tmp_path, policy=ea.GraduationPolicy(
        min_streak=3, min_samples=3, armed=True))  # default ceiling: medium
    assert _graduate(engine).graduated == ()
    # An unknown ceiling is the MOST restrictive, never permissive.
    verdict = ea.evaluate_graduation(
        ea.TrustState("p", "read_file", hits=50, misses=0, streak=50),
        risk="low", reversible_share=1.0, frozen=False,
        policy=ea.GraduationPolicy(min_streak=3, min_samples=3, armed=True,
                                   max_auto_risk="unlimited"))
    assert verdict.graduate is False
    assert any(g.gate == "risk_ceiling" and not g.ok for g in verdict.gates)


def test_irreversible_cards_block_graduation(tmp_path):
    engine = _engine(tmp_path)
    _card(engine, episode_id=1, reversible=False)
    for ep in (2, 3):
        _card(engine, episode_id=ep)
    report = engine.reconcile(resolve=lambda g, e: 1.0)
    assert report.graduated == ()
    states = {g.gate: g for g in ea.evaluate_graduation(
        engine.ledger.state("cfo-bot", "wire_transfer"), risk="high",
        reversible_share=2 / 3, policy=engine.policy, frozen=False).gates}
    assert states["reversibility"].ok is False


def test_calibration_freeze_blocks_graduation(tmp_path):
    engine = _engine(tmp_path, frozen=True)
    assert _graduate(engine).graduated == ()

    def _boom():
        raise RuntimeError("no verdict")

    engine2 = _engine(tmp_path / "b")
    engine2.frozen_fn = _boom  # can't confirm the judge -> treated frozen
    assert _graduate(engine2).graduated == ()


def test_grant_failure_means_no_graduation_record(tmp_path):
    def _refuse(action):
        raise RuntimeError("ledger sealed")

    engine = _engine(tmp_path)
    engine.grant_fn = _refuse
    report = _graduate(engine)
    assert report.graduated == ()
    assert engine.ledger.state("cfo-bot", "wire_transfer").graduated is False


def test_operator_revoke(tmp_path):
    revokes = []
    engine = _engine(tmp_path, revokes=revokes)
    _graduate(engine)
    assert engine.revoke("cfo-bot", "wire_transfer", reason="quarter close") is True
    assert revokes[-1] == "wire_transfer"
    assert engine.ledger.state("cfo-bot", "wire_transfer").graduated is False


# -- decide(): every uncertainty keeps the human ---------------------------


def test_decide_fails_toward_human(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    _graduate(engine)
    assert engine.decide("cfo-bot", "wire_transfer", risk="high").auto is True
    # Frozen verifier suspends the dial.
    engine.frozen_fn = lambda: True
    assert engine.decide("cfo-bot", "wire_transfer", risk="high").auto is False
    engine.frozen_fn = lambda: False
    # Operational halt suspends the dial.
    monkeypatch.setattr("maverick.killswitch.is_active", lambda: True)
    assert engine.decide("cfo-bot", "wire_transfer", risk="high").auto is False
    monkeypatch.setattr("maverick.killswitch.is_active", lambda: False)
    # Disabled feature, unproven pairs, over-ceiling risk: all human.
    assert engine.decide("cfo-bot", "run_payroll", risk="high").auto is False
    monkeypatch.setenv("MAVERICK_EARNED_AUTONOMY", "0")
    assert engine.decide("cfo-bot", "wire_transfer", risk="high").auto is False


def test_status_orders_most_proven_first(tmp_path):
    engine = _engine(tmp_path)
    _graduate(engine)
    _card(engine, episode_id=8, action="send_invoice")
    engine.reconcile(resolve=lambda g, e: 1.0)
    states = engine.status()
    assert [s.action for s in states] == ["wire_transfer", "send_invoice"]
    assert states[0].graduated is True and states[1].graduated is False


# -- the compensating-action saga ------------------------------------------


def test_saga_commits_when_every_step_succeeds():
    trail = []
    steps = [
        ea.SagaStep("hold", lambda: trail.append("hold") or "held",
                    lambda: trail.append("-hold") or "released"),
        ea.SagaStep("post", lambda: trail.append("post") or "posted",
                    lambda: trail.append("-post") or "reversed"),
    ]
    result = ea.run_saga(steps)
    assert result.committed is True
    assert [r.step for r in result.results] == ["hold", "post"]
    assert trail == ["hold", "post"] and result.compensated == ()


def test_saga_rolls_back_completed_prefix_in_reverse():
    trail = []

    def _fail():
        raise RuntimeError("wire rejected")

    steps = [
        ea.SagaStep("hold", lambda: trail.append("hold") or "ok",
                    lambda: trail.append("undo-hold") or "ok"),
        ea.SagaStep("post", lambda: trail.append("post") or "ok",
                    lambda: trail.append("undo-post") or "ok"),
        ea.SagaStep("wire", _fail, lambda: "never-reached"),
    ]
    result = ea.run_saga(steps)
    assert result.committed is False
    assert "wire" in result.reason
    assert [r.step for r in result.compensated] == ["post", "hold"]
    assert trail == ["hold", "post", "undo-post", "undo-hold"]


def test_saga_refuses_irreversible_steps_before_any_effect():
    ran = []
    steps = [ea.SagaStep("wire", lambda: ran.append("wire") or "ok", None)]
    result = ea.run_saga(steps)
    assert result.committed is False and ran == []
    assert "without an inverse" in result.reason
    assert ea.run_saga(steps, allow_irreversible=True).committed is True


def test_saga_records_failed_compensation():
    def _bad_undo():
        raise RuntimeError("cannot release")

    def _fail():
        raise RuntimeError("boom")

    steps = [
        ea.SagaStep("hold", lambda: "ok", _bad_undo),
        ea.SagaStep("wire", _fail, lambda: "ok"),
    ]
    result = ea.run_saga(steps)
    assert result.committed is False
    assert [(r.step, r.ok) for r in result.compensated] == [("hold", False)]
    assert "RuntimeError" in result.compensated[0].detail


# -- run-path wiring -------------------------------------------------------


def test_shared_singleton_uses_data_dir(tmp_path, monkeypatch):
    base = tmp_path / "home"
    monkeypatch.setattr("maverick.paths.data_dir",
                        lambda *p, **k: base.joinpath(*p))
    ea.reset_shared()
    try:
        engine = ea.shared()
        assert engine.record_card(
            principal="p", action="deploy", risk="high", predicted_outcome=0.7,
            goal_id=1, episode_id=1, ts=1.0)
        assert (base / "consequence_cards.ndjson").exists()
        assert ea.shared() is engine
    finally:
        ea.reset_shared()


class _Verdict:
    def __init__(self, known=True, predicted=0.9, reason="well-trodden"):
        self.known = known
        self.predicted_outcome = predicted
        self.reason = reason


def test_capture_prediction_filters_placeholders(tmp_path):
    engine = _engine(tmp_path)
    assert ea.capture_prediction(
        _Verdict(known=False), principal="p", action="shell", risk="high",
        goal_id=1, episode_id=1, engine=engine) is None
    card_id = ea.capture_prediction(
        _Verdict(), principal="p", action="shell", risk="high",
        goal_id=1, episode_id=1, engine=engine)
    assert card_id is not None
    row = engine.cards.cards()[0]
    assert row["source"] == "rehearsal" and row["predicted_outcome"] == 0.9
    # Garbage verdicts never raise into the caller.
    assert ea.capture_prediction(
        object(), principal="p", action="shell", risk="high",
        goal_id=1, episode_id=1, engine=engine) is None


# -- config getter ---------------------------------------------------------


def test_get_earned_autonomy_defaults_and_strict_arming(monkeypatch):
    from maverick import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load_config", lambda *a, **k: {
        "earned_autonomy": {
            "enable": True, "auto_graduate": "true",  # string never arms
            "min_streak": -5, "min_accuracy": 7, "max_auto_risk": "HIGH",
        },
    })
    cfg = cfg_mod.get_earned_autonomy()
    assert cfg["enable"] is True
    assert cfg["auto_graduate"] is False
    assert cfg["min_streak"] == 10          # invalid -> strict default
    assert cfg["min_accuracy"] == 0.9       # out of range -> strict default
    assert cfg["max_auto_risk"] == "high"
    assert cfg["require_reversible"] is True
    monkeypatch.setattr(cfg_mod, "load_config", lambda *a, **k: {})
    assert cfg_mod.get_earned_autonomy()["enable"] is False


# -- agent integration: the rehearsal verdict lands as a card --------------


def _ctx(tmp_path, fake_llm):
    from maverick.blackboard import Blackboard
    from maverick.budget import Budget
    from maverick.sandbox import LocalBackend
    from maverick.swarm import SwarmContext
    from maverick.world_model import WorldModel

    world = WorldModel(tmp_path / "w.db")
    gid = world.create_goal("test", "")
    return SwarmContext(
        llm=fake_llm, world=world, budget=Budget(max_dollars=1.0),
        blackboard=Blackboard(), sandbox=LocalBackend(workdir=tmp_path),
        goal_id=gid, max_depth=1,
    )


@pytest.mark.asyncio
async def test_agent_pins_rehearsal_prediction_as_card(
    tmp_path, fake_llm, monkeypatch,
):
    from maverick import rehearsal
    from maverick.agent import Agent

    monkeypatch.setenv("MAVERICK_REHEARSAL", "1")
    monkeypatch.setattr(
        "maverick.rehearsal_runtime.gate_tool",
        lambda **kw: rehearsal.RehearsalVerdict(
            rehearsal.PROCEED, 0.92, 0.05, 12, True, "well-trodden"))
    base = tmp_path / "home"
    monkeypatch.setattr("maverick.paths.data_dir",
                        lambda *p, **k: base.joinpath(*p))
    ea.reset_shared()
    try:
        ctx = _ctx(tmp_path, fake_llm)
        agent = Agent(ctx=ctx, role="orchestrator", brief="t", depth=0)
        out = await agent._run_tool("shell", {"cmd": "echo PINNED"})
        assert "PINNED" in out  # PROCEED: the tool actually ran
        rows = ea.shared().cards.cards()
        assert len(rows) == 1
        assert rows[0]["action"] == "shell"
        assert rows[0]["predicted_outcome"] == 0.92
        assert rows[0]["source"] == "rehearsal"
    finally:
        ea.reset_shared()
