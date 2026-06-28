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
