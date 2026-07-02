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

from .budget import BudgetExceeded

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


def corpus_kfold_splits(cases: list[dict], *, k: int = 5,
                        ) -> list[tuple[list[str], list[str]]]:
    """Partition corpus cases into ``k`` DETERMINISTIC ``(held_in, held_out)``
    rotations -- the holdout-rotation basis for cross-validating a candidate line.

    Goals are ordered by the same stable content hash :func:`corpus_split` uses,
    then split into ``k`` near-equal contiguous folds; rotation ``i`` holds OUT
    fold ``i`` and holds IN the rest, so every case is unseen in exactly one fold.
    A single fixed 30%% holdout can flatter (or punish) a line by luck; requiring
    the lift to hold across rotations is the lower-variance generalization test.

    ``k`` is clamped to ``[1, n]``. With ``< 2`` distinct goals there is no usable
    holdout, so a single ``(goals, [])`` rotation is returned (the caller then
    falls back to the in-sample check, exactly like :func:`corpus_split`). Pure
    function -- no RNG, no I/O -- so a re-run cross-validates against the same folds."""
    goals = [str(c.get("goal") or "") for c in cases if str(c.get("goal") or "").strip()]
    goals = sorted(set(goals), key=_goal_rank)
    n = len(goals)
    if n < 2:
        return [(goals, [])]
    k = max(1, min(int(k), n))
    if k == 1:
        return [(goals, [])]
    # Near-equal contiguous folds: the first (n % k) folds get one extra goal, so
    # fold sizes differ by at most one and every goal lands in exactly one fold.
    base, extra = divmod(n, k)
    bounds, start = [], 0
    for i in range(k):
        size = base + (1 if i < extra else 0)
        bounds.append((start, start + size))
        start += size
    out: list[tuple[list[str], list[str]]] = []
    for lo, hi in bounds:
        held_out = goals[lo:hi]
        held_in = goals[:lo] + goals[hi:]
        out.append((held_in, held_out))
    return out


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
    """Mean success over ``goals``. A case that RAISES is INDETERMINATE and is
    excluded (not counted as a failure). If goals were given but EVERY one was
    indeterminate, return NaN -- not 0.0 -- so the caller's ``math.isfinite`` guard
    (validate_proposal) rejects the run instead of treating an all-error arm as a
    0% pass rate, which would otherwise inflate the measured lift of the other arm.
    An empty ``goals`` list is a legitimately empty split -> 0.0 (unchanged)."""
    vals = []
    attempted = 0
    for g in goals or []:
        attempted += 1
        try:
            vals.append(1.0 if success_of(g) else 0.0)
        except Exception:  # one bad case is indeterminate, excluded (not a failure)
            log.debug("eval: scoring case failed", exc_info=True)
    if vals:
        return sum(vals) / len(vals)
    return float("nan") if attempted else 0.0


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
    means over the passed goals, matching the ``ScoreFn`` contract.

    A goal NOT in the corpus is INDETERMINATE (the scorer raises ``KeyError``,
    which ``_rate`` excludes) rather than a silent failure -- so metamorphic
    validation, whose paraphrased goals are not corpus members, cleanly SKIPS (an
    all-indeterminate arm is NaN and ``validate_proposal``'s finite-check no-ops
    the metamorphic branch) with the deterministic heuristic judge instead of
    being a silent pass. Inject an ``llm_judge`` to actually judge paraphrases."""
    judge = judge_fn or _heuristic_judge
    expected = {str(c.get("goal") or ""): str(c.get("expected") or "") for c in cases}

    def score_with(line: str, goals: list[str]) -> float:
        return _rate(goals, lambda g: judge(g, run_fn(line, g), expected[g]))

    def score_without(_line: str, goals: list[str]) -> float:
        return _rate(goals, lambda g: judge(g, run_fn("", g), expected[g]))

    return score_with, score_without


# ---- LLM-backed run/judge (the live seam, concretely) --------------------
# corpus_ab_scorers takes injected run_fn/judge_fn; these BUILD them on a real
# provider -- same shape as llm_proposer (an injected LLM, fail-open). A flaky or
# absent model degrades to a safe default (judge -> heuristic, run -> "") so the
# evaluation never crashes a learning pass. Offline-tested with a fake LLM.
#
# The ONE exception is an exhausted Budget: BudgetExceeded re-raises instead of
# degrading, so the case lands INDETERMINATE in ``_rate`` (excluded) rather than
# scored. Failing open here would poison the A/B -- exhaustion mid-pass hits the
# arms asymmetrically (the baseline arm runs after the candidate arm), so a
# runner returning "" or a judge falling to the heuristic for just one arm would
# skew the measured delta and could promote on garbage. Raised cases are
# excluded per-case; an arm exhausted entirely goes NaN and the validator's
# finite-check rejects the candidate -- the evaluation fails CLOSED.

# Self-consistency framings: meaning-PRESERVING rephrasings of the same yes/no
# question. Sampling the judge across diverse reasoning paths and taking the
# majority vote is more robust than one call (Wang et al., self-consistency) --
# and because the seam exposes no temperature knob, varying the FRAMING is what
# diversifies the paths, so it works even with a near-deterministic judge.
_JUDGE_FRAMINGS = (
    "You are a strict evaluator. Answer ONLY 'yes' or 'no': did the OUTPUT "
    "correctly accomplish the GOAL?",
    "Grade the OUTPUT against the GOAL. Reply with a single word -- 'yes' if it "
    "fully accomplishes the goal, 'no' otherwise.",
    "As a careful judge, decide whether the OUTPUT satisfies the GOAL. Respond "
    "'yes' or 'no' only.",
)


def llm_judge(llm, *, budget=None, model: str | None = None, max_tokens: int = 8,
              samples: int = 1) -> Callable[[str, str, str], bool]:
    """An LLM-as-judge ``judge_fn`` for :func:`corpus_ab_scorers`: ask the model
    whether the OUTPUT accomplished the GOAL (yes/no). The model is NOT hard-coded
    (kernel rule 2). Falls open to :func:`_heuristic_judge` on any provider error
    or an unparseable answer, so evaluation is robust to a flaky judge.

    ``samples`` > 1 enables SELF-CONSISTENCY: the judge is asked ``samples`` times,
    each with a different meaning-preserving framing of the question (diverse
    reasoning paths, since the seam has no temperature knob), and the MAJORITY
    yes/no vote wins. A tie or all-indeterminate samples fall open to the
    heuristic. ``samples == 1`` (the default) is the single-call behavior,
    unchanged."""
    n = max(1, int(samples))

    def _judge(goal: str, output: str, expected: str) -> bool:
        yes = no = 0
        for i in range(n):
            try:
                system = _JUDGE_FRAMINGS[i % len(_JUDGE_FRAMINGS)]
                user = (f"GOAL:\n{goal}\n\nOUTPUT:\n{output}\n\n"
                        f"Expected (hint): {expected}\n\nyes/no:")
                resp = llm.complete(system, [{"role": "user", "content": user}],
                                    budget=budget, max_tokens=max_tokens, model=model)
                text = (getattr(resp, "text", "") or "").strip().lower()
                if text.startswith("yes"):
                    yes += 1
                elif text.startswith("no"):
                    no += 1
                # an unparseable sample is INDETERMINATE -- it abstains, it doesn't vote
            except BudgetExceeded:
                raise  # exhausted budget fails the CASE closed, never the heuristic
            except Exception as e:  # pragma: no cover -- one bad sample abstains
                log.warning("eval: llm judge sample failed (%s)", e)
        if yes > no:
            return True
        if no > yes:
            return False
        # tie (incl. zero usable votes) -> deterministic heuristic tie-break
        return _heuristic_judge(goal, output, expected)
    return _judge


def llm_runner(llm, *, system_prefix: str = "", budget=None, model: str | None = None,
               max_tokens: int = 512) -> Callable[[str, str], str]:
    """An LLM-backed ``run_fn`` for :func:`corpus_ab_scorers`: generate an answer
    for the goal with the candidate guidance ``line`` injected into the system
    prompt (``""`` = baseline, no line). The model is NOT hard-coded. Returns
    ``""`` on any provider error -- a failed generation simply scores as a
    non-success, never crashing the pass."""
    def _run(line: str, goal: str) -> str:
        try:
            system = system_prefix
            if line:
                system = (system + "\n" if system else "") + line
            resp = llm.complete(system or "You are a helpful assistant.",
                                [{"role": "user", "content": goal}],
                                budget=budget, max_tokens=max_tokens, model=model)
            return getattr(resp, "text", "") or ""
        except BudgetExceeded:
            raise  # exhausted budget is indeterminate, not a scored failure
        except Exception as e:  # pragma: no cover -- a failed generation isn't a success
            log.warning("eval: llm runner failed (%s)", e)
            return ""
    return _run


__all__ = [
    "load_eval_corpus", "corpus_cases", "corpus_split", "corpus_kfold_splits",
    "corpus_ab_scorers", "llm_judge", "llm_runner",
]
