# Self-Harness frontier roadmap — what's built, and the designed-but-deferred remainder

**Status:** living design note · **Module:** `maverick.self_harness` (+
`self_improvement_runner`, `cli`) · companion to `docs/proposals/self-harness.md`

This note closes out the self-harness improvement backlog. The **cleanly,
deterministically, offline-buildable** items have shipped as waves (below). The
**remainder requires either a live model to exercise or a change to the
determinism-critical store format** — building those blind would be reckless, so
they are specified here with their exact seam, wiring point, and the property
that must be preserved, ready for a focused future PR.

## Shipped (waves 5–14)

| Stage | What shipped | Key knob / seam |
|-------|--------------|-----------------|
| Config | operator config + wizard floors + dashboard parity | `[self_harness]`, `settings()` |
| Validate | Wilson confidence, cost/latency/tool gates, adaptive support | `confidence_z`, `max_*_factor`, `min_support_by_class` |
| Propose | structured `{line,hypothesis}`, best-of-N Pareto selection | `candidates_per_signature`, structured `propose_fn` |
| Mine | semantic-similarity seam + offline default; per-domain bucketing | `semantic_mining`/`similarity_fn`, `mine_bucket_by` |
| Drive | one-shot cycle (mine→gate→retire) + `self-harness run` + fleet sweep | `run_self_harness_cycle` / `--all-models` |
| Observe | governance-readiness (frozen / gate-off) on the report + CLI | `SelfHarnessReport.frozen` / `.gate_enabled` |
| Validate | metamorphic robustness (survive paraphrase) | `metamorphic_fn` |
| Conflicts | semantic-judge seam over the lexical heuristic | `find_conflicts(classifier_fn=)` |

Every wave is opt-in, leaves the default behavior byte-identical, and keeps the
determinism proof at **7/7**.

## Deferred — needs a live model (build the evaluator, not blind code)

The validation A/B (`score_with`/`score_without`) and the proposer/classifier are
already **injected seams**. The work below is to supply real implementations of
those seams; the harness side is done.

### Reference-free / LLM-as-judge evaluator (#2, #4)
- **Seam (exists):** `validate_proposal(score_with, score_without)` and
  `run_self_harness(score_with=, score_without=)` already take a
  `(addendum_line, cases) -> rate | {success,cost,latency,tool_calls}` scorer.
- **To build:** an evaluator that estimates per-case success **without
  ground-truth labels** — an LLM-as-judge over the candidate prompt's output, or
  self-consistency agreement across samples — and returns the rate the seam
  expects. Pair it with `metamorphic_fn` (a paraphraser over held-out cases) so a
  line must survive rewording, and let the existing **calibration-freeze
  interlock** guard judge drift (`SelfHarnessReport.frozen` already surfaces it).
- **Why deferred:** a judge needs a real model; offline we can only stub it. No
  harness change is required — this is an evaluator implementation injected at
  `run_self_harness_cycle` call sites.

### Outcome-correlated efficacy (#17)
- **Seam (partial):** `note_recall` already records *that* a line was recalled.
- **To build:** extend the sidecar meta (`*.meta.json`) with per-line
  `recall_with_success` / `recall_with_failure` counters, fed from a trustworthy
  run-outcome signal (a coding-mode test result, a hindsight regression — the
  same ground truth `collect_calibration` already consumes). A periodic efficacy
  review demotes/retires a line whose recall correlates with **worse** outcomes
  (negative lift), routed through the same governed gate as promotion.
- **Determinism:** counters live in the sidecar (already timestamp-bearing and
  never byte-compared), so `addenda.json` stays stable.
- **Why deferred:** needs a live outcome stream to be meaningful; offline it is a
  no-op counter. The retirement-by-efficacy decision should reuse `retire_stale`'s
  audited removal path.

### Eval corpus (#3)
- **To build:** a curated, versioned `{model_id|domain: [(goal, expected)]}`
  corpus + loader, so the driver has a stable held-in/held-out source for the live
  A/B instead of ad-hoc splits. The corpus split feeds `held_in`/`held_out`;
  `metamorphic_fn` paraphrases the held-out slice.
- **Why deferred:** corpus authorship is a data task, not a code task; the loader
  is trivial once the corpus exists.

## Deferred — touches the determinism-critical store (focused PR, not late-session)

### Per-domain scoped recall (#22)
Per-domain **mining** shipped (wave 9): a department weakness yields a
domain-scoped *line*. But that line is stored under the model-wide key and so
rides **every** prompt for the model. Scoped *recall* would inject it only for
matching-domain runs.

- **Design (store-safe):** keep model-wide lines under `model_id` (unchanged);
  store domain-scoped lines under a **composite key** `f"{model_id}\x00domain={d}"`
  (NUL can't appear in a model id, so the two namespaces can't collide).
  `recall_addendum(model_id, *, domain=None)` returns the model-wide block plus,
  when `domain` is set, the domain block. `_apply_addendum` routes a proposal with
  `context="domain=X"` to the composite key. `Agent._build_system` passes the
  run's resolved domain to `recall_addendum`.
- **Determinism preserved:** with no domain mining the store has only `model_id`
  keys, so `addenda.json` is byte-identical and the proof holds. The composite-key
  path only activates under `mine_bucket_by=("domain",)`.
- **Why a focused PR:** it changes recall keying, the apply path, *and* the agent
  hot path — the most safety-critical surface. It deserves its own review and its
  own determinism-proof extension (a domain-keyed scenario), not a late add-on.

### Canary / staged rollout (#16)
- **Design:** a `canary` flag in the sidecar meta on a freshly promoted line;
  `recall_addendum` includes canary lines but the driver tracks their
  recall→outcome (see #17) and **graduates** to permanent after N clean uses, or
  auto-demotes on regression — a slow-rollout valve on top of the gate.
- **Why deferred:** only meaningful with the #17 outcome signal wired; build them
  together.

### Component-level harness profiles (#7)
- **Status:** partially delivered by `mine_bucket_by` (per-domain). The full
  version buckets *and recalls* per `(model, component/tool)`; the recall half is
  the same composite-key mechanism as #22, and `tool` would join `domain` in
  `_BUCKET_DIMS` once tool attribution on reflexions is first-class (today only
  `tools_used` list is recorded, not a single attributed tool).

## Principle

Every shipped wave kept three invariants: **off-by-default / byte-identical
default**, **determinism proof 7/7**, and **the governed gate decides promotion**.
The deferred items inherit the same bar — none should ship until it can show the
proof still passes (extended with the new scenario where relevant) and a live
evaluator (not a stub) backs any promotion it enables.
