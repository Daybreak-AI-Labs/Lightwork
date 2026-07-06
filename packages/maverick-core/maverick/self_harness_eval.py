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
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from .budget import BudgetExceeded

log = logging.getLogger(__name__)


# ---- corpus -------------------------------------------------------------

def _world_corpus_kind(path: str | Path) -> str | None:
    """Which world-corpus family a path denotes when the operator selected
    the world learning store (``[self_harness] store = "world"`` -- phase 2 of
    docs/proposals/fleet-learning-state.md): the configured ``eval_corpus``
    path is ``"live"``, its harvest sidecars ``"pending"``/``"rejected"``.
    ``None`` (file semantics, byte-identical) for any other path or when the
    store is files -- so tests and ad-hoc paths are unchanged."""
    try:
        from .self_harness import settings
        st = settings()
        if str(st.get("store") or "files").strip().lower() != "world":
            return None
        base = st.get("eval_corpus")
        if not base:
            return None
        p = str(Path(path))
        if p == str(Path(base)):
            return "live"
        if p == str(pending_corpus_path(base)):
            return "pending"
        if p == str(rejected_corpus_path(base)):
            return "rejected"
    except Exception:  # pragma: no cover -- config trouble means file store
        return None
    return None


def _corpus_rmw_lock(corpus_path: str | Path):
    """DB-side critical-section lock for world-routed corpus writes (flock
    only serializes one host); a no-op for the file store."""
    if _world_corpus_kind(corpus_path) is not None:
        from . import learning_store
        return learning_store.rmw_lock()
    import contextlib
    return contextlib.nullcontext()


def _read_json_text(path: str | Path) -> str:
    """The file's JSON text, transparently unsealing an at-rest-encrypted
    sidecar (a plaintext file is returned unchanged -- ``unseal`` is
    plaintext-tolerant). A sealed blob that cannot be opened surfaces as
    ``ValueError`` so the tolerant loaders' catch tuples apply. A
    world-routed corpus path serves the same JSON from the world store."""
    kind = _world_corpus_kind(path)
    if kind is not None:
        from . import learning_store
        return json.dumps(learning_store.load_corpus_db(kind))
    raw = Path(path).read_bytes()
    try:
        from .crypto_at_rest import unseal
        raw = unseal(raw)
    except Exception as e:
        raise ValueError(f"unreadable sealed corpus file {path}: {e}") from e
    return raw.decode("utf-8")


def load_eval_corpus(path: str | Path) -> dict[str, list[dict]]:
    """Load a ``{key: [{"goal":..., "expected":...}, ...]}`` eval corpus from JSON.

    ``key`` is a model id or a domain tag (whatever the caller splits/scores by).
    Tolerant: a missing file, bad JSON, or malformed entries yield ``{}`` /
    skipped rows rather than raising -- an eval corpus is operator data and a typo
    must not crash a learning pass."""
    try:
        data = json.loads(_read_json_text(path))
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
    A :class:`BudgetExceeded` makes the WHOLE arm indeterminate, not just the one
    case: the pot never un-exhausts mid-arm, so a partial prefix mean would be
    compared against a full-corpus other arm with a fabricated sample size --
    return NaN immediately and let the finite-check reject (fail closed).
    An empty ``goals`` list is a legitimately empty split -> 0.0 (unchanged)."""
    vals = []
    attempted = 0
    for g in goals or []:
        attempted += 1
        try:
            vals.append(1.0 if success_of(g) else 0.0)
        except BudgetExceeded:  # a dead pot invalidates the whole arm
            log.debug("eval: budget exhausted mid-arm; arm is indeterminate")
            return float("nan")
        except Exception:  # one bad case is indeterminate, excluded (not a failure)
            log.debug("eval: scoring case failed", exc_info=True)
    if vals:
        return sum(vals) / len(vals)
    return float("nan") if attempted else 0.0


def corpus_ab_scorers(
    cases: list[dict], *, run_fn: Callable[[str, str], str],
    judge_fn: Callable[[str, str, str], bool] | None = None,
    judge_unknown: bool = False,
) -> tuple[Callable[[str, list[str]], float], Callable[[str, list[str]], float]]:
    """Build the ``(score_with, score_without)`` pair ``validate_proposal`` needs.

    ``run_fn(line, goal) -> output`` generates an answer with the candidate
    guidance ``line`` injected (``""`` = baseline, no line); ``judge_fn(goal,
    output, expected) -> bool`` decides success (defaults to the heuristic judge).
    ``score_with`` runs WITH the line, ``score_without`` runs the baseline -- so
    their delta is exactly the line's causal lift on the corpus. Both are pure
    means over the passed goals, matching the ``ScoreFn`` contract.

    By default a goal NOT in the corpus is INDETERMINATE (the scorer raises
    ``KeyError``, which ``_rate`` excludes) rather than a silent failure -- so
    metamorphic validation, whose paraphrased goals are not corpus members,
    cleanly SKIPS (an all-indeterminate arm is NaN and ``validate_proposal``'s
    finite-check no-ops the metamorphic branch). That is the right behavior for
    the hint-dependent heuristic judge, but it also silently no-ops the
    metamorphic check for ANY judge. ``judge_unknown`` opts a hint-free judge
    (an ``llm_judge``) into scoring unknown goals with an empty expected hint,
    which is what makes paraphrase-robustness validation real on this seam."""
    judge = judge_fn or _heuristic_judge
    expected = {str(c.get("goal") or ""): str(c.get("expected") or "") for c in cases}
    expected_of = expected.get if judge_unknown else expected.__getitem__

    def _degraded() -> int:
        # llm_runner/llm_judge count fail-open degradations (provider errors
        # swallowed into ""/heuristic). Non-LLM seams have no counter -> 0.
        return (int(getattr(run_fn, "degraded", 0))
                + int(getattr(judge, "degraded", 0)))

    def score_with(line: str, goals: list[str]) -> float:
        before = _degraded()
        r = _rate(goals, lambda g: judge(g, run_fn(line, g), expected_of(g) or ""))
        score_with.last_clean = _degraded() == before
        return r

    def score_without(_line: str, goals: list[str]) -> float:
        before = _degraded()
        r = _rate(goals, lambda g: judge(g, run_fn("", g), expected_of(g) or ""))
        score_without.last_clean = _degraded() == before
        return r

    # ``last_clean`` marks whether the most recent call completed without any
    # fail-open degradation -- a memoizing caller must never cache a dirty
    # score (an outage 0.0 is indistinguishable from a real 0.0 afterwards).
    score_with.last_clean = True
    score_without.last_clean = True
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
                _judge.degraded += 1
                log.warning("eval: llm judge sample failed (%s)", e)
        if yes == no:
            # tie (incl. zero usable votes) -> deterministic heuristic tie-break;
            # the LLM abstained, so there is no verdict to calibrate either.
            return _heuristic_judge(goal, output, expected)
        verdict = yes > no
        _note_judge_verdict(goal, output, expected, verdict,
                            confidence=max(yes, no) / (yes + no))
        return verdict
    # Fail-open degradation counter (provider errors swallowed into abstains);
    # corpus_ab_scorers reads it to mark a score dirty for memoizing callers.
    _judge.degraded = 0
    return _judge


def _note_judge_verdict(goal: str, output: str, expected: str, verdict: bool, *,
                        confidence: float) -> None:
    """Feed one LLM-judge verdict into the calibration interlock (opt-in,
    ``[self_harness] calibrate_judge``) -- so ``calibration.learning_frozen``
    can detect THIS loop's judge drifting, not only the coding verifier's.

    Ground truth is the corpus's operator-authored ``expected`` label, checked
    by the same deterministic heuristic the judge falls back to: ``correct`` is
    whether the LLM's verdict AGREES with that label. ``confidence`` is the
    self-consistency vote share (1.0 for a single-call judge). An UNLABELED
    case -- e.g. a metamorphic paraphrase judged under ``judge_unknown`` with an
    empty hint -- carries no ground truth and is skipped, as is a tie (the
    caller never gets here on one). Best-effort; never perturbs an evaluation."""
    try:
        if not expected:
            return
        from .self_harness import settings
        if not settings().get("calibrate_judge"):
            return
        from .self_improvement_runner import collect_calibration
        correct = _heuristic_judge(goal, output, expected) == bool(verdict)
        # The [self_harness] knob IS the gate for this source; don't also
        # require the coding-mode collection toggle.
        collect_calibration(float(confidence), correct,
                            source="self_harness_judge", enabled_fn=lambda: True)
    except Exception:  # pragma: no cover -- calibration is telemetry, never load-bearing
        log.debug("eval: judge calibration note failed", exc_info=True)


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
            _run.degraded += 1
            log.warning("eval: llm runner failed (%s)", e)
            return ""
    # Fail-open degradation counter (provider errors swallowed into "");
    # corpus_ab_scorers reads it to mark a score dirty for memoizing callers.
    _run.degraded = 0
    return _run


def llm_paraphraser(llm, *, budget=None, model: str | None = None,
                    max_tokens: int = 256) -> Callable[[list[str]], list[str]]:
    """An LLM-backed ``metamorphic_fn`` for ``validate_proposal``: rewrite each
    held-out goal in different words, preserving its meaning, so a candidate
    line's lift is tested against wording it was never validated on -- a line
    that only helps the exact phrasing is overfit to surface form. The model is
    NOT hard-coded (kernel rule 2); the driver wires the summarizer role.

    One call per goal. A goal whose paraphrase fails, comes back empty, or
    comes back UNCHANGED is DROPPED -- an identical "paraphrase" would let an
    overfit line sail through the very check this builds. Returns the surviving
    paraphrases; ``[]`` (no usable transform) makes the validator skip the
    metamorphic branch, exactly like every other degraded transform."""
    def _paraphrase(goals: list[str]) -> list[str]:
        out: list[str] = []
        for g in goals or []:
            try:
                resp = llm.complete(
                    "Rewrite the task below in different words, preserving its "
                    "meaning exactly. Return ONLY the rewritten task -- no "
                    "preamble, no quotes, no markdown.",
                    [{"role": "user", "content": str(g)}],
                    budget=budget, max_tokens=max_tokens, model=model)
                text = (getattr(resp, "text", "") or "")
                line = next((s.strip() for s in text.splitlines() if s.strip()), "")
                if line and line != str(g):
                    out.append(line)
            except BudgetExceeded:
                raise  # the validator catches this and skips the check whole
            except Exception as e:  # pragma: no cover -- one bad paraphrase drops
                log.warning("eval: llm paraphraser failed (%s)", e)
        return out
    return _paraphrase


# ---- corpus bootstrapping (hindsight-pair harvest) ------------------------
# The eval corpus is the loop's ground truth, and authoring it by hand is the
# last manual step. HINDSIGHT PAIRS are the strongest natural labels a run
# history offers: a goal that FAILED (a reflexion exists) and whose wording
# later ran to DONE. The succeeded goal's text becomes the case's ``goal`` and
# a short fragment of its recorded result becomes the ``expected`` hint.
# Modes (the operator chooses): "propose" stages candidates in a pending file
# an operator reviews (`self-harness corpus review`) before anything enters
# the live corpus; "auto" merges directly -- knob-gated, eyes open (label-free
# ground truth degrades silently; ACE's caveat).

_HINT_CHARS = 80


def _htokens(text: str) -> set[str]:
    # Reuse mining's canonical tokenizer so harvest pairing can never drift
    # from the token rules the rest of the subsystem indexes with.
    from .self_harness import _tokens
    return _tokens(text)


def _result_hint(result: str) -> str:
    """A short, distinctive ``expected`` hint from a succeeded goal's result:
    the first non-empty line, whitespace-normalized, clipped."""
    for ln in str(result or "").splitlines():
        ln = " ".join(ln.split())
        if ln:
            return ln[:_HINT_CHARS]
    return ""


# Several reflexion recorders clip goal_text to its first 500 chars; overlap is
# also checked against the same-length goal prefix so a long goal's truncated
# failure record can still pair with its own later success.
_REFL_TEXT_CLIP = 500


def harvest_corpus_candidates(reflexions: list, done_goals: list, *,
                              min_overlap: float = 0.5,
                              max_candidates: int = 20,
                              known: set[str] | None = None) -> list[dict]:
    """Mine ``{goal, expected}`` corpus candidates from hindsight pairs.

    A reflexion (a recorded failure, already unscoped-filtered by the caller)
    pairs with a DONE goal when the goal wording strongly overlaps
    (token-Jaccard ``>= min_overlap``, also checked against the goal's first
    500 chars to match recorders that clip ``goal_text``) and the success came
    LATER than the failure -- the task demonstrably went from failing to
    working, so its result is operator-visible ground truth, not a guess. A
    pair missing a timestamp on either side is REJECTED (fail closed): without
    both, the failed-then-worked ordering cannot be proven. ``known`` goals
    (already live, pending, or operator-rejected) are excluded BEFORE the
    ``max_candidates`` cap, so long-known goals can't starve fresh candidates
    out of the slice. Deterministic and pure: same inputs, same candidates,
    ordered by goal text; deduped; goals with empty results yield nothing (no
    hint, no case)."""
    from .self_harness import _jaccard
    skip = known or set()
    done = []
    for g in done_goals or []:
        text = f"{getattr(g, 'title', '') or ''}\n{getattr(g, 'description', '') or ''}".strip()
        hint = _result_hint(getattr(g, "result", "") or "")
        ts = getattr(g, "updated_at", None) or getattr(g, "created_at", None) or 0
        if text and hint and text not in skip:
            done.append((text, hint, float(ts or 0), _htokens(text),
                         _htokens(text[:_REFL_TEXT_CLIP])))
    out: dict[str, str] = {}
    for r in reflexions or []:
        rtext = str((r.get("goal_text") if isinstance(r, dict)
                     else getattr(r, "goal_text", "")) or "")
        rts = float((r.get("ts") if isinstance(r, dict)
                     else getattr(r, "ts", 0)) or 0)
        rtok = _htokens(rtext)
        if not rtok:
            continue
        for text, hint, ts, ttok, ttok_clip in done:
            if not (ts and rts) or ts < rts:
                continue  # need BOTH timestamps, and the success at/after the failure
            if max(_jaccard(rtok, ttok), _jaccard(rtok, ttok_clip)) >= min_overlap:
                out.setdefault(text, hint)
    return [{"goal": g, "expected": out[g]}
            for g in sorted(out)][:max(0, int(max_candidates))]


def pending_corpus_path(corpus_path: str | Path) -> Path:
    return Path(str(corpus_path) + ".pending.json")


def rejected_corpus_path(corpus_path: str | Path) -> Path:
    return Path(str(corpus_path) + ".rejected.json")


def load_pending(corpus_path: str | Path) -> dict[str, list[dict]]:
    """The staged-candidates file, same ``{key: [cases]}`` shape as the corpus."""
    return load_eval_corpus(pending_corpus_path(corpus_path))


def load_rejected(corpus_path: str | Path) -> dict[str, list[str]]:
    """``{key: [goal, ...]}`` -- goals an operator rejected in review. The
    durable NO: without it the nightly harvest would re-stage the same
    candidate every beat until its source rows aged out of the mining windows."""
    try:
        data = json.loads(_read_json_text(rejected_corpus_path(corpus_path)))
        if isinstance(data, dict):
            return {str(k): [str(g) for g in v]
                    for k, v in data.items() if isinstance(v, list)}
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {}


# Bounds the reject memory per key -- comfortably above the harvest's own
# mining windows, so a verdict outlives every re-minable source row.
_REJECTED_CAP = 500

# The corpus, pending, and rejected files form one logical store mutated from
# more than one process (the cron dream beat vs. an interactive review), so
# every read-modify-write below serializes on the LIVE corpus path -- the
# pending/rejected sidecars derive from it, one lock covers the trio. Same
# in-process + flock pattern as the addenda store's mutators.
_corpus_lock = threading.Lock()


def _write_corpus_file(path: Path, data: dict, *, seal: bool = False) -> None:
    """Serialize a corpus-family file. ``seal`` opts a MACHINE-OWNED sidecar
    (pending/rejected) into at-rest encryption when the deployment seals its
    stores -- goal text there is the same content the world DB seals. The LIVE
    corpus is operator-authored/hand-editable by design and never sealed here.
    A sealing failure propagates (fail closed, per crypto_at_rest.seal): the
    caller must not silently write sensitive plaintext. A world-routed corpus
    path writes rows to the world store instead (the world DB is its own
    at-rest surface; goal text is already secret-redacted before staging)."""
    kind = _world_corpus_kind(path)
    if kind is not None:
        from . import learning_store
        learning_store.write_corpus_db(kind, data)
        return
    text = json.dumps(data, indent=2, sort_keys=True)
    if seal:
        from .crypto_at_rest import at_rest_enabled
        if at_rest_enabled():
            from .crypto_at_rest import seal_text
            from .file_lock import atomic_write_bytes
            atomic_write_bytes(Path(path), seal_text(text), mode=0o600)
            return
    from .file_lock import atomic_write_text
    atomic_write_text(Path(path), text, mode=0o600)


def _load_raw(path: str | Path) -> dict:
    """The file's raw JSON dict (tolerant: ``{}`` on any error). Writers merge
    into THIS, not the normalized :func:`load_eval_corpus` view -- rewriting the
    normalized view would silently strip operator-authored per-case fields and
    unknown keys from the live ground-truth file."""
    try:
        data = json.loads(_read_json_text(path))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {}


def _add_fresh(dest_path: Path, dest_raw: dict, key: str,
               candidates: list[dict], known: set[str], *,
               seal: bool = False) -> int:
    """The one owner of candidate validity + dedup: append candidates whose
    goal is non-empty, labeled, and not in ``known`` to the RAW ``dest_raw[key]``
    rows and write the file. Returns how many were added (0 writes nothing)."""
    fresh = [c for c in candidates or []
             if c.get("goal") and c.get("expected") and c["goal"] not in known]
    if not fresh:
        return 0
    rows = dest_raw.get(str(key))
    if not isinstance(rows, list):
        rows = dest_raw[str(key)] = []
    # ``added_at`` rides in the RAW file only (the normalized eval view strips
    # it): it is what lets the quality lifecycle age harvested cases later.
    now = time.time()
    rows.extend({"goal": c["goal"], "expected": c["expected"], "added_at": now}
                for c in fresh)
    _write_corpus_file(dest_path, dest_raw, seal=seal)
    return len(fresh)


def _stage_locked(corpus_path: str | Path, key: str,
                  candidates: list[dict]) -> int:
    known = {c["goal"] for c in load_eval_corpus(corpus_path).get(str(key), [])}
    known |= {c["goal"] for c in load_pending(corpus_path).get(str(key), [])}
    known |= set(load_rejected(corpus_path).get(str(key), []))
    return _add_fresh(pending_corpus_path(corpus_path),
                      _load_raw(pending_corpus_path(corpus_path)),
                      key, candidates, known, seal=True)


def _merge_locked(corpus_path: str | Path, key: str,
                  candidates: list[dict]) -> int:
    known = {c["goal"] for c in load_eval_corpus(corpus_path).get(str(key), [])}
    return _add_fresh(Path(corpus_path), _load_raw(corpus_path),
                      key, candidates, known)


def stage_candidates(corpus_path: str | Path, key: str,
                     candidates: list[dict]) -> int:
    """PROPOSE mode: add candidates for ``key`` to the pending file, skipping
    goals already live in the corpus, already pending, or previously REJECTED
    by an operator (a review verdict is durable). Returns how many were newly
    staged."""
    from .file_lock import cross_process_lock
    with _corpus_lock, cross_process_lock(Path(corpus_path)), \
            _corpus_rmw_lock(corpus_path):
        return _stage_locked(corpus_path, key, candidates)


def merge_candidates(corpus_path: str | Path, key: str,
                     candidates: list[dict]) -> int:
    """AUTO mode (or an accepted review): merge candidates for ``key`` straight
    into the live corpus, deduped by goal. Returns how many were added."""
    from .file_lock import cross_process_lock
    with _corpus_lock, cross_process_lock(Path(corpus_path)), \
            _corpus_rmw_lock(corpus_path):
        return _merge_locked(corpus_path, key, candidates)


def resolve_pending(corpus_path: str | Path, key: str, *,
                    accept: list[int] | None = None,
                    reject: list[int] | None = None,
                    accept_all: bool = False) -> dict:
    """Operator review verdicts over the pending file (1-based indexes as the
    CLI lists them): accepted candidates merge into the live corpus, rejected
    ones are dropped AND remembered (the harvest never re-stages them),
    everything else stays pending. ``accept_all`` accepts the remainder --
    explicit ``reject`` indexes still win, so ``--accept-all --reject 7`` means
    "all but 7". An index outside the current listing raises ``ValueError``
    (a silent no-op would be indistinguishable from a typo). Returns
    ``{"merged", "duplicates", "rejected", "accepted_goals",
    "rejected_goals"}`` -- ``duplicates`` counts accepted rows that were
    already live or unlabeled (resolved, but not re-added)."""
    from .file_lock import cross_process_lock
    with _corpus_lock, cross_process_lock(Path(corpus_path)), \
            _corpus_rmw_lock(corpus_path):
        rows = load_pending(corpus_path).get(str(key), [])
        if accept_all:
            rej_idx = {int(i) for i in (reject or [])}
            acc_idx = set(range(1, len(rows) + 1)) - rej_idx
        else:
            acc_idx = {int(i) for i in (accept or [])}
            rej_idx = {int(i) for i in (reject or [])} - acc_idx
        bad = (acc_idx | rej_idx) - set(range(1, len(rows) + 1))
        if bad:
            raise ValueError(
                f"pending index out of range: {sorted(bad)} "
                f"({len(rows)} candidate(s) pending)")
        accepted = [c for i, c in enumerate(rows, 1) if i in acc_idx]
        rejected_rows = [c for i, c in enumerate(rows, 1) if i in rej_idx]
        kept = [c for i, c in enumerate(rows, 1)
                if i not in acc_idx and i not in rej_idx]
        added = _merge_locked(corpus_path, key, accepted) if accepted else 0
        pending_raw = _load_raw(pending_corpus_path(corpus_path))
        if kept:
            pending_raw[str(key)] = kept
        else:
            pending_raw.pop(str(key), None)
        _write_corpus_file(pending_corpus_path(corpus_path), pending_raw,
                           seal=True)
        if rejected_rows:
            rej = load_rejected(corpus_path)
            seen = rej.get(str(key), [])
            seen.extend(c["goal"] for c in rejected_rows
                        if c["goal"] not in set(seen))
            rej[str(key)] = seen[-_REJECTED_CAP:]
            _write_corpus_file(rejected_corpus_path(corpus_path), rej,
                               seal=True)
        return {"merged": added, "duplicates": len(accepted) - added,
                "rejected": len(rejected_rows),
                "accepted_goals": [c["goal"] for c in accepted],
                "rejected_goals": [c["goal"] for c in rejected_rows]}


# ---- corpus quality lifecycle ---------------------------------------------
# The corpus is the loop's ground truth, but nothing above reviews the corpus
# ITSELF: a case the baseline model already always solves can no longer
# measure a candidate line's lift (non-discriminative), and it dilutes every
# split it lands in. The probe below measures per-case baseline pass rates so
# the operator can see -- and retire -- the dead weight.

def corpus_quality(cases: list[dict], *, run_fn: Callable[[str, str], str],
                   judge_fn: Callable[[str, str, str], bool] | None = None,
                   samples: int = 2) -> list[dict]:
    """Per-case discriminativeness of the corpus's ground truth.

    Runs the BASELINE (no candidate line) ``samples`` times per case and
    judges each attempt: a case the baseline passes every time cannot show a
    candidate's lift -- it is dead weight in every split. Returns one row per
    case: ``{goal, expected, passes, samples, baseline_rate,
    discriminative}`` where ``discriminative`` is False only when every
    sample passed. A sample that RAISES is indeterminate and excluded
    (``baseline_rate`` is over the judged samples; a case with no judged
    samples reports ``baseline_rate=None`` and stays discriminative --
    unknown is not dead). ``BudgetExceeded`` propagates: a half-probed
    quality report must not silently masquerade as a full one."""
    judge = judge_fn or _heuristic_judge
    out: list[dict] = []
    n = max(1, int(samples))
    for c in cases or []:
        goal = str(c.get("goal") or "")
        expected = str(c.get("expected") or "")
        if not goal:
            continue
        passes = judged = 0
        for _ in range(n):
            try:
                if judge(goal, run_fn("", goal), expected):
                    passes += 1
                judged += 1
            except BudgetExceeded:
                raise
            except Exception:  # one bad sample is indeterminate, excluded
                log.debug("eval: quality sample failed", exc_info=True)
        rate = (passes / judged) if judged else None
        out.append({"goal": goal, "expected": expected, "passes": passes,
                    "samples": judged, "baseline_rate": rate,
                    "discriminative": not (judged and passes == judged)})
    return out


def retire_corpus_cases(corpus_path: str | Path, key: str,
                        goals: list[str]) -> int:
    """Remove the named cases from the live corpus's ``key`` -- the quality
    lifecycle's pruning hand. RAW-preserving (other rows keep every
    operator-authored field) and serialized like every other corpus write.
    Returns how many rows were removed."""
    from .file_lock import cross_process_lock
    drop = {str(g) for g in goals or []}
    if not drop:
        return 0
    with _corpus_lock, cross_process_lock(Path(corpus_path)), \
            _corpus_rmw_lock(corpus_path):
        raw = _load_raw(corpus_path)
        rows = raw.get(str(key))
        if not isinstance(rows, list):
            return 0
        kept = [r for r in rows
                if not (isinstance(r, Mapping) and str(r.get("goal")) in drop)]
        removed = len(rows) - len(kept)
        if not removed:
            return 0
        if kept:
            raw[str(key)] = kept
        else:
            raw.pop(str(key), None)
        _write_corpus_file(Path(corpus_path), raw)
        return removed


def _merge_corpus_rows(dest: dict, incoming: dict) -> int:
    """Merge ``incoming`` (file-shaped corpus dict) into ``dest`` by goal:
    incoming rows whose goal is new append with EVERY field preserved;
    non-list top-level entries copy over only when absent. Returns rows
    added. The shared body of import/migration merges."""
    added = 0
    for k, rows in (incoming or {}).items():
        if not isinstance(rows, list):
            dest.setdefault(str(k), rows)
            continue
        cur = dest.setdefault(str(k), [])
        if not isinstance(cur, list):
            continue
        goals = {str(r.get("goal")) for r in cur if isinstance(r, Mapping)}
        for r in rows:
            if isinstance(r, Mapping) and r.get("goal") \
                    and str(r["goal"]) not in goals:
                cur.append(dict(r))
                goals.add(str(r["goal"]))
                added += 1
    return added


def import_corpus(corpus_path: str | Path, data: dict, *,
                  replace: bool = False) -> int:
    """Write an exported (hand-edited) corpus JSON back to the LIVE corpus --
    the hand-editing handle when the corpus lives in the world store, and a
    plain locked write in file mode. Default merges by goal (every field on
    an incoming row preserved; untouched keys left alone); ``replace``
    overwrites wholesale. Returns rows added (or total rows, on replace)."""
    from .file_lock import cross_process_lock
    if not isinstance(data, dict):
        raise ValueError("corpus root must be a JSON object")
    with _corpus_lock, cross_process_lock(Path(corpus_path)), \
            _corpus_rmw_lock(corpus_path):
        if replace:
            _write_corpus_file(Path(corpus_path), data)
            return sum(len(v) for v in data.values() if isinstance(v, list))
        raw = _load_raw(corpus_path)
        added = _merge_corpus_rows(raw, data)
        _write_corpus_file(Path(corpus_path), raw)
        return added


def migrate_corpus_files(corpus_path: str | Path) -> dict | None:
    """One-way import of the FILE corpus family into the world store (used by
    ``self-harness migrate-store``): merge-by-goal for live/pending, union
    for the reject memory; the files are renamed to ``*.migrated`` backups
    afterwards. ``None`` when the corpus isn't world-routed; a counts dict
    otherwise. Idempotent -- a second run finds no files."""
    if _world_corpus_kind(corpus_path) != "live":
        return None

    def _file_raw(p: Path) -> dict:
        try:
            raw = p.read_bytes()
        except OSError:
            return {}
        try:
            from .crypto_at_rest import unseal
            data = json.loads(unseal(raw).decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    lp = Path(corpus_path)
    pp, rp = pending_corpus_path(corpus_path), rejected_corpus_path(corpus_path)
    live_f, pend_f, rej_f = _file_raw(lp), _file_raw(pp), _file_raw(rp)
    counts = {"live": 0, "pending": 0, "rejected": 0}
    if live_f or pend_f or rej_f:
        with _corpus_lock, _corpus_rmw_lock(corpus_path):
            live = _load_raw(corpus_path)              # routed -> world rows
            counts["live"] = _merge_corpus_rows(live, live_f)
            _write_corpus_file(lp, live)
            pend = _load_raw(pp)
            counts["pending"] = _merge_corpus_rows(pend, pend_f)
            _write_corpus_file(pp, pend)
            rej = load_rejected(corpus_path)
            for k, goals in rej_f.items():
                if not isinstance(goals, list):
                    continue
                cur = rej.setdefault(str(k), [])
                for g in goals:
                    if str(g) not in cur:
                        cur.append(str(g))
                        counts["rejected"] += 1
            _write_corpus_file(rp, rej)
    for p in (lp, pp, rp):
        if p.exists():
            p.rename(p.with_name(p.name + ".migrated"))
    return counts


__all__ = [
    "load_eval_corpus", "corpus_cases", "corpus_split", "corpus_kfold_splits",
    "corpus_ab_scorers", "llm_judge", "llm_runner", "llm_paraphraser",
    "harvest_corpus_candidates", "stage_candidates", "merge_candidates",
    "resolve_pending", "load_pending", "pending_corpus_path",
    "load_rejected", "rejected_corpus_path",
    "corpus_quality", "retire_corpus_cases",
    "import_corpus", "migrate_corpus_files",
]
