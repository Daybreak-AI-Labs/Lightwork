# Evaluator co-evolution: promote a better judge, don't just freeze

**Status:** shipped (governed default-on) · **Module:** `maverick.evaluator_evolution`
· **Reference:** *The Red Queen Gödel Machine: Co-Evolving Agents and Their
Evaluators* (arXiv 2606.26294)

## Motivation

Lightwork's learning loop already defends against a rotting evaluator — but only
by *freezing*. `maverick.calibration` watches whether the verifier still
discriminates correct from incorrect answers and, when it stops,
`learning_frozen()` halts learning so the system never trains on its own drift.
That is the conservative half of the problem.

The Red Queen Gödel Machine (RQGM) paper identifies the other half: a **fixed**
evaluator eventually stops giving an informative signal *even when it is not
broken*. As agents improve they saturate the judge; the search plateaus; and
with a static evaluator the only safe move left is the one we already make —
freeze. The paper measures exactly this: a frozen-evaluator baseline (HGM-H)
stagnates at longer horizons "because a frozen evaluator eventually ceases to
provide an informative signal," while co-evolving the judge keeps improving
(writers: 21.8% → 40.5% panel acceptance; graders: best accuracy at 3× lower
search cost; coding: +1.8pt pass-rate at 1.35–1.72× fewer tokens). Crucially, a
co-evolving judge is also the only way to *correct* a judge: the paper turns a
reviewer that over-accepts AI text (up to 1.91× the human rate) into a
calibrated one by introducing an adversarial objective at an epoch boundary —
something a fixed objective cannot express.

The catch, stated plainly in the paper's limitations: **everything hinges on the
anchor.** "A weak or biased anchor could yield uninformative evaluators." For a
platform whose moat is *provable* learning, that is the line in the sand — an
evolving judge is only safe if the ground truth it is judged against is itself
governed and immutable.

## What ships

A bridge from *freeze-on-drift* to *promote-on-drift*, built on the existing
self-improvement governance spine rather than beside it.

1. **epsilon-best-belief (`best_belief`)** — the eps-quantile of a `Beta(1+S, 1+F)`
   posterior, the same conservative lower bound the paper uses to rank both
   agents and evaluators. Dependency-free (a small regularized-incomplete-beta
   inverted by bisection — there is no scipy in the tree).
2. **Anchor (`Anchor` / `load_anchor` / `score_on_anchor`)** — a role's fixed,
   held-out ground-truth set. An evaluator is scored by its *agreement* with the
   anchor, never by self-report. The checksum pins every evaluator-visible
   `(id, label, prompt)` item; editing any of those fields, re-labeling an item,
   or dropping an item changes the released measurement and is refused.
3. **Controlled utility evolution (`EvaluatorSlot` / epochs / `selective_erasure`)**
   — each evaluator slot carries an epoch index that advances on every swap. On a
   swap, only the learning records the displaced evaluator produced are erased;
   anchor evidence and other slots' records survive. Within an epoch the judge is
   frozen, so the signal stays stationary and the existing per-epoch guarantees
   hold.
4. **Anchor-gated promotion (`consider_promotion`)** — a challenger replaces the
   incumbent only when its anchor best-belief beats it, routed through
   `maverick.self_improvement` on a dedicated **`evaluator` rung**. It therefore
   inherits every existing interlock: the calibration freeze, the evidence floor
   (≥12 anchor evaluations), reversibility, human approval, and the signed audit.
   An evaluator only *scores* — it cannot widen the capability envelope — so no
   non-escalation proof is demanded; but because a swap re-ranks everything the
   judge scores, the rung sits above the default `max_auto_rung` (`policy`), so a
   swap needs human approval until an operator raises the ceiling to `evaluator`.
5. **The anchor is the guardrail, so the anchor is governed.** Released anchors
   are immutable, pinned by checksum in a committed
   `evaluator_anchors.lock.json`, exactly as released world-model migrations are.
   `verify_anchor_integrity` refuses to promote against a missing, malformed,
   unregistered, or changed anchor before scoring. Surfaced as a CI gate:
   `python -m maverick.evaluator_evolution --ci` (`--regen` after an intentional
   anchor addition).

The committed `self_harness_protocol_v1` anchor is a balanced synthetic
regression set for evaluator-boundary decisions. It makes the repository lock
and CI check non-vacuous. It is protocol test data, not deployment ground truth,
and cannot establish evaluator quality, external custody, or production
qualification.

## Posture

ON by default with governed self-improvement, and a no-op when explicitly
paused. Promotion runs only when `[self_improvement] enable` **and**
`[self_improvement] evaluator_evolution` are true. The promotion path requires a
complete matching release lock and fails closed for an unregistered role or a
missing or malformed lock. Everything in the
module is pure and dependency-injected — it never calls an evaluator agent; the
caller supplies each evaluator's verdicts on the anchor — so the whole mechanism
is deterministic and testable without a live model.

Arbitrary evaluator-candidate Python is separately brokered by
`maverick.self_harness_isolation` through the strict local Linux research
container. That protects the controller from candidate code but does not make a
Windows host an independent custodian: the same host account still controls the
ledger and Docker daemon. Confirmatory mode fails closed until a separately
administered custodian is available.

## What this is not

- It does **not** make the anchor itself evolvable. The paper is explicit that
  improvement *beyond* the anchor must come from objectives layered on top of a
  fixed ground truth (e.g. the adversarial-debias term), with the anchor as a
  drift guardrail. The adversarial-objective layer is future work; this ships the
  safe substrate it would need.
- It does **not** evolve the scheduler or the replacement rule, which the paper
  flags as needing "significantly more robust guardrails."
- Convergence guarantees remain epoch-local, as in the paper.

## Config

```toml
[self_improvement]
enable = true               # master switch (required)
evaluator_evolution = true  # promote a better judge instead of only freezing
evaluator_eps = 0.05        # confidence level of the best-belief lower bound
max_auto_rung = "evaluator" # optional: allow autonomous evaluator swaps
```
