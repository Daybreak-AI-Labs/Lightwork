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
