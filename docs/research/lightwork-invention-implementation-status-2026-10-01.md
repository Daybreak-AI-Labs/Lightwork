# Invention register: implementation status

Initial static source review on October 1, 2026 at `1c27445dda93e21e2eb38ef2dde5193646832da8`;
dated follow-through below updates the selected implementations through October 6.
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
| Bounded pilot; mechanism unproven | A bounded in-product mechanism exists; wider customer outcomes and a distinct invention remain unqualified | 16 |
| Adjacent component | Code related to the direction, without its proposed general advance | 2 |
| Research only | Mechanism remains a proposal or a research question without adjacent code | 0 |
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

The next October 5 batch adds frozen current/proposed specialist instruction
comparisons through ordinary questions and typed Copilot, plus owner-consented
instruction recording from actual native AI calls. Historical instruction
records have review, download, withdrawal and uncertain-receipt recovery;
recording makes no additional model call. Paired specialist checks bind the
exact instructions, model and chosen case versions before spending. These
close concrete operating gaps in F04/F05, but neither literal answer checks nor
recorded instructions establish causal customer lift or the proposed research
mechanisms. The status counts stay unchanged.

The subsequent October 5 batch adds an independently retainable native learning-
history checkpoint. Download checks the complete supported immutable journal;
later comparison checks its exact earlier prefix while allowing new records.
The native UI journey detects validly rebuilt or missing earlier history when
the original file is retained. This narrows F03/F06's replacement-detection gap
without creating external custody, a signature, approval proof or an all-action
recording guarantee. Selected employee-companion policy and metadata controls
also use the same native authority in the page and Copilot, with current-state
readback after an uncertain save. Neither addition implements the proposed
research mechanisms or changes the status counts.

The next October 5 follow-through adds ordinary self-coaching question authoring
through the native page and typed Copilot, exact current-collection checks,
original-operation recovery and private draft restoration. The native checker
consumes the actual saved questions; offline answer/judge functions establish
that connection without claiming live quality. Learning work also retains
bounded encrypted observations and original stop acknowledgements across a
single-host process restart. An unavailable former worker stays unresolved,
not completed or automatically replayed. These narrow F03/F04/F05 operating
gaps; they do not establish an entire invention or alter the status counts.

October 5 follow-through: native reviewed audit encryption/copy custody,
manual-skill deletion, complete retained-example decisions and current delayed
worker/API-key authority narrow F03/F04/F05/F06. Each has ordinary UI and typed
Copilot controls with exact originals and uncertain-effect handling. Native
page/phone and lost-response journeys passed in disposable local installations;
remote retention, independent custody and whole-product acceptance remain open.
The live disposable PostgreSQL advisory-lock check qualified same-thread nested
learning writes, cross-thread exclusion and exception release. It does not
qualify all product PostgreSQL paths or a shared multi-host deployment. These
are scoped product results; the 28-item status counts remain unchanged.

| ID | Status | Present code and exact remaining boundary |
| --- | --- | --- |
| F01 | Partial product support | [Outcome pilots](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/outcome_pilots.py#L1-L26), [outcome accounting](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/outcome_accounting.py#L1-L8), and [candidate promotion](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/candidate_promotion.py#L1-L15) cover separate stages. The source does not establish one completed customer-owned path from selected work through measured result and recovery. October 5 adds a stock installed release adapter for bounded taught-rule workflows on a single-computer installation. Its native UI and Copilot bind exact practice, owner authorization, independent review, publication, actual release-run failure checks and retained-rule restoration. Practice establishes readiness, not measured accuracy or business lift; owned, acyclic rule-only nested workflows now share the complete pinned release and restoration path; connected actions, shared-storage qualification and the general measured-result path remain open. Custom deployment composition still refuses missing adapters: [composition refuses its absence](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/candidate_promotion_composition.py#L281-L315). |
| F02 | Partial product support | [Frozen paired pilots](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/outcome_pilots.py#L1-L7), [source-bound cost and outcome records](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/outcome_accounting.py#L1-L8), and an [injected A/B harness](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/learning_proof.py#L1-L17) exist. An [in-product evidence-readiness check](../../packages/maverick-dashboard/maverick_dashboard/outcome_pilots.py) now identifies missing paired observations, frozen-criteria decisions, and cost-family coverage; Copilot reads the same result. A passing or uploaded score, or complete operator-reported pair coverage, is not measured causal customer lift. The [simulator explicitly disallows live business-outcome acceptance](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/research_simulator.py#L1-L14). |
| F03 | Partial product support | [The shared learning stop](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/learning_guard.py#L1-L19), [authority/effect fencing](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/authority_effects.py#L1-L14), and the [evolution ledger’s stated local-only integrity limit](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/evolution_ledger.py#L1038-L1058) are concrete. This is not a complete installed, externally anchored, every-operation audit or recovery guarantee. |
| F04 | Partial product support | [Teaching and withdrawal controls](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/templates/flow_teach.html#L15-L24), a [labelled learned-flow input form](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/templates/flow_designer_inputs.js), and [exact lesson removal on Learned](../../packages/maverick-dashboard/maverick_dashboard/templates/_learning_loop.html) exist. Copilot has a bounded review/removal adapter for currently shared lessons. The [coverage register](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/post-install-operation-coverage.md#L40-L51) still requires installed-app acceptance and many direct/Copilot journeys. [Page guidance is explicitly read-only](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-dashboard/maverick_dashboard/copilot_context.py#L1-L17); separate operation catalogs cover only bounded actions. |
| F05 | Partial product support | [Tenant-scoped learned capability history and guarded generated tools](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/self_learning.py#L1-L37), [opt-in world-DB learning stores](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/learning_store.py#L1-L28), [operator-invoked staged rollout](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/learning_rollout.py#L1-L20), and [exact scoped lesson withdrawal](../../packages/maverick-core/maverick/self_harness.py) exist. The page and Copilot use that guarded rollback for currently shared lessons. Complete store/runtime/refinement/withdrawal consistency across learning families, failure modes, installations, and customers is unqualified. |
| F06 | Partial product support | [Signed recipe lineage](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/flow/recipe_lineage.py#L1-L8), [durable candidate-promotion authority](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/candidate_promotion.py#L1-L16), and [bounded ledger projection](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/evolution_ledger.py#L1038-L1058) exist. Recipe signatures attest recorded evidence, not correctness; receipt-byte verification alone cannot establish human approval without private approval material [as the verifier states](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/evolution_ledger.py#L883-L929). The ledger is local rather than externally anchored. |

## Earlier research directions

October 6 adds three bounded native mechanisms and one observed-evidence
component. P01 searches complete current teaching observations for every
smallest distinction set and admits a supported set through the unchanged
signed two-action learner. P05 keeps exact component dependency evidence and
reruns affected rules, including changed dependencies and included sources,
against a separate complete native rerun. N05 shares a reported observation
across explicitly consented cases, extracts minimum sufficient observed subsets
over authored correlated situations, and invalidates only dependent checks when
source meaning, permission or expiry changes. All three have ordinary protected
forms, typed Copilot operations and retained originals. The strong N05 exhaustive
same-information acquisition comparator selected the same information in the
executed synthetic case; no superiority is claimed. P04 projects exact actual
paired-comparison observations separately for each immutable version pair,
preserving contradictory and unfinished cells. It does not infer unobserved
applicability regions. The counts distinguish these implementations from the
broader unqualified research proposals and retain every rejected result.

The October 5 bounded F01/F02 native path now joins actual retained published
runs, owner-labeled expected answers, complete frozen baseline/candidate
executions, verified source and replay receipts, and the installed independent
release and exact restoration adapter. Designer provides ordinary case controls;
Copilot opens them, reads saved results and operates the existing release
decisions. This specific local rule-only journey, including owned acyclic nested workflows
with pinned child versions and retained child restoration, passed native and Chromium
checks. Child-only drift invalidates the old review and practice; the baseline
and candidate run their exact retained child maps. The current bounds are
16 child workflows, 64 defined steps and eight nesting levels. Its constraints and source limits are recorded in the
[post-install coverage register](../post-install-operation-coverage.md). It
establishes a usable bounded implementation, without claiming held-out accuracy,
causal customer lift, universal workflow coverage or research novelty.

The learning safety-copy path now covers safeguards, habits and accepted model
instructions/provenance in addition to its earlier six families. Exact saved
bytes and tenant/storage bindings are checked before restoration; legacy copies
cannot erase newer learning families they never covered. The next native recall
uses the restored guidance. Required safety copies precede manual and scheduled
cycles, and current coverage is reviewed in the ordinary UI and Copilot. This
narrows F03/F05's recovery gap; it does not roll back customer observations,
external actions, provider training or every proposed learning mechanism.

October 5 runtime corrections add two specific foundations. F03 outside-agent
requests now retain distinct unknown-effect and asynchronous-acceptance states,
with read-only recent receipts in the ordinary page and authorized Copilot;
neither state automatically repeats an action or claims a business result.
Native AI circuit admission also binds the admitted tenant/connection and
permits one cooldown probe, keeping local spending refusals separate from
provider outages. F05 learned-answer recall now requires the original task's
current ownership and learning permission, isolates the cache by tenant/path,
and withholds unbound legacy answers. These are concrete runtime repairs, not
proof that an entire research direction or installation has been qualified.

The next F03 correction binds a human's finished-result decision and its exact
actor/result receipt in the native sign-off transaction. Direct Chat controls
and Copilot recover that original receipt without repeating learning or a
configured handoff, and label prior reviewers and historical results honestly.
Channel decisions now carry the original card into the same transaction-local
approval guard, refusing a competing claim or changed card rather than voting
on stale material. F05 also checks the exact admitted learned-skill file and
tenant after selection and before prompt construction; withdrawal, replacement
and relabeling cannot revive a retained learned object. These concrete repairs
do not undo text already sent to a model or qualify an entire learning family.

| ID | Status | Present code and exact remaining boundary |
| --- | --- | --- |
| P01 | Bounded pilot; mechanism unproven | [Operational distinctions](../../packages/maverick-core/maverick/flow/operational_distinctions.py) identify action disagreements hidden by a selected projection, enumerate every smallest additional available distinction set and preserve conflicts even with all information. A supported set filters only the choice observations while preserving full argument/source contracts in the native signed learner. [Protected custody](../../packages/maverick-dashboard/maverick_dashboard/learning_distinctions.py), the ordinary page and Copilot prepare an unpublished package for the existing Teach/admission path. Bounds remain 4–32 matching accepted examples, six observations and two demonstrated actions with the original distinct-input/grammar qualification. This does not learn new sensors, discover arbitrary latent business state or establish unseen-case superiority. |
| P02 | Bounded pilot; mechanism unproven | [Joint procedure checks](../../packages/maverick-core/maverick/flow/procedure_interactions.py) run two owned published pure rules over the complete supported finite starting domain, both orders, repeated local state and fresh independent native references. Current permitted retained cases and all surviving source alternatives remain bound. Conditional guards preserve both separate outputs; ambiguous orders stay unresolved. The ordinary page and protected Copilot use durable request custody. This models local read/write interactions; it does not discover remote effects, authorize approvals, establish future accuracy or show research superiority. |
| P03 | Bounded pilot; mechanism unproven | [Complete teaching-question acquisition](../../packages/maverick-core/maverick/flow/operator_acquisition.py) retains every surviving simple behavior, plans every possible finite answer branch and uses currently permitted effort reports with explicit defaults. A separate same-information full Bellman reference checks the complete policy. [Native custody](../../packages/maverick-dashboard/maverick_dashboard/operator_acquisition.py), ordinary inputs and fourteen typed Copilot controls bind source versions, reports, answer grants, lifetime allowances and originals. Complete native candidate/reference checks can open a private unsaved Teach draft; separately authorized independent acceptance and normal Save are required. Bounds are three inputs, thirty-two situations and sixteen different behaviors. Prior reports are entered effort evidence, not measured savings; unseen operational meta-learning, algorithmic superiority and customer lift remain unproven. |
| P04 | Adjacent component | [Observed cells](../../packages/maverick-core/maverick/flow/operational_distinctions.py) consume actual immutable native paired comparisons after current source and exact arm-receipt checks. They retain full input/expected/both-result evidence, separate version pairs and classify conflicting or incomplete observations as ambiguous/unknown. The page and protected Copilot handoff show the complete retained evidence. This is empirical evidence on selected known inputs, not discovery or qualification of wider changing applicability regions, causal lift or unseen generalization. |
| P05 | Bounded pilot; mechanism unproven | [Selective qualification](../../packages/maverick-core/maverick/flow/selective_qualification.py) executes exact pure native rule components on a retained real case, with source/input/included-rule/policy/permission dependency checks. Changed outputs propagate to dependent components; unchanged successful components can be reused. [Native custody](../../packages/maverick-dashboard/maverick_dashboard/flow_requalification.py), the ordinary page and Copilot independently rerun the complete changed workflow, record both calculations, retain unknown completion and permit separate reuse withdrawal. Current bounds cover finite linear local taught-rule/set-value workflows with owned included rules; AI, connectors, retries, waits and cycles are excluded. Both calculations count toward work; no speed saving, general policy/connector repair or unseen accuracy is established. |
| P06 | Rejected or parked | The [register preserves a negative, bounded native-effect experiment](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L39): 26/72 correct observable-state claims versus 51/72 for a manually configured policy, with a wrong unanimous prediction. Existing recovery code is not this learned-contract algorithm. |
| P07 | Rejected or parked | The [register records the broad proposal’s rejection](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L40). Finite black-box observations cannot supply a missing remote guarantee. No production protocol-discovery claim is supported. |
| P08 | Bounded pilot; mechanism unproven | [Recovery visibility](../../packages/maverick-core/maverick/recovery_visibility.py) performs complete bounded native sign-off exploration in isolated fictional databases, discovers and minimizes paired histories with identical selected observations and different first-reviewer receipt obligations, and finds a smallest permitted separating observation. [Protected custody](../../packages/maverick-dashboard/maverick_dashboard/recovery_visibility.py), ordinary controls and Copilot retain exact requests and unknowns. The ordinary exhaustive same-information comparison matches. No live incident, remote delivery, missing protocol guarantee or customer reliability improvement is inferred. |
| P09 | Bounded pilot; mechanism unproven | [Request-specific native arrangements](../../packages/maverick-core/maverick/flow/request_program.py) compile every declared sequence of one to three owned taught steps through three positions, preserving complete original artifact alternatives. Every permitted retained case has an explicit desired answer; surviving arrangements run the current request through the unchanged native runner. Only complete answer agreement is accepted. Ordinary controls and protected Copilot author handoff preserve current source custody and exact originals. Prose describes the request; it is not an executable specification. No arbitrary generated program, connected action or workflow publication is supported. |
| P10 | Bounded pilot; mechanism unproven | [Complete finite rule behavior](../../packages/maverick-core/maverick/flow/semantic_rule_evidence.py) evaluates every declared eligible input through the unchanged native runner and compares the full typed input/output table before reusing a retained case output across structurally different generated rule sets. [Existing native evidence custody](../../packages/maverick-dashboard/maverick_dashboard/flow_requalification.py), ordinary opt-in controls and protected Copilot preserve source, case, policy, permission, deadlines and original requests. A separate complete changed-workflow comparison remains required. Bounds are at most three Yes/No or bounded whole-number inputs and sixty-four combinations per eligible step; unresolved and unsupported behavior cannot gain semantic reuse. This is ordinary finite-function equivalence, not general program/effect evidence, arbitrary authorization proof or a demonstrated improvement over an indexed checker. |
| P11 | Rejected or parked | The [register preserves the unfavorable M0 operator comparison](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L44): 42.90 ms generated reference versus 3.89 ms indexed implementation on that workload. No reusable transformation learner has been qualified. |
| P12 | Bounded pilot; mechanism unproven | [Conditional economic alternatives](../../packages/maverick-core/maverick/commitment_economic_alternatives.py) enumerate jointly feasible offers and inclusion-minimal resource additions under every surviving current capacity explanation while protecting all accepted commitments. Complete exact entered money and permission premises bind the conditional contribution/cash-outlay comparison. Native controls and typed Copilot preserve original effects, withdrawals and unknowns without allowance refund. Costs, demand and availability remain attributed reports; no general enterprise economic model, actual revenue or optimizer advantage is inferred. |
| P13 | Bounded pilot; mechanism unproven | [Private task computation](../../packages/maverick-core/maverick/private_task_computation.py) compiles ordinary finite conditions to a reduced decision structure, checks the complete declared domain against direct and indexed references, and releases only separately approved fixed answer bytes. [Native custody](../../packages/maverick-dashboard/maverick_dashboard/private_task_computation_store.py), the page and Copilot enforce exact owned task attachments, current inherited analyze/export purpose rights, physical-source lifetime uses and one release across copies/removal. Bounds are six fields, eight conditions and 65,536 declared states. Fixed-projection byte equivalence is narrow; no timing, existence, cumulative-query or general privacy guarantee, new algorithm or performance advantage is claimed. |
| P14 | Rejected or parked | The [register rejects the additional computational claim](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/docs/research/lightwork-invention-register.md#L47) beyond strong SAT and information-acquisition baselines. No deployed decision-before-reconciliation benefit was measured. |
| P15 | Bounded pilot; mechanism unproven | Existing scoped fleet stores and signed recipe lineage are joined by [anonymous structural competence sharing and native recipient qualification](../../packages/maverick-dashboard/maverick_dashboard/competence_transport.py). Two independently running installations with separate storage and keys exercised ordinary file transfer, different private bindings, complete recipient finite-state checks and exact local/Copilot execution. Originals retain actor and source authority; signed recipe drafts retain their lineage through ordinary Save. This is the bounded N03 transport component, not a second independent novel algorithm. General cross-installation privacy, fleet-wide convergence and transferred customer benefit remain unproven. |
| P16 | Adjacent component | [Self-improvement gates](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/self_improvement.py#L1-L30) and [research-only code patch review/archive](https://github.com/Daybreak-AI-Labs/Lightwork/blob/1c27445dda93e21e2eb38ef2dde5193646832da8/packages/maverick-core/maverick/self_modify.py#L1-L35) exist. The latter explicitly cannot apply/promote live code. No qualified competitive superiority or customer-operable governed code/evaluator evolution exists. |

## New candidates

The register identifies N01–N06 as hypotheses. N01 has a bounded feasibility
pilot and N02 has a model-only approval-challenge pilot. Their proposed novel
mechanisms and discriminating experiments remain unqualified. The related
components below should not be marketed as completed inventions.

| ID | Status | Closest current component and missing mechanism |
| --- | --- | --- |
| N01 | Bounded pilot; novel mechanism unproven | [Commitment viability model](../../packages/maverick-core/maverick/commitment_viability.py) and [Promise reviews](../../packages/maverick-dashboard/maverick_dashboard/commitment_viability_store.py) support two manually entered orders, conditional options, owner-reviewed proposed contention, UI/Copilot controls and source-labeled assessment. They do not provide verified live resources, external reservations, full production constraints, customer-accepted outcomes, or demonstrated advantage over a robust scheduler. [Definition](lightwork-invention-register.md#n01-preserve-achievable-customer-commitments). |
| N02 | Bounded model-only pilot; novel mechanism unproven | [Approval challenge compiler and fixed native receiver](../../packages/maverick-core/maverick/approval_challenges.py), [ordinary protected controls](../../packages/maverick-dashboard/maverick_dashboard/approval_challenges_api.py) and [Copilot](../../packages/maverick-dashboard/maverick_dashboard/product_operations_approval_challenges.py) generate, check, consent to sharing, receive and remove closed fictional approval-race programs. Actual local native guards are checked against the selected weakened variant, with source-version binding and retained receipts. No private-incident witness synthesis, joint permitted-release noninterference algorithm, automatic target semantic mapping or superiority comparison is implemented. [Definition](lightwork-invention-register.md#n02-transfer-executable-lessons-from-private-failures). |
| N03 | Bounded pilot; mechanism unproven | [Competence transport](../../packages/maverick-core/maverick/flow/competence_transport.py) extracts all qualifying anonymous structural rule patterns and automatically searches typed target input/constant bindings. [Native local qualification](../../packages/maverick-dashboard/maverick_dashboard/competence_transport.py), a file chooser and Copilot bind separate target-owned original cases and complete finite target behavior. A qualified unpublished rule runs through the unchanged executor while its receipt and source rights remain current; no independently published workflow is created. Bounds are one published scalar answer, two to eight retained cases, thirty-two source patterns, 4,096 adapters and sixty-four complete target states. Same-information ordinary adapter search matches. Cross-owner and two independent-installation native journeys exercised anonymous file transfer, complete local finite-state qualification and exact ordinary/Copilot answers; broader business transfer, general privacy and novelty remain unproven. |
| N04 | Bounded pilot; mechanism unproven | [Work restructuring](../../packages/maverick-core/maverick/flow/work_restructuring.py) groups exact permitted preparation, orders cases by individual local deadlines and keeps every full input, case result, permission and confidentiality group separate. [Native custody](../../packages/maverick-dashboard/maverick_dashboard/work_restructuring.py), ordinary per-case forms and protected Copilot bind two to eight different permitted retained cases from one published supported local-rule workflow. Every case receives a separate unchanged complete native run; all calculation/source/intermediate-receipt work shares the allowance. Exact originals survive lost replies and disabled history without replay. Same-information ordinary invariant-safe batching matches; general learned work transformations, connected effects, approvals, research superiority and customer savings remain unproven. |
| N05 | Bounded pilot; mechanism unproven | [Shared decision evidence](../../packages/maverick-core/maverick/shared_decision_evidence.py) supports explicit correlated situations, exact minimum observed subsets, shared observation acquisition and per-case meaning/consent/freshness dependencies. Its closed verifier checks actual entailment and source obligations rather than trusting a matching hash. [Native custody](../../packages/maverick-dashboard/maverick_dashboard/shared_decision_evidence_store.py), ordinary authoring and Copilot retain exact effects/originals, shared budget and uncertain admission. Bounds are eight facts, sixteen cases, four choices per case and 128 explicit joint situations. Entered reports are not independently verified; separate case checks do not prove joint resource feasibility or execute a business choice. The strong exhaustive same-information comparator matched the synthetic selection; novelty, superiority and customer benefit remain unproven. P14's rejection is unchanged. |
| N06 | Bounded pilot; mechanism unproven | [Capability learning](../../packages/maverick-core/maverick/commitment_capability_learning.py) eliminates competing entered material/capacity/shared-setup explanations using attributed completed trials or exact owned published pure-rule results. It finds inclusion-minimal conditional additions supporting jointly offered work while the native feasibility engine protects every accepted commitment across all surviving explanations. A shared test/effort allowance selects the most distinguishing bounded next test. [Native custody](../../packages/maverick-dashboard/maverick_dashboard/commitment_capability_store.py), ordinary Promise review forms and typed Copilot bind accepted-work revision, complete model, exact rule sources, current permission and review deadline. Originals survive interrupted replies; unknown attempts never repeat, and separate acknowledged release does not refund used allowance. Bounds are four offers due together, twenty-seven entered explanations, six additions and thirty-two observations; current qualification permits at most eight tests. Hypothetical additions and completed operator reports do not independently establish capacity, permission, profitable demand or actual fulfilment. Exhaustive same-information acquisition is the stated matched comparator; no research advantage, customer revenue or automatic business action is claimed. |

## Customer release and claim boundary

October 5 also closes two ordinary operating gaps in the existing foundations.
F03/F04 instruction application and Undo now retain exact native decision
receipts, enforce source access for every restored instruction, and support a
separate current-state human reconciliation after an interrupted confirmation.
An authorized successor can resolve the shared hold without inheriting a former
actor's private receipt. F04/F05's deterministic supplied-answer checker now has
ordinary domain choices, complete answer review, protected Copilot handoff and
retained original scoring readback for all three packaged public example sets.
Published synthetic cases and a user-reported model label do not establish model
quality, unseen-case generalization, autonomous evaluator improvement or a novel
algorithm. The register's research status counts are unchanged.

The same foundations now have reviewed native meaning-check admission, retained
local ranking-model publication, and exact original suggested-skill changes.
Ordinary Learning and Skills controls share their source and authority guards
with Copilot, and lost replies recover recorded work without repeating it.
Native provider transport was checked with synthetic responses, the existing
local trainer ran on eligible synthetic task outlines, and native skill files
were added and removed through reviewed controls. These improve operability and
custody; they do not qualify semantic accuracy, a new learning algorithm,
independent audit custody, causal business value or invention novelty.

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
