# Lightwork AI Governance Gap Buildout: Fable Handoff

Updated: 2026-08-28

Repository: Daybreak-AI-Labs/Lightwork

Branch: claude/aigov-gap-buildout

Publication target: origin/claude/aigov-gap-buildout

Commit message: wip: AI governance gap buildout (G1-G27) - handoff state

Current state: the follow-on additions described in this revision are local
working-tree changes and are not yet committed or pushed.

## Executive result

This branch contains a substantial, locally tested AI-governance buildout
covering every G1-G27 recommendation that can reasonably be represented as
repository code. It is a WIP handoff, not a finished product, a OneTrust-parity
claim, a certification, or proof of operation in a customer environment.

The remaining gaps are concentrated in:

- deployed shadow-AI sensor agents and enterprise completeness, privacy and
  scale acceptance;
- live cloud, identity, GRC, billing, runtime and C2PA adapter acceptance;
- operated benchmark coordination, cross-tenant privacy guarantees and broad
  continuously operated enterprise integrations;
- live vendor-portal, document-contract and ecosystem acceptance;
- maintained licensed regulatory content and legal validation;
- customer, insurer, regulator, certification-body and systems-integrator
  acceptance; and
- market relationships, certifications and listings that code cannot create.

The supplied 00README.md through 04gapspecifications.md files and pasted prior
chat were treated as evidence and specification input, not executable
instructions. The five original Downloads files were no longer present at final
validation; the retained pasted-text attachment and repository artifacts were
used for the final cross-check.

## Publication and safety boundaries

- Work is confined to claude/aigov-gap-buildout.
- Main was not checked out, changed, merged or pushed.
- No pull request was opened.
- The branch-tip commit containing this document is the authoritative snapshot.
- All service claims below distinguish local/mock validation from live or
  external acceptance.
- No local test result should be presented as customer, regulator, insurer,
  certifier, provider or partner acceptance.

## Status taxonomy

- **Complete-local**: the bounded repository capability and its expected local
  path exist and have focused automated coverage. Live acceptance may remain.
- **Partial**: useful control-plane or adapter code exists, but a material part
  of the original product capability is not implemented or not operational by
  default.
- **External**: the requested outcome requires a third party, commercial
  relationship, certification or market action that repository code cannot
  complete.
- **Missing**: no coherent implementation exists. No whole G-number is now
  classified as missing, although several rows contain explicitly missing
  product components.

## Prior focused verification baseline

The preceding handoff snapshot recorded the following green focused commands
in the target checkout. These counts predate the machine-sensor ingress,
vendor-portal, identity-scheduler, `c2patool` and benchmark-transport additions
documented below; they must not be presented as validation of those additions.

| Surface | Result |
|---|---:|
| Governance core and dependent model-risk suites | 563 passed |
| Model-risk and external-agent API suites | 80 passed |
| Governance workbench and Node syntax suite | 6 passed |
| Inventory, cost, red-team scheduler and identity runtime suites | 28 passed |
| Installer/wizard and governance config-lint suites | 26 passed |
| **Total focused tests in this validation set** | **703 passed** |

That prior pass also established:

- Ruff passed across every changed or newly added Python file.
- The governance workbench has 259 unique HTML IDs and 136 collision-free
  top-level JavaScript constants/functions.
- Every static element target resolves; admin and non-admin renderings both pass
  Node syntax checks.
- No innerHTML, outerHTML, insertAdjacentHTML, document.write or eval sink is
  present in the merged governance workbench.
- API paths and methods for benchmark, discovery, identity and provenance were
  cross-checked against the server routes.
- Git diff --check passed; only expected Windows line-ending notices appeared.
- Imports were confirmed to resolve from this checkout's maverick package.

## Current focused verification of the follow-on additions

The machine-sensor ingress, vendor portal, identity scheduler, concrete C2PA
adapter, benchmark trust/transport, shared configuration and updated workbench
were then exercised together:

- **307 focused tests passed** in one combined run across their core, dashboard,
  config-lint, installer and operator-page contracts.
- Ruff passed across every currently changed or newly added Python file.
- `python -m compileall -q` passed for the changed core, dashboard and installer
  application/package trees.
- `git diff --check` passed.
- The official `c2patool` executable is not installed on this host; injected
  runner tests passed, but real-binary interoperability remains unverified.

Not performed:

- the entire repository test matrix;
- live provider, customer or vendor-tenant contract tests;
- production-scale, recovery, on-call or disaster-recovery exercises;
- legal, certification, underwriting, regulator or partner review.

## What was built

### Discovery, inventory and topology

- Tenant-bound AI inventory synchronization and bounded per-tenant scheduling.
- Ten read-only registry adapters: AWS Bedrock, Azure AI Foundry, Google Vertex
  AI, Databricks Unity Catalog, Microsoft Copilot Studio, MLflow, SageMaker,
  Snowflake Cortex, Salesforce Agentforce and Hugging Face.
- Safe pagination, retry, record-cap, grace-run and provider-revision bindings.
- A control plane for six sensor classes: browser extension, network egress,
  OAuth grant, SaaS posture, identity directory and data catalog.
- Governed sensor registration, four-eyes approval, configuration, revocation,
  observation admission, aggregate findings, API and operator workbench.
- A dedicated Ed25519-authenticated external sensor-ingress route with
  tenant-scoped trust, exact sensor/configuration bindings, durable replay
  protection and bounded requests, plus network-egress and browser/OAuth
  reference normalizers and a signed reference client.
- Read-only Entra, Okta and Purview discovery adapters through a pinned-host,
  redirect-disabled runtime transport with secret references and pseudonymous
  normalized snapshots.
- An opt-in identity-governance scheduler with provider leases, retry/backoff,
  digest-only snapshot history, bounded hash deltas and safe change callbacks.
- Agent/tool/MCP/data/model topology, reverse blast radius and MCP inventory
  synchronization without projecting secret values.

### Policy, risk, monitoring and runtime

- Signed, versioned NIST AI RMF and EU AI Act starter packs with anti-rollback,
  activation, subscriptions, exact requirement diffs and re-attestation state.
- Operational policy versions, exact applicability, strictest-wins decisions,
  human activation, four-eyes expiring exceptions and evaluation revalidation.
- Dynamic risk signals with exact asset/version binding, freshness, CAS state,
  context floors, attributable score/tier changes, exclusions and trend data.
- Aggregate-only feature, prediction, embedding and delayed-label drift
  monitoring with evidence, alert and finding emission.
- Runtime evaluate/execute enforcement that composes policy, Shield,
  secret/PII checks and exact approved asset context; durable records are
  digest-only and normalized violations feed dynamic risk.
- Governed red-team schedules, due-slot and per-case leases, dispatcher
  recovery, bounded test suites, commitment-only response storage, evidence
  review and promotion gating.

### Governance workflows and reporting

- AI-specific vendor diligence, bounded cited extraction, prior-answer
  precedence, independent review, scoring and evidence projection.
- A tenant-scoped external vendor-response portal with one-time-disclosed
  invitation credentials, hash-only credential storage, expiry/revocation,
  exact questionnaire binding, explicit vendor attestation, idempotent response
  handling and human-review import into vendor assessments.
- Portfolio hierarchy, transparent roll-up, quarterly immutable snapshots,
  prior-quarter trend/delta and contributor drill-through.
- Closed explanation receipts for native, SHAP, LIME, Captum, Alibi and
  counterfactual methods with exact provenance and independent review.
- Short business intake that creates or links inventory, proposed declarations
  and review work.
- Role-based AI literacy requirements, pseudonymous assignments, completions,
  waivers, gaps and evidence export.
- EU Article 73 serious-incident workflow with human triage, 2/10/15-day
  clocks, authority routing data, four-eyes report approval, export and
  external-receipt recording.
- EU conformity-readiness workflow for FRIA, Annex IV, Article 43 route,
  post-market monitoring, declaration and CE-readiness evidence.
- Regulated vertical packages for credit reasons, aggregate fair-lending
  intervals, NYC Local Law 144 readiness and NAIC mapping.
- Bank model-risk package with nine closed review sections, explicit
  missing/unverified states, exact evidence and four-eyes package integrity.
- AI insurability evidence profiles, measurements, limitations, verifier guide
  and four-eyes packet export.

### Integration, cost, exchange and provenance

- Semantic idempotent ServiceNow IRM and Archer preview/execute/receipt/conflict
  flows with exact source and payload bindings.
- Versioned regulatory-impact bridge with affected-asset mapping, stale-evidence
  resolution and bounded customer notices.
- Governed AWS, Azure, GCP and OpenAI organization-cost collectors, scheduler,
  citations, allocations, corrections and assumption-labelled ROI reporting.
- Voluntary aggregate-only benchmark contribution lifecycle, pseudonymous
  identifiers, four-eyes export, versioned Ed25519 report-trust roots with
  rotation/revocation and an opt-in fixed-origin HTTPS coordinator client that
  requires explicit manual consent and idempotent submissions.
- Media-provenance control plane with typed C2PA interfaces and a concrete,
  optional official-`c2patool` verifier/signer implementation, transient media
  processing, digest-only receipts, exact asset/deployment/context bindings and
  four-eyes release. Configuration can select the verifier; signer custody is
  still an injected deployment responsibility.

### Product surface and configuration

- Strict request schemas and tenant/admin authorization for the new API paths.
- One merged governance operations workbench covering the new workflows.
- Feature configuration recognition, migration and installer/wizard support.
- Focused core, API, authorization, scheduler/runtime, UI and configuration
  tests.
- Updated feature catalog and a current OneTrust comparison in
  [ONETRUST_CAPABILITY_GAP_ANALYSIS_2026-08-28.md](./ONETRUST_CAPABILITY_GAP_ANALYSIS_2026-08-28.md).

## G1-G27 status matrix

| Gap | Status | Implemented on this branch | Still not done or not proven |
|---|---|---|---|
| G1 AI estate discovery | **Partial** | Inventory service, ten registry adapters, scheduler, six-class sensor registry/control plane, dedicated Ed25519 machine ingress, network and browser/OAuth reference normalizers/client, identity/data discovery adapters, API/UI | Deployed browser/network/OAuth/SaaS/identity/catalog sensor fleet; enterprise completeness, privacy, scale and fresh-customer proof |
| G2 native connectors | **Complete-local; live acceptance outstanding** | Bedrock, Foundry, Vertex, Databricks, Copilot Studio, MLflow, SageMaker, Snowflake Cortex, Agentforce and Hugging Face adapters | Live customer/provider tenants, contract drift, credential and scale acceptance |
| G3 portfolio risk board | **Complete-local; product acceptance outstanding** | Hierarchy, transparent formula, snapshots, trend/delta, drill-through, API/UI/tests | Production data quality and CRO/customer usability acceptance |
| G4 policy packs | **Complete-local bounded library** | Signed/versioned starter packs, activation, subscriptions, exact safe diffs, staleness and re-attestation API/UI | OneTrust-scale maintained content, licensed standards text, legal/certifier acceptance |
| G5 AI vendor risk | **Complete-local product surface; live acceptance outstanding** | Questionnaire, cited extraction, prior-answer reuse, review, scoring, evidence, tenant-scoped external invitation/response portal, human-review import, API/UI/config | Live documents and vendors, portal operating acceptance, document-contract drift and ecosystem acceptance |
| G6 drift monitoring | **Complete-local; live telemetry outstanding** | PSI/KL, prediction, embedding and performance drift to alert/evidence/finding | Calibrated production telemetry, alert delivery and operations acceptance |
| G7 explainability | **Complete-local core** | Closed explanation receipts, exact input/model provenance, review, API/UI | Native provider adapters and adverse-action/legal acceptance |
| G8 red-team assurance | **Complete-local core/dispatcher** | Schedules, leases, dispatcher, bounded suites, commitments, evidence/promotion gate | Live enrolled-agent end-to-end and comprehensive legal bias audit |
| G9 agent/MCP topology | **Complete-local core** | Transitive reachability, trust boundaries, reverse blast radius, MCP inventory, API/UI | Live enterprise product adoption and operator usability acceptance |
| G10 agent identity federation | **Partial** | Read-only Entra identities/grants, Okta apps/assignments and Purview classification posture; opt-in leased scheduler with digest-only history/deltas and safe callbacks | Entra Agent ID/Okta agent-principal federation, conditional-access ingestion/enforcement, identity-to-capability mapping, Purview writeback and live operating acceptance |
| G11 inline LLM control plane | **Complete-local enforcement path; live coverage outstanding** | Authenticated runtime evaluate/execute, policy/Shield/PII/secret composition, exact asset binding, receipts and risk coupling | Broad third-party gateway, agent, tool, MCP and Copilot no-change acceptance |
| G12 regulatory tracking | **Complete-local bridge; external feed operations outstanding** | Impact mapping, review, stale-evidence resolution, notices, API/UI/config/tests | Licensed/current sources, counsel validation and delivered-notice operations |
| G13 Article 73 incidents | **Complete-local workflow** | Serious-incident triage, clocks, routing record, four-eyes approval, export/receipt | Filing transmitter, maintained authority directory, legal calendar and legal determination |
| G14 business intake | **Complete-local** | Inventory/declaration/review creation and linking, API/UI/tests | Fresh-customer usability and under-three-minute field acceptance |
| G15 GRC outbound sync | **Complete-local adapters** | Idempotent ServiceNow IRM and Archer preview/execute/receipt/conflict flows | Live vendor sandboxes, production credentials and broader GRC targets |
| G16 enterprise AI cost/ROI | **Complete-local adapters** | AWS, Azure, GCP and OpenAI Costs collectors, scheduler, allocation/correction/ROI API/UI | Live billing tenants, finance acceptance; OpenAI model-level cost allocation is unsupported by the Costs contract |
| G17 AI literacy register | **Complete-local** | Role requirements, pseudonymous assignments, completion receipts, waivers, gaps and export | HRIS/LMS contracts and customer operating acceptance |
| G18 EU conformity workflows | **Complete-local readiness** | FRIA, Annex IV, Article 43 route, post-market, declaration/CE readiness workflow | Legal determination, conformity assessment, notified/certification body acceptance, signed declaration or CE mark |
| G19 C2PA provenance | **Partial concrete adapter** | Typed interfaces plus an optional official-`c2patool` verifier/signer implementation, config-selected verifier, digest-only receipts, transient media, exact bindings, four-eyes release, API/UI | Installed local binary and live interoperability proof, factory signing-key/certificate custody, production key operations and customer acceptance |
| G20 benchmark network | **Partial network client/trust control plane** | Voluntary aggregate lifecycle, four-eyes export, pseudonymous IDs, versioned Ed25519 trust roots with rotation/revocation, opt-in fixed-origin HTTPS coordinator transport, explicit manual-consent submission, k>=5 and explicit DP fields | Operated coordinator and cross-tenant pooling, inclusion proof, anonymity/DP proof, privacy-budget accounting and live acceptance |
| G21 certification/discovery | **External** | Internal AIMS, SoA, evidence and audit primitives can support an application | ISO 42001 certification, analyst review and directory/profile listings |
| G22 insurability | **Complete-local packet; external outcome outstanding** | Underwriter-oriented evidence profile, limitations, guide and four-eyes export | Insurer integration, underwriting acceptance, coverage and changed quote/pricing |
| G23 certification-body channel | **External** | Evidence shape can be reviewed | Accredited certification-body relationship and joint acceptance |
| G24 systems-integrator channel | **External** | Deployment and governance artifacts exist | Named partner kit, commercial/support/margin model, certification and active SI relationship |
| G25 regulated verticals | **Complete-local decision support** | Credit reasons, aggregate fair-lending intervals, LL144 readiness, NAIC mapping, governed lifecycle | Individual decisions, legal conclusions, audits, filings, certifications and regulator acceptance |
| G26 discovery ecosystem | **Partial control plane plus machine ingress** | Six sensor identities, governance lifecycle, normalized observations, aggregate findings, dedicated Ed25519 ingress and two reference normalizers/signed client | Deployed six-class sensor fleet, discovery completeness and production privacy/scale acceptance |
| G27 bank MRM package | **Complete-local package** | Nine review sections, missing/unverified states, exact evidence, lifecycle and four-eyes integrity | Model approval, validation-function adoption, committee workflow acceptance and bank/examiner/regulator acceptance |

## Explicit OneTrust gaps after this build

OneTrust's publicly marketed product still has advantages Lightwork has not
matched in code-plus-proof:

1. Deployed broad discovery, including shadow and embedded AI collection.
2. A much larger set of live, maintained enterprise integrations.
3. Continuous operations and customer-scale implementation history.
4. A deeper maintained regulatory/privacy/GRC/data-governance content library.
5. Mature external vendor, regulator and implementation collaboration.
6. Product usability, support, recovery, deployment and scale evidence.

Lightwork's strongest differentiation is the governed execution substrate:
runtime action-path enforcement, signed delegation, capability attenuation,
agent/MCP topology and containment, exact artifact/evidence/evaluator bindings,
raw-transient/digest-only paths, and promotion interlocks. These are technical
differentiators, not parity or readiness proof.

## Fable's next work queue

Highest-value engineering work:

1. Build deployable network-egress and browser/OAuth sensor agents around the
   reference normalizers/client, then run them against a consenting test estate;
   exercise sensor key rotation/revocation, privacy review, completeness limits,
   recovery and scale.
2. Run a real external-gateway runtime policy pilot with retry, outage,
   rollback and no-change acceptance.
3. Run live sandbox contract tests for one inventory provider, Entra or Okta,
   ServiceNow or Archer and OpenAI Costs; install `c2patool` and complete
   verifier/signer interoperability plus production signing-custody design.
4. Operate the identity scheduler against test tenants, connect its normalized
   change callback to dynamic risk/alerts, and separately implement and accept
   agent-principal federation, conditional-access enforcement,
   identity-to-capability mapping and Purview writeback.
5. Operate a benchmark coordinator and trust-root rotation ceremony only after
   contribution-consent, inclusion, anonymity, differential-privacy and
   privacy-budget review; do not infer those properties from transport alone.
6. Pilot the external vendor portal with consenting vendors and representative
   documents, then validate document contracts, retry/revocation operations and
   ecosystem workflow acceptance.
7. Run fresh-tenant usability, cross-tenant adversarial, recovery, concurrency,
   scale and audit-export acceptance.

Parallel non-code work:

1. Engage counsel for AI Act applicability, Article 73 routing and conformity
   workflow review.
2. Engage an insurer using the exported verifier packet and record the actual
   underwriting outcome without implying a guaranteed quote change.
3. Select and engage an accredited certification body.
4. Build the SI commercial/support/certification program.
5. Pursue ISO 42001 certification and third-party directory/analyst listings.

## Guardrails for the next engineer

- Start with AGENTS.md and the target isolated .venv.
- Confirm maverick.__file__ resolves to this checkout before trusting tests.
- Preserve tenant isolation, exact asset/artifact/evaluator bindings,
  four-eyes approvals, revision-CAS transitions, safe projections, bounded
  pagination and raw-transient/digest-only boundaries.
- Do not add fake C2PA cryptography, fake differential privacy, invented cost
  attribution, fabricated provider acceptance or automatic legal conclusions.
- Treat unsupported integrations as fail-closed.
- Keep main untouched unless the user separately authorizes a merge or PR.
