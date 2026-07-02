# Self-Harness: a governed loop that learns a model-specific harness addendum

**Status:** shipped (opt-in, OFF by default) · **Module:** `maverick.self_harness`
· **Reference:** *Self-Harness: Harnesses That Improve Themselves* (arXiv 2606.09498)

## Motivation

Lightwork already learns **behaviors** — skills and dream insights distilled from
experience and recalled as prompt context. But the **harness itself** (the
operating instructions a model runs under) was static and operator-owned: a
model that keeps making the same class of mistake never adjusts how it is
instructed.

The Self-Harness paper shows that treating the harness as a *model-specific,
learnable* artifact — mining a model's own failure traces, proposing minimal
edits, and regression-validating them — is worth double-digit gains
(+14–21pt pass-rate on Terminal-Bench-2.0 across MiniMax/Qwen/GLM, same models,
harness-only change). The companion paper *Understanding the Challenges in
Iterative Generative Optimization with LLMs* (arXiv 2603.23994) explains why so
few systems do this safely: the make-or-break design choices are hidden, and an
edit that overfits its own examples silently regresses unseen cases.

## What ships

A four-stage loop that **reuses Lightwork's existing governance spine** rather
than adding a new ungoverned optimizer:

1. **MINE** — `mine_failures(reflexions, model_id=…)` clusters one model's
   failure reflexions into recurring *weakness signatures*. Model-specific by
   construction: a weakness mined for one model can never leak into another's
   harness (the paper's key lever). A `model_id` was added to the reflexion
   record to enable this; older lines load as `None` (backward compatible).
   Clustering is token-overlap by default; `semantic_mining` (or an injected
   `similarity_fn`) groups failures by *meaning* instead — an embedding-cosine
   seam for the live path, or the deterministic built-in `semantic_similarity`
   (token + character-trigram overlap) offline, so morphological/reworded
   variants cluster. The seam must be deterministic (it is, by default), so the
   determinism guarantee holds with the flag on; a raising seam degrades to "no
   match", never crashing the pass.
2. **PROPOSE** — `propose_addendum(sig, propose_fn=…)` produces a single
   *minimal* operating-guidance line targeting the signature. The LLM proposer
   is an injected seam: `llm_proposer(llm)` ships a reflective proposer in the
   GEPA/RPT shape (arXiv 2507.19457 / 2605.21781) — read the signature + example
   goals, write one minimal line, **fail open** to the deterministic fallback on
   any provider error. That fallback is **failure-class-grounded** (specific
   per-class guidance, not a generic "slow down" line — arXiv 2603.23994 warns
   the starting artifact bounds what the loop can learn) and runs without a
   provider, so the loop stays testable offline. Either way the line flows
   through `_sanitize_line` + the length gate. A proposer may also return a
   **structured** `{line, hypothesis}` (the LLM proposer asks for JSON and parses
   it best-effort, falling back to a plain line then the deterministic fallback);
   the *hypothesis* — a one-line "why this should help" — is carried as METADATA
   into the signed audit + provenance sidecar, never into the prompt (it is
   scrubbed like the line but not policy-screened, since it can't ride a prompt).
   With `candidates_per_signature > 1` the stage does **best-of-N**: it draws
   several candidate lines for the signature, validates each, and promotes the
   strongest survivor (highest held-out delta) — GEPA-style Pareto selection that
   only matters for a *stochastic* proposer (a deterministic one collapses to the
   single candidate, so the determinism guarantee is unchanged at the default).
3. **VALIDATE** — `validate_proposal(...)` runs the paper's acceptance test: the
   edit must not regress **either** a held-in split (the mined cases) or a
   **held-out** split (unseen cases — the overfitting guard), and must help at
   least one. Scorers are injected; a live A/B needs a real model, exactly like
   `learning_rollout`'s constraints.
4. **GATE** — each validated proposal becomes a `self_improvement.Candidate` on
   the `prompt` rung and goes through `consider()`. It therefore inherits the
   evidence floor, the calibration-freeze interlock (no learning while the
   verifier is drifting), capability non-escalation, the reversibility
   requirement, and the signed learning audit. **Promotion requires
   `[self_improvement] enable`** — self-harness proposes, the shared gate
   decides.

The accepted addendum is a small per-model block **recalled into the system
prompt** at build time (`recall_addendum(model_id)`, wired into
`Agent._build_system`), keyed on the agent's resolved model so a worker's lesson
never bleeds into the orchestrator's. It is **never a mutation of the kernel
templates** — it is a file entry, and removing it is the rollback handle. This
keeps it inside the same "behavior recalled as context, snapshot + rollback"
safety model as skills and insights.

## Safety properties

- **OFF by default** (`[self_harness] enable` / `MAVERICK_SELF_HARNESS=1`); when
  off, `recall_addendum` returns `""` and the prompt is byte-for-byte unchanged.
- **Trace-poisoning is closed (two layers).** The addendum is recalled into
  every future run of a model across all channels/tenants, so an attacker who
  could plant text in a failure trace could otherwise poison it. (1) `mine_failures`
  only considers **unscoped** failures — no `channel`, no `user_id` — i.e.
  operator-local runs, never remote-user-driven ones (mirrors dreaming's
  unscoped-only guard). (2) Every proposed line — deterministic *or* from an LLM
  proposer — passes through `_sanitize_line`: control chars stripped, all
  whitespace collapsed to single spaces (no multi-line break-out), secrets
  scrubbed, length bounded. A corrupt/tampered store with non-string values is
  rejected by `load_addenda` (no literal `"None"` reaching a prompt). (3) A
  **semantic policy-erosion screen** (`_erodes_policy`) refuses a proposed line
  that tells the model to disable/bypass/ignore its own safety machinery
  (validation, auth, budget, sandbox, audit, ...) — `_sanitize_line` guards
  syntax and the gate guards capability, but neither reads the MEANING of prose
  that rides in every future prompt.
- **Two gates, not one:** self-harness only proposes; promotion needs the
  self-improvement controller. A frozen verifier or a disabled controller leaves
  the store untouched.
- **No overfitting promotion:** the held-out split is mandatory; a pure trade is
  rejected. A live caller can tighten the evidence bar with opt-in floors
  (default off, back-compatible): `require_held_out` (never promote on only the
  mined examples), `min_held_out` (an unseen-sample floor — a 1-of-1 "win" is not
  evidence), `min_delta` (an effect-size floor), `confidence_z` (the candidate's
  held-out **Wilson lower bound** must clear the baseline — statistically-confident
  generalization, not luck), and `max_cost_factor`/`max_latency_factor`/
  `max_tool_calls_factor` (reject a line that lifts pass-rate while regressing
  **cost/latency/tool-churn** — a scorer may return `{success, cost, latency,
  tool_calls}`), and `metamorphic_fn` (a meaning-preserving paraphraser — the
  held-out improvement must **survive paraphrasing** the unseen cases, catching a
  line overfit to surface wording, not the weakness). Mining support is
  per-class-adaptable (`min_support_by_class`).
- **Reversible + audited:** every applied line has a rollback handle and a signed
  `LEARNING_UPDATE` audit row.
- **Bounded + non-eroding:** an addendum is capped (`_MAX_LINES_PER_MODEL`,
  `_MAX_ADDENDUM_CHARS`) so it can't bloat every prompt, and `_compose_addendum`
  delta-merges a re-promoted line (normalized-exact: case/whitespace/punctuation)
  rather than spending a second slot on a trivial reword — an ACE-style guard
  against "context collapse"/"brevity bias" (arXiv 2510.04618).
- **Auditable provenance:** every applied line's signed `LEARNING_UPDATE` row
  carries *why* it was learned — the weakness signature + rationale, the
  proposer's *hypothesis* (when a structured proposer supplied one), and the
  unseen-split evidence (`held_out_delta`, `samples`) — not just the text, so a
  rollback or compliance review can see the diagnostic behind each edit. The
  same provenance is also kept in a **structured per-line sidecar** (`*.meta.json`,
  keyed by a content-addressed line id) alongside the prompt-bound store — which
  stays byte-stable — reconciled to the block under the same lock, restored on
  rollback, and best-effort (a missing sidecar never affects recall).
- **Retirable (anti-staleness):** prompt guidance goes stale as models, tools,
  and APIs change. `retire_stale(older_than_days=…)` removes lines not active
  within a TTL — where "active" means last re-promoted **or** last *recalled*,
  so a line that keeps getting **used** survives even if it isn't re-promoted.
  Usage is tracked by `note_recall` (wired into `Agent._build_system`'s consumer,
  in-process throttled so the per-prompt hot path stays a pure read with at most
  one cheap write per model per interval). A line with no provenance record
  (legacy) is never auto-retired since its age is unknown. Audited (`retire`).
- **Conflict-aware:** addenda are cumulative, so a later lesson can quietly
  oppose an earlier one. `find_conflicts` / `detect_store_conflicts` flag
  suspected contradictions (shared topic, opposite polarity) into the
  `SelfHarnessReport` and `maverick self-harness conflicts` for operator review.
  An optional injected `classifier_fn` (a semantic judge, e.g. an LLM) refines
  the lexical heuristic — catching reworded conflicts it misses and suppressing
  its false positives, falling back to the heuristic per-pair on any judge error.
  Advisory only — a false positive must never silently drop real guidance.
- **Scoped recall (per-domain AND per-tool):** a line mined scoped to a
  department or a tool (`mine_bucket_by=("domain","tool")`) is stored under a
  composite key (`"<model>\x00domain=<d>"` or `"<model>\x00tool=<t>"`,
  NUL-separated so it can't collide with a model id) and recalled **only for
  matching runs** via `recall_addendum(model_id, domain=…, tools=…)` — wired into
  `Agent._with_harness_addendum` from the run's `self.domain` and its available
  tools. The "tool" a failure belongs to is the tool in play at failure (the last
  of the reflexion's `tools_used`); tool scopes are sorted so the recalled block
  is byte-identical regardless of registry order. Model-wide lines stay under the
  bare model id, so the default store is **byte-identical** and the determinism
  proof is unaffected.
- **Outcome-aware lifecycle:** `note_outcome` records per-line recall→success/
  failure counters (crediting the same model-wide + domain + tool scopes recall
  injected); `review_efficacy` re-measures a line's causal lift with the live A/B
  and **demotes dead weight** (audited `forget`, the reversible direction — never
  the promotion gate); and a `canary` line graduates or is pulled by
  `review_canaries` from those counters (a staged-rollout valve). Outcomes
  credit **every model whose guidance the run recalled** — each agent registers
  its resolved model on `ctx.harness_models` at recall time, so worker models'
  lines accumulate counters too, not just the orchestrator's. The
  live A/B itself is the injected `score_with`/`score_without` seam, built out by
  `self_harness_eval.corpus_ab_scorers` (corpus + `run_fn`/`judge_fn`).
- **Robust validation:** `holdout_rotations` cross-validates a candidate across K
  deterministic holdout folds (`corpus_kfold_splits`) and promotes only if the
  lift holds on **every** fold — a lucky single split can't carry it. The LLM
  judge supports **self-consistency** (`judge_samples` → `llm_judge(samples=)`):
  ask N times with diverse meaning-preserving framings, take the majority vote,
  fall open to the deterministic heuristic on a tie. Both default to the
  single-split / single-call historical behavior.
- **Scoped validation:** a domain-scoped candidate is judged on its own
  department's ground truth. The corpus's domain keys (the loader's documented
  `{model|domain: [...]}` shape) become per-domain evaluation quads via the
  `eval_for_context` seam on `run_self_harness` — the driver auto-builds it
  (`_context_evaluator`) alongside the model-wide A/B, sharing the same
  eval-budget pot. A general corpus under-credits a narrow line; its own
  domain's cases don't.

## Operating it

- `maverick self-harness preview [--model M] [--min-support N]` — read-only dry
  run: shows the weaknesses and the lines it *would* propose, writes nothing.
- `maverick self-harness run [--model M] [--all-models] [--retire/--no-retire]
  [--canary]` —
  run one governed **cycle**: mine → propose → validate → gate, then retire stale
  guidance. The on-demand / scheduled entry point (`run_self_harness_cycle`).
  `--canary` stages this run's promotions on probation (recalled, but graduated
  or pulled from real run outcomes by the canary review); leaving it off defers
  to `[self_harness] promote_as_canary`.
  `--all-models` runs the cycle for **every distinct configured role model** (the
  fleet, `run_self_harness_all_models`), so worker models learn their own
  harness, not just the orchestrator — each mines only its own traces.
  Without a live A/B scorer (a real model + eval harness, injected
  programmatically) it is a **dry** pass that writes no new guidance; stale-line
  retirement still runs — so a cron'd `run` keeps guidance fresh before live
  scoring is wired. Promotion still requires `[self_improvement] enable`. The
  command also surfaces **governance readiness** — if a pass promoted nothing it
  says *why*: learning is frozen (verifier drift), the promotion gate is off, or
  it was a dry pass — so a paused loop is explained, not mistaken for broken
  (`SelfHarnessReport.frozen` / `.gate_enabled`).
- `maverick self-harness show [--verbose]` — what was learned per model;
  `--verbose` adds each line's provenance (signature, held-out delta, samples,
  learned/updated dates) so an operator can judge the evidence behind it.
- `maverick self-harness efficacy --model M` — each line's recall→outcome record
  (successes / failures / rate, with the department tag) so dead weight is visible
  before it's pruned.
- `maverick self-harness canary --model M [--review]` — inspect lines on canary
  probation, or `--review` to advance the lifecycle (graduate the proven, pull the
  failing) from the accumulated counters.
- `maverick self-harness retire --older-than-days N` — prune stale guidance.
- `maverick self-harness conflicts` — flag contradictory lines for review.
- `maverick self-harness log` / `forget [--domain D]` — the audit trail and the
  undo handle (`--domain` scopes the rollback to one department's block).

Config (`[self_harness]`, resolved by `config.get_self_harness` / `settings()`):
`enable`, `min_support`, the validation floors (`require_held_out`, `min_held_out`,
`min_delta`, `confidence_z`, `max_cost_factor`/`max_latency_factor`/
`max_tool_calls_factor`, `min_support_by_class`), `candidates_per_signature`
(best-of-N proposing), `semantic_mining`, `mine_bucket_by` (per-domain/per-tool
mining), `holdout_rotations` (K-fold cross-validation), `judge_samples`
(self-consistency judging), `promote_as_canary` (stage every promotion on canary
probation), `efficacy_review`, `eval_corpus`, `eval_budget_dollars` (spend
ceiling for one auto-evaluated cycle's LLM calls — the runner and judge share
the pot, and an exhausted budget fails the evaluation *closed*: candidates are
rejected, never promoted on partial scores), and
`retire_after_days`. Defaults preserve the
loop's historical behavior; the installer wizard ships production-safe floors
when self-harness is enabled and writes the advanced knobs (eval corpus,
per-department scoping, semantic mining, best-of-N, efficacy review, auto-retire)
when an operator opts into the advanced follow-up. `run_self_harness_pass` reads
these automatically, and the dashboard `/learned` page surfaces per-line
provenance, recall→outcome counters, canary state, the department tag, and
conflict warnings (parity with `show --verbose` / `efficacy` / `canary` /
`conflicts`).
- Automatic operation: a scheduler runs `maverick self-harness run` (or calls
  `run_self_harness_cycle(...)`), which executes one governed pass then prunes
  stale guidance. For live promotion the caller injects a held-in/held-out scorer
  (the A/B over the candidate prompt — a real evaluation needs a real model) and
  engages the self-improvement controller; without it the cycle is a safe dry
  pass that still retires. The scorer + controller are the only seams between the
  offline-testable loop and a fully-autonomous, live one.

## Addresses paper #2's "hidden design choices"

The loop makes the previously-implicit choices explicit and tunable:
*starting artifact* = the current per-model addendum; *editability scope* = a
single appended guidance line on the `prompt` rung (never code/tools);
*credit horizon* = the failure signature mined from traces; *batching* =
`min_support` (the evidence floor) and the held-in/held-out split sizes.
