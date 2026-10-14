# Work intent and estimated value

The **Intent & value** page connects the work people ask for, the work Lightwork
performs, and the assumptions behind its estimated economic benefit. Copilot
reads the same authorized metrics to answer questions such as “What is the ROI
benefit of this specialist over the last 30 days?” Existing page navigation
alone is not the feature.

## From opportunity to evidence

Choose a reporting period and the platform, a specialist, an agent or a process.
Select a metric or work item to inspect its contributing tasks, execution
evidence, attribution, costs, benchmark range and retained changes. An observed
request is an opportunity until Lightwork's execution records support performed
work. Completion and performed activity are distinct states. A person saying
“done” does not create an execution receipt.

Opportunity details suggest relevant existing specialists and processes.
Opening one continues through its normal review and authorized execution flow;
it does not start work, publish a process or grant new permissions. Subsequent
execution updates the same work's explanation. Opportunity history remains
available; it is not a second benefit to add to execution value.

Related messages within the same task share one baseline. Separate task records
can be linked as follow-up or rework, with a reason and a retained audit history.
Returning after completion must not create another full baseline for that same
work. The explanation retains contributing activity and costs after linking.
Existing process savings reports are another view of overlapping work, not
additional value to sum into these totals.

## What the numbers mean

- **Gross labor value** estimates the human effort represented by eligible,
  audit-backed Lightwork work, using the selected task-size adjustment and the
  company's effort/rate assumptions.
- **AI/execution cost** uses recorded costs. Missing or incomplete cost evidence
  remains unknown; it is not silently treated as zero.
- **Net benefit** is estimated gross labor value less the recorded execution
  cost. It is withheld when the cost evidence is incomplete.
- **Opportunity** describes observed work that Lightwork has not yet performed.
  It is an estimate of potential assistance, not a completed-work benefit.

Audit evidence establishes performed activity. It does not measure the human
counterfactual or prove payroll savings, cash savings, increased revenue or a
realized financial return. The report supplies no new ROI-percentage formula
and does not connect to external platforms to invent missing costs. Use the
same period and population when comparing views; shared work is counted once
within the platform total. Incomplete source coverage is disclosed.

## Benchmarks and corrections

Onboarding requires no company benchmark setup. The starter catalog covers RFP
drafting and review, proposal responses, finance, budgets and forecasts,
reconciliation, invoices, contracts, research, reports, presentations,
correspondence, summaries, data work, customer support, compliance, recruiting,
project planning, marketing, software and procurement.

The starter low/typical/high effort ranges and USD hourly rate are **Lightwork
product heuristics**, with a version and provenance. They are not externally
validated research or measured customer effort. Small, typical and large work
use explicit size adjustments; they cannot represent every complexity factor.
An authorized company administrator can change a work type's range and rate in
the page, explain its source, and recalculate the estimate. Changes remain in
the tenant's ledger with the prior values, actor, reason, time and version.
They do not overwrite the older deployment-wide Savings assumptions.

Detection uses explicit topic/action rules from available task instructions,
without a model request. Ambiguous or unmatched instructions stay uncertain or
unknown; they do not receive an invented confident intent. Users can correct
an accessible task's work type or size, such as “Draft an RFP” to “Review an
RFP.” Corrections recalculate the estimate and preserve history.

## Settings, access and recovery

`[intent_value] enable` defaults to `true`. The installer offers a guided
reporting choice; fast, consumer and express setup persist the same defaults
without extra benchmark questions. An explicit disabled choice survives
advanced-setup resume. A deployment-level disable is a ceiling that a tenant
cannot override; the page explains a refused attempt to enable reporting.

`confirmation_required` defaults to `false`. Completion confirmation is an
optional company setting. Confirmation alone never qualifies observed work as
audited execution, and a later follow-up still belongs to the same business
task. Tenant settings and benchmarks use the dedicated page's audited controls,
not a second generic configuration form with conflicting precedence.

Current tenant and record access apply to the page, API and Copilot. Workspace
rollups do not confer permission to read another employee's task instructions,
outputs or process definitions. Every contributor in a linked task must still
be readable before its private explanation is returned. Administrative audit
access remains separate from content access. The value ledger does not store
raw prompts, results or credentials; live metric answers need no model call.

Changes use current revisions and operation references generated by the product.
An exact retry returns its original outcome. A stale revision requires refresh
and review rather than overwriting another person's change. Unavailable records
produce a recoverable error; missing evidence is never reported as zero work.

## Coverage and qualification

| Customer job | Direct journey | Copilot / shared service | Qualification |
| --- | --- | --- | --- |
| Inspect value by period and scope | Intent & value filters and metrics | Current authorized metric answer; same report service | Native permission and accounting tests required |
| Explain a number or opportunity | Contributing work and evidence detail | Selected-task metric explanation | Private contributor access must be rechecked |
| Correct intent or link rework | Task detail controls with reason | Ordinary reviewed controls; no arbitrary config edits | Revision conflict and retry checks required |
| Tune company benchmarks/settings | Human-effort benchmarks and reporting settings | Dedicated authorized service | Tenant isolation and deployment ceiling required |
| Act on an opportunity | Existing specialist/process discovery | Context retained in the native journey | Existing execution authority remains in force |
| Configure installation defaults | Guided installer; automatic short-mode defaults | Runtime reads the same settings | Focused configuration and installer tests |

This document describes the feature contract and its implemented entry points;
it does not certify a deployed release or complete customer acceptance. The
focused configuration/installer checks cover defaults, opt-out persistence,
optional confirmation, strict booleans, round trips and resume. Authenticated
application execution, browser accessibility and responsive rendering need
separate qualification; report them separately from unit or synthetic UI tests.
The optional repeat-work automation judge and draft-builder journey must also
be qualified before it is advertised as ready. No merge or deployment is part
of this implementation task.
