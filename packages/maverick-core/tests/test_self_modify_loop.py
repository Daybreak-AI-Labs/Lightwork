"""DGM cycle driver + proof-gated surface widening.

These pin the governed loop's contract: OFF by default (no proposer ever runs),
the boundary still refuses control-plane edits inside the loop, promotion flows
through the real ladder, the archive accumulates promoted AND unpromoted
candidates, and the editable surface widens only as promotions accumulate and
FREEZES the moment a promoted change is rolled back.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from maverick import self_improvement as si
from maverick import self_modify as sm
from maverick.self_modify_archive import CodeArchive, CodeCandidate
from maverick.self_modify_loop import (
    Proposal,
    SurfaceTier,
    WideningPolicy,
    earned_surface,
    run_cycle,
    run_loop,
)

EDIT = "packages/maverick-core/maverick/domains/foo.py"


@dataclass
class FakeEval:
    ok: bool = True
    baseline_score: float = 0.5
    candidate_score: float = 0.9
    samples: int = 8
    reason: str = ""


def _patch(path: str = EDIT, added: str = "TUNING = 0.7") -> str:
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1 +1,2 @@\n old\n+{added}\n")


def _tiers() -> WideningPolicy:
    return WideningPolicy(tiers=(
        SurfaceTier("t0", (), 0),
        SurfaceTier("t1", ("packages/maverick-core/maverick/domains/*.py",), 1),
        SurfaceTier("t2", ("packages/maverick-core/maverick/**",), 3),
    ))


def _controller():
    return si.SelfImprovementController(
        min_improvement=0.0, max_auto_rung="policy",
        frozen_fn=lambda: False, audit_fn=lambda **k: None)


@pytest.fixture
def _on(monkeypatch):
    monkeypatch.setattr(sm, "enabled", lambda: True)
    monkeypatch.setattr(si, "enabled", lambda: True)


class TestWideningPolicy:
    def test_earned_grows_with_promotions(self):
        p = _tiers()
        assert p.earned(promotions=0, had_rollback=False).name == "t0"
        assert p.earned(promotions=1, had_rollback=False).name == "t1"
        assert p.earned(promotions=3, had_rollback=False).name == "t2"

    def test_rollback_freezes_at_tier0(self):
        p = _tiers()
        assert p.earned(promotions=100, had_rollback=True).name == "t0"

    def test_empty_policy_grants_nothing(self):
        t = WideningPolicy().earned(promotions=5, had_rollback=False)
        assert t.editable_globs == ()

    def test_earned_surface_reads_the_archive(self):
        a = CodeArchive()
        a.add(CodeCandidate(summary="p1", patch="+a", score=1.0, promoted=True))
        tier, surface = earned_surface(a, _tiers())
        assert tier.name == "t1"
        assert surface.editable_globs == (
            "packages/maverick-core/maverick/domains/*.py",)

    def test_earned_surface_freezes_after_rollback(self):
        a = CodeArchive()
        for i in range(5):
            a.add(CodeCandidate(summary=f"p{i}", patch=f"+a{i}", score=1.0,
                                promoted=True))
        a.add(CodeCandidate(summary="bad", patch="+bad", score=1.0,
                            promoted=True, rolled_back=True))
        tier, _ = earned_surface(a, _tiers())
        assert tier.name == "t0"             # a regression forfeits widening


class TestRunCycleDisabled:
    def test_off_by_default_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(sm, "enabled", lambda: False)
        called = []
        rep = run_cycle(
            archive=CodeArchive(),
            proposer=lambda parent, surface: called.append(1) or Proposal(_patch()),
            evaluate=lambda p, s: FakeEval(), policy=_tiers())
        assert rep.proposed is False
        assert called == []                  # the proposer never ran
        assert "disabled" in rep.reason


class TestRunCycle:
    def test_no_surface_yet_skips(self, _on):
        # Empty track record -> tier 0 grants no surface -> proposer never runs.
        called = []
        rep = run_cycle(
            archive=CodeArchive(),
            proposer=lambda parent, surface: called.append(1) or Proposal(_patch()),
            evaluate=lambda p, s: FakeEval(), policy=_tiers())
        assert called == []
        assert "no editable surface" in rep.reason

    def test_proposer_none_is_reported(self, _on):
        a = CodeArchive()
        a.add(CodeCandidate(summary="seed", patch="+a", score=1.0, promoted=True))
        rep = run_cycle(archive=a, proposer=lambda p, s: None,
                        evaluate=lambda p, s: FakeEval(), policy=_tiers())
        assert rep.proposed is False and "no patch" in rep.reason

    def test_boundary_refuses_control_plane_in_loop(self, _on):
        # Even inside the loop, a control-plane edit is refused structurally.
        a = CodeArchive()
        a.add(CodeCandidate(summary="seed", patch="+a", score=1.0, promoted=True))
        # A permissive tier so ONLY the boundary (not the allowlist) can refuse.
        policy = WideningPolicy(tiers=(SurfaceTier("wide", ("**",), 0),))
        cp = _patch("packages/maverick-core/maverick/verifier.py")
        rep = run_cycle(archive=a, proposer=lambda p, s: Proposal(cp),
                        evaluate=lambda p, s: FakeEval(), policy=policy)
        assert rep.proposed is True
        assert rep.review_ok is False
        assert "protected" in rep.reason

    def test_promotes_a_clean_prompt_change_and_archives_it(self, _on):
        a = CodeArchive()
        a.add(CodeCandidate(summary="seed", patch="+a", score=1.0, promoted=True))
        rep = run_cycle(
            archive=a, proposer=lambda p, s: Proposal(_patch(), "tune pack"),
            evaluate=lambda p, s: FakeEval(), policy=_tiers(),
            rung="prompt", controller=_controller())
        assert rep.review_ok and rep.evaluated
        assert rep.promoted is True, rep.reason
        # the candidate is now in the archive, marked promoted
        cand = a.get(rep.candidate_id)
        assert cand is not None and cand.promoted is True
        assert cand.summary == "tune pack"

    def test_code_rung_without_signature_archives_but_does_not_promote(self, _on):
        a = CodeArchive()
        a.add(CodeCandidate(summary="seed", patch="+a", score=1.0, promoted=True))
        rep = run_cycle(
            archive=a, proposer=lambda p, s: Proposal(_patch(), "risky"),
            evaluate=lambda p, s: FakeEval(), policy=_tiers(),
            rung="code", controller=_controller())
        assert rep.evaluated is True
        assert rep.promoted is False         # no human signature -> refused
        cand = a.get(rep.candidate_id)
        assert cand is not None and cand.promoted is False   # kept as an ancestor

    def test_failed_evaluation_is_not_promoted(self, _on):
        a = CodeArchive()
        a.add(CodeCandidate(summary="seed", patch="+a", score=1.0, promoted=True))
        rep = run_cycle(
            archive=a, proposer=lambda p, s: Proposal(_patch(), "x"),
            evaluate=lambda p, s: FakeEval(ok=False, reason="did not apply"),
            policy=_tiers(), rung="prompt", controller=_controller())
        assert rep.evaluated is False
        assert rep.promoted is False
        assert "did not apply" in rep.reason

    def test_widening_construct_blocks_promotion(self, _on):
        a = CodeArchive()
        a.add(CodeCandidate(summary="seed", patch="+a", score=1.0, promoted=True))
        rep = run_cycle(
            archive=a,
            proposer=lambda p, s: Proposal(_patch(added="os.system('x')"), "sneaky"),
            evaluate=lambda p, s: FakeEval(), policy=_tiers(),
            rung="prompt", controller=_controller())
        assert rep.evaluated is True
        assert rep.promoted is False         # capability screen -> gate refused
        cand = a.get(rep.candidate_id)
        assert cand.capability_widens is True


class TestRunLoop:
    def test_disabled_returns_single_inert_report(self, monkeypatch):
        monkeypatch.setattr(sm, "enabled", lambda: False)
        reps = run_loop(archive=CodeArchive(), proposer=lambda p, s: None,
                        evaluate=lambda p, s: FakeEval(), cycles=5)
        assert len(reps) == 1 and "disabled" in reps[0].reason

    def test_threads_generation_and_accumulates(self, _on):
        a = CodeArchive()
        a.add(CodeCandidate(summary="seed", patch="+a", score=1.0, promoted=True))
        # Each cycle proposes a distinct patch so the archive grows.
        n = {"i": 0}

        def propose(parent, surface):
            n["i"] += 1
            return Proposal(_patch(added=f"TUNING = {n['i']}"), f"tune {n['i']}")

        reps = run_loop(
            archive=a, proposer=propose, evaluate=lambda p, s: FakeEval(),
            policy=_tiers(), cycles=3, rung="prompt", controller=_controller())
        assert len(reps) == 3
        assert all(r.promoted for r in reps)
        # seed + 3 new promoted candidates
        assert len([c for c in a.candidates if c.promoted]) == 4
