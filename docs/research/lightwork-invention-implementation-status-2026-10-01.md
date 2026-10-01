# Invention register: implementation status

Static source review on October 1, 2026 at `1c27445dda93e21e2eb38ef2dde5193646832da8`.
This maps the 28 stable IDs in the [invention register](lightwork-invention-register.md)
to code that actually exists. It is an implementation inventory, not a runtime,
installed-product, customer-outcome, novelty, or acquisition-value test. “Adjacent”
means a narrower component exists; it does **not** mean the proposed mechanism is
implemented. No whole register item has been qualified end to end for a customer.

| Status | Meaning | Count |
| --- | --- | ---: |
| Partial product support | Working code for part of a broad product foundation; integrated customer acceptance still open | 6 |
| Adjacent component | Code related to the direction, without its proposed general advance | 4 |
| Research only | Mechanism remains a proposal or a research question | 14 |
| Rejected or parked | Retained to preserve an unfavorable or insufficient result | 4 |

## Product foundations

| ID | Status | Present code and exact remaining boundary |
| --- | --- | --- |
| F01 | Partial product support | [Outcome pilots](../../packages/maverick-dashboard/maverick_dashboard/outcome_pilots.py#L1-L26), [outcome accounting](../../packages/maverick-core/maverick/outcome_accounting.py#L1-L8), and [candidate promotion](../../packages/maverick-core/maverick/candidate_promotion.py#L1-L15) cover separate stages. The source does not establish one completed customer-owned path from selected work through measured result and recovery. Promotion needs an installed deployment adapter; [composition refuses its absence](../../packages/maverick-dashboard/maverick_dashboard/candidate_promotion_composition.py#L281-L315). |
| F02 | Partial product support | [Frozen paired pilots](../../packages/maverick-dashboard/maverick_dashboard/outcome_pilots.py#L1-L7), [source-bound cost and outcome records](../../packages/maverick-core/maverick/outcome_accounting.py#L1-L8), and an [injected A/B harness](../../packages/maverick-core/maverick/learning_proof.py#L1-L17) exist. A passing or uploaded score is not measured customer lift. The [simulator explicitly disallows live business-outcome acceptance](../../packages/maverick-core/maverick/research_simulator.py#L1-L14). |
| F03 | Partial product support | [The shared learning stop](../../packages/maverick-core/maverick/learning_guard.py#L1-L19), [authority/effect fencing](../../packages/maverick-core/maverick/authority_effects.py#L1-L14), and the [evolution ledger’s stated local-only integrity limit](../../packages/maverick-core/maverick/evolution_ledger.py#L1038-L1058) are concrete. This is not a complete installed, externally anchored, every-operation audit or recovery guarantee. |
| F04 | Partial product support | [Teaching and withdrawal controls](../../packages/maverick-dashboard/maverick_dashboard/templates/flow_teach.html#L15-L24) and a [labelled learned-flow input form](../../packages/maverick-dashboard/maverick_dashboard/templates/flow_designer_inputs.js) exist. The [coverage register](../post-install-operation-coverage.md#L40-L51) still requires installed-app acceptance and many direct/Copilot journeys. [Page guidance is explicitly read-only](../../packages/maverick-dashboard/maverick_dashboard/copilot_context.py#L1-L17); separate operation catalogs cover only bounded actions. |
| F05 | Partial product support | [Tenant-scoped learned capability history and guarded generated tools](../../packages/maverick-core/maverick/self_learning.py#L1-L37), [opt-in world-DB learning stores](../../packages/maverick-core/maverick/learning_store.py#L1-L28), and [operator-invoked staged rollout](../../packages/maverick-core/maverick/learning_rollout.py#L1-L20) exist. Complete store/runtime/refinement/withdrawal consistency across learning families, failure modes, installations, and customers is unqualified. |
| F06 | Partial product support | [Signed recipe lineage](../../packages/maverick-core/maverick/flow/recipe_lineage.py#L1-L8), [durable candidate-promotion authority](../../packages/maverick-core/maverick/candidate_promotion.py#L1-L16), and [bounded ledger projection](../../packages/maverick-core/maverick/evolution_ledger.py#L1038-L1058) exist. Recipe signatures attest recorded evidence, not correctness; receipt-byte verification alone cannot establish human approval without private approval material [as the verifier states](../../packages/maverick-core/maverick/evolution_ledger.py#L883-L929). The ledger is local rather than externally anchored. |

## Earlier research directions

| ID | Status | Present code and exact remaining boundary |
| --- | --- | --- |
| P01 | Research only | A [feature-prefix transition model](../../packages/maverick-core/maverick/generative_world_model.py#L1-L25) generalizes within a fixed representation. It does not discover missing state distinctions and revise representation and procedure together. |
| P02 | Research only | [Composed operator synthesis](../../packages/maverick-core/maverick/flow/composed_operator.py#L1-L12) concerns bounded expression trees, not learned cross-procedure resource, approval, freshness, and duplicate-effect interactions. |
| P03 | Adjacent component | [Bounded operator questions](../../packages/maverick-core/maverick/flow/operator_queries.py#L1-L10) seek disagreement among candidate expressions. There is no demonstrated meta-learning policy that reduces qualification effort on unfamiliar operations. |
| P04 | Research only | [Stratified effect estimation](../../packages/maverick-core/maverick/promotion_effect.py#L1-L24) and [conditional counterfactual rollout](../../packages/maverick-core/maverick/counterfactual_rollout.py#L1-L31) are adjacent estimators. They do not discover and qualify changing regions where a customer improvement helps, harms, or remains unknown. |
| P05 | Research only | [Recipe refinement](../../packages/maverick-core/maverick/flow/action_recipe_transforms.py#L1-L14) filters the same bounded grammar against new evidence. It is not dependency-aware selective requalification after a policy or connector change. |
| P06 | Rejected or parked | The [register preserves a negative, bounded native-effect experiment](lightwork-invention-register.md#L39): 26/72 correct observable-state claims versus 51/72 for a manually configured policy, with a wrong unanimous prediction. Existing recovery code is not this learned-contract algorithm. |
| P07 | Rejected or parked | The [register records the broad proposal’s rejection](lightwork-invention-register.md#L40). Finite black-box observations cannot supply a missing remote guarantee. No production protocol-discovery claim is supported. |
| P08 | Research only | The [register retains a narrow paired-history testing hypothesis](lightwork-invention-register.md#L41), without a qualified advantage over a stateful fault harness. No customer operation exposes the proposed generator. |
| P09 | Adjacent component | [Self-learning can generate a constrained tool under explicit consent](../../packages/maverick-core/maverick/self_learning.py#L1-L37). That is narrower than synthesizing a request-specific implementation under current intent, authority, effects, and concrete checks. [Code self-modification is research-only and cannot deploy](../../packages/maverick-core/maverick/self_modify.py#L1-L35). |
| P10 | Research only | [Recipe lineage](../../packages/maverick-core/maverick/flow/recipe_lineage.py#L1-L8) binds exact artifacts and examples. The proposed reusable semantic-obligation evidence across structurally different generated programs has no qualifying implementation or comparator result. |
| P11 | Rejected or parked | The [register preserves the unfavorable M0 operator comparison](lightwork-invention-register.md#L44): 42.90 ms generated reference versus 3.89 ms indexed implementation on that workload. No reusable transformation learner has been qualified. |
| P12 | Research only | The [register defines the unmeasured opportunity-construction hypothesis](lightwork-invention-register.md#L45). Outcome accounting records what happened; it does not infer a feasible supplier/schedule/capacity alternative or customer willingness. |
| P13 | Research only | [Sandboxed generated tools](../../packages/maverick-core/maverick/self_learning.py#L28-L37) are a safety primitive. There is no synthesis of a task-sufficient private local computation plus a formally bounded external release policy as proposed. |
| P14 | Rejected or parked | The [register rejects the additional computational claim](lightwork-invention-register.md#L47) beyond strong SAT and information-acquisition baselines. No deployed decision-before-reconciliation benefit was measured. |
| P15 | Adjacent component | [Fleet memory ingests and recalls scoped learning records](../../packages/maverick-core/maverick/fleet_memory.py#L1-L23), [opt-in shared stores](../../packages/maverick-core/maverick/learning_store.py#L1-L28) and [signed local recipe lineage](../../packages/maverick-core/maverick/flow/recipe_lineage.py#L1-L8) exist. They do not establish a portable cross-installation competence qualification algorithm or transferred business benefit. |
| P16 | Adjacent component | [Self-improvement gates](../../packages/maverick-core/maverick/self_improvement.py#L1-L30) and [research-only code patch review/archive](../../packages/maverick-core/maverick/self_modify.py#L1-L35) exist. The latter explicitly cannot apply/promote live code. No qualified competitive superiority or customer-operable governed code/evaluator evolution exists. |

## New candidates

The register itself identifies N01–N06 as hypotheses, and says N01 and N02
still need mechanism and discriminating-experiment specifications before
implementation. The related components below should not be marketed as those
inventions.

| ID | Status | Closest current component and missing mechanism |
| --- | --- | --- |
| N01 | Research only | [Outcome accounting](../../packages/maverick-core/maverick/outcome_accounting.py#L1-L8) retains costs/outcomes; no online feasible-continuation controller for interacting customer commitments is present. [Definition](lightwork-invention-register.md#L55-L69). |
| N02 | Research only | [Fleet memory](../../packages/maverick-core/maverick/fleet_memory.py#L1-L23) can share scoped lessons, but there is no joint private-failure witness synthesis, permitted-release noninterference check, and target-local semantic retargeting. [Definition](lightwork-invention-register.md#L71-L93). |
| N03 | Research only | [Signed recipe lineage](../../packages/maverick-core/maverick/flow/recipe_lineage.py#L1-L8) is local evidence; no joint abstract-procedure/adapter discovery and target qualification across organizations. [Definition](lightwork-invention-register.md#L95-L107). |
| N04 | Research only | [Flow rewrites](../../packages/maverick-core/maverick/flow/evolve.py#L1-L20) propose node-type swaps; they do not learn obligation-preserving split/merge/batch/reorder transformations across jobs. [Definition](lightwork-invention-register.md#L109-L121). |
| N05 | Research only | [Operator questions](../../packages/maverick-core/maverick/flow/operator_queries.py#L1-L10) address one bounded operator. No shared decision-sufficiency certificate and freshness/permission dependency update across simultaneous business cases. [Definition](lightwork-invention-register.md#L123-L133). |
| N06 | Research only | [Self-learning capability search](../../packages/maverick-core/maverick/self_learning.py#L1-L26) finds/acquires existing catalog items or a constrained generated tool. It does not infer/test minimal capability expansions that unlock feasible additional customer work without displacing commitments. [Definition](lightwork-invention-register.md#L135-L147). |

## Customer release and claim boundary

An engine, API route, test fixture, or research archive is not a complete
customer feature. The [learning journey contract](../learned-capabilities-product-contract.md#L1-L16)
requires nontechnical setup, capture, review, teaching, use, result, withdrawal,
and recovery through the product. The [post-install coverage register](../post-install-operation-coverage.md#L40-L51)
lists the known learning-family gaps and [Copilot’s narrow present catalog](../post-install-operation-coverage.md#L86-L87).
`copilot_context.py` describes its page guide as read-only; bounded operation
sidecars do not give Copilot whole-platform operating authority. Until the
proposed algorithms, strong comparators, native authority, direct UI, Copilot
operations, and authenticated installed-product journeys are independently
qualified, none of these 28 IDs should be claimed as a complete breakthrough or
customer-ready capability.
