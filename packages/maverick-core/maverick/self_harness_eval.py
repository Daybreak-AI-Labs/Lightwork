"""Reference-free evaluation for the self-harness A/B -- BUILDING the score seam.

``validate_proposal`` takes injected ``score_with``/``score_without`` because a
real held-in/held-out evaluation needs a real model. This module supplies that
seam concretely:

* a curated eval **corpus** -- ``{key: [{goal, expected}, ...]}`` per model or
  domain -- with a DETERMINISTIC held-in/held-out split (no randomness, so a
  re-run validates against the same cases);
* ``corpus_ab_scorers`` -- composes an injected ``run_fn`` (generate an answer
  under the candidate prompt) and ``judge_fn`` (was the answer a success?) into
  the ``(score_with, score_without)`` pair the validator expects. The judge is
  what decides success, so the evaluation is **reference-free** in the paper's
  sense -- no exact-match label is required beyond the corpus's own ``expected``
  hint, which the built-in heuristic judge uses and a real LLM judge may ignore.

``run_fn``/``judge_fn`` are the live-model seams (a real evaluation needs a real
model); everything here is deterministic and offline-testable by injecting stub
run/judge fns or using the built-in heuristic judge. Nothing here promotes
anything -- it only produces the scores the governed validator/gate consume.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path

log = logging.getLogger(__name__)


# ---- corpus -------------------------------------------------------------

def load_eval_corpus(path: str | Path) -> dict[str, list[dict]]:
    """Load a ``{key: [{"goal":..., "expected":...}, ...]}`` eval corpus from JSON.

    ``key`` is a model id or a domain tag (whatever the caller splits/scores by).
    Tolerant: a missing file, bad JSON, or malformed entries yield ``{}`` /
    skipped rows rather than raising -- an eval corpus is operator data and a typo
    must not crash a learning pass."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for key, rows in data.items():
        if not isinstance(rows, list):
            continue
        cases = []
        for r in rows:
            if isinstance(r, Mapping) and str(r.get("goal") or "").strip():
                cases.append({"goal": str(r["goal"]),
                              "expected": str(r.get("expected") or "")})
        if cases:
            out[str(key)] = cases
    return out


def corpus_cases(corpus: dict[str, list[dict]], key: str) -> list[dict]:
    """The cases for ``key`` (empty if absent)."""
    return list(corpus.get(str(key)) or [])


def _goal_rank(goal: str) -> str:
    """Stable content hash of a goal -- the deterministic split key (no RNG, so
    the same corpus always splits the same held-in/held-out cases)."""
    return hashlib.sha256(goal.encode("utf-8")).hexdigest()


def corpus_split(cases: list[dict], *, held_out_frac: float = 0.3,
                 ) -> tuple[list[str], list[str]]:
    """Split corpus cases into ``(held_in_goals, held_out_goals)`` DETERMINISTICALLY.

    Cases are ordered by a stable content hash of their goal and the last
    ``held_out_frac`` become the unseen split -- reproducible across runs (the
    overfitting guard needs a *stable* unseen set, not a random one each pass).
    Always leaves at least one case on each side when there are >= 2 cases."""
    goals = [str(c.get("goal") or "") for c in cases if str(c.get("goal") or "").strip()]
    goals = sorted(set(goals), key=_goal_rank)
    n = len(goals)
    if n == 0:
        return [], []
    if n == 1:
        return goals, []
    frac = min(max(float(held_out_frac), 0.0), 1.0)
    n_out = min(n - 1, max(1, round(n * frac)))
    return goals[:n - n_out], goals[n - n_out:]


# ---- reference-free A/B scorers (the injected seam, built) ---------------

def _heuristic_judge(goal: str, output: str, expected: str) -> bool:
    """Deterministic offline judge default: success when the (normalized)
    ``expected`` hint appears in the output. NOT a real evaluator -- inject a
    ``judge_fn`` (LLM-as-judge / self-consistency) for production -- but it makes
    the whole seam runnable and testable WITHOUT a model."""
    out = " ".join(str(output or "").lower().split())
    exp = " ".join(str(expected or "").lower().split())
    return bool(exp) and exp in out


def _rate(goals: list[str], success_of: Callable[[str], bool]) -> float:
    vals = []
    for g in goals or []:
        try:
            vals.append(1.0 if success_of(g) else 0.0)
        except Exception:  # one bad case can't break the whole score
            log.debug("eval: scoring case failed", exc_info=True)
    return sum(vals) / len(vals) if vals else 0.0


def corpus_ab_scorers(
    cases: list[dict], *, run_fn: Callable[[str, str], str],
    judge_fn: Callable[[str, str, str], bool] | None = None,
) -> tuple[Callable[[str, list[str]], float], Callable[[str, list[str]], float]]:
    """Build the ``(score_with, score_without)`` pair ``validate_proposal`` needs.

    ``run_fn(line, goal) -> output`` generates an answer with the candidate
    guidance ``line`` injected (``""`` = baseline, no line); ``judge_fn(goal,
    output, expected) -> bool`` decides success (defaults to the heuristic judge).
    ``score_with`` runs WITH the line, ``score_without`` runs the baseline -- so
    their delta is exactly the line's causal lift on the corpus. Both are pure
    means over the passed goals, matching the ``ScoreFn`` contract."""
    judge = judge_fn or _heuristic_judge
    expected = {str(c.get("goal") or ""): str(c.get("expected") or "") for c in cases}

    def score_with(line: str, goals: list[str]) -> float:
        return _rate(goals, lambda g: judge(g, run_fn(line, g), expected.get(g, "")))

    def score_without(_line: str, goals: list[str]) -> float:
        return _rate(goals, lambda g: judge(g, run_fn("", g), expected.get(g, "")))

    return score_with, score_without


__all__ = [
    "load_eval_corpus", "corpus_cases", "corpus_split", "corpus_ab_scorers",
]
