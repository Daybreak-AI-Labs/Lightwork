# Intent, work value and customer-controlled opportunities

Open **Work value & opportunities** at `/work-value` (also linked from Savings).
The page and Copilot use the same authorized report service. No model call or
provider credential is needed to classify supported requests, read reports,
correct intent, evaluate opportunities or create a workflow draft.

## Customer journey

1. Choose a UTC period, **My work** or permitted **Company totals**, business
   activity and contributor. Open a headline metric to see its exact contributing
   work population; open a work item for evidence, assumptions and history.
2. Review an opportunity. Its explanation separates the original baseline,
   unexecuted potential, current supporting records and incremental forecast.
3. **Use existing** records the choice and opens the existing capability.
   Chat receives a prefilled, editable task and an opportunity reference. The
   normal task submission binds the exact goal before dispatch. Nothing starts
   on selection. Existing workflow reuse binds the owned definition generation
   and selection time; later matching runs are associated, not causal proof.
4. If repeatability, supported completed examples and provisional net benefit
   support a draft, choose **Build reviewable workflow draft**. Input selection
   and final review remain manual; preparation can also stay manual. The native
   workflow designer receives a saved, editable definition with no schedule.
5. Inspect the definition and connection/provider readiness. Use the existing
   explicit **Publish** and **Run** controls. Draft creation neither publishes,
   executes, schedules, adds credentials nor grants permissions.
6. Return to the opportunity to inspect linked outcomes and cost feedback. Ask
   Copilot what the selected process delivered, then “What about last month?”,
   “Why?”, “Show me what contributed to that”, or “What could we improve?”
   Metric replies include their scope, UTC period, cost limitations and evidence
   link. Ambiguous entity names require clarification.

Defer and keep-current decisions persist. Unchanged declined/deferred proposals
are hidden unless **Include deferred and declined suggestions** is selected.
Reopening is explicit. None of these decisions deletes an existing process.

## Integration map

| Existing mechanism | Integration |
| --- | --- |
| WorldModel goals, parent IDs, conversations/turns, goal origins | `intent_value.sources` reads native authorized tenant stores; related goals share a work identity; unexecuted channel requests are observations only |
| Signed/strict audit reader and native `goal_end` | Successful orchestrator/source-analysis receipts establish performed work; status fields or user assertions alone do not |
| Flow execution, immutable snapshots and run generations | `flow.execution` records content-free terminal/progress receipts and binds agent goals to their native run; retries share a work identity |
| Episodes, `episode_reporting`, provider receipt catalogue | Failure/rework spend is retained; exact linked provider receipts replace overlapping runtime amounts, with separate measured/estimated coverage |
| Existing Savings, `workforce_value`, `[value]` rates | Same source episodes and department rate configuration; numeric bridge explains alternative effort/count/cost assumptions |
| `GovernedRecordStore` | Tenant-scoped work, settings, benchmark and recommendation namespaces reuse private local/shared Postgres storage, CAS, revision history and audit-outbox recovery |
| Domain profiles and owned native flows | Accessible capability matching prefers an existing process; reuse does not create a duplicate builder |
| `flow.evolve`, node outcomes/tool traces | Existing supported evolution signals are shown in reuse explanations; linked execution costs update the next-review reference without overwriting forecasts |
| Ekko `governed_candidate_projection` | Live owner/device/consent-bound patterns appear with native evidence/draft links; no raw event content is copied, no business intent or executed benefit is fabricated |
| Copilot sealed conversation context, product operations | Provider-free metric answers call `read_report`; scoped follow-ups are sealed to tenant/account/current permissions. Reviewed correction, recalculation, optional confirmation, company assumptions, benchmark override, decision, use and draft operations call the native endpoints |
| Dashboard route catalogue, Jinja shell, shared style tokens | `/work-value`, native explanation dialog, responsive report, opportunities, benchmark/rate controls, error and retry states |
| Installer and All settings | `[intent_value] enabled` and `recommendations` controls; optional completion confirmation defaults off |

There is no new world-schema migration or queue. Derived work is refreshed on
demand from complete native reads, including page/Copilot requests. A bad source
read returns unavailable rather than a zero report. Native governed stores
support both existing storage backends; this feature does not change deployment,
API authentication, billing, connection or activation policy.

## Economic and grouping contract

`taxonomy.py` composes stable business-object/action identifiers across nine
families (sales, finance, procurement, operations, support, marketing,
engineering/product, HR/communications, research/compliance). Each definition
includes description, examples, dimensions, component scope and benchmark
mapping. Draft, review, summary and extraction are distinct components.

Classification is a deterministic, local, English instruction parser with
explicit abstention. It removes bounded quoted/source blocks, associates
actions within clauses, records multiple supported activities and exposes
`supported`, `contextual`, `ambiguous`, `unknown` or `corrected` confidence.
These are evidence labels, not calibrated probabilities. It never executes
instructions from analyzed content and cannot mint an execution receipt.
Unsupported wording/languages stay unknown and may be corrected by an authorized
owner. Audited explicit intent changes in continuing work append a new estimate;
an unexecuted follow-up does not revalue already performed work. User corrections
take precedence over subsequent inference.

Ten related prompts or retries are one work item, not ten task credits. Parent
links and native flow retry/origin IDs take precedence over text similarity.
An explicit separate/new deliverable request or a separate root task creates
another item across every supported business object, including invoices. Explicit
bounded quantities are retained per object and can be corrected in the work
explanation. More than 50 deliverables or documents over ten pages require scope
review. Uncertain grouping is stated in the drilldown. Distinct tasks are
not inferred from conversation elapsed time or model latency.

For each distinct business object, the largest applicable component is used;
overlapping draft/review/extraction components are not added. Different business
objects can contribute separate components. This avoids awarding the same
document's full benchmark several times. Two independently requested documents
count as two deliverable components in one business work item; drafting and
reviewing those same two documents still use the largest component twice.

```
attributable avoided minutes = deliverable count × max(0, component minutes × Lightwork share − retained review minutes)
gross estimated labor value = attributable avoided minutes × applicable hourly rate / 60
estimated net benefit = gross estimated labor value − same-scope, same-period known execution costs
```

The benchmark concerns a bounded component, not an entire business project.
Known large/complex scope is left unvalued until reviewed. Only completed root
work or a completed native workflow qualifies for the full component. Audited
partial child/flow work is visible as performed but unvalued; costs remain.
A later revision does not award a second full benefit.

For the specification's synthetic fixture, 30 avoided minutes at USD 60/hour
with USD 2 known cost gives USD 30 gross and USD 28 estimated net. Coverage still
states excluded/unverified costs. No cost record, an unknown receipt, an unvalued
performed item or incompatible currencies makes the total net unavailable.
Known subtotal costs remain visible. No foreign-exchange conversion is invented.

Episode amounts are runtime estimates, not provider invoices. Settled, verified
observed/reconciled provider amounts are shown separately as measured. The exact
native receipt reference, pricing basis and coverage remain in the explanation
response. Native receipts have month precision; their day is allocated to the
latest linked runtime event in that month, or month start if none exists, and
labelled as such. The reporting method does not pretend this is an exact incurred
day. A positive unmatched runtime residual remains estimated rather than being
dropped. Parent flow totals are not added to child costs. No linked agent cost
and a zero default flow reservation means missing cost, not free execution.

Full credit occurs once, at the first qualifying completion. Costs use their
own event period even after the work was first credited. Specialist, named
agent and process views are overlapping **contributions**, not additional
platform benefit. Human execution principals are not labelled as agents.
Every customer total counts the underlying work once.

The legacy bridge uses the existing completed-episode predicate and configured
whole-task hours/rates on the same contributing goals/window. It reports old
attempt counts and labor values beside unique audited work and numeric
differences. Savings dates finished episodes by start; this feature dates runtime
cost at completion (or start while running), and provider costs as above. The
two labor estimates are alternatives, never summed.

Original opportunities and forecasts survive conversion. An observed item is
allocated to a linked adopted execution once; remaining potential excludes that
item. Neither its original potential nor an incremental automation forecast is
added to executed labor value. The assignment is inspectable from the original
work explanation.

## Benchmarks and research qualification

Default version: `2026-10-08.1`, effective 2026-10-08. All default task-minute
ranges, action factors, attributable shares and retained-review assumptions are
explicitly **provisional Lightwork planning assumptions**. They are not validated
industry averages. No mandatory benchmark-design onboarding is required.

Research checked while implementing:

- Noy & Zhang, *Experimental evidence on the productivity effects of generative
  artificial intelligence*, Science 381 (2023), 187–192,
  [DOI 10.1126/science.adh2586](https://doi.org/10.1126/science.adh2586),
  [indexed abstract](https://pubmed.ncbi.nlm.nih.gov/37440646/).
  This concerns bounded professional writing tasks; it does not validate full
  RFP drafting, review or finance-analysis minute baselines.
- Brynjolfsson, Li & Raymond, *Generative AI at Work*,
  [NBER Working Paper 31161](https://www.nber.org/papers/w31161).
  Customer-support productivity varies with experience and workflow. Its results
  do not establish a universal effort-reduction multiplier for other tasks.

Those studies support task-sensitive evaluation, not transferring a published
percentage to every Lightwork activity. Accordingly the product does not cite
them as the source of the provisional minute values. Companies should calibrate
component scopes, effort ranges, attributable share, retained review and labor
rates using their own work before relying on financial claims.

An admin can edit a tenant benchmark and record its basis. New estimates use the
new version. Historical estimates retain full benchmark/rate/method and cost
snapshots. **Recalculate with current benchmarks** deliberately appends a new
version, leaving original estimates and audit evidence intact. The company's
dedicated labor-rate override takes precedence over department defaults; a
deployment labor-rate override remains authoritative. No tenant edit changes
another tenant's records. Completion confirmation is off by default and records
only the user's opinion about the present result; it neither creates execution
evidence nor prevents reopening.

## Recommendation method and controls

The evaluator requires at least three distinct work items, two audit-backed
valued examples, a supported business component and a positive provisional
incremental planning net to offer a new reusable draft. These are conservative,
testable product rules, not learned confidence probabilities. Existing matching
workflows are preferred. Observed tool names are evidence of past use, never
permission grants or proof of replayable arguments.

The supported draft is a supplied-material preparation step between mandatory
input selection and result review. It does not invent external API bindings.
The customer can keep the preparation step manual too. Provider/connection
readiness and native permissions remain necessary to run agent steps.

The provisional incremental labor forecast is 20% of the assisted component's
avoided-effort estimate, capped at ten minutes per run. For a conservative
planning net, the entire observed known execution-cost mean is subtracted.
This is a disclosed planning scenario: marginal cost, external fees and retained
manual effort still require qualification. Forecast and baseline snapshots are
versioned; explicit decisions freeze their original proposal. New audited
results update a separate next-review cost reference and existing workflow
learning signals. They do not retroactively validate a labor forecast or prove
causality.

After adoption, the explanation compares the original forecast with a separate
before/after estimate. It compares mean benchmark labor values and includes costs
from every linked attempt, including failures, per audit-backed work item. A
positive forecast can therefore be followed by zero or negative estimated
improvement. Missing baseline, valuation or cost inputs leave that comparison
unavailable; known cost subtotals remain visible. Task mix and changed assumptions
can affect this estimate. It is never added again to platform value.

Draft ID reservation occurs before native save; retries recover that same
definition, including interruption after save and before linkage. A conflicting
definition or changed generation fails closed. After creation, edit manual steps
in the native builder. Publication, revocation, run admission, approvals, budget,
connection and learning controls remain the existing ones.

## Privacy, access and operational limits

- APIs use the pinned tenant and native view/operate/admin authority. Company
  totals additionally require unscoped analytics authority. Private content
  access is checked for owned work; administrative analytics do not grant chat
  access. Company explanations omit source links, employee correction reasons
  and actors. No prompt/result text is copied to the value stores or explanations.
- Copilot queries the identical API service and binds follow-up context to
  tenant, account, permissions and expiry. It does not send metric records to a
  model. Leaving a metric conversation starts a fresh guide context.
- Consent-bound Ekko patterns are owner/device reads, not company aggregates.
  Device IDs are selected explicitly; hashed stores do not provide an account-wide
  device-name catalogue. Application metadata alone is insufficient for defensible
  business intent or effort, so those patterns remain unvalued with native action
  links. Revoked/expired consent cannot release prior patterns.
- Refresh is bounded to 10,000 goals, user turns, native flow runs and work
  records. Exceeding a source bound or encountering unreadable audit/store data
  makes reporting unavailable; it does not silently publish a partial total.
  Large-tenant indexing/archival needs qualification before raising that bound.
- Source deletion retires derived current value and clears source/evidence
  references. Non-content assumption/history receipts remain for explainability.
  Policy/access changes are rechecked on detail and effect boundaries.
- Work recorded before this change may lack a workflow completion receipt or
  agent/run correlation. Such evidence is not fabricated or inferred solely from
  `completed` status. Legacy gaps are unvalued/missing, with the alternative
  Savings bridge available. Historical goal receipts remain usable when the
  native evidence is present.
- Complete invoice/subscription/external-tool coverage, causal time savings,
  calibrated inference probabilities, inferred external data bindings and
  deployment-scale/load qualification are not claimed. No paid-provider
  test, production migration, deployment or merge is part of this delivery.

## Requirement and verification map

The focused automated suites are `test_intent_value.py`,
`test_intent_value_api.py`, `test_wizard_value.py` and
`tests/browser/intent_value.spec.js`. Existing flow execution, Copilot,
configuration, privacy and reporting regressions are also checked.

| Acceptance scenario | Working implementation and focused evidence |
| --- | --- |
| 1: ten RFP prompts / separate work | Native parent/new-work grouping; `test_ten_prompts_retries_reopen_and_separate_work` |
| 2: draft corrected to review | CAS correction + snapshot/rollup recalculation; core/API correction tests and browser correction |
| 3: observation without execution | Observations create potential, never executed credit; confirmation test and exact handoff conversion test |
| 4: audited execution | Strict native receipts and flow driver seam; `test_goal_status_without_audit_does_not_award_value`, native driver test |
| 5: optional confirmation | Default off, separate opinion receipt, revisions preserved; core/API/browser checks |
| 6: tenant-specific benchmark | Governed override/version + deliberate recalculation; tenant override arithmetic test |
| 7: unknown inputs | Abstention, size review, missing cost, mixed currency; unknown/receipt/currency tests |
| 8: rework/failure | One business credit, all attempt costs, partial unvalued, native workflow residual above linked agent spend; duplicate/failure/partial/driver tests |
| 9: reconciled rollups | Unique work totals, overlapping attribution, native Savings numeric bridge; parent/agent and driver tests |
| 10: dashboard explanation | Clickable population and source/assumption/history detail; browser desktop and narrow checks |
| 11: Copilot parity | Shared `read_report`, current dashboard filters, sealed period/entity clarification, business-name resolution and ambiguity rejection; API and native execution browser checks |
| 12: access boundaries | Tenant/account/content, pending audit and reviewed-operation boundaries; company metadata, cross-tenant and least-privilege PostgreSQL tests |
| 13: opportunity conversion | Explicit handoff, exact task link, separate remaining potential; handoff/conversion test |
| 14: recommendation quality | Distinct-work and cost support, existing capability preference, versioned maturing proposal; recommendation tests |
| 15: retained control | Manual preparation, required reviews, persistent defer/decline; core/API/browser checks |
| 16: draft versus activation | Native save only, no run/schedule/publication; API/browser checks; explicit publish outcome test |
| 17: duplicate/interrupted flows | Deterministic reservation, native CAS, recover same saved draft; repeat/interruption API tests |
| 18: post-adoption feedback | Native generation/selection linkage, separate before/after cost reference; outcome/driver/handoff tests |
| 19: historical explainability | Original snapshots survive corrections, override and proposal maturation; history tests |
| 20: intuitive complete journey | Chromium drives the native opportunity → draft → explicit publish → cancelled/confirmed start → input approval → agent execution → result approval → outcomes → identical Copilot/dashboard totals; only paid inference is replaced by a synthetic runner |

## Validation record

Focused synthetic verification is not full-product or paid-provider
certification. Counts below overlap; they are not a sum of unique tests.

- Final core/API feature run: **36 passed**, including a positive forecast
  followed by negative before/after improvement when failed-attempt costs are
  included, and unavailable comparison when additional cost is unknown.
- Final `MAVERICK_TEST_SECURE_DEFAULT=1` core/API run: **36 passed**.
- Final Chromium journey/recovery run: **2 passed**. It exercises real dashboard
  and native builder APIs with synthetic data, metric explanations, correction,
  Copilot parity, retained manual steps, duplicate draft clicks, 390-pixel layout,
  keyboard focus, unavailable/retry states and zero scoped axe WCAG violations.
- Copilot, feature and installer correction rerun: **733 passed**. The initial
  broader adjacent run had **1,238 passed and 7 failed**. Its failures were the
  Copilot fallback interaction, page-registry expectation and five installer
  step-count expectations; all seven pass in the targeted rerun. An intermediate
  rerun still had one old step-count expectation; that also passes now.
- Native flow execution/index/hardening regression run: **86 passed**.
- Ruff, Vulture, JavaScript syntax, diff whitespace, plugin matrix, domain prose,
  deprecations, gRPC contract, accessibility inventory, schema/release packaging,
  migration governance and evaluator-anchor governance: **passed**. No Python
  type checker is configured in this repository; the TypeScript SDK is unchanged.
- Control/data-plane native queue/worker gate and serial soak: **passed**;
  60 goals, four workers, no duplicates or lost work. The agent loop is stubbed
  at its provider boundary, as in the existing harness.
- Full `scripts/pre-push-lint.sh`: **failed only at the existing secret-baseline
  gate**, with 19 findings in 13 files. Each flagged file's Git blob is identical
  to base `cf3804fc18c56ea3efdd773e2e132142ec64b78c`. None belongs to this patch.
  A separate scan of all 38 changed files using the same baseline settings found
  **zero new findings**. The baseline and unrelated files were not altered.
- Full-product, live paid-provider, complete invoice, production Postgres and
  deployment-scale/load qualification: **not run**. No merge or deployment.

The PR records the exact remote commit and GitHub check state. Earlier browser
runs exposed missing static routes and narrow
layout overflow, both fixed. A later signed-audit refresh exceeded the default
five-second assertion wait while completing correctly; the explicit 15-second
wait now matches these asynchronous checks. One final verification run was
interrupted by the execution environment restart and was restarted.
