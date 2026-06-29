# Self-Harness frontier roadmap — what's built, and the live-model seams that remain

**Status:** living design note · **Module:** `maverick.self_harness` (+
`self_harness_eval`, `self_improvement_runner`, `cli`, `agent`) · companion to
`docs/proposals/self-harness.md`

The self-harness improvement backlog is **built**. Every item is implemented as
real, tested, offline-verifiable code; where an item genuinely needs a live model
(generate / judge an answer), that piece is an **injected seam** with a
deterministic offline default — exactly how the existing A/B scorer,
`similarity_fn`, `metamorphic_fn`, and `classifier_fn` seams already work, all
tested offline with stubs. This note records the shape and the remaining live
wiring.

## Shipped (waves 5–19)

| Stage | What shipped | Key knob / seam |
|-------|--------------|-----------------|
| Config | operator config + wizard floors + dashboard parity | `[self_harness]`, `settings()` |
| Validate | Wilson confidence, cost/latency/tool gates, adaptive support | `confidence_z`, `max_*_factor`, `min_support_by_class` |
| Validate | metamorphic robustness (survive paraphrase) | `metamorphic_fn` |
| Propose | structured `{line,hypothesis}`, best-of-N Pareto selection | `candidates_per_signature`, structured `propose_fn` |
| Mine | semantic-similarity seam + offline default; per-domain bucketing | `semantic_mining`/`similarity_fn`, `mine_bucket_by` |
| Evaluate | reference-free A/B evaluator + eval corpus | `self_harness_eval.corpus_ab_scorers` / `corpus_split` |
| Drive | cycle (mine→gate→retire) + `self-harness run` + fleet sweep | `run_self_harness_cycle` / `--all-models` |
| Efficacy | recall→outcome counters + causal A/B demotion | `note_outcome` / `line_efficacy` / `review_efficacy` |
| Rollout | canary / staged rollout (graduate / demote) | `run_self_harness(canary=)` / `review_canaries` |
| Recall | per-domain scoped recall (composite key + agent wiring) | `recall_addendum(domain=)` |
| Observe | governance-readiness (frozen / gate-off) on report + CLI | `SelfHarnessReport.frozen` / `.gate_enabled` |
| Conflicts | semantic-judge seam over the lexical heuristic | `find_conflicts(classifier_fn=)` |

Every wave is opt-in, leaves default behavior **byte-identical**, keeps the
determinism proof at **7/7**, and routes any promotion through the one governed
gate. Backlog item map: #1 driver (`run_self_harness_cycle`), #2/#4 reference-free
evaluator (`self_harness_eval`), #3 eval corpus (`load_eval_corpus`/`corpus_split`),
#5 best-of-N, #6 semantic mining, #7 component profiles (per-domain shipped; see
below), #8 per-domain mining, #9 conflict detection, #13 metamorphic, #14
judge-drift visibility, #16 canary, #17 efficacy, #18 scheduled retirement,
#19 LLM conflict classifier, #22 scoped recall, #23 worker-model learning.

## Remaining — operator data + one provider binding, not harness code

The harness side is done, including the **LLM-backed** evaluator (`llm_runner` /
`llm_judge` in `self_harness_eval`, same fail-open shape as `llm_proposer`, tested
with a fake provider). What's left is operator-supplied:

- **An LLM client + eval corpus**: pass your `llm` to `llm_runner`/`llm_judge`
  (or inject custom `run_fn`/`judge_fn`) and author the `{model|domain: [(goal,
  expected)]}` corpus the JSON loader + deterministic split already consume. The
  calibration-freeze interlock (surfaced as `SelfHarnessReport.frozen`) guards
  judge drift.
- **An outcome stream** wired into `note_outcome` from a trustworthy run signal
  (coding-mode test result, hindsight regression — the same ground truth
  `collect_calibration` consumes). `review_efficacy`/`review_canaries` then act on
  real correlations rather than test fixtures.

## Component-level profiles (#7) — partial, with a clear extension

Per-**domain** mine + scoped recall is shipped (composite key
`"<model>\x00domain=<d>"`, recalled only for matching-domain runs). The remaining
half — per-**tool/component** scope — reuses the *same* mechanism: add `tool` to
`_BUCKET_DIMS` and key recall by `"<model>\x00tool=<t>"`. It is held back only
because tool attribution on a reflexion is a list (`tools_used`), not a single
attributed component; once a primary tool is recorded, the scoping is a one-line
allowlist change plus the agent passing the active tool to `recall_addendum`
(mirroring how it now passes `domain`).

## Principle (unchanged across every wave)

**Off-by-default / byte-identical default**, **determinism proof 7/7**, and **the
governed gate decides promotion** (demotion uses the reversible audited `forget`
path, never the gate). Scoped recall keeps the default store byte-identical
because model-wide lines stay under the bare `model_id` key — the composite
namespace only appears when domain mining is on.
