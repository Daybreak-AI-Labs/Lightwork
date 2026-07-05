"""Targeted code-eval corpus + held-in/held-out split (Gap 2).

Pins: the deterministic split, that promotion is judged on the held-OUT set
(generalization), and that a candidate which only gains on the seen held-in set
is flagged OVERFIT and refused.
"""
from __future__ import annotations

from pathlib import Path

from maverick.self_modify_corpus import CodeEvalCorpus, evaluate_on_corpus

IDS = tuple(f"tests/test_x.py::test_{i}" for i in range(6))


def _seed(root: Path) -> Path:
    src = root / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    return src


def _patch() -> str:
    return ("diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n"
            "@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n")


class _FakeSB:
    """Returns canned pytest counts keyed on candidate-vs-baseline and whether
    the command targets the held-out subset."""
    host_visible_fs = False

    def __init__(self, is_candidate: bool, held_out: set[str], scenario: str):
        self.is_candidate = is_candidate
        self.held_out = held_out
        self.scenario = scenario

    def exec(self, cmd, timeout=None):
        is_out = any(i in cmd for i in self.held_out)
        # baseline: 1/2 pass on both splits.
        passed, failed = 1, 1
        if self.is_candidate:
            if self.scenario == "genuine":
                passed, failed = 2, 0            # improves on BOTH splits
            elif self.scenario == "overfit":
                passed, failed = (2, 0) if not is_out else (1, 1)  # held-in only
            elif self.scenario == "attrition" and is_out:
                passed, failed = 1, 0            # a held-out test stopped running
        text = f"{passed} passed, {failed} failed in 0.1s" if failed else \
            f"{passed} passed in 0.1s"

        class R:
            stdout = text
            stderr = ""
            exit_code = 0 if failed == 0 else 1
        return R()


def _factory(held_out: set[str], scenario: str):
    def make(copy_dir):
        return _FakeSB("candidate" in str(copy_dir), held_out, scenario)
    return make


class TestSplit:
    def test_split_is_deterministic_and_nonempty(self):
        c = CodeEvalCorpus(test_ids=IDS)
        a1 = c.split()
        a2 = c.split()
        assert a1 == a2
        held_in, held_out = a1
        assert held_in and held_out
        assert set(held_in).isdisjoint(held_out)
        assert set(held_in) | set(held_out) == set(IDS)

    def test_empty_corpus_has_no_holdout(self):
        assert CodeEvalCorpus(test_ids=()).split() == ([], [])


class TestEvaluateOnCorpus:
    def _run(self, tmp_path, scenario):
        src = _seed(tmp_path)
        corpus = CodeEvalCorpus(test_ids=IDS)
        _, held_out = corpus.split()
        return evaluate_on_corpus(
            _patch(), src=src, workroot=tmp_path / "w", corpus=corpus,
            sandbox_factory=_factory(set(held_out), scenario))

    def test_genuine_improvement_promotes_on_heldout(self, tmp_path):
        res = self._run(tmp_path, "genuine")
        assert res.ok is True and res.applied is True
        assert res.candidate_score > res.baseline_score   # held-OUT gain
        assert res.overfit is False

    def test_overfit_is_flagged_and_refused(self, tmp_path):
        res = self._run(tmp_path, "overfit")
        assert res.overfit is True
        assert res.ok is False                            # refused despite held-in gain
        assert res.held_in_candidate > res.held_in_baseline
        assert res.candidate_score <= res.baseline_score  # no held-out gain
        assert "OVERFIT" in res.reason

    def test_source_is_untouched(self, tmp_path):
        self._run(tmp_path, "genuine")
        assert (tmp_path / "src" / "pkg" / "a.py").read_text() == "VALUE = 1\n"

    def test_empty_holdout_fails_closed(self, tmp_path):
        src = _seed(tmp_path)
        res = evaluate_on_corpus(
            _patch(), src=src, workroot=tmp_path / "w",
            corpus=CodeEvalCorpus(test_ids=("tests/test_x.py::only",)),
            sandbox_factory=_factory(set(), "genuine"))
        assert res.ok is False and "held-out" in res.reason

    def test_denominator_attrition_fails_closed(self, tmp_path):
        # Candidate makes a held-out test stop being collected -> its pass-rate
        # rises by attrition, not merit. Different denominators -> refuse.
        res = self._run(tmp_path, "attrition")
        assert res.ok is False
        assert "different denominators" in res.reason

    def test_non_applying_patch_fails_closed(self, tmp_path):
        src = _seed(tmp_path)
        bad = ("diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n"
               "@@ -1 +1 @@\n-NOPE\n+x\n")
        res = evaluate_on_corpus(
            bad, src=src, workroot=tmp_path / "w",
            corpus=CodeEvalCorpus(test_ids=IDS),
            sandbox_factory=_factory(set(), "genuine"))
        assert res.ok is False and res.applied is False
