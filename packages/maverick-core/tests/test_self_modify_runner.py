"""Operable DGM runner (Gap 7): config → policy, and an end-to-end governed run.

Pins: the widening ladder is built from config, the runner is a no-op while OFF,
and an enabled run proposes → evaluates → archives (persisting lineage) while the
code rung still refuses promotion without a human signature.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from maverick import self_improvement as si
from maverick import self_modify as sm
from maverick import self_modify_runner as runner
from maverick.self_modify_loop import Proposal


@dataclass
class FakeEval:
    ok: bool = True
    baseline_score: float = 0.5
    candidate_score: float = 0.9
    samples: int = 12
    reason: str = ""


def _patch(path="pkg/a.py", added="TUNING = 0.7"):
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1 +1,2 @@\n old\n+{added}\n")


class TestBuildPolicy:
    def test_tiers_from_config(self):
        cfg = {"tiers": [
            {"name": "narrow", "editable_paths": ["pkg/a/*.py"], "min_promotions": 1},
            {"name": "wide", "editable_paths": ["pkg/**"], "min_promotions": 5},
        ]}
        pol = runner.build_policy(cfg)
        names = [t.name for t in pol.tiers]
        assert names == ["none", "narrow", "wide"]     # tier 0 empty, then ascending
        assert pol.tiers[0].editable_globs == ()

    def test_editable_paths_fallback_single_tier(self):
        pol = runner.build_policy({"editable_paths": ["pkg/*.py"]})
        assert [t.name for t in pol.tiers] == ["none", "configured"]
        assert pol.tiers[1].min_promotions == 0

    def test_empty_config_grants_nothing(self):
        pol = runner.build_policy({})
        assert all(t.editable_globs == () for t in pol.tiers)


class TestRun:
    def test_noop_while_disabled(self, monkeypatch):
        monkeypatch.setattr(sm, "enabled", lambda: False)
        summ = runner.run(cycles=3)
        assert summ.ran is False and "disabled" in summ.reason
        assert summ.reports == []

    @pytest.fixture
    def _enabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sm, "enabled", lambda: True)
        monkeypatch.setattr(si, "enabled", lambda: True)
        monkeypatch.setattr(runner, "_self_modify_config",
                            lambda: {"enable": True, "editable_paths": ["pkg/*.py"]})
        monkeypatch.setattr(runner, "archive_path", lambda: tmp_path / "arch.json")
        return tmp_path

    def _controller(self, tmp_path):
        return si.SelfImprovementController(
            min_improvement=0.0, max_auto_rung="policy",
            frozen_fn=lambda: False, audit_fn=lambda **k: None,
            ledger=si.PromotionLedger(path=tmp_path / "ledger.json"))

    def test_enabled_run_evaluates_and_archives(self, _enabled, monkeypatch):
        tmp_path = _enabled
        summ = runner.run(
            cycles=2,
            proposer=lambda parent, surface: Proposal(_patch(), "tune"),
            evaluate=lambda p, s: FakeEval(),
            controller=self._controller(tmp_path))
        assert summ.ran is True
        assert len(summ.reports) == 2
        assert summ.evaluated == 2
        # code rung without a human signature -> not promoted, but archived
        assert summ.promoted == 0
        arch = runner.load_archive()
        assert len(arch.candidates) >= 1
        assert (tmp_path / "arch.json").exists()      # lineage persisted

    def test_run_persists_across_invocations(self, _enabled, monkeypatch):
        tmp_path = _enabled
        ctrl = self._controller(tmp_path)
        runner.run(cycles=1, proposer=lambda p, s: Proposal(_patch(added="A = 1"), "a"),
                   evaluate=lambda p, s: FakeEval(), controller=ctrl)
        n1 = len(runner.load_archive().candidates)
        runner.run(cycles=1, proposer=lambda p, s: Proposal(_patch(added="B = 2"), "b"),
                   evaluate=lambda p, s: FakeEval(), controller=ctrl)
        n2 = len(runner.load_archive().candidates)
        assert n2 > n1                                # archive accumulated

    def test_control_plane_proposal_is_refused_in_run(self, _enabled, monkeypatch):
        tmp_path = _enabled
        cp = _patch("packages/maverick-core/maverick/verifier.py")
        # widen the surface so only the boundary (not the allowlist) can refuse
        monkeypatch.setattr(runner, "_self_modify_config",
                            lambda: {"enable": True, "editable_paths": ["**"]})
        summ = runner.run(
            cycles=1, proposer=lambda p, s: Proposal(cp, "sneaky"),
            evaluate=lambda p, s: FakeEval(), controller=self._controller(tmp_path))
        assert summ.promoted == 0
        assert summ.reports[0].review_ok is False


def test_negative_min_promotions_is_clamped():
    # A negative min_promotions must not sort a non-empty tier ahead of tier-0
    # (which would make the rollback-freeze unlock a wide surface).
    pol = runner.build_policy({"tiers": [
        {"name": "evil", "editable_paths": ["**"], "min_promotions": -5}]})
    assert pol.tiers[0].editable_globs == ()          # narrowest tier stays empty
    assert all(t.min_promotions >= 0 for t in pol.tiers)
