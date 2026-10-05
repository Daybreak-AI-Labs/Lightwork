# Invention register: implementation status

Static source review on October 1, 2026 at `1c27445dda93e21e2eb38ef2dde5193646832da8`.
This maps the 28 stable IDs in the [invention register](lightwork-invention-register.md)
to code that actually exists. It is an implementation inventory, not a runtime,
installed-product, customer-outcome, novelty, or acquisition-value test. “Adjacent”
means a narrower component exists; it does **not** mean the proposed mechanism is
implemented. No whole register item has been qualified end to end for a customer.

October 2 draft addendum: N01 now has a bounded core feasibility model in
`maverick.commitment_viability` and a durable, account/workspace-bound pilot in
`maverick_dashboard.commitment_viability_store`. The optional Promise reviews
page and typed Copilot controls let an owner enter two accepted orders, team-
reported stock and capacity, a conditional option, and observed contention;
the owner can confirm a proposed dependency or choice in the model. Source
review times, stale states, operation receipts and uncertain-response readback
remain visible. An authenticated synthetic browser journey passes from Settings
through those controls and reload; it is not an installed-app or customer test.
The service has no customer order, stock, supplier or reservation adapter and no
qualified improvement over a strong baseline. An October 3 draft adds owner-
confirmed, auditable archive and in-product readback so finished reviews can
release slots under its 1,000-active-review shared tenant limit. This has not
been qualified in an installed app or with a customer. A separately written exhaustive
robust scheduler matched all 27 small, fully observed comparator cases. N01's
novel research mechanism is still unproven; the count below records a bounded
pilot separately from a completed invention. A pilot-page
readiness check and tab-local Copilot transcript repair likewise do not
complete F01, F02 or F04.

October 3 worktree update: [Promise review resource-gap explanations](../../packages/maverick-core/maverick/commitment_viability.py)
now distinguish a confirmed shortage from a possible one and suppress stale
order figures; the owner can explore a separate fresh choice in the UI, and
Copilot reads the same review. This is an **adjacent component** for N06, not
its proposed capability-expansion learner. [Outcome pilot evidence readiness](../../packages/maverick-dashboard/maverick_dashboard/outcome_pilots.py)
and its UI/Copilot readback tell owners which paired observations and cost
families are still missing; F02 remains partial because this is operator-
reported evidence, not independently measured release lift or customer
savings. The [Learned lesson withdrawal path](../../packages/maverick-dashboard/maverick_dashboard/learning_loop_api.py)
adds exact current shared-lesson rollback with reason, administrator authority,
source visibility, and pre-change audit through both the page and Copilot.
It narrows F04/F05's in-product recovery gap but does not qualify the full
self-improvement lifecycle, installed acceptance, or externally anchored audit.

| Status | Meaning | Count |
| --- | --- | ---: |
| Partial product support | Working code for part of a broad product foundation; integrated customer acceptance still open | 6 |
| Bounded pilot; mechanism unproven | An in-product pilot exists, but neither complete customer operation nor a distinct invention is qualified | 1 |
| Adjacent component | Code related to the direction, without its proposed general advance | 5 |
| Research only | Mechanism remains a proposal or a research question | 12 |
| Rejected or parked | Retained to preserve an unfavorable or insufficient result | 4 |

## Product foundations

October 5 product follow-through adds guarded Copilot teaching from selected
permitted runs, native rule/procedure refinement forms, exact whole-instruction
restoration, readable source/publication explanations, current nightly scheduling,
and selected specialist questions from larger saved sets. These narrow F04/F05/F06
operating gaps. Authenticated disposable native-dashboard journeys exercise
teaching, saving, running and withdrawal stopping the next action. Learning history
now downloads its actual digest-only receipts and checks them through the page.
Its copy separates stored-chain consistency from human approval and an independent
history copy. This does not establish external anchoring, measured improvement,
customer qualification, or a new research mechanism; the status counts stay unchanged.

| ID | Status | Present code and exact remaining boundary |
| --- | --- | --- |
| F01 | Partial product support | [Outcome pilots](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/outcome_pilots.py#L1-L26), [outcome accounting](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/outcome_accounting.py#L1-L8), and [candidate promotion](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/candidate_promotion.py#L1-L15) cover separate stages. The source does not establish one completed customer-owned path from selected work through measured result and recovery. October 5 adds a stock installed release adapter for bounded taught-rule workflows on a single-computer installation. Its native UI and Copilot bind exact practice, owner authorization, independent review, publication, actual release-run failure checks and retained-rule restoration. Practice establishes readiness, not measured accuracy or business lift; connected actions, nested workflows, shared-storage qualification and the general measured-result path remain open. Custom deployment composition still refuses missing adapters: [composition refuses its absence](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/candidate_promotion_composition.py#L281-L315). |
| F02 | Partial product support | [Frozen paired pilots](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/outcome_pilots.py#L1-L7), [source-bound cost and outcome records](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/outcome_accounting.py#L1-L8), and an [injected A/B harness](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/learning_proof.py#L1-L17) exist. An [in-product evidence-readiness check](../../packages/maverick-dashboard/maverick_dashboard/outcome_pilots.py) now identifies missing paired observations, frozen-criteria decisions, and cost-family coverage; Copilot reads the same result. A passing or uploaded score, or complete operator-reported pair coverage, is not measured causal customer lift. The [simulator explicitly disallows live business-outcome acceptance](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/research_simulator.py#L1-L14). |
| F03 | Partial product support | [The shared learning stop](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/learning_guard.py#L1-L19), [authority/effect fencing](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/authority_effects.py#L1-L14), and the [evolution ledger’s stated local-only integrity limit](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/evolution_ledger.py#L1038-L1058) are concrete. This is not a complete installed, externally anchored, every-operation audit or recovery guarantee. |
| F04 | Partial product support | [Teaching and withdrawal controls](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/templates/flow_teach.html#L15-L24), a [labelled learned-flow input form](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/templates/flow_designer_inputs.js), and [exact lesson removal on Learned](../../packages/maverick-dashboard/maverick_dashboard/templates/_learning_loop.html) exist. Copilot has a bounded review/removal adapter for currently shared lessons. The [coverage register](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/post-install-operation-coverage.md#L40-L51) still requires installed-app acceptance and many direct/Copilot journeys. [Page guidance is explicitly read-only](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/copilot_context.py#L1-L17); separate operation catalogs cover only bounded actions. |
| F05 | Partial product support | [Tenant-scoped learned capability history and guarded generated tools](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/self_learning.py#L1-L37), [opt-in world-DB learning stores](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/learning_store.py#L1-L28), [operator-invoked staged rollout](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/learning_rollout.py#L1-L20), and [exact scoped lesson withdrawal](../../packages/maverick-core/maverick/self_harness.py) exist. The page and Copilot use that guarded rollback for currently shared lessons. Complete store/runtime/refinement/withdrawal consistency across learning families, failure modes, installations, and customers is unqualified. |
| F06 | Partial product support | [Signed recipe lineage](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/flow/recipe_lineage.py#L1-L8), [durable candidate-promotion authority](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/candidate_promotion.py#L1-L16), and [bounded ledger projection](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/evolution_ledger.py#L1038-L1058) exist. Recipe signatures attest recorded evidence, not correctness; receipt-byte verification alone cannot establish human approval without private approval material [as the verifier states](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/evolution_ledger.py#L883-L929). The ledger is local rather than externally anchored. |

## Earlier research directions

| ID | Status | Present code and exact remaining boundary |
| --- | --- | --- |
| P01 | Research only | A [feature-prefix transition model](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/generative_world_model.py#L1-L25) generalizes within a fixed representation. It does not discover missing state distinctions and revise representation and procedure together. |
| P02 | Research only | [Composed operator synthesis](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/flow/composed_operator.py#L1-L12) concerns bounded expression trees, not learned cross-procedure resource, approval, freshness, and duplicate-effect interactions. |
| P03 | Adjacent component | [Bounded operator questions](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/flow/operator_queries.py#L1-L10) seek disagreement among candidate expressions. There is no demonstrated meta-learning policy that reduces qualification effort on unfamiliar operations. |
| P04 | Research only | [Stratified effect estimation](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/promotion_effect.py#L1-L24) and [conditional counterfactual rollout](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/counterfactual_rollout.py#L1-L31) are adjacent estimators. They do not discover and qualify changing regions where a customer improvement helps, harms, or remains unknown. |
| P05 | Research only | [Recipe refinement](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/flow/action_recipe_transforms.py#L1-L14) filters the same bounded grammar against new evidence. It is not dependency-aware selective requalification after a policy or connector change. |
| P06 | Rejected or parked | The [register preserves a negative, bounded native-effect experiment](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L39): 26/72 correct observable-state claims versus 51/72 for a manually configured policy, with a wrong unanimous prediction. Existing recovery code is not this learned-contract algorithm. |
| P07 | Rejected or parked | The [register records the broad proposal’s rejection](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L40). Finite black-box observations cannot supply a missing remote guarantee. No production protocol-discovery claim is supported. |
| P08 | Research only | The [register retains a narrow paired-history testing hypothesis](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L41), without a qualified advantage over a stateful fault harness. No customer operation exposes the proposed generator. |
| P09 | Adjacent component | [Self-learning can generate a constrained tool under explicit consent](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/self_learning.py#L1-L37). That is narrower than synthesizing a request-specific implementation under current intent, authority, effects, and concrete checks. [Code self-modification is research-only and cannot deploy](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/self_modify.py#L1-L35). |
| P10 | Research only | [Recipe lineage](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/flow/recipe_lineage.py#L1-L8) binds exact artifacts and examples. The proposed reusable semantic-obligation evidence across structurally different generated programs has no qualifying implementation or comparator result. |
| P11 | Rejected or parked | The [register preserves the unfavorable M0 operator comparison](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L44): 42.90 ms generated reference versus 3.89 ms indexed implementation on that workload. No reusable transformation learner has been qualified. |
| P12 | Research only | The [register defines the unmeasured opportunity-construction hypothesis](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L45). Outcome accounting records what happened; it does not infer a feasible supplier/schedule/capacity alternative or customer willingness. |
| P13 | Research only | [Sandboxed generated tools](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/self_learning.py#L28-L37) are a safety primitive. There is no synthesis of a task-sufficient private local computation plus a formally bounded external release policy as proposed. |
| P14 | Rejected or parked | The [register rejects the additional computational claim](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L47) beyond strong SAT and information-acquisition baselines. No deployed decision-before-reconciliation benefit was measured. |
| P15 | Adjacent component | [Fleet memory ingests and recalls scoped learning records](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/fleet_memory.py#L1-L23), [opt-in shared stores](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/learning_store.py#L1-L28) and [signed local recipe lineage](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/flow/recipe_lineage.py#L1-L8) exist. They do not establish a portable cross-installation competence qualification algorithm or transferred business benefit. |
| P16 | Adjacent component | [Self-improvement gates](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/self_improvement.py#L1-L30) and [research-only code patch review/archive](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/self_modify.py#L1-L35) exist. The latter explicitly cannot apply/promote live code. No qualified competitive superiority or customer-operable governed code/evaluator evolution exists. |

## New candidates

The register identifies N01–N06 as hypotheses. N01 now has a bounded pilot,
but its proposed novel mechanism and discriminating experiment remain
unqualified; N02 still needs those specifications before implementation. The
related components below should not be marketed as completed inventions.

| ID | Status | Closest current component and missing mechanism |
| --- | --- | --- |
| N01 | Bounded pilot; novel mechanism unproven | [Commitment viability model](../../packages/maverick-core/maverick/commitment_viability.py) and [Promise reviews](../../packages/maverick-dashboard/maverick_dashboard/commitment_viability_store.py) support two manually entered orders, conditional options, owner-reviewed proposed contention, UI/Copilot controls and source-labeled assessment. They do not provide verified live resources, external reservations, full production constraints, customer-accepted outcomes, or demonstrated advantage over a robust scheduler. [Definition](lightwork-invention-register.md#n01-preserve-achievable-customer-commitments). |
| N02 | Research only | [Fleet memory](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/fleet_memory.py#L1-L23) can share scoped lessons, but there is no joint private-failure witness synthesis, permitted-release noninterference check, and target-local semantic retargeting. [Definition](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L71-L93). |
| N03 | Research only | [Signed recipe lineage](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/flow/recipe_lineage.py#L1-L8) is local evidence; no joint abstract-procedure/adapter discovery and target qualification across organizations. [Definition](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L95-L107). |
| N04 | Research only | [Flow rewrites](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/flow/evolve.py#L1-L20) propose node-type swaps; they do not learn obligation-preserving split/merge/batch/reorder transformations across jobs. [Definition](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L109-L121). |
| N05 | Research only | [Operator questions](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/flow/operator_queries.py#L1-L10) address one bounded operator. No shared decision-sufficiency certificate and freshness/permission dependency update across simultaneous business cases. [Definition](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L123-L133). |
| N06 | Adjacent component | [Promise review resource-gap explanations](../../packages/maverick-core/maverick/commitment_viability.py) identify a confirmed or possible shortage against manually entered order, stock, and capacity figures; the owner can explore a separate fresh choice in the UI and Copilot can read the gap. [Self-learning capability search](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/self_learning.py#L1-L26) finds/acquires existing catalog items or a constrained generated tool. Neither component infers and tests minimal capability expansions that unlock feasible additional customer work without displacing commitments. [Definition](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L135-L147). |

## Customer release and claim boundary

An engine, API route, test fixture, or research archive is not a complete
customer feature. The [learning journey contract](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/learned-capabilities-product-contract.md#L1-L16)
requires nontechnical setup, capture, review, teaching, use, result, withdrawal,
and recovery through the product. The [post-install coverage register](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/post-install-operation-coverage.md#L40-L51)
lists the known learning-family gaps and [Copilot’s narrow present catalog](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/post-install-operation-coverage.md#L86-L87).
`copilot_context.py` describes its page guide as read-only; bounded operation
sidecars do not give Copilot whole-platform operating authority. Until the
proposed algorithms, strong comparators, native authority, direct UI, Copilot
operations, and authenticated installed-product journeys are independently
qualified, none of these 28 IDs should be claimed as a complete breakthrough or
customer-ready capability.
