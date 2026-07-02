"""Reference-free evaluation seam for the self-harness A/B: corpus + scorers.

`validate_proposal` needs injected `score_with`/`score_without` (a real eval needs
a real model). This module builds that seam from a curated corpus + injected
run/judge fns; these tests pin the corpus loader, the DETERMINISTIC split, the
heuristic judge default, and that the composed scorers actually drive validation.
"""
from __future__ import annotations

import json

from maverick import self_harness as sh
from maverick import self_harness_eval as ev


def test_load_eval_corpus(tmp_path):
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps({
        "claude-x": [{"goal": "export the ledger", "expected": "done"},
                     {"goal": "", "expected": "skip"},          # blank goal -> dropped
                     {"not": "a case"}],                          # malformed -> dropped
        "empty": [],                                             # no cases -> key dropped
    }))
    corpus = ev.load_eval_corpus(p)
    assert list(corpus) == ["claude-x"]
    assert corpus["claude-x"] == [{"goal": "export the ledger", "expected": "done"}]
    # missing file / bad json -> {} (never raises)
    assert ev.load_eval_corpus(tmp_path / "nope.json") == {}
    (tmp_path / "bad.json").write_text("{not json")
    assert ev.load_eval_corpus(tmp_path / "bad.json") == {}


def test_corpus_split_is_deterministic():
    cases = [{"goal": f"goal number {i}", "expected": "ok"} for i in range(10)]
    a = ev.corpus_split(cases, held_out_frac=0.3)
    b = ev.corpus_split(cases, held_out_frac=0.3)
    assert a == b                                               # reproducible
    held_in, held_out = a
    assert len(held_out) == 3 and len(held_in) == 7
    assert set(held_in).isdisjoint(held_out)                    # no leakage
    assert set(held_in) | set(held_out) == {c["goal"] for c in cases}
    # edge cases
    assert ev.corpus_split([]) == ([], [])
    assert ev.corpus_split([{"goal": "solo"}]) == (["solo"], [])
    hi, ho = ev.corpus_split([{"goal": "a"}, {"goal": "b"}])    # >=2 -> both non-empty
    assert hi and ho


def test_corpus_kfold_splits_partition_every_case_once():
    cases = [{"goal": f"goal number {i}", "expected": "ok"} for i in range(10)]
    folds = ev.corpus_kfold_splits(cases, k=5)
    assert len(folds) == 5
    all_goals = {c["goal"] for c in cases}
    seen_out: list[str] = []
    for held_in, held_out in folds:
        assert held_out                                   # every fold holds something out
        assert set(held_in).isdisjoint(held_out)          # no leakage within a fold
        assert set(held_in) | set(held_out) == all_goals  # together they cover the corpus
        seen_out.extend(held_out)
    # Every case is unseen in EXACTLY one fold (a true K-fold partition).
    assert sorted(seen_out) == sorted(all_goals)


def test_corpus_kfold_splits_is_deterministic():
    cases = [{"goal": f"g{i}", "expected": "ok"} for i in range(7)]
    assert ev.corpus_kfold_splits(cases, k=3) == ev.corpus_kfold_splits(cases, k=3)
    # order-independent: shuffling the corpus yields the same folds
    assert ev.corpus_kfold_splits(cases, k=3) == ev.corpus_kfold_splits(
        list(reversed(cases)), k=3)


def test_corpus_kfold_splits_clamps_and_degrades():
    # k clamped to n; < 2 goals -> a single (goals, []) rotation (no usable holdout)
    folds = ev.corpus_kfold_splits([{"goal": "a"}, {"goal": "b"}], k=9)
    assert len(folds) == 2 and all(ho for _hi, ho in folds)
    assert ev.corpus_kfold_splits([{"goal": "solo"}], k=5) == [(["solo"], [])]
    assert ev.corpus_kfold_splits([], k=5) == [([], [])]


def test_heuristic_judge():
    assert ev._heuristic_judge("g", "the answer is DONE here", "done") is True
    assert ev._heuristic_judge("g", "no match", "done") is False
    assert ev._heuristic_judge("g", "anything", "") is False    # no expected -> not a pass


def test_corpus_ab_scorers_measure_causal_lift():
    cases = [{"goal": f"g{i}", "expected": "ok"} for i in range(6)]
    # WITH the line the run produces a passing answer; the baseline does not
    run_fn = lambda line, goal: "ok answer" if line else "wrong"   # noqa: E731
    score_with, score_without = ev.corpus_ab_scorers(cases, run_fn=run_fn)
    goals = [c["goal"] for c in cases]
    assert score_with("Verify first.", goals) == 1.0
    assert score_without("Verify first.", goals) == 0.0


def test_corpus_scorers_drive_validate_proposal():
    cases = [{"goal": f"g{i}", "expected": "ok"} for i in range(6)]
    held_in, held_out = ev.corpus_split(cases, held_out_frac=0.5)
    run_fn = lambda line, goal: "ok answer" if line else "nope"   # noqa: E731
    sw, wo = ev.corpus_ab_scorers(cases, run_fn=run_fn)
    p = sh.HarnessProposal("M", "timeout: boom", "Bound the export window.", "r")
    vr = sh.validate_proposal(p, held_in=held_in, held_out=held_out,
                              score_with=sw, score_without=wo)
    assert vr.accepted and vr.held_out_delta > 0


def test_corpus_ab_scorers_use_injected_judge():
    cases = [{"goal": "g", "expected": "ignored"}]
    seen = []
    def judge(goal, output, expected):
        seen.append((goal, output, expected))
        return output == "GOOD"
    run_fn = lambda line, goal: "GOOD" if line else "BAD"        # noqa: E731
    sw, wo = ev.corpus_ab_scorers(cases, run_fn=run_fn, judge_fn=judge)
    assert sw("line", ["g"]) == 1.0 and wo("line", ["g"]) == 0.0
    assert seen and seen[0][2] == "ignored"                     # corpus expected passed through


# ---- LLM-backed run/judge (the live seam, tested with a fake provider) ----

class _FakeLLM:
    """Sync stand-in for maverick.llm.LLM.complete (the eval seam)."""

    def __init__(self, text="", raises=False):
        self._text, self._raises = text, raises
        self.model = "fake:test"
        self.calls = []

    def complete(self, system, messages, **kw):
        self.calls.append((system, messages, kw))
        if self._raises:
            raise RuntimeError("provider down")
        return type("R", (), {"text": self._text})()


def test_llm_judge_parses_yes_no():
    assert ev.llm_judge(_FakeLLM(text="Yes, it did."))("g", "out", "exp") is True
    assert ev.llm_judge(_FakeLLM(text="no"))("g", "out", "exp") is False


class _SeqLLM:
    """Returns a scripted sequence of texts across successive complete() calls --
    so a self-consistency judge sees diverse votes."""

    def __init__(self, texts):
        self._texts = list(texts)
        self.model = "fake:seq"
        self.calls = []

    def complete(self, system, messages, **kw):
        self.calls.append((system, messages, kw))
        i = min(len(self.calls) - 1, len(self._texts) - 1)
        return type("R", (), {"text": self._texts[i]})()


def test_llm_judge_self_consistency_majority_vote():
    # 2 "yes" vs 1 "no" -> majority yes; uses a DIFFERENT framing per sample.
    llm = _SeqLLM(["yes", "no", "yes"])
    j = ev.llm_judge(llm, samples=3)
    assert j("g", "out", "exp") is True
    assert len(llm.calls) == 3
    framings = {c[0] for c in llm.calls}
    assert len(framings) == 3                         # three distinct reasoning paths
    # 2 "no" vs 1 "yes" -> majority no.
    assert ev.llm_judge(_SeqLLM(["no", "yes", "no"]), samples=3)("g", "o", "e") is False


def test_llm_judge_self_consistency_tie_breaks_to_heuristic():
    # 1 yes / 1 no tie with samples=2 -> heuristic decides (expected substring).
    j = ev.llm_judge(_SeqLLM(["yes", "no"]), samples=2)
    assert j("g", "the answer is DONE", "done") is True   # heuristic: present
    assert j("g", "mismatch", "done") is False            # heuristic: absent


def test_llm_judge_single_sample_is_unchanged():
    # Default samples=1 keeps the single-call behavior (exactly one judge call).
    llm = _SeqLLM(["yes"])
    assert ev.llm_judge(llm)("g", "o", "e") is True
    assert len(llm.calls) == 1


def test_llm_judge_fails_open_to_heuristic():
    # provider error -> heuristic (expected substring present)
    j = ev.llm_judge(_FakeLLM(raises=True))
    assert j("g", "the answer is DONE", "done") is True
    assert j("g", "mismatch", "done") is False
    # unparseable answer -> heuristic too
    assert ev.llm_judge(_FakeLLM(text="maybe?"))("g", "has done", "done") is True


def test_llm_runner_injects_line_and_generates():
    llm = _FakeLLM(text="generated answer")
    run = ev.llm_runner(llm, system_prefix="base")
    assert run("Verify first.", "do the thing") == "generated answer"
    # the candidate line is injected into the system prompt; baseline omits it
    sys_with = llm.calls[-1][0]
    assert "Verify first." in sys_with and "base" in sys_with
    run("", "do the thing")
    assert "Verify first." not in llm.calls[-1][0]              # baseline


def test_llm_runner_returns_empty_on_error():
    assert ev.llm_runner(_FakeLLM(raises=True))("line", "goal") == ""


def test_llm_backed_evaluator_end_to_end():
    # a fake model that "succeeds" only when the guidance line is present
    class _Model:
        def complete(self, system, messages, **kw):
            ok = "Bound the window" in system            # run reflects the line...
            if "evaluator" in system.lower():            # ...and the judge verdict
                ok = "GOAL-MET" in (messages[0]["content"])
            return type("R", (), {"text": "GOAL-MET" if ok else "nope"})()
    llm = _Model()
    cases = [{"goal": f"g{i}", "expected": "GOAL-MET"} for i in range(4)]
    sw, wo = ev.corpus_ab_scorers(
        cases, run_fn=ev.llm_runner(llm), judge_fn=ev.llm_judge(llm))
    goals = [c["goal"] for c in cases]
    assert sw("Bound the window first.", goals) == 1.0          # line -> success
    assert wo("Bound the window first.", goals) == 0.0          # baseline -> fail


# ---- review fixes: _rate indeterminate + metamorphic skip ----

def test_rate_nan_on_all_indeterminate_but_zero_on_empty():
    import math
    def boom(g):
        raise RuntimeError("indeterminate")
    assert ev._rate([], boom) == 0.0                  # empty split -> 0.0 (unchanged)
    assert math.isnan(ev._rate(["a", "b"], boom))     # all raised -> NaN (not false 0%)
    assert ev._rate(["a"], lambda g: True) == 1.0     # normal path unchanged


# ---- budget exhaustion fails the evaluation CLOSED, never open ----

class _BrokeLLM:
    """A provider whose budget is exhausted: every call raises BudgetExceeded."""

    model = "fake:broke"

    def complete(self, system, messages, **kw):
        from maverick.budget import BudgetExceeded
        raise BudgetExceeded("$5.01 > $5.00")


def test_llm_runner_reraises_budget_exceeded():
    # An exhausted budget must NOT score as a failed generation ("") -- that
    # would deflate whichever arm runs after exhaustion and skew the A/B.
    import pytest
    from maverick.budget import BudgetExceeded
    with pytest.raises(BudgetExceeded):
        ev.llm_runner(_BrokeLLM())("line", "goal")
    # any other provider error still fails open (unchanged)
    assert ev.llm_runner(_FakeLLM(raises=True))("line", "goal") == ""


def test_llm_judge_reraises_budget_exceeded():
    # ...and must NOT fall to the heuristic: post-exhaustion heuristic verdicts
    # for one arm only would bias the delta. Non-budget errors keep failing open.
    import pytest
    from maverick.budget import BudgetExceeded
    with pytest.raises(BudgetExceeded):
        ev.llm_judge(_BrokeLLM())("g", "the answer is DONE", "done")
    assert ev.llm_judge(_FakeLLM(raises=True))("g", "the answer is DONE", "done") is True


def test_exhausted_budget_is_indeterminate_through_the_scorers():
    # Through corpus_ab_scorers + _rate the raised case is EXCLUDED, and an arm
    # exhausted entirely goes NaN -- which validate_proposal's finite-check
    # rejects. The evaluation fails closed end to end.
    import math
    sw, _wo = ev.corpus_ab_scorers(
        [{"goal": "g", "expected": "x"}], run_fn=ev.llm_runner(_BrokeLLM()))
    assert math.isnan(sw("candidate line", ["g"]))


# ---- llm_paraphraser (the metamorphic seam, built) + judge_unknown --------

class _ParaLLM:
    """Scripted paraphraser: per-call behavior "ok" | "echo" | "empty" | "raise"."""

    def __init__(self, behaviors):
        self._b = list(behaviors)
        self.model = "fake:para"
        self.calls = 0

    def complete(self, system, messages, **kw):
        mode = self._b[min(self.calls, len(self._b) - 1)]
        self.calls += 1
        if mode == "raise":
            raise RuntimeError("provider down")
        goal = messages[0]["content"]
        text = {"ok": "REWORDED " + goal, "echo": goal, "empty": ""}[mode]
        return type("R", (), {"text": text})()


def test_llm_paraphraser_rewrites_and_drops_bad_outputs():
    # A failed, empty, or UNCHANGED paraphrase is dropped -- an identical
    # "paraphrase" would let an overfit line pass the very check this builds.
    p = ev.llm_paraphraser(_ParaLLM(["ok", "echo", "empty", "raise", "ok"]))
    assert p(["g1", "g2", "g3", "g4", "g5"]) == ["REWORDED g1", "REWORDED g5"]
    assert ev.llm_paraphraser(_ParaLLM(["raise"]))(["g"]) == []   # none usable
    assert ev.llm_paraphraser(_ParaLLM(["ok"]))([]) == []


def test_llm_paraphraser_reraises_budget_exceeded():
    import pytest
    from maverick.budget import BudgetExceeded
    with pytest.raises(BudgetExceeded):
        ev.llm_paraphraser(_BrokeLLM())(["g"])


def test_corpus_scorers_judge_unknown_scores_paraphrases():
    # Default: a non-corpus goal is indeterminate (NaN arm) -- so the
    # metamorphic branch no-ops. judge_unknown lets a hint-free judge score
    # paraphrases with an empty expected hint, making the check real.
    import math

    def judge(goal, output, expected):
        return "OK" in output

    def run(line, goal):
        return "OK" if line else "no"

    cases = [{"goal": "g", "expected": "x"}]
    sw, _ = ev.corpus_ab_scorers(cases, run_fn=run, judge_fn=judge)
    assert math.isnan(sw("line", ["not-in-corpus"]))              # unchanged default
    sw2, swo2 = ev.corpus_ab_scorers(cases, run_fn=run, judge_fn=judge,
                                     judge_unknown=True)
    assert sw2("line", ["not-in-corpus"]) == 1.0                  # actually scored
    assert swo2("line", ["not-in-corpus"]) == 0.0
    assert sw2("line", ["g"]) == 1.0                              # corpus goals unchanged


def test_corpus_scorer_indeterminate_for_unknown_goal():
    import math
    cases = [{"goal": "g0", "expected": "ok"}]
    sw, _wo = ev.corpus_ab_scorers(cases, run_fn=lambda line, g: "ok")
    assert sw("line", ["g0"]) == 1.0                  # corpus goal judged
    assert math.isnan(sw("line", ["not-in-corpus"]))  # unknown -> indeterminate -> NaN


def test_metamorphic_skips_cleanly_with_corpus_heuristic_scorer():
    from maverick import self_harness as sh
    cases = [{"goal": f"g{i}", "expected": "ok"} for i in range(6)]
    held_in, held_out = ev.corpus_split(cases, held_out_frac=0.5)
    sw, wo = ev.corpus_ab_scorers(cases, run_fn=lambda line, g: "ok" if line else "no")
    p = sh.HarnessProposal("M", "sig", "Bound the window.", "r")
    # paraphrased goals aren't corpus members -> metamorphic scoring is NaN -> the
    # metamorphic branch cleanly skips (no silent pass-with-zeros, no false reject)
    vr = sh.validate_proposal(
        p, held_in=held_in, held_out=held_out, score_with=sw, score_without=wo,
        metamorphic_fn=lambda goals: [f"PARA::{g}" for g in goals])
    assert vr.accepted
