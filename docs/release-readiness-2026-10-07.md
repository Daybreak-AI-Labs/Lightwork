# Lightwork delivery inventory — October 7, 2026

PR [#2529](https://github.com/Daybreak-AI-Labs/Lightwork/pull/2529) is the single
consolidation point for the customer-product and learning work from Codex and
Claude. It contains working product changes, narrower research implementations,
and retained experimental results. Those are different kinds of deliverables.
This inventory makes the distinction explicit rather than treating a large diff
or an available button as proof of a finished customer experience.

At the consolidation review, `main` was `6daa3165ff5a844320ec970553d5ecdec7e396c4`.
The integrated runtime at `4126faeddd03688a909da75ac31a08d9582f3294` included the
latest upstream PR work at `07fc3afd` and the subsequent question-import,
workflow-limit and Chat-search repairs. The branch accumulated 1,401 commits
above that main revision and changed 5,392 files before this inventory was added.
The cleanup preserves the combined code and uses one squash merge into main;
it does not discard one contributor's changes or delete evidence to shrink
the reported work. The final head, installation evidence and merge result are
recorded in the PR and delivery receipt.

**Where the work lives**

| Location | Responsibility |
| --- | --- |
| `packages/maverick-core/maverick` | Task execution, specialist definitions, tools, budgets, workflows, learning stores, release controls, audit and native persistence |
| `packages/maverick-dashboard/maverick_dashboard` | Ordinary product pages, protected forms, Chat, Copilot operations and native API adapters |
| `packages/maverick-channels` | Incoming/outgoing communication and channel-provider contracts |
| `packages/maverick-knowledge` | Company sources and task knowledge integration |
| Shield, Evolve and MCP packages | Protection, improvement machinery and the outside-app interface |
| `apps/installer-cli` | Installation and setup choices for the same native configuration |
| `docs/post-install-operation-coverage.md` | Delivered customer journeys and their precise qualification boundaries |
| `docs/ux-backlog-fixes/2026-10-06-delivered.md` | Original backlog mappings and dated repairs |
| `docs/research/lightwork-invention-register.md` | All 28 stable research/product directions, including unfavorable results |
| `docs/research/lightwork-invention-implementation-status-2026-10-01.md` | Implementation status and later source-specific follow-through |

Scratch patches, browser runtime files, local package builds and temporary
fixtures are separate from the reviewed shipment. The tracked audit records
are historical evidence, not current runtime configuration or proof that every
backlog item remains broken.

**What the consolidated product contains**

“Delivered” below refers to the documented supported path. It does not mean
every outside service, platform, device or customer scenario was exercised.

| Product area | Delivered supported behavior | Important limit |
| --- | --- | --- |
| Chat and outputs | Message actions, selected-answer exports, editable Word downloads, code copying, paged history, personal AI/thinking/specialist defaults, restored drafts and named published-specialist work | Word-engine layout fidelity and complete parity with every ChatGPT/Claude capability are not established. Unpublished specialist trials are answer-only. |
| Specialists and connected tools | Complete model/service editing, reviewed Save, separate publication, exact Original and real native selected-service bindings | Selecting a service does not connect it or prove outside delivery. Execution uses current published settings when work begins. |
| Workflows and schedules | Ordinary supported input forms, versioned saves, run/progress/recovery, schedules, readable list limits and saved parallel choices that reach execution | Unsupported input schemas refuse. Oversized implicit lists stop before that loop starts; earlier effects remain. Nested aggregate concurrency is not newly guaranteed. |
| Learning and improvement | Guided teaching/correction, task-derived questions, complete bounded file import, question checks, planning, instruction comparison/restoration, reviewed code practice and original-outcome recovery | Practice admission and an available control do not prove that an improvement occurred. A full customer improvement loop remains broader than these individual mechanisms. |
| Local learning releases | Owned rule-only workflows have reviewed publication, retained runs, canary failure checks and restoration | Shared-storage release support, connected actions and waiting steps remain outside this local release adapter. |
| Knowledge | Protected source setup, native source checks, preserved newer drafts and file reads, explicit oversized-input refusal | Real customer source/provider integrations still need their own acceptance. |
| Governance and privacy | Source-bound fields, complete approvals/exceptions, protected decisions on SQLite/PostgreSQL, privacy outcomes, consent delivery and recoverable native writes | Generic Studio collector/report execution remains incomplete; the supported questionnaire-builder/assessment path is connected. |
| Activity and recovery | Signed audit records, retained uncertain-effect states, Original reads, backup custody and independently retained checkpoint-copy controls | A signature does not prove business truth. Live independent custody and every-operation audit coverage are not established. |
| Reports and notifications | Fresh recurring executive PDFs, named saved report starting points, private destinations, useful own-run alerts and native specialist activity | Local/isolated delivery qualification is not proof that a customer received an external message or PDF. |
| Setup and runtime controls | Protected Settings/Copilot handoffs, sign-in/workspace recovery, one effective short-tool-list control, startup budget/reporting choices, graceful Learning shutdown and aligned installer retry limits | Startup settings require a service restart. Two repaired budget/reporting controls do not complete every advanced environment-only setting. |

The final follow-through repairs prevent silent partial completion of repeated
work, preserve 2/4/8 workflow choices through Save/Load, reject stale question
replacement, retain the actual original import after a lost reply, and repair
restored Chat selections without silently substituting a different specialist.
Question-file structure is checked against the ordinary question form, while
valid extra saved fields and exact text are preserved. Current access/source
withdrawal remains binding; an uncertain effect is not automatically replayed.

The short-tool-list repair reuses the existing core/full modes. Native core
construction exposed 25 of 3,309 registered tools in its qualified fixture;
parent denials and the model's tool ceiling remained enforced. This is a
catalog and precedence repair, not a measured customer cost-saving percentage.

**The 28-item register**

The register currently records **6 partial product foundations, 18 bounded
pilot mechanisms and 4 rejected or parked directions**. No item is certified
as a novel invention with a measured customer advantage or acquisition value.
“Bounded” means working code exists for a defined, restricted mechanism; it
does not mean the feature is merely a proposal, and it also does not establish
the proposed general breakthrough.

| ID | Direction | Recorded disposition |
| --- | --- | --- |
| F01 | Complete work-to-outcome learning lifecycle | Partial product foundation |
| F02 | Prove improvement against a strong baseline | Partial product foundation |
| F03 | Truthful customer audit and operation custody | Partial product foundation |
| F04 | Complete in-product teaching and control | Partial product foundation |
| F05 | Reliable learning stores and runtime use | Partial product foundation |
| F06 | Governed learning and verifiable records | Partial product foundation |
| P01 | Discover missing operational distinctions | Bounded pilot |
| P02 | Learn interactions between procedures | Bounded pilot |
| P03 | Acquire operational competence faster | Bounded pilot |
| P04 | Learn where an improvement applies | Bounded pilot |
| P05 | Repair only affected procedure components | Bounded pilot |
| P06 | Learn native effect and recovery contracts | Rejected or parked |
| P07 | Infer the missing recovery protocol | Rejected or parked |
| P08 | Generate minimal histories exposing recovery defects | Bounded pilot |
| P09 | Generate software for a request | Bounded pilot |
| P10 | Reuse semantic evidence across generated programs | Bounded pilot |
| P11 | Learn efficient standalone operators | Rejected or parked |
| P12 | Construct feasible profitable business alternatives | Bounded pilot |
| P13 | Keep secret-dependent computation local | Bounded pilot |
| P14 | Decide before reconciling disputed records | Rejected or parked research direction; related product decision controls are retained |
| P15 | Govern fleet learning and portable competence records | Bounded pilot |
| P16 | Govern code and evaluator evolution | Bounded pilot |
| N01 | Preserve achievable customer commitments | Bounded pilot |
| N02 | Transfer executable lessons from private failures | Bounded model-only pilot |
| N03 | Transfer executable competence through local qualification | Bounded pilot |
| N04 | Restructure work while preserving obligations | Bounded pilot |
| N05 | Share decision evidence across business cases | Bounded pilot |
| N06 | Find the capability that unlocks additional work | Bounded pilot |

The dated implementation register supplies the source, bounds, comparisons and
remaining questions for each row. Unfavorable experiments remain visible;
related useful product code is not presented as reversing a negative research
result. Complete operator-entered observations also do not prove causal lift.

**Genuine remaining work**

These are source-confirmed gaps or qualification boundaries, rather than a
repetition of the old audit's entire issue count:

1. Full unpublished-specialist trials still need a task-scoped draft execution
   path using actual permitted tools and company knowledge. The existing
   [trial implementation](../packages/maverick-dashboard/maverick_dashboard/workforce_ops_api.py)
   deliberately uses a tool-free answer check; ordinary copy explains that.
2. Generic Studio collector/report execution needs installed native target
   resolver/apply adapters. The current
   [composition](../packages/maverick-dashboard/maverick_dashboard/no_code_grc_studio_composition.py)
   connects store/readiness seams; unavailable execution adapters refuse.
3. Learning releases need broader shared-storage and connected-effect support.
   The current
   [release adapter](../packages/maverick-dashboard/maverick_dashboard/local_flow_promotions.py)
   supports local, rule-only work and refuses connected actions/waits.
4. Provider history still has a size-dependent integrity-read cost:
   [its SQLite implementation](../packages/maverick-core/maverick/provider_spend_sqlite.py)
   authenticates durable pages when opening a connection. Replacing that
   structure must preserve integrity; removing the seal would be a regression.
5. Remaining advanced environment-only controls need individual native/Settings
   integration. The synthesis reserve and spending refresh repairs cover their
   own two fields, not the whole historical UX654 family.
6. Live AI answers, customer systems, outside delivery, independent checkpoint
   retention and whole-product acceptance remain separate from the local
   qualification. Recorded spending is not a certified complete invoice.

The original `NOT_DONE.md` listed 563 historical “never started” entries and
required checking the other branch. That is not a count of 563 current confirmed
defects. The backlog mapping and source-specific delivery records take precedence
over using that old status as a current finding. This consolidation did not find
a new merge-blocking regression in its bounded review; it does not certify that
no remaining defect exists anywhere in this large repository.

**Verification and deployment**

The installed runtime at `4126faed` passed local assembly from all eight exact
committed packages: 5,516 packaged source resources, 481 registered operations
and 60 served assets. Individual native/browser/phone witnesses cover their
documented paths, including delayed replies, real retained records, changed
source/access, and Original recovery. Registration counts are composition
checks, not 481 separate customer journeys.

The final budget/reporting and tool-discovery source additions separately passed
native Settings review/save/readback, original recovery, current session
withdrawal, fresh startup/configuration reads, preserved permissions and actual
installer configuration parsing. Their combined installed build is recorded in
the final PR description. No hosted CI or paid provider call was required for
this delivery. The full historical unit-test suite was not run as a substitute
for building the requested product.

AWS infrastructure sources exist under `deploy/aws/terraform`, but **AWS
deployment is deferred by the owner's October 7 instruction**. This document
does not claim a cloud service was created or that a live deployment was verified.
