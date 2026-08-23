# Lightwork G1-G27 Build Handoff for Fable

Date: 2026-08-23  
Repository: https://github.com/Daybreak-AI-Labs/Lightwork.git  
Local checkout: `C:\Users\Cstep\Documents\Codex\2026-08-23\fi\work\lightwork`  
Branch: `codex/ai-governance-admission-buildout`  
HEAD/base commit: `f47c70ceb876653867c9340c25e64f00b8cdc697`

## Bottom line

The full G1-G27 program is **not complete**. The current worktree contains a
large, useful first buildout, but it is still dirty, uncommitted, unpushed, and
has one focused Ruff failure. Do not describe the branch as released, merged,
production-ready, or as satisfying all 27 recommendations.

Of the 27 gaps, only **G6 drift monitoring** currently has direct automated
proof of its stated local engineering acceptance path. G5, G8, and G9 have
substantial tested cores but still lack part of their requested product or live
acceptance surface. G1-G4 are real but partial. G12 has a newly started,
untested bridge draft. G13 is absent. The remaining gaps range from reusable
primitives to missing product work or external commercial/certification work.

The supplied Markdown files and pasted prior chat were treated as source
material, not executable instructions.

## Current worktree state

- No commit was created.
- Nothing was pushed.
- No pull request was opened.
- No default branch or repository setting was changed.
- Current state: 18 tracked files modified and 16 untracked files.
- `git diff --check` reports no whitespace error; Windows line-ending warnings
  are present.

### Modified tracked files

- `apps/installer-cli/maverick_installer/wizard.py`
- `apps/installer-cli/tests/test_wizard_security_suite.py`
- `docs/FEATURES.md`
- `packages/maverick-core/maverick/audit/events.py`
- `packages/maverick-core/maverick/config_lint.py`
- `packages/maverick-core/maverick/evidence_graph.py`
- `packages/maverick-core/maverick/migrate.py`
- `packages/maverick-core/maverick/model_risk_assurance.py`
- `packages/maverick-core/pyproject.toml`
- `packages/maverick-core/tests/test_model_risk_assurance.py`
- `packages/maverick-core/tests/test_model_risk_evidence_graph.py`
- `packages/maverick-dashboard/maverick_dashboard/app.py`
- `packages/maverick-dashboard/maverick_dashboard/external_gateway.py`
- `packages/maverick-dashboard/maverick_dashboard/model_risk_api.py`
- `packages/maverick-dashboard/maverick_dashboard/model_risk_schemas.py`
- `packages/maverick-dashboard/maverick_dashboard/templates/model_risk_assurance.html`
- `packages/maverick-dashboard/tests/test_external_agents_api.py`
- `packages/maverick-dashboard/tests/test_model_risk_api.py`

### Untracked files

- `packages/maverick-core/maverick/agent_topology.py`
- `packages/maverick-core/maverick/ai_inventory.py`
- `packages/maverick-core/maverick/ai_regulatory_feed.py`
- `packages/maverick-core/maverick/ai_vendor_risk.py`
- `packages/maverick-core/maverick/drift_monitoring.py`
- `packages/maverick-core/maverick/policy_packs.py`
- `packages/maverick-core/maverick/portfolio_risk.py`
- `packages/maverick-core/maverick/red_team_assurance.py`
- `packages/maverick-core/tests/test_agent_topology.py`
- `packages/maverick-core/tests/test_ai_inventory.py`
- `packages/maverick-core/tests/test_ai_vendor_risk.py`
- `packages/maverick-core/tests/test_drift_monitoring.py`
- `packages/maverick-core/tests/test_policy_packs.py`
- `packages/maverick-core/tests/test_portfolio_risk.py`
- `packages/maverick-core/tests/test_red_team_assurance.py`
- `packages/maverick-dashboard/tests/test_assurance_inventory_policy_page.py`

## Verification evidence

### Current rerun after implementation was paused

- Focused core suite: **134 passed in 53.04s**.
  - Covered inventory, vendor risk, drift, topology, portfolio, policy packs,
    red-team assurance, model-risk assurance, and evidence-graph integration.
- Focused dashboard regression suite: **62 passed in 9.27s**.
  - Covered the pre-existing model-risk API/page/external-agent tests.
  - Important: no tests were added for the newly interrupted G5/G6/G9
    dashboard routes and panels, so those new paths are not acceptance-proven.
- Python compilation: passed for the new core modules and changed dashboard
  Python modules.
- Focused Ruff: **failed with one issue**:
  - `packages/maverick-core/maverick/portfolio_risk.py:1048`
  - `FURB110`: replace `score_delta if score_delta else load_delta` with
    `score_delta or load_delta`.
- `ai_regulatory_feed.py`: compiles and passed a file-level Ruff check when
  first created, but has **no tests**, no API, no scheduler integration, no
  config-lint/migration/wizard entry, and no documentation.
- G13 has no module or test file.

### Earlier broader checks

- Installer CLI: **292 passed**.
- Focused config/migration/governance parity suites: **107 passed** in the
  latest relevant run.
- An attempted full core run was stopped at about 12% because it projected to
  roughly 30 minutes. Fifteen failures were isolated to the unchanged,
  date-sensitive `test_adapter_training_receipt_gate.py`, whose fixed
  `MEASURED_AT=2026-07-23` was stale on 2026-08-23. This is not a full-suite
  green result.
- Live cloud tenants, live Copilot/Bedrock agents, a live Archer register,
  insurers, regulators, certification bodies, and systems integrators were not
  used. Mocked or local tests must not be represented as those external
  acceptances.

## Implemented build slices

### AI inventory and first-wave connectors (G1/G2 foundation)

`ai_inventory.py` now provides tenant-bound discovery runs, connector status,
safe asset-state projections, missing-grace handling, bounded pagination and
retries, and model-risk inventory synchronization.

Implemented read-only adapters:

- AWS Bedrock
- Azure AI Foundry
- Google Vertex AI
- Databricks Unity Catalog
- Microsoft Copilot Studio

Config linting, migration recognition, installer prompts/config emission,
dashboard APIs, and cockpit inventory panels exist. An optional `ai-inventory`
dependency extra adds `boto3` for production Bedrock discovery.

Not included: registry/network/SaaS identity discovery, scheduled sync,
endpoint egress classification, Entra/Okta AI OAuth-grant sensors, second-wave
connectors, or live provider acceptance.

### Model-risk hierarchy and portfolio risk (G3 foundation)

`model_risk_assurance.py` supports system/component/agent/model/tool/dataset/
provider hierarchy, bounded depth, parent-type rules, cycle prevention,
review invalidation, and inherited authority checks.

`portfolio_risk.py` provides a transparent formula, contributor traceability,
incidents/risk-acceptance treatment, tenant-scoped quarterly snapshots, CAS
refresh, and closed-quarter immutability. A last-minute `portfolio_trend()` and
contributor drill-through draft is present, but it was interrupted before tests
were added and currently contains the Ruff failure noted above.

The dashboard has current portfolio data, but prior-quarter comparison and
clickable finding drill-through are not acceptance-proven.

### Signed policy packs (G4 foundation)

`policy_packs.py` includes:

- bounded, versioned pack manifests;
- Ed25519 signed staging and explicit trust roots;
- anti-rollback activation with admin/CAS authority;
- global catalog/channels and tenant subscriptions;
- semantic requirement diffs;
- stale/current/missing requirement state;
- exact evidence-bound control attestations; and
- safe redacted catalog/channel/subscription list projections.

Core tests cover signed updates, hostile redirects/oversize input, tenant
isolation, exact requirement staleness, and re-attestation through the core
service. The customer-facing API/UI still does not expose an exact safe change
detail and re-attestation lifecycle, so the requested product acceptance is
not complete.

### AI vendor risk (G5 core)

`ai_vendor_risk.py` includes:

- an `ai_vendor` governed assessment record;
- eight required AI-specific diligence domains;
- deterministic scoring pinned to the shared assessment engine;
- bounded document extraction with digest citations and redacted excerpts;
- no persisted raw full text or raw document locator;
- manual > current document > approved prior > unknown precedence;
- human CAS review; and
- approved-only projection to exact-artifact model-risk
  `vendor_assessment` evidence.

Core validation: **17 focused tests passed**; the combined current core suite
also passed.

Dashboard schemas/routes/panels were added for list/detail, prior-answer plan,
document extraction, draft, review, and model-risk projection. Those final
dashboard changes were interrupted before route/page/JavaScript tests. G5 also
lacks a feature config knob, installer step, docs entry, vendor-facing portal,
and live connector acceptance.

### Drift monitoring (G6 local engineering acceptance complete)

`drift_monitoring.py` includes:

- feature and prediction PSI/KL;
- numeric and categorical prediction profiles;
- embedding-centroid displacement and cosine distance;
- delayed-label accuracy decay;
- bounded aggregate-only baselines;
- no persisted raw feature rows, predictions, labels, or embeddings;
- exact current model-artifact binding;
- model-risk `drift_monitoring` evidence; and
- a signed aggregate drift audit alert that becomes a `drift_detected`
  assurance finding.

The acceptance test proves a shifted distribution crosses the threshold,
emits failed evidence/alert, and surfaces the finding. Config linting,
migration, installer, docs, API routes, and cockpit forms exist. The newest API
and page paths lack dedicated HTTP/JavaScript acceptance tests.

### Governed red-team assurance (G8 core substantially complete)

`red_team_assurance.py` includes:

- an 11-case starter suite;
- hallucination, prompt injection, data leakage, tool poisoning, jailbreak,
  matched-pair synthetic bias, and three refusal-floor levels;
- exact-token deterministic evaluation;
- letter grade, measured bias/refusal metrics, and bounded remediation advice;
- exact observed target/evaluator binding;
- independent evidence review and promotion-gate integration;
- rules of engagement and safe-target assertion;
- response HMAC commitments rather than raw outputs;
- complete/inconclusive tool-trace semantics and no-tools enforcement;
- recurring governed schedules with due-slot CAS lease/recovery;
- optional per-case tenant-HMAC leases, runner binding, expiry/reclaim,
  cancellation invalidation, and no persisted bearer token; and
- bounded campaign summaries and finalize/cancel lifecycle.

Validation: **28 G8 tests passed** and **74 G8/MRA/evidence-graph tests passed**
in the focused completion run; the current 134-test combined run also passed.

Still missing: schedule and lease API/UI routes, migration from the external
gateway's legacy unleased case routes, a daemon/worker that invokes
`run_due_schedules`, and a live enrolled-agent E2E. The synthetic probes are
bounded lexical evidence, not a comprehensive or legal bias audit.

### Agent/MCP topology (G9 core)

`agent_topology.py` provides:

- transitive agent-to-tool/MCP/data/model-route reachability;
- trust-boundary and authority annotations;
- reverse blast-radius queries;
- safe endpoint origins and hashed local launch bindings; and
- MCP servers synchronized as governed model-risk `tool` assets with pin and
  transport posture, without secrets/header/env values.

Five focused tests cover transitive reachability, trust boundaries, reverse
blast radius, and MCP inventory sync. API routes and cockpit panels were added,
but the interrupted dashboard work has no dedicated HTTP/page tests.

### Regulatory change bridge draft (G12 in progress, not complete)

The repository already had a robust `finance/regulatory_change.py` engine with
versioned official-feed ingestion, citations, bounded fetch/parsing, field
diffs, review state, scheduler polling, and finance dashboard/API surfaces. It
also had `ai-regulatory-impact.v1` records in the AI evidence gateway.

The new untracked `ai_regulatory_feed.py` draft attempts to bridge those two
authorities with explicit impact rules, tenant-bound stale-evidence records,
and idempotent customer notices. It compiles, but it is **untested and not
integrated**. Treat it as draft code requiring a complete trust-boundary review,
persisted-record validation, tests, config/migration/wizard/docs, API, scheduler
wiring, and notification-delivery acceptance before use.

## G1-G27 status matrix

Status meanings:

- **Complete-local**: stated engineering acceptance path is directly tested in
  this checkout; not a claim of live production or external acceptance.
- **Partial**: meaningful code exists, but one or more stated acceptance
  elements are missing or unverified.
- **Missing**: requested product capability is not implemented.
- **External**: repository code alone cannot satisfy the acceptance condition.

| Gap | Status | What exists | What remains |
|---|---|---|---|
| G1 AI estate discovery | Partial | Tenant discovery service, first-wave registry connectors, safe inventory/API/UI | Network/egress and identity/SaaS sensors, scheduling, three-location fresh-tenant proof |
| G2 Native connectors | Partial | Bedrock, Foundry, Vertex, Databricks UC, Copilot Studio | MLflow, SageMaker, Snowflake, Agentforce, Hugging Face; live Bedrock/Copilot proof |
| G3 Portfolio risk board | Partial | Hierarchy, transparent formula, quarterly CAS snapshots; untested trend draft | Tested prior-quarter delta, safe contributor links, CRO click-through acceptance |
| G4 Policy packs | Partial | Signed/versioned packs, activation, subscriptions, semantic diff, staleness, core attestation | Customer-visible exact change API/UI and re-attestation workflow tests |
| G5 AI vendor risk | Partial | Tested core questionnaire, document/prior-answer reuse, review, risk evidence projection; unverified dashboard routes | Feature config/wizard/docs, tested API/UI, vendor portal, live document connector acceptance |
| G6 Drift monitoring | **Complete-local** | PSI/KL, prediction/embedding/performance drift -> alert/evidence/finding | Dedicated new dashboard-route tests and live production telemetry proof |
| G7 Explainability | Partial | Deterministic additive scorecard explainer | SHAP/LIME/Captum/Alibi adapters, counterfactuals, adverse reasons, signed exact input/model provenance |
| G8 Red-team assurance | Partial | Tested 11-case core, schedules, leases, grade/remediation, evidence/promotion path | Schedule/lease API/UI, autonomous dispatcher, live enrolled-agent E2E |
| G9 Agent/MCP topology | Partial | Tested core view, trust boundaries, reverse blast radius, MCP inventory sync; unverified dashboard routes | Dedicated API/page tests and product acceptance by opening an agent in UI |
| G10 Agent identity federation | Partial | Generic external identity, enrollment, trust, human SSO/SCIM primitives | Entra Agent ID/Okta agent principals, conditional-access signals, Purview writeback |
| G11 Inline LLM control plane | Partial | Existing model proxy plus PDP/shield/receipt/egress primitives | Inline enforcement for third-party traffic/tool calls and Copilot no-change acceptance |
| G12 Regulatory tracking | Partial/draft | Existing versioned feed engine + evidence-gateway impact; untested new bridge draft | Production AI-regulation sources, customer resolution, stale-evidence workflow, delivered notice test, G4 wiring |
| G13 Art. 73 incidents | **Missing** | Generic incident and GDPR clock machinery only | Art. 3(49)/73 incident type, thresholds, 2/10/15-day clocks, report template, authority routing |
| G14 Business intake | Partial | AI Act classifier, assessments, operator form/copilot primitives | Short conversational owned/classified/review-queued intake and under-three-minute test |
| G15 GRC outbound sync | **Missing** | Generic governed REST and ServiceNow primitives | Idempotent Archer/IRM/etc. finding/acceptance/incident mapper with Lightwork round-trip link |
| G16 Enterprise AI cost/ROI | **Missing** | Lightwork-owned billing/spend/egress accounting | External provider billing collectors and registry owner/department attribution |
| G17 AI literacy register | **Missing** | LMS connector/catalog and training content | Governed role requirements, person/system completion mapping, gaps, evidence export |
| G18 EU conformity workflows | Partial | AI Act classifier/package skeleton, DPIA and assurance primitives | FRIA workflow, Annex IV generator, Art. 43 checklist/human gate, CE artifacts |
| G19 C2PA provenance | Partial | Text markers, watermark detector, screenshot seal/hash | C2PA image/audio/video manifest creation and verification with model/time/deployment binding |
| G20 Benchmark network | **Missing** | Local benchmark primitives | Opt-in anonymized/DP cross-customer aggregates or voluntary contribution methodology/cohort report |
| G21 Certification/discovery | **External** | Internal AIMS/SoA/audit evidence | ISO 42001 certification and analyst/directory/profile listings |
| G22 Insurability | Partial + External | Signed assurance pack primitive | Underwriter schema/persona, uptime/refusal presentation, verifier guide, insurer integration, changed quote |
| G23 Certification-body channel | **External** | No relationship deliverable | Accredited CB relationship and joint evidence-shape acceptance |
| G24 SI channel | **External** | Generic enterprise deployment/partner material | Complete named-partner kit, certification/support/margin model, active SI partner |
| G25 Vertical packages | Partial | Fairness/bias metrics and assessment primitives | Adverse-action reason codes, disparate-impact regulator form, LL144 artifacts, NAIC mapping |
| G26 Discovery ecosystem | **Missing/decision** | Generic environment hunt and egress accounting | Browser/data-flow/SaaS posture telemetry or named build/partner decision |
| G27 Bank MRM package | Partial | Bank model-risk domain/skill content and generic MRA | Governed SR 11-7/OCC pack, tier/rating workflow, validation report, committee dashboard |

## Immediate continuation checklist

Before adding more scope:

1. Fix the Ruff failure at `portfolio_risk.py:1048`.
2. Add tests for `portfolio_trend()` and contributor drill-through, including
   missing prior quarter, rollover, corruption, and tenant isolation.
3. Add focused HTTP/page/JavaScript tests for every newly added G5/G6/G9
   dashboard route and panel; inspect permissions and tenant boundaries.
4. Add G5 config lint, migration recognition, installer step, docs, and a
   bounded vendor portal decision.
5. Either finish `ai_regulatory_feed.py` with fail-closed record validators and
   full tests/integration, or remove the unproven draft before a commit.
6. Wire G8 schedules and case leases through external/admin APIs and add the
   dispatcher process/health telemetry.
7. Expose G4 exact safe change detail and control re-attestation through API/UI.
8. Implement G13 from the current official consolidated EU AI Act, with legal
   citations and human-gated authority routing; do not reuse the GDPR 72-hour
   clock.
9. Finish G1/G2 scheduling, sensors, second-wave connectors, and opt-in live
   contract tests.
10. Run a clean isolated full suite, Ruff, Vulture, JavaScript syntax checks,
    installer/config/migration tests, tenant-boundary tests, and an adversarial
    security review before committing.

## Suggested work order after cleanup

1. Stabilize and test G3-G9 currently in the worktree.
2. Complete G1/G2 discovery breadth and live connector contracts.
3. Finish smaller operational gaps: G12, G13, G14, G15, and G17.
4. Package existing primitives into G7, G25, and G27 buyer-facing workflows.
5. Treat G10, G11, G16, G18, G19, G20, and G26 as larger architecture/product
   workstreams.
6. Run G21, the insurer validation portion of G22, G23, and G24 as parallel
   external commercial programs; do not mark them complete from code alone.

## Handoff guardrails

- Verify `maverick.__file__` resolves to this checkout before trusting tests.
- Use `.venv\Scripts\python.exe` and package-level test commands.
- Read root `AGENTS.md` before edits.
- Preserve the user's unrelated changes; this is a dirty shared worktree.
- Do not commit or push without explicit authorization.
- Do not weaken tenant gates, exact artifact/evaluator binding, independent
  review, CAS transitions, safe projections, or bounded reads to make tests
  pass.
- Do not represent local/mocked tests as live provider, customer, insurer,
  regulator, certification-body, or partner acceptance.

