"""Glue that completes the *model-agnostic* self-improvement rungs.

These are the rungs that compound on a customer's data WITHOUT owning or
fine-tuning a generative model -- which is the whole strategy: the default brain
stays a frontier model (kernel rule 2, never compete on the model), and the moat
is governance + per-customer compounding on top of it. This module ties capture
-> evidence -> the governed controller for:

* **judgment** -- ``build_prm_examples`` turns captured trajectories into
  training rows for the small reward *head* (an MLP over the frontier model's
  outputs -- NOT an LLM, so no open-weights model is implied);
* **tools** -- ``review_generated_tools`` promotes a synthesized tool that earns
  it and retires one that doesn't;
* **prompts / skills / policies** -- ``emit_strategy_candidate`` routes a strategy
  change through the gate;
* **calibration** -- ``collect_calibration`` feeds the verifier-drift interlock
  from any ground-truth source so the freeze is always armed.

Weight-level fine-tuning (``si_producers.propose_weights`` / ``propose_policy``
on an adapter) stays an explicitly optional, sovereign-/air-gap-only seam; it is
deliberately NOT on this model-agnostic completion path.

Everything here is deterministic and offline-testable. The producers it calls
are no-ops unless ``[self_improvement] enable`` is set.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping

from .si_producers import ToolOutcomeTracker, propose_policy, propose_prompt, propose_tool

log = logging.getLogger(__name__)


# -- calibration: arm the verifier-drift interlock from any ground truth -----

def collect_calibration(confidence: float, correct: bool, *, source: str = "auto",
                        enabled_fn: Callable[[], bool] | None = None) -> bool:
    """Record a ``(verifier_confidence, ground_truth)`` sample when collection is on.

    Ground truth is anything trustworthy: a coding-mode test outcome, a human
    approval/denial, a hindsight regression. Feeding these keeps
    ``calibration.learning_frozen`` meaningful -- the gate that refuses to let
    the system learn from a drifting judge. No-op (returns False) when off.
    """
    try:
        from . import calibration
        on = enabled_fn() if enabled_fn is not None else calibration.collect_from_coding_enabled()
        if not on:
            return False
        return bool(calibration.record_sample(float(confidence), bool(correct), source=source))
    except Exception:  # pragma: no cover -- never block a run on calibration capture
        log.debug("calibration capture failed", exc_info=True)
        return False


# -- tools: promote what helps, retire what doesn't (Phase 3) ----------------

def should_retire(name: str, tracker: ToolOutcomeTracker, *,
                  floor: float = 0.2, min_samples: int = 5) -> bool:
    """A synthesized tool earns retirement when it has enough use and a low
    success rate -- the FORGET half of the action-space loop."""
    return tracker.samples(name) >= min_samples and tracker.success_rate(name) < floor


def review_generated_tools(names, tracker: ToolOutcomeTracker, *, baseline_success: float = 0.5,
                           rollback_for: Callable[[str], object] | None = None,
                           controller=None) -> dict[str, str]:
    """For each synthesized tool decide promote / retire / hold via the gate.

    Returns ``{name: action}`` where action is ``"promoted"``, ``"retire"``, or
    ``"hold"``. ``retire`` is advisory -- the caller removes the tool file (the
    rollback handle makes that reversible). Promotion runs through the full
    controller, so a tool that beats baseline but would widen authority is held.
    """
    rollback_for = rollback_for or (lambda n: f"retire:{n}")
    out: dict[str, str] = {}
    for name in names:
        if should_retire(name, tracker):
            out[name] = "retire"
            continue
        verdict = propose_tool(name, tracker, baseline_success,
                               rollback=rollback_for(name), capability_widens=False,
                               controller=controller)
        out[name] = "promoted" if verdict.ok else "hold"
    return out


# -- judgment: a training set for the small reward head (Phase 1) ------------

def build_prm_examples(store, *, limit: int = 10_000) -> list[dict]:
    """Turn captured trajectory steps into ``{features, promise, progress}`` rows.

    Model-agnostic: ``features`` is the deterministic 12-vector
    ``prm.step_features`` builds from a step (role one-hot, tool outcome, error,
    finality, ...). These rows train the LearnedPRM *head* (a tiny MLP) -- the
    judgment rung -- with no LLM training involved. Only steps that carry a
    promise label are emitted.
    """
    try:
        from .prm import StepContext, step_features
    except Exception:  # pragma: no cover
        return []
    rows: list[dict] = []
    for s in store.iter_steps(limit=limit):
        if s.promise is None:
            continue
        try:
            ctx = StepContext(
                goal_id=int(s.goal_id or 0), step_index=int(s.step or 0), role=s.role or "other",
                tool_name=(s.tool or None), tool_succeeded=s.tool_succeeded,
                is_final=bool(s.is_final), error=(s.error or None), prior_step_score=0.5,
            )
            rows.append({
                "features": step_features(ctx),
                "promise": float(s.promise),
                "progress": float(s.progress) if s.progress is not None else 0.0,
            })
        except Exception:  # pragma: no cover -- skip a malformed row, never crash
            continue
    return rows


# -- prompts / skills / policies (Phase 4) -----------------------------------

def emit_strategy_candidate(kind: str, summary: str, baseline: float, candidate: float,
                            samples: int, *, rollback, controller=None):
    """Route a strategy change through the gate. ``kind`` is ``"prompt"`` (a
    prompt/playbook variant, no capability surface) or ``"policy"`` (a routing/
    decision policy)."""
    if kind == "prompt":
        return propose_prompt(summary, baseline, candidate, samples,
                              rollback=rollback, controller=controller)
    return propose_policy(summary, baseline, candidate, samples,
                          rollback=rollback, capability_widens=False, controller=controller)


# -- self-harness: learn a model-specific harness addendum (Phase 4 sibling) --

def run_self_harness_pass(
    reflexions=None, *, model_id: str | None = None,
    held_in=None, held_out=None,
    score_with=None, score_without=None, propose_fn=None, controller=None,
    min_support: int | None = None, limit: int = 500,
    require_held_out: bool | None = None, min_delta: float | None = None,
    min_held_out: int | None = None, candidates_per_signature: int | None = None,
    semantic_mining: bool | None = None, similarity_fn=None,
    bucket_by: tuple[str, ...] | None = None,
    metamorphic_fn=None, metamorphic_tolerance: float | None = None,
    holdout_rotations: int | None = None,
    canary: bool | None = None,
    eval_for_context=None,
):
    """The automatic entry point for the self-harness loop (mine -> propose ->
    validate -> gate), to be called by a scheduler / the self-improvement loop.

    Resolves ``model_id`` to the configured orchestrator model and loads recent
    model-tagged reflexions when not supplied. The tuning knobs
    (``min_support``/``require_held_out``/``min_delta``/``min_held_out``/
    ``candidates_per_signature``/``semantic_mining``/``bucket_by``/``canary``)
    default to ``None`` and are then filled from ``[self_harness]`` config, so an
    operator's settings take effect automatically; an explicit argument overrides
    config. ``canary`` stages this pass's promotions on probation
    (``promote_as_canary`` in config): still recalled, but graduated/demoted from
    real run outcomes by the cycle's canary review instead of being permanent
    on arrival. An
    embedding-backed ``similarity_fn`` may be injected for true semantic
    clustering; otherwise ``semantic_mining`` selects the deterministic built-in. ``score_with``/``score_without`` are the LIVE held-in/held-out A/B
    over the candidate prompt -- injected by the caller because a real evaluation
    needs a real model; without them the pass is a dry inspection that writes
    nothing. Never raises -- a learning pass must not perturb anything.
    """
    try:
        from . import self_harness
        if not self_harness.enabled():
            return self_harness.SelfHarnessReport(model_id=str(model_id or ""))
        st = self_harness.settings()
        if min_support is None:
            min_support = st["min_support"]
        if require_held_out is None:
            require_held_out = st["require_held_out"]
        if min_delta is None:
            min_delta = st["min_delta"]
        if min_held_out is None:
            min_held_out = st["min_held_out"]
        if candidates_per_signature is None:
            candidates_per_signature = st["candidates_per_signature"]
        if semantic_mining is None:
            semantic_mining = st["semantic_mining"]
        if bucket_by is None:
            bucket_by = st["mine_bucket_by"]
        if holdout_rotations is None:
            holdout_rotations = st.get("holdout_rotations", 1)
        if canary is None:
            canary = st.get("promote_as_canary", False)
        if metamorphic_tolerance is None:
            metamorphic_tolerance = st.get("metamorphic_tolerance", 0.0)
        if not model_id:
            from .llm import model_for_role
            model_id = model_for_role("orchestrator")
        if reflexions is None:
            from . import reflexion
            reflexions = [r.to_dict() for r in reflexion.list_recent(limit=limit)]
        return self_harness.run_self_harness(
            reflexions, model_id=model_id, held_in=held_in, held_out=held_out,
            score_with=score_with, score_without=score_without,
            propose_fn=propose_fn, controller=controller, min_support=min_support,
            require_held_out=require_held_out, min_delta=min_delta,
            min_held_out=min_held_out, confidence_z=st["confidence_z"],
            max_cost_factor=st["max_cost_factor"],
            max_latency_factor=st["max_latency_factor"],
            max_tool_calls_factor=st["max_tool_calls_factor"],
            min_support_by_class=st["min_support_by_class"],
            candidates_per_signature=candidates_per_signature,
            semantic_mining=semantic_mining, similarity_fn=similarity_fn,
            bucket_by=bucket_by, metamorphic_fn=metamorphic_fn,
            metamorphic_tolerance=metamorphic_tolerance,
            holdout_rotations=holdout_rotations, canary=bool(canary),
            eval_for_context=eval_for_context)
    except Exception:  # pragma: no cover -- learning never perturbs a run
        log.debug("self-harness pass failed", exc_info=True)
        from . import self_harness
        return self_harness.SelfHarnessReport(model_id=str(model_id or ""))


def _auto_evaluator(model_id: str, *, corpus_path: str, held_out_frac: float = 0.3,
                    budget=None, judge_samples: int = 1,
                    judge_unknown: bool = False):
    """Build ``(held_in, held_out, score_with, score_without)`` -- the LIVE A/B --
    from a configured eval corpus + the model's own LLM client, so the driver can
    actually PROMOTE rather than only dry-inspect. The candidate ``model_id``
    GENERATES; a verifier-role model JUDGES (cross-model, to avoid self-grading;
    neither is hard-coded -- :func:`model_for_role`, kernel rule 2). ``judge_samples``
    > 1 turns on self-consistency judging (majority vote over diverse framings).
    Returns ``None`` (caller stays dry) when there is no corpus, no cases for the
    model, or no usable held-out split. Never raises."""
    try:
        from . import self_harness_eval as ev
        from .llm import LLM, model_for_role
        cases = ev.corpus_cases(ev.load_eval_corpus(corpus_path), model_id)
        if not cases:
            return None
        held_in, held_out = ev.corpus_split(cases, held_out_frac=held_out_frac)
        if not held_out:
            return None
        score_with, score_without = ev.corpus_ab_scorers(
            cases,
            run_fn=ev.llm_runner(LLM(model_id), budget=budget),
            judge_fn=ev.llm_judge(LLM(model_for_role("verifier")), budget=budget,
                                  samples=judge_samples),
            judge_unknown=judge_unknown)
        return held_in, held_out, score_with, score_without
    except Exception:  # pragma: no cover -- a missing provider/corpus stays dry
        log.debug("self-harness auto-evaluator construction failed", exc_info=True)
        return None


def _domain_of_context(context: str) -> str:
    """The ``domain=<d>`` value inside a mining-context string (e.g.
    ``"domain=finance"`` or ``"domain=finance, tool=web_fetch"``), or ``""``
    when the context carries no domain part."""
    for part in (context or "").split(","):
        k, _, v = part.strip().partition("=")
        if k == "domain" and v:
            return v
    return ""


def _context_evaluator(model_id: str, *, corpus_path: str, budget=None,
                       judge_samples: int = 1, held_out_frac: float = 0.3,
                       judge_unknown: bool = False):
    """Build the ``eval_for_context`` seam from the corpus: a domain-scoped
    signature (``"domain=finance"``) is validated against the corpus's
    ``finance``-keyed cases -- its own department's ground truth -- instead of
    the model-wide pool, which under-credits a narrow line. The corpus loader
    has always documented ``{model|domain: [...]}`` keys; this makes the domain
    keys actually reach validation.

    Returns ``None`` when the corpus has no keys beyond the model's (nothing
    scoped to serve -- no LLM clients are built in that case). Otherwise the
    candidate model generates and a verifier-role model judges exactly as in
    :func:`_auto_evaluator`, sharing the same ``budget`` pot; per-domain quads
    are cached so repeated signatures don't rebuild them. A domain with no
    usable held-out split yields ``None`` for that domain (the pass keeps its
    defaults). Never raises."""
    try:
        from . import self_harness_eval as ev
        from .llm import LLM, model_for_role
        corpus = ev.load_eval_corpus(corpus_path)
        domain_keys = {k for k in corpus if k != str(model_id)}
        if not domain_keys:
            return None
        run_fn = ev.llm_runner(LLM(model_id), budget=budget)
        judge_fn = ev.llm_judge(LLM(model_for_role("verifier")), budget=budget,
                                samples=judge_samples)
        cache: dict[str, tuple | None] = {}

        def _for_context(context: str):
            d = _domain_of_context(context)
            if not d or d not in domain_keys:
                return None
            if d not in cache:
                held_in, held_out = ev.corpus_split(
                    ev.corpus_cases(corpus, d), held_out_frac=held_out_frac)
                if not held_out:
                    cache[d] = None
                else:
                    sw, swo = ev.corpus_ab_scorers(
                        ev.corpus_cases(corpus, d), run_fn=run_fn,
                        judge_fn=judge_fn, judge_unknown=judge_unknown)
                    cache[d] = (held_in, held_out, sw, swo)
            return cache[d]

        return _for_context
    except Exception:  # pragma: no cover -- a missing provider/corpus stays dry
        log.debug("self-harness context-evaluator construction failed", exc_info=True)
        return None


def _eval_budget(settings: dict):
    """A ``Budget`` capping ONE auto-evaluated cycle's LLM spend, from
    ``[self_harness] eval_budget_dollars`` -- or ``None`` (uncapped, the
    historical behavior) when the knob is unset/<=0. The runner and judge share
    the pot (kernel rule 3: an unattended evaluation must not have unbounded
    spend). Dollars is the BINDING cap: the token/tool ceilings are raised far
    above any eval pass so the operator's dollar figure is what trips (``Budget``
    enforces every cap at record time, so its defaults -- sized for one agent
    run -- would otherwise trip first); the default 1h wall clock stays as a hang
    backstop. Never raises."""
    try:
        dollars = settings.get("eval_budget_dollars")
        if not dollars or float(dollars) <= 0:
            return None
        from .budget import Budget
        big = 10**9
        return Budget(max_dollars=float(dollars), max_input_tokens=big,
                      max_output_tokens=big, max_tool_calls=big)
    except Exception:  # pragma: no cover -- a bad knob can't block the cycle
        log.debug("self-harness eval budget construction failed", exc_info=True)
        return None


def run_self_harness_cycle(
    reflexions=None, *, model_id: str | None = None, limit: int = 500,
    retire: bool = True, retire_after_days: float | None = None,
    held_in=None, held_out=None, score_with=None, score_without=None,
    propose_fn=None, controller=None, **pass_kwargs,
):
    """One full DRIVER cycle -- a governed learning pass THEN stale-line
    retirement -- the unit a scheduler (cron / the self-improvement loop) invokes
    to operate the harness end-to-end. Returns ``(report, retired_count)``.

    The pass is :func:`run_self_harness_pass` (mine -> propose -> validate ->
    gate). The live A/B (``score_with``/``score_without``) is injected, OR -- when
    the caller injects none and ``[self_harness] eval_corpus`` is configured --
    AUTO-BUILT by :func:`_auto_evaluator` (the model generates, a verifier judges)
    so a scheduled ``maverick self-harness run`` actually PROMOTES. With no scorer
    and no corpus it is a DRY inspection that writes no NEW guidance, but
    retirement still runs -- so the cycle keeps guidance fresh either way.
    Retirement uses
    ``retire_after_days`` (arg overrides ``[self_harness]`` config; 0/None = off)
    and is scoped to the pass's resolved model. Never raises -- a maintenance
    cycle must not perturb anything."""
    try:
        from . import self_harness
        st = self_harness.settings() if self_harness.enabled() else None
        # ONE budget pot for everything this cycle evaluates -- the model-wide
        # A/B, the domain-scoped quads, and the metamorphic paraphraser
        # together, so the operator's cap bounds the whole cycle, not each
        # evaluator.
        eval_budget = _eval_budget(st) if st is not None else None
        # AUTO-WIRE the live A/B from config so a scheduled cycle can actually
        # PROMOTE, not just dry-inspect: if the caller injected no scorers and
        # [self_harness] eval_corpus is set, build the model-generates /
        # verifier-judges evaluator. Unset corpus (the default) -> stays dry, so
        # behavior is unchanged out of the box.
        if score_with is None and score_without is None and st is not None:
            corpus_path = st.get("eval_corpus")
            if corpus_path:
                from .llm import model_for_role
                model_id = model_id or model_for_role("orchestrator")
                # With metamorphic validation on, paraphrased (non-corpus)
                # goals must be JUDGEABLE, not indeterminate -- the LLM judge
                # doesn't need the expected hint.
                judge_unknown = bool(st.get("metamorphic"))
                built = _auto_evaluator(model_id, corpus_path=corpus_path,
                                        judge_samples=st.get("judge_samples", 1),
                                        budget=eval_budget,
                                        judge_unknown=judge_unknown)
                if built is not None:
                    held_in, held_out, score_with, score_without = built
                # Scoped evaluation: the corpus's domain keys validate matching
                # domain-scoped candidates -- independent of whether the
                # model-wide quad built, so a domain-keyed-only corpus still
                # promotes its departments' lines.
                if pass_kwargs.get("eval_for_context") is None:
                    efc = _context_evaluator(model_id, corpus_path=corpus_path,
                                             judge_samples=st.get("judge_samples", 1),
                                             budget=eval_budget,
                                             judge_unknown=judge_unknown)
                    if efc is not None:
                        pass_kwargs["eval_for_context"] = efc
        # Metamorphic validation (opt-in, [self_harness] metamorphic): reject a
        # candidate whose lift does not survive PARAPHRASING the held-out cases
        # (overfit to exact wording). Built only when something will actually
        # validate (a scorer exists) and the caller injected no paraphraser.
        if (st is not None and st.get("metamorphic") and score_with is not None
                and pass_kwargs.get("metamorphic_fn") is None):
            try:
                from . import self_harness_eval as ev
                from .llm import LLM, model_for_role
                pass_kwargs["metamorphic_fn"] = ev.llm_paraphraser(
                    LLM(model_for_role("summarizer")), budget=eval_budget)
            except Exception:  # pragma: no cover -- no paraphraser = check off
                log.debug("self-harness paraphraser construction failed",
                          exc_info=True)
        report = run_self_harness_pass(
            reflexions, model_id=model_id, limit=limit,
            held_in=held_in, held_out=held_out, score_with=score_with,
            score_without=score_without, propose_fn=propose_fn,
            controller=controller, **pass_kwargs)
        resolved = (report.model_id or model_id) or None
        # OUTCOME-DRIVEN LIFECYCLE: graduate/demote canaries from the accumulated
        # recall->outcome counters (safe -- only touches canary-flagged lines), and
        # optionally re-measure each line's live A/B lift to demote dead weight
        # (opt-in: a general corpus can under-credit a narrow line).
        if resolved and self_harness.enabled():
            try:
                res = self_harness.review_canaries(resolved)
                report.graduated = list(res.get("graduated", []))
                report.demoted = list(res.get("demoted", []))
            except Exception:  # pragma: no cover -- lifecycle never perturbs a run
                log.debug("self-harness canary review failed", exc_info=True)
            # Relapse recency guard (opt-in): AFTER the canary review, so a
            # line re-probated now gets adjudicated on the NEXT cycle's
            # evidence rather than instantly demoted in the same pass.
            try:
                share = float((st or {}).get("relapse_failure_share") or 0.0)
                if share > 0:
                    report.relapsed = self_harness.review_relapses(
                        resolved, failure_share=share,
                        min_outcomes=int((st or {}).get("relapse_min_outcomes", 5)))
            except Exception:  # pragma: no cover -- lifecycle never perturbs a run
                log.debug("self-harness relapse review failed", exc_info=True)
            if (self_harness.settings().get("efficacy_review")
                    and score_with is not None and score_without is not None):
                try:
                    cases = list(held_out or []) + list(held_in or [])
                    report.demoted += self_harness.review_efficacy(
                        resolved, cases, score_with=score_with,
                        score_without=score_without)
                except Exception:  # pragma: no cover
                    log.debug("self-harness efficacy review failed", exc_info=True)
        retired = 0
        if retire and self_harness.enabled():
            days = retire_after_days
            if days is None:
                days = self_harness.settings().get("retire_after_days", 0.0)
            if days and float(days) > 0:
                retired = self_harness.retire_stale(
                    older_than_days=float(days), model_id=resolved)
        return report, retired
    except Exception:  # pragma: no cover -- a maintenance cycle never perturbs a run
        log.debug("self-harness cycle failed", exc_info=True)
        from . import self_harness
        return self_harness.SelfHarnessReport(model_id=str(model_id or "")), 0


def _memo_scorer(fn):
    """Cache a scorer on its goals tuple. The BASELINE arm ignores the
    candidate line, yet ``validate_proposal`` re-scores it per candidate --
    with L transferable lines that is (L-1)x redundant LLM generations per
    target, the dominant cost of a transfer sweep. An INDETERMINATE result
    (NaN arm: budget-dead pot, broken scorer) is returned but never cached --
    the sweep shares one context across all sources, so caching it would
    poison every later candidate against this target. (A memoized baseline is
    one frozen draw of a stochastic measurement; acceptable because transfers
    land as canaries and real outcomes re-adjudicate them.)"""
    cache: dict[tuple, object] = {}

    def _scored(line, goals):
        key = tuple(goals)
        if key in cache:
            return cache[key]
        val = fn(line, goals)
        s = val.get("success") if isinstance(val, Mapping) else val
        if not (isinstance(s, float) and math.isnan(s)):
            cache[key] = val
        return val

    return _scored


def _memo_paraphraser(fn):
    """Cache a paraphraser on its input goals tuple: paraphrases depend only
    on the held-out cases, never on the candidate line, yet
    ``validate_proposal`` regenerates them per candidate -- L x H redundant
    LLM calls per target. A stable paraphrase list also keeps the memoized
    baseline arm's cache key stable across candidates. A falsy result is NOT
    cached, so one transient paraphraser failure can't silently disable the
    metamorphic check for the whole sweep."""
    cache: dict[tuple, list] = {}

    def _para(goals):
        key = tuple(goals)
        if key not in cache:
            out = fn(goals)
            if not out:
                return out
            cache[key] = out
        return cache[key]

    return _para


def _transfer_context(st: dict, corpus_path: str):
    """The shared per-sweep transfer state: ``(eval_for_target, validate_kwargs)``.

    ``eval_for_target`` caches each target's corpus-built quad (so a fleet
    sweep builds every evaluator once, not once per source) with the baseline
    arm memoized, one eval-budget pot across everything, and the SAME floors +
    metamorphic/judge_unknown wiring the cycle applies -- a transferred line
    must clear the gate home-grown lines clear, not a weaker copy of it."""
    budget = _eval_budget(st)
    judge_unknown = bool(st.get("metamorphic"))
    cache: dict[str, tuple | None] = {}

    def eval_for_target(m: str):
        if m not in cache:
            built = _auto_evaluator(m, corpus_path=corpus_path,
                                    judge_samples=st.get("judge_samples", 1),
                                    budget=budget, judge_unknown=judge_unknown)
            if built is not None:
                hi, ho, sw, swo = built
                built = (hi, ho, sw, _memo_scorer(swo))
            cache[m] = built
        return cache[m]

    validate_kwargs = dict(
        min_delta=st["min_delta"], min_held_out=st["min_held_out"],
        confidence_z=st["confidence_z"], max_cost_factor=st["max_cost_factor"],
        max_latency_factor=st["max_latency_factor"],
        max_tool_calls_factor=st["max_tool_calls_factor"],
        metamorphic_tolerance=st.get("metamorphic_tolerance", 0.0))
    if st.get("metamorphic"):
        try:
            from . import self_harness_eval as ev
            from .llm import LLM, model_for_role
            validate_kwargs["metamorphic_fn"] = _memo_paraphraser(
                ev.llm_paraphraser(LLM(model_for_role("summarizer")),
                                   budget=budget))
        except Exception:  # pragma: no cover -- no paraphraser = check off
            log.debug("transfer paraphraser construction failed", exc_info=True)
    return eval_for_target, validate_kwargs


def run_self_harness_transfer(source_model: str, *, targets=None,
                              corpus_path: str | None = None,
                              controller=None, force: bool = False,
                              raise_errors: bool = False,
                              _context=None) -> dict:
    """Driver entry for a governed cross-model transfer trial: try
    ``source_model``'s graduated lines on ``targets`` (default: the whole
    fleet) against each target's corpus-built A/B, applying the configured
    validation floors (including ``holdout_rotations`` -- the same
    cross-validation home-grown lines face), one shared eval-budget pot, and
    the canary landing ``self_harness.run_transfer`` enforces. The
    tried-memory makes repeat invocations spend nothing on already-judged
    pairs (``force`` overrides). Returns the per-target report; ``{}`` when
    the loop is off or no corpus is configured. Never raises unless
    ``raise_errors`` -- the interactive CLI sets it, because a swallowed
    error is indistinguishable from an empty fleet."""
    try:
        from . import self_harness
        if not self_harness.enabled():
            return {}
        st = self_harness.settings()
        corpus_path = corpus_path or st.get("eval_corpus")
        if not corpus_path:
            return {}
        eval_for_target, validate_kwargs = (
            _context if _context is not None
            else _transfer_context(st, corpus_path))
        if targets is None:
            targets = harness_fleet_models()
        return self_harness.run_transfer(
            str(source_model), list(targets), eval_for_target=eval_for_target,
            controller=controller, force=force, validate_kwargs=validate_kwargs,
            holdout_rotations=int(st.get("holdout_rotations", 1) or 1))
    except Exception:  # pragma: no cover -- a transfer trial never perturbs a run
        if raise_errors:
            raise
        log.warning("self-harness transfer failed", exc_info=True)
        return {}


def run_self_harness_transfer_sweep(*, controller=None) -> dict:
    """Fleet-wide transfer: every configured role model as source in turn,
    all sources sharing ONE transfer context (evaluator cache, budget pot,
    floors). The tried-memory keeps steady-state spend near zero -- only lines
    learned since the last sweep generate new evaluations. Returns
    ``{source: per-target report}`` for sources that attempted anything."""
    out: dict[str, dict] = {}
    try:
        from . import self_harness
        if not self_harness.enabled():
            return out
        st = self_harness.settings()
        corpus_path = st.get("eval_corpus")
        if not corpus_path:
            return out
        context = _transfer_context(st, corpus_path)
        fleet = harness_fleet_models()
        for m in fleet:
            # run_transfer skips the source itself -- no pre-filter needed.
            rep = run_self_harness_transfer(
                m, targets=fleet,
                corpus_path=corpus_path, controller=controller, _context=context)
            if any(r.get("attempted") for r in rep.values()):
                out[m] = rep
    except Exception:  # pragma: no cover -- a sweep never perturbs a run
        log.debug("self-harness transfer sweep failed", exc_info=True)
    return out


def run_corpus_harvest(world, *, mode: str | None = None, key: str | None = None,
                       corpus_path: str | None = None,
                       raise_errors: bool = False) -> int:
    """Driver entry for corpus bootstrapping: mine hindsight-pair candidates
    from the reflexion log + the world's DONE goals and stage ("propose") or
    merge ("auto") them for ``key`` (default: the orchestrator model). The one
    owner of the harvest policy -- the unscoped-reflexion filter (via
    reflexion's own scope predicate), the history limits, and the
    secret-redaction pass live here, not in each CLI. Goals already live,
    pending, or operator-rejected are excluded BEFORE the candidate cap so
    they can't starve fresh candidates. Returns how many candidates were
    newly staged/merged; 0 when off/unconfigured. Never raises unless
    ``raise_errors`` -- the interactive CLI sets it so a write failure isn't
    indistinguishable from "nothing to harvest"."""
    try:
        from . import reflexion, self_harness
        from . import self_harness_eval as ev
        from .reflexion import _sanitize_text, _scope_matches
        st = self_harness.settings()
        mode = mode or st.get("corpus_harvest", "off")
        corpus_path = corpus_path or st.get("eval_corpus")
        if mode not in ("propose", "auto") or not corpus_path or world is None:
            return 0
        if key is None:
            from .llm import model_for_role
            key = model_for_role("orchestrator")
        refl = [r.to_dict() for r in reflexion.list_recent(limit=500)
                if _scope_matches(r, channel=None, user_id=None)]
        # Isolation parity with the reflexion side: owner-scoped goals stay
        # out of the shared corpus -- one tenant's goal text must never be
        # mined into ground truth other contexts evaluate against.
        goals = [g for g in world.list_goals(status="done", limit=200,
                                             order="desc")
                 if not getattr(g, "owner", "")]
        known = {c["goal"] for c in ev.load_eval_corpus(corpus_path).get(str(key), [])}
        known |= {c["goal"] for c in ev.load_pending(corpus_path).get(str(key), [])}
        known |= set(ev.load_rejected(corpus_path).get(str(key), []))
        # The corpus/pending files are plaintext sidecars; goal content can be
        # sealed at rest and is secret-redacted on every other persistence
        # path -- redact here too, before it leaves the world DB.
        cands = [{"goal": _sanitize_text(c["goal"]),
                  "expected": _sanitize_text(c["expected"])}
                 for c in ev.harvest_corpus_candidates(refl, goals, known=known)]
        # The files hold REDACTED text, so re-check known post-redaction too
        # (a secret-bearing goal changes form between mining and staging).
        cands = [c for c in cands if c["goal"] not in known]
        add = ev.merge_candidates if mode == "auto" else ev.stage_candidates
        return add(corpus_path, key, cands)
    except Exception:  # pragma: no cover -- harvesting never perturbs a run
        if raise_errors:
            raise
        log.warning("self-harness corpus harvest failed", exc_info=True)
        return 0


def harness_fleet_models() -> list[str]:
    """The DISTINCT set of models the fleet runs, resolved across every role in
    ``llm.ROLE_MODELS`` (each via ``model_for_role``, deduped, sorted for a
    deterministic order). The harness is model-specific, so a worker model
    accumulates its OWN guidance -- this is the fleet the driver should cover, not
    just the orchestrator. Empty on any error (never raises)."""
    try:
        from .llm import ROLE_MODELS, model_for_role
    except Exception:  # pragma: no cover
        return []
    models: list[str] = []
    for role in ROLE_MODELS:
        try:
            m = model_for_role(role)
        except Exception:  # pragma: no cover -- one bad role can't drop the rest
            continue
        if m and m not in models:
            models.append(m)
    return sorted(models)


def run_self_harness_all_models(*, limit: int = 500, retire: bool = True, **cycle_kwargs):
    """Run a self-harness cycle for EVERY distinct configured role model, so
    WORKER models learn their own harness, not just the orchestrator -- the
    harness is model-specific and a real fleet runs several models. Returns
    ``{model_id: (report, retired)}``.

    Each model's pass mines only ITS OWN traces (mining is per-model), so a
    worker's lesson never bleeds into the orchestrator's. Without injected
    scorers it is a fleet-wide DRY inspection + stale-line retirement; live
    per-model A/B scoring is the operator's to wire (a real evaluation needs a
    real model). Never raises."""
    out: dict[str, tuple] = {}
    try:
        from . import self_harness
        if not self_harness.enabled():
            return out
        for m in harness_fleet_models():
            out[m] = run_self_harness_cycle(
                model_id=m, limit=limit, retire=retire, **cycle_kwargs)
    except Exception:  # pragma: no cover -- a fleet sweep never perturbs a run
        log.debug("self-harness all-models cycle failed", exc_info=True)
    return out


__all__ = [
    "collect_calibration", "should_retire", "review_generated_tools",
    "build_prm_examples", "emit_strategy_candidate", "run_self_harness_pass",
    "run_self_harness_cycle", "run_self_harness_all_models", "harness_fleet_models",
    "run_self_harness_transfer", "run_self_harness_transfer_sweep",
    "run_corpus_harvest",
]
