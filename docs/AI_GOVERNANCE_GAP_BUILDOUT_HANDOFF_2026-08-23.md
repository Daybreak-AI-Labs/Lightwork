# Lightwork AI Governance Gap Buildout: Fable Handoff

Updated: 2026-09-01

Repository: Daybreak-AI-Labs/Lightwork

Branch: claude/aigov-gap-buildout

Publication target: origin/claude/aigov-gap-buildout

Commit message: wip: AI governance gap buildout (G1-G27) - handoff state

Publication state: `9cadfabccb59ed81b9e4e1421c08769d1eb5e1e0` is the last
previously verified remote snapshot. This document describes the complete
2026-09-01 handoff candidate on `claude/aigov-gap-buildout`; the final committer
must record and verify the new branch tip after push. Main remains untouched.

## Executive result

This branch contains a substantial, locally tested AI-governance buildout
covering every G1-G27 recommendation that can reasonably be represented as
repository code. It is a WIP handoff, not a finished product, a OneTrust-parity
claim, a certification, or proof of operation in a customer environment.

The remaining gaps include both buildable product breadth and external
operation or acceptance. Production compositions now exist for packaged
Governance Agents, AI Evidence Analysis, California DROP and identity
governance. AI-entity tasks, consent bulk jobs/custom indexes/current publish
and Global Opt-Out contracts, current assessment projections and first-class
TPRM Contract/Engagement records are also implemented and mounted. The current
tree additionally includes tenant-private document custody for administration
tokens and incident attachments, reviewed evidence-to-assessment proposals,
role-aware deterministic executive PDF reporting, a mounted six-suite shared
governance inventory, a native application scanner and device-bound consent
source/server contracts, and six stock privacy-discovery readers with an
approved-review digest target. Current material code/deployment gaps instead
include full canonical-history coverage and independently operated verifiers;
compiled/deployed native consent host integrations and certified TCF/GPP/
downstream AdTech interoperability; broad privacy-source, format, database-value
and RoPA semantic coverage; exact identity-provider transports and live
provider/customer operation; deployment of the Bubblewrap-contained optional
OCR and legacy-XLS dependencies; visual-semantic image analysis beyond OCR; and
the broader product gaps enumerated in the current OneTrust matrix.
External gaps include:

- deployment of the new sensor agents into a consenting external estate and
  enterprise completeness, privacy and production-scale acceptance;
- credentials and consenting test tenants for live cloud, identity, GRC,
  billing and external-gateway contract acceptance;
- production C2PA certificate/key custody and relying-party acceptance;
- live benchmark participants and independently reviewed anonymity,
  differential-privacy and privacy-budget guarantees;
- consenting external vendors, representative documents and ecosystem
  acceptance for the vendor portal;
- maintained licensed regulatory content and legal validation;
- live web/app traffic, privacy review, causal-validity review and distributed
  runtime acceptance for the new consent-experiment path;
- production consent IdP/JWKS and subject-digest-key custody, native mobile/TV
  SDK operation and live cross-domain/device synchronization acceptance;
- live Snowflake/Databricks provider-audit interoperability, permission,
  latency, retention, completeness, continuous-operation, recovery and scale
  acceptance for the new native typed transports;
- whole-suite history coverage beyond the six canonical stock readers,
  independently operated signed/anchored unified-audit verifiers, a real S3
  Object Lock account, accepted off-host signing-key custody and live custody-
  scheduler/recovery/scale/customer/SIEM acceptance;
- a maintained external TRC collector catalog and continuously operated broad
  control-evidence estate;
- licensed privacy-intelligence content, destination enrollment and operated
  alert delivery;
- named commercial third-party screening/cyber/breach feeds and accepted
  continuous-monitoring operations;
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
- Verify the final commit SHA and `origin/claude/aigov-gap-buildout` equality
  after publication; this document deliberately does not predict its own
  containing commit hash.
- All service claims below distinguish local/mock validation from live or
  external acceptance.
- No local test result should be presented as customer, regulator, insurer,
  certifier, provider or partner acceptance.

## Status taxonomy

- **Complete-local**: the bounded repository capability and its expected local
  path exist, are mounted and have a shipped, documented production composition
  whose readiness can pass without test-only dependency injection. Focused
  automated coverage exists; live acceptance may remain.
- **Partial**: useful control-plane or adapter code exists, but a material part
  of the original product capability, mount, configured readiness, integration,
  deployment or validation is absent.
- **External**: the requested outcome requires a third party, commercial
  relationship, certification or market action that repository code cannot
  complete.
- **Missing**: no coherent implementation exists. No whole G-number and no
  current OneTrust capability-matrix row is classified Missing in this working
  tree. Zero Missing does not mean parity; Partial and External rows remain.

Every claim below should preserve five separate evidence stages: **Core**,
**Mounted**, **Configured / ready**, **Live interoperable**, and **Operated /
accepted**. A local test double, mock transport or manual `app.state` injection
does not prove a later stage.

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

## 2026-08-31 continuation verification

The final changed-surface Python command selected 647 tests across Governance
Agents, AI Evidence Analysis, assessment evidence, document custody, incident
attachments, executive reports, DROP, Copilot and installer/config paths. Its
first run produced 645 passes and exposed two stale installer-test expectations
for the new fail-closed parser fields. Only those two contracts were corrected;
both then passed in a targeted rerun. Production code did not change after the
645 passing cases, so all 647 selected cases have current passing evidence
without repeating the full slice.

The associated browser/client and repository checks were also green:

- 66 focused Node client/JSON-contract tests passed.
- At that verification checkpoint, the 149-row matrix integrity gate passed at
  69 Implemented, 73 Partial, zero Missing and seven External / unverifiable;
  the later cross-suite context and no-code GRC continuation supersedes those
  counts below.
- Ruff passed each changed security surface and the matrix verifier.
- `git diff --check` passed; only expected Windows line-ending notices appeared.
- Browser smoke checks served the assessment-evidence, document-vault and
  executive-report pages with their shared liquid-glass shell and Copilot
  context, without horizontal overflow. Protected data calls failed closed
  without credentials, as expected.

This is focused local/synthetic verification, not a full repository suite or
live provider, customer, authority, regulator, recovery or scale acceptance.

## Current focused verification of the follow-on additions

The 2026-08-28 operational batch was exercised in bounded local estates:

| Surface | Current result | Acceptance boundary |
|---|---:|---|
| Five-suite governance platform runner | 140 passed, 0 skipped | Automated local fresh-tenant, cross-tenant, recovery, concurrency/scale and audit-export suites; tenant-bound at-rest field decryption is now exercised across tenants |
| External-gateway runtime pilot | 156 passed | Real local FastAPI routes/auth/approval/ledger; controlled connector and policy doubles; no external/customer gateway |
| Provider contract harness and dependent adapters | 131 passed | Readiness only on this host; Hugging Face, Entra, ServiceNow and OpenAI Costs all blocked because credentials are unavailable; the exact ServiceNow origin allowlist is configured; no provider I/O |
| Discovery sensor agents/estate | 16 passed | Local consenting synthetic estate only; no external deployment or completeness claim |
| Benchmark coordinator and related exchange/transport | 129 passed | Local synthetic contributors and review references only |
| C2PA provenance | 68 passed | Development interoperability and digest-pinned external-signer boundary; no production custody acceptance |
| Vendor, config and installer subset | 38 passed | Local/synthetic paths only |
| Identity governance control plane, API/UI and scheduler | 127 passed | Local doubles; no Entra/Okta/Purview tenant operation |

Additional concrete evidence:

- `c2patool` 0.27.16 is installed at the per-user application path. Real-binary
  development-fixture signing and verification passed against a pinned local
  development trust root. The test credential is intentionally untrusted for
  production, and production signing custody is not accepted.
- The vendor-portal pilot passed with two synthetic vendors/documents, exact
  submission retry, same-process import replay, revocation, tenant isolation and pending
  human review. Its consent gate is bound to the named synthetic fixture at
  `scripts/acceptance/fixtures/vendor-portal-synthetic-consent.json`, which
  explicitly records that no live vendor consent was obtained. No external
  vendor or customer participated.
- The benchmark coordinator operated with five synthetic contributors,
  restart/replay and trust-root rotation. It explicitly records that anonymity
  and differential privacy were not established and no privacy budget was
  consumed.
- The provider readiness report is blocked for all four requested contracts;
  `live_request_attempted` and `mutation_attempted` are false, and the configured
  ServiceNow origin matches the closed allowlist without claiming independent
  estate approval.
- A final changed-surface run passed 351 tests. Ruff passed all 40 changed or
  untracked Python files; compileall and `git diff --check` passed. Exact proof
  digests and boundaries are recorded in
  `AI_GOVERNANCE_OPERATIONAL_ACCEPTANCE_2026-08-28.md`.

Not performed in that 2026-08-28 acceptance batch:

- the entire repository test matrix (a later current-tree run is recorded below);
- live provider, external-gateway, customer, vendor or identity-tenant contract
  tests;
- production-scale, on-call or disaster-recovery exercises;
- legal, certification, underwriting, regulator or partner review.

## 2026-08-31 adversarial UI, Copilot and wiring audit

The final continuation pass treated each new surface as an untrusted operator
workflow instead of assuming that a rendered page was operational. It found and
repaired the following missed implementation defects:

- malformed, empty or non-JSON HTTP `2xx` responses could be presented as
  successful operations by multiple governance clients; the shared and
  page-specific clients now reject these responses without reflecting raw
  provider text;
- California DROP mutation routes did not consistently require literal verified
  readiness before resolving authority or opening a store; every operation now
  fails closed with a sanitized `503` on false, missing, malformed or failing
  readiness;
- the Governance Agents status path could imply more operational proof than it
  performed; it is now store-free and reports callable configuration separately
  from operation-by-operation verification, with every live-verification field
  false until independently established;
- Governance Agents, Evidence Analysis and DROP controls were not uniformly
  constrained by both caller role and current readiness; server-rendered
  permissions and client-side bounded status checks now keep unavailable
  controls disabled;
- the Copilot did not cover every new canonical route or qualify advice by the
  caller's authority; it now supplies page-specific context for 30 routes,
  includes role/readiness boundaries, excludes assistant history from model
  context and falls back to a deterministic local page guide when the model
  provider is unavailable;
- Evidence Analysis was outside the shared application shell, had incomplete
  CSP/theme integration and could blur the boundary between installed parser
  packages and the external Tesseract binary; it now uses the shared navigation
  and Copilot, a per-response nonce, honest dependency/readiness fields and
  explicit transient/unverified-output labels;
- the Assurance workspace eagerly requested all module summaries; it now loads
  summaries near the viewport or on focus and provides an explicit **Load all**
  action with completion feedback;
- the collapsed mobile navigation remained discoverable to assistive technology
  and did not reliably restore focus; it now uses `inert`, synchronized
  `aria-hidden`/expanded state, Escape-to-close and focus restoration;
- several legacy/custom surfaces and the Copilot had incomplete liquid-glass,
  high-contrast or reduced-transparency treatment. The shared glass primitives
  now cover the application shell and direct fixes cover Finance, Privacy,
  Start, Partner, Chat Goal, Learning and Run Tree. High-contrast and
  reduced-transparency modes use opaque, non-blurred surfaces, while destructive
  controls retain their red danger treatment.

### Live UI evidence

A fresh local Uvicorn estate was exercised through a real browser, then stopped
and removed. Governance Agents, Evidence Analysis and DROP rendered in the
shared shell with one current navigation item, no horizontal overflow and the
Copilot available. At that earlier browser-audit checkpoint their disabled state
was intentional and accurate because the production compositions had not yet
been installed. Later 2026-08-31 work added those compositions; this historical
browser observation must not be read as the current code state. The status
responses at the checkpoint reported no store opening, network I/O or
operational verification.

The page-aware Copilot exposed the correct prompts for Governance Agents,
Evidence Analysis, DROP and Assurance, included the caller's read-only/authority
boundary and successfully used the deterministic local guide during a provider
outage. Finance and Privacy resolved their live local data states; representative
glass cards computed a translucent background with blur/saturation. Assurance
started with lazy placeholders and completed all summaries only after the
explicit load-all action. At a 390 x 844 viewport, DROP had no horizontal
overflow; navigation open, Escape close, inert state and focus return worked;
the Copilot remained inside the viewport. High-contrast mode was verified live
as opaque/no-blur with legible borders and preserved danger styling. The
reduced-transparency branch is covered by CSS/source-contract tests rather than
live OS emulation in this pass.

### Current-tree verification

- 328 focused Python tests passed across the repaired governance, Copilot,
  readiness, authorization, UI and accessibility surfaces.
- All 93 dashboard JavaScript tests passed.
- Ruff, Vulture, Python compileall, JavaScript syntax checks and
  `git diff --check` passed; the latter emitted only expected Windows line-ending
  notices.
- At that checkpoint, the capability-matrix verifier passed at 149 outcomes:
  53 Implemented, 86 Partial, 3 Missing and 7 External / unverifiable. The later
  dated continuation rebaseline supersedes those counts; `parity_claimed`
  remains false in both snapshots.
- The complete default-environment repository run reached 23,388 passed,
  287 skipped and 3 expected xfails. Its sole failure was the explicitly live
  Anthropic self-learning benchmark: the provider connection failed and the
  circuit opened, with zero completed provider calls and $0.00 spent. It was not
  retried because doing so could invoke a paid external provider.
- A second full run with the Anthropic credential removed was stopped at 54%
  after the user challenged redundant testing. Its single early marker was not
  allowed to consume another full run; the mapped AI-governance-loop and
  identity-connector/control block passed directly (79 tests). This is recorded
  as a non-reproduced order/state marker, not as a second full-suite pass.

### Completion boundary after this audit

The requested local UI/UX, page wiring, liquid-glass system, authorization and
readiness behavior, and Copilot extension are implemented and locally
exercised. This still does **not** mean every OneTrust outcome or production
acceptance is complete. The production compositions for Governance Agents,
Evidence Analysis and DROP were added after this browser checkpoint. Remaining
work includes their source/provider dependencies, installation and attestation
of the shipped optional OCR and legacy-BIFF8 parser dependencies, live provider/
customer/vendor/identity and registered-broker estates, and independent
recovery, scale, accessibility, legal, regulator, certification and operational
acceptance. The current audit changes remain local and uncommitted/unpushed on
`claude/aigov-gap-buildout`; `main` was not touched.

## 2026-08-30 OneTrust full-platform increment

The new
[OneTrust full-platform public capability matrix](./ONETRUST_FULL_PLATFORM_CAPABILITY_MATRIX_2026-08-30.md)
maps 149 publicly advertised outcomes across shared platform capabilities, AI
Governance, Consent & Preferences, Data Use Governance, Privacy Automation,
Tech Risk & Compliance and Third-Party Management.

| Matrix status | Count |
|---|---:|
| Implemented | 69 |
| Partial | 73 |
| Missing | 0 |
| External / unverifiable | 7 |
| **Total** | **149** |

No row remains **Missing** after the 2026-08-31 continuation. AI-entity tasks,
custom consent data-element indexes/search, first-class TPRM
Contract/Engagement lifecycle, tenant-private governed document delivery,
reviewed evidence-assisted assessment proposals and deterministic executive PDF
reporting are now coherent mounted local products. A
**Partial** row may still contain substantial code while lacking a material part
of the advertised outcome, configured authority, live interoperability,
content, operations, recovery, scale or customer acceptance. The seven
**External / unverifiable** rows depend on licensed content, third parties,
commercial relationships, certifications or market evidence that repository
code cannot create. These are capability-level classifications, not proof that
every feature is complete.

A 2026-08-30 official-site refresh added six previously implicit named
outcomes: packaged shared-platform AI Agents; AWS Bedrock, Azure AI Foundry and
Google Vertex agent detection; the Databricks AI Security Framework; the
Privacy AI agent/Copilot/Knowledgebase suite; multimodal AI Evidence Analysis;
and the early-access Third-Party Risk Agent. The official
[2026-07-10 changelog](https://developer.onetrust.com/onetrust/changelog/onetrust-20260710-released)
adds Assessment Comments APIs, California DROP request/status APIs and the
`aiEvidenceAnalysisResult` evidence-task result. A 2026-08-31 refresh then added
the official February, March, June and August 2026 changelogs: AI-entity tasks;
consent bulk export/action jobs and custom indexes; assessment score and AI
metadata projections; CMP publish controls and Global Opt-Out; administration
document/event APIs; incident attachments; and TPRM Contracts/Engagements. The
sources mix public preview, early access, solution-packaging-only,
migration-gated, GA and unstated maturity, so this handoff preserves the exact
published qualifier rather than promoting a mention to a generally available
baseline.

### Current continuation addendum

The current tree adds the following local cores and mounted surfaces; their
configured/readiness stage is stated separately:

- `governance_agents.py` and the strict workbench/API package five closed agent
  kinds: breach response, privacy risk assessment, program Copilot,
  knowledgebase governance and third-party risk. Policies are immutable and
  default-off; context, adapters, dispatchers and execution credentials are
  server-resolved; findings/actions are closed and bounded; consequential
  execution requires human approval and verified receipts; quotas, replay,
  recovery, revocation, retention and integrity are explicit. The shipped app
  now installs a stock production composition with deterministic offline
  analysis, internal-intent dispatch and an encrypted rotatable token vault.
  The stock app also installs authoritative tenant-exact cross-suite context
  projections over privacy, assessment, governed-document/evidence and third-
  party histories with revision, digest, completeness and availability pins.
  The shared packaged-agent row is therefore Implemented locally; Privacy AI
  and Third-Party Risk Agent rows remain Partial for maintained/licensed content,
  commercial feeds and live external operation.
- `ai_evidence_analysis.py` and its workbench/API implement bounded PDF, JPEG,
  Excel and text analysis against immutable criteria with independently
  attested adapters, transient raw content, closed cited facts, deterministic
  summaries, transparent justification components, gaps/remediation,
  provenance, distinct human release and a durable result object corresponding
  to the newly public `aiEvidenceAnalysisResult` outcome. The shipped app now
  installs the enable/store/authority/retention composition. Authority history
  now has a tenant-configured hard admission budget with a bounded fail-closed
  revocation reserve; per-case artifact quota counts only current unexpired
  authority. Composed maintenance purges due analyses, replaces retention-due
  non-adapter authority chains with minimized digest-only replay tombstones,
  later folds expired tombstones into a rolling anchor, and compacts the active
  SQLite file. Status is tenant-media-specific and reports legacy XLS/OCR
  availability only when enabled configuration and operational readiness both
  succeed. Local adapter authority is excluded from compaction and remains an
  explicit rotation/revocation decision. The durable-result
  row is Implemented locally. The broader format-analysis row is operationally
  Partial: optional default-off OCR and legacy XLS workers require an exact-
  pinned POSIX Bubblewrap network namespace, pre-exec containment, bounded
  readiness probes and attested runtime/data manifests. This Windows host fails
  closed because Bubblewrap is unavailable, so no live network-denied sandbox
  execution was demonstrated; quality, privacy, calibration and customer
  acceptance also remain unverified.
- `ai_cloud_agent_governance.py` and its workbench/API normalize Bedrock, Azure
  AI Foundry and Vertex agent inventories, ingest closed observability,
  evaluate policy, create exact enforcement intents, verify adapter receipts
  and reconcile later inventory. It also provides a versioned, content-free
  Databricks framework review/attestation/activation control plane.
- Assessment sessions now create audited comments bound only to questions in
  the saved questionnaire and return deterministic bounded pages by section,
  question and time.
- `privacy_drop_management.py` and its strict API create independently bound
  `DataDeletion`/`OptOut` requests through the existing rights-intake callback
  and record evidence-bound DROP status updates without persisting raw subject
  data. The shipped app now installs its versioned secret-provider-backed
  principal key authority, durable four-eyes proposal queue, broker/record/outcome
  registry, store factory, strict authority API, liquid-glass/Copilot workbench,
  truthful operational probe and opt-in lifespan-managed scheduler. Previous key
  revisions remain usable for retained outcomes after rotation; dependency-bound
  retirement and unknown/revoked revisions fail closed. This is a locally
  operated control plane, while live California authority/registered-broker,
  legal, regulator and customer acceptance remain external and unverified.

This addendum does **not** claim literal OneTrust parity. At the 2026-08-30
checkpoint, final integrated repository verification against the then-current
checkout passed **23,145 tests**, with **292 skipped**, **3 expected xfails**
and **0 failures** in **43m45s**. That checkpoint run
removed live LLM-provider variables so the credential-gated real-provider
self-learning benchmark was skipped instead of spending or making external
inference calls. This is strong local repository evidence, not external
acceptance. Exact external residuals are: live
California DROP authority/registered-broker and legal/regulator acceptance;
credentialed Bedrock/Azure/Vertex operation, durable canonical production
storage and broader safe provider writes; licensed Databricks and knowledge
content; an encrypted execution-token vault with accepted HSM/KMS custody;
independently operated adapters/dispatchers; and live provider, customer,
adversarial, recovery, scale and certification acceptance.

### Local product additions

- **Governed consent experiments:** an opt-in tenant-bound authority now covers
  immutable control/holdout/template/language/layout variants, independently
  approved definitions, keyed sticky audience assignment, legal-signal and
  accessibility safety precedence, aggregate-only exposure/outcome analytics,
  bounded sequential analysis with an evidence floor, pause/resume/kill/
  rollback and verified audit export. The strict API and operator console are
  locally covered. The public API now derives experiment identity and subject
  from the existing property/profile authority, while the web SDK assigns
  before render, records exposure after safe DOM insertion and records outcomes
  only after a successful consent save. It has not seen live web/app traffic.
- **Governed volunteered profile data and progressive journeys:** an opt-in
  exact-tenant authority now provides independently approved closed-schema
  boolean/single/multi-select definitions; publication/policy-bound values,
  history, export, unset/revoke and retention; independently approved,
  purpose-signal-targeted progressive journeys with required-first prompts,
  closed conditions, cooldown/frequency caps, kill/rollback and aggregate
  interactions. The mounted strict admin API and `/consent/profile-data`
  operator workbench derive tenant/principal authority server-side; the public
  API derives exact
  tenant/profile/property/catalog/journey/capability authority server-side and
  the SDK renders safe controls, retains assignment before insertion and marks
  completion only after successful saves. It stores no raw capability or free-
  text PII and has not seen live traffic or customer/accessibility acceptance.
- **Verified consent identity federation and cross-domain/device pairing:** the
  default-off `[consent_preferences.identity]` path now uses a server-injected
  OIDC verifier or exact-JWKS-origin/`kid` key resolver, enforces issuer,
  single-audience, time and nonce contracts, and stores no raw token or subject.
  A one-time short-lived digest-only PKCE grant pins both client/profile CAS
  authorities, property, publication/configuration/policy and browser-origin or
  mobile/TV service-client binding, then links through the existing consent
  store so latest-negative receipt semantics remain authoritative. Replay,
  expiry, revocation, drift, cross-tenant access and exact partial-operation
  recovery are covered by immutable audit/integrity and retention paths. The
  focused changed-surface run passed 67 Python tests and six Node SDK tests;
  targeted Ruff, compileall and Vulture passed. No live IdP/JWKS fetch,
  compiled/deployed native host integration, production key custody, customer
  synchronization, recovery or scale acceptance is claimed.
- **Reviewed scan-to-disclosure proposals:** an independently gated exact-
  tenant authority now re-reads an enrolled property's exact immutable scan
  report, discoveries and independently reviewed classifications through a
  constructor-bound inventory adapter. It rejects unknown, stale, retired,
  revoked or drifting authority, and rejects incomplete/truncated reports by
  default unless an exact property/report-bound, current independently reviewed
  exception is installed and remains explicitly labeled. The inventory exposes
  exact forward-cursor discovery pages, which the adapter follows up to an
  explicit 10,000-item ceiling. It creates only digest-bound
  structured metadata proposals with distinct creator, approver and local
  acceptor, desired/no-change, reconcile, rollback, signed export and
  integrity. It generates no legal prose, changes no notice and publishes
  nothing to a site, app or provider; no live-estate completeness or legal/
  customer acceptance is claimed.
- **Durable approved discovery remediation:**
  `privacy_data_discovery_remediation.py` now converts only exact fresh
  independently approved discovery records into a digest-only tenant outbox.
  It double-reads public source/report/finding/review authority, leases bounded
  work to an injected idempotent callback, and records retry/dead-letter/replay,
  revocation/cancellation, ambiguous-result reconciliation, retention, history,
  audit and integrity evidence. The core remains network-free by default; the
  current stock provider/data-map composition and its narrower evidence boundary
  are described below. A new explicit `remediation.scheduler` gate drives only
  this built-in digest target: bounded active-tenant cycles use internally
  derived tenant/principal identities, reconcile exact local ambiguous outcomes
  before retry, retain minimized durable control health, recover interrupted
  cycles, and exclude overlapping process/cross-process cycles. It does not run
  discovery adapters or establish provider/completeness evidence.
- **Reviewed discovery to Data Use Governance:**
  `privacy_discovery_data_use_bridge.py` now re-reads exact discovery and target
  asset/context authority before a revision/digest-pinned, CAS/idempotent
  desired, no-change, reconcile or rollback write to only the
  `observed_classification` context. It retains immutable history and audit,
  detects payload/index tampering and never infers business-purpose, regulatory
  or consent authority. No provider/network write or live-estate operation is
  claimed.
- **Native provider audit evidence for Data Use Governance:**
  `data_use_provider_audit.py`, `data_use_provider_audit_authority.py` and the
  dashboard audit transport/runtime/composition now pin exact Snowflake/
  Databricks
  operations, bindings, compiled IR, expected controls, independently issued
  read authority and credential revisions. Provider writes stage a digest-only
  outbox before mutation and finalize/register from durable adapter evidence;
  post-write failure returns `recovery_pending`, while exact retry and the
  scheduler recover without repeating the native mutation. The implementation
  includes staged/finalized/registered/revoked authority, revision/integrity
  CAS, digest-only revocation reasons, fair persistent recovery cursors,
  audit-first retention, a credential bridge, strict operator APIs and app-
  lifespan scheduling. Snowflake mutations carry query tags and documented
  retry flags; Databricks mutations carry query tags; raw statement handles are
  reduced to digests. Evidence contracts distinguish Snowflake
  `QUERY_HISTORY`/`ACCESS_HISTORY`, Databricks `system.query.history` primary
  statement references and supplemental `system.access.audit` request
  references, rejecting mixed inputs. Built-in DNS-pinned, redirect-disabled,
  byte/time-bounded HTTP transports validate Snowflake result metadata and
  subsecond keyset pagination across `QUERY_HISTORY` plus `ACCESS_HISTORY`, and
  Databricks inline/chunked statement results across `system.query.history`
  plus supplemental `system.access.audit`. Source-retention and independently
  accepted delivery watermarks fail closed when completeness cannot be
  established. Matched, missing, extra, drift, ambiguity, no-change and
  incomplete states are retained without raw SQL, principals, origins,
  credentials, statement IDs or provider payloads. No live provider tenant,
  provider-specific latency/permission/retention outcome, continuously operated
  estate, scale or customer acceptance is claimed; Databricks query tags remain
  a preview dependency.
- **Unified consumer Trust Center:** a default-off bounded read model now joins
  the current active publication, consent history, volunteered profile data and
  explicitly authorized privacy-rights summaries. A separate persisted
  authority stores only digest-bound tenant/property/profile context,
  requester-plus-two-approver capability grants and per-case rights
  authorizations; it provides CAS revision, revocation, retention, integrity,
  signed metadata export and a zero-argument reauthenticating context resolver.
  A mounted authenticated authority API/workbench now covers context, approvals,
  revocation, history, retention, integrity, signed-export creation, persisted
  read and verification while deriving tenant/principal server-side. Exact-
  origin public routes and the web SDK expose only granted sections and a signed
  consumer export. A lazy production composition now binds that authority to
  the same public-consent registry, exact current publication/policy/profile
  pairing and authenticated operator RBAC. Its safe profile-selector listing
  and preparation route require the current client/profile revisions plus fresh
  redeemed identity evidence, persist only immutable digest/revision pins, and
  deliberately create no grant: `rights_cases` still needs a separate proposer
  and two distinct approvers. The subject-bound public SDK/API can then create
  and check signed privacy-rights requests through the existing orchestrator,
  with tenant and per-subject active-request quotas. Versioned localized theme
  tokens bind contrast-checked colors and plain copy into the immutable
  publication/rollback and render across the CMP and Trust Center. Production
  IdP/identity-assurance/signing-key custody, logo/media/custom-domain branding,
  native channels, provider fulfillment and live identity/accessibility/
  recovery/scale/customer acceptance remain outstanding.
- **Privacy regulatory intelligence and delivery:** an opt-in tenant-private
  authority now provides independently approved source rights, immutable and
  cited legal documents, purpose-bound grounded search/research with explicit
  abstention, jurisdiction comparison, ACL collections, revocable scoped
  shares, trackers, deterministic alerts, reports, retention and integrity
  verification. Its leased outbox and opaque-destination-to-SMTP worker record
  retry, dead-letter and acknowledgement-ambiguity evidence without exposing
  recipients through the API. Lightwork does not bundle DataGuidance content,
  legal advice or accepted live delivery operations.
- **Third-party intelligence and due diligence:** a provider-neutral authority
  now covers approved source enrollment and revision-pinned snapshots;
  sanctions, PEP, watchlist, adverse-media, cyber-posture, breach, compliance
  and performance screening; independent match review; approved risk policies;
  fail-closed guarded autoapproval; human override/rollback; EDD; monitoring;
  an alert outbox and opaque-destination SMTP worker; and consent- and purpose-
  constrained exchange contributions. The core, bounded delivery worker,
  strict dashboard API, operator page and main application mounts have focused
  local coverage. The delivery run fails closed without an opaque destination
  resolver, requires an explicit operator action, records acknowledgement
  ambiguity and never returns the resolved recipient.
- **Third-party lifecycle and conditional assessments:** an opt-in tenant-
  private authority now provides versioned independently approved conditional
  questionnaires across six risk domains; evidence-bound responses and
  explainable risk/mitigation rules; onboarding, active, suspended,
  offboarding and offboarded stages; opaque dependency inventories; legal-
  hold, task and access blockers; independent completion; durable callback
  intent with leases, retry, dead-letter recovery and revocation; retention,
  integrity and evidence exports; and a strict API/operator workbench. No
  callback worker, provider write or live vendor/customer operation is claimed.
- **Privacy-rights orchestration:** an opt-in provider-neutral authority, strict
  API and operator workbench now HMAC raw subject/request references before
  persistence, enroll opaque source/credential references, plan right-specific
  retrieve/delete/verify phases, require independent deletion and closure
  approvals, enforce legal-hold/retention gates, operate a recoverable callback
  outbox, record provider evidence, create bounded completeness manifests and
  encrypted-object response-package metadata, and provide retention/integrity
  evidence. The final stock source runtime now fulfills those intents through
  exact Microsoft Graph, Google Workspace, Salesforce and ServiceNow contracts
  with atomic pre-mutation reservation, ambiguity reconciliation, authority
  rechecks, encrypted exact-target custody and governed-vault retrieval. The
  separate rights-delivery composition adds independent identity/reauth seams
  and one-time response delivery. Neither path converts local source/runtime
  code into live provider, estate-completeness or customer acceptance.
- **OTT/CTV consumer-rights bridge:** `privacy_rights_ott_ctv.py` and its
  selector-free public API require a fresh exact redeemed OIDC/device pairing,
  service-client authority, current publication/policy and Trust Center
  `rights_cases` grant before creating an idempotent access or deletion case in
  the existing rights orchestrator. Effective authority expires at the earliest
  identity, pairing, Trust Center or service-client boundary; link, account,
  client and grant transitions fail closed; status is signed and digest-only;
  interrupted planning is recoverable; and controlled expiry/purge is explicit.
  Deletion still requires separate destructive approval and respects legal
  hold. This is a local control-plane bridge, not a native TV/mobile SDK,
  household/device synchronization product, built-in provider retrieval or
  deletion adapter, response delivery path, or live device/provider/customer
  acceptance result.
- **Managed customer sandbox workspaces:** `customer_sandbox_lifecycle.py`, its
  deployment-global admin API and native composition now provide a default-off
  production-source-to-isolated-workspace lifecycle over safe projections of
  all six OneTrust-comparison suites. Source revision/digest, module allowlist,
  expiry, quota, plan and immutable snapshot authority are pinned; independent
  create/destroy or terminal-failure-abandon approval, suspend/resume/resync,
  expiry, exact target-collision authority, retention tombstones and bounded
  retry/crash recovery are explicit. The final adversarial pass covered replay
  ordering and crash windows around owner/current/registry writes, collision
  cleanup/recreation, expiry status, baseline rollback and partial quarantine
  deletion. Disabled admission remains closed while exact existing-authority
  remediation is available to a global administrator without creating a new
  authority. This is an isolated local workspace, not a registered product
  tenant or execution/network sandbox; it copies no arbitrary configuration,
  secrets or tenant data. Integrity covers the SQLite lifecycle authority only,
  the audit chain is unkeyed, and hosted/live customer recovery, concurrency,
  scale, usability and SLA acceptance remain unproven.
- **One-time privacy-rights consumer delivery:** a separate three-gate,
  default-off authority revalidates closed-case identity, completeness,
  redaction, callback and package evidence; mints a raw consumer capability
  once while retaining only its digest; binds the consumer, reauthentication,
  case/package revision and digest, expiry and retry policy; and atomically
  redeems into an opaque one-time server grant. The authenticated operator
  API/page covers status, mint, digest-only lookup, revoke, signed export,
  retention purge and integrity without a capability list/readback, redeem,
  object-byte or credential route. The selector-free public API/SDK accepts
  reauthentication only through a dedicated header, claims the server grant
  immediately and returns bounded bytes only when an injected adapter matches
  the pinned package digest. Production identity, object storage/decryption,
  source adapters, post-claim recovery and live redaction/provider/customer
  acceptance remain outstanding.
- **Technology-compliance scoping:** an opt-in local authority, strict API and
  workbench now provide immutable independently published questionnaires;
  deterministic conditional traversal and evidence-digest responses;
  reason-coded framework/control applicability; explicit overlaps and
  crosswalks; first-line, second-line, control-owner and evidence-owner
  assignments; independently approved/finalized scope results; signed exports,
  retention and tenant-scoped integrity verification. It contains no licensed
  framework text, legal advice, external discovery, certification or live
  operating acceptance.
- **Compliance scope actions:** an independently gated local authority, strict
  API and mounted `/compliance/scope-actions` workbench now translate an exact
  finalized scope through an independently approved opaque mapping catalog
  into reason-coded control and evidence-task plans. Scope, assessment and
  catalog revisions/digests are pinned; shared evidence is deduplicated; plan
  approval, no-change, reconcile, rollback, history, signed export and
  deterministic ambiguous-callback recovery are covered. Apply requires a
  server-injected idempotent local callback, is not cross-store atomic, and no
  licensed framework content, provider write or live acceptance is bundled.
- **TRC continuous evidence:** independently approved collector definitions,
  exact asset/credential/control/capability enrollments, leased jobs, bounded
  cursor/completeness state, digest-only observations, change alerts, retry,
  recovery, dead-letter/replay, cancellation, retention and full integrity now
  sit behind a strict mounted API. A separately gated active-tenant scheduler
  is composed into dashboard lifespan and isolates invalid tenants and job
  failures. Config-driven production composition now installs the local SOC 2
  observer plus normalized read-only AWS Security Hub, AWS Config, Azure
  Defender, Azure Policy, Google Cloud Security Command Center, GitHub security
  stream and Okta posture collectors. Closed source manifests pin exact
  definition version/integrity, enrollment, origin/scope, capability/key
  revision, credential reference and control bindings; the bounded DNS-pinned,
  redirect-free transport resolves credential values only during a job. Config
  lint, the installer, status/API, the liquid-glass
  `/compliance/continuous-evidence` operator workbench and Copilot expose the
  same provider set. Readiness performs no provider I/O or secret-value
  resolution, and runtime jobs still recheck approved definitions and active
  enrollments. No compliance conclusion, certification, live-provider
  interoperability, broad estate completeness, continuous operation,
  outage/recovery, concurrency, scale or customer/auditor acceptance is
  claimed.
- **Privacy-notice governance:** an opt-in exact-tenant authority, strict API
  and operator workbench now provide role/quorum/TTL approval policies,
  independently reviewed digest-bound releases, approval revocation,
  scheduling, leased retry/dead-letter publication intent, rollback and
  withdrawal, cited regulatory-update proposals, effective-version resolution,
  signed exports, retention and integrity verification. A constructor-bound
  local bridge now maps exact publish, rollback and withdrawal intents into the
  consent-publishing compare-and-swap lifecycle, including immutable fail-
  closed withdrawal tombstones and exact lost-response recovery. The two stores
  are not atomically committed. This content-free path accepts no raw notice
  content or signing-key value and performs no transport, consent-provider
  write, SDK deployment or site/app/native mutation.
- **Shared six-suite governance inventory:** `shared_governance_inventory.py`
  plus its stock composition, strict API and liquid-glass/Copilot workbench now
  provide one tenant-private normalized read model over fixed AI Governance,
  Consent & Preferences, Data Use Governance, Privacy Automation, Tech Risk &
  Compliance and Third-Party Management source contracts. Lazy canonical
  readers preserve exact source revision/head, completeness, retention and
  available/partial/unavailable/drifted state. The inventory stores HMAC
  references and minimized relationship provenance rather than raw source
  actors, identifiers or names, and covers CAS/idempotent refresh, bounded
  query/export, integrity, retention and recovery. It is not a second system of
  record, each stock reader is limited to one canonical history per suite, and
  live scheduler, whole-suite completeness, recovery/scale and customer
  operation remain unproven.
- **Native consent scanner, protocol and operator composition:**
  `consent_application_scanner.py` inventories enrolled Android/FireOS, iOS/
  tvOS, Roku, Tizen and webOS application manifests, permissions, detected
  dependencies/SDKs and Apple privacy manifests through a reviewed lifecycle.
  `consent_native_sdk.py`, the stock backend/composition and strict public/admin
  surfaces bind an application to an existing public-consent client, admit an
  installation Ed25519 key through fresh injected platform attestation, and
  execute eight closed configuration/profile/preference/signal/link/rights/
  revoke operations under exact application, device, public-client,
  publication, policy and profile authority. Monotonic counters, signed
  receipts, downstream idempotency, durable offline replay/renewal, key
  rotation/revocation and fresh application-authority refresh are explicit.
  Swift, Kotlin, JavaScript and BrightScript reference adapters cover iOS/tvOS,
  Android/Fire TV, Tizen/webOS and Roku source contracts. They were not compiled,
  packaged, store-reviewed or operated on physical devices; production
  attestation, protected key/profile/outbox/transport custody, certified TCF/
  GPP handling, downstream AdTech, accessibility, recovery/scale and customer
  acceptance remain outstanding. Application registration is create-only;
  authority changes use a new globally unique application reference, while
  device refresh requires fresh attestation and exact revision CAS. The stock
  limiter is process-local and needs fleet-wide upstream enforcement. `CP-16`
  through `CP-19` therefore remain material-code Partial.
- **Stock privacy discovery and approved data-map delivery:**
  `privacy_data_discovery_adapters.py` supplies bounded readers for exact
  enrolled AWS S3, Azure Blob and Google Cloud Storage text objects, Snowflake
  column metadata, ServiceNow CMDB and Microsoft Entra assets. The mounted
  tenant-exact composition pins provider/config authority, connects fresh
  independently approved findings to the durable remediation outbox and a
  built-in tenant-private digest-only data-map target, and exposes admin-only
  work-list, worker, reconcile and maintenance operations with fail-closed
  side-effect attestations. The optional managed scheduler is now lifespan-owned,
  config/wizard-reachable, bounded by tenant/worker/reconciliation/lease limits,
  interval/jitter/retry controlled, and visible in the API, workbench and Copilot
  through tenant-free recovery/concurrency status. Only enrolled resources and supported text media are
  covered; permissions and non-transactional pagination limit completeness,
  Snowflake data values and broad PDF/OCR/archive/proprietary formats are not
  scanned, no purpose/transfer/subject/recipient/vendor semantics are inferred,
  the target is not a complete Article 30 RoPA, and no credentials, live estate,
  continuously running deployment, provider interoperability, recovery/scale or
  customer acceptance was operated. Local scheduler recovery/concurrency tests
  do not establish multi-host production acceptance.
- **Unified cross-suite audit projection:** `unified_audit.py` plus the
  dashboard source adapters, composition, strict API and bounded lifespan
  scheduler now provide a default-off, signed-only projection over the fixed
  AI Governance, Consent & Preferences, Data Use Governance, Privacy
  Automation, Tech Risk & Compliance and Third-Party Management sources.
  A reusable core capture contract and stock dashboard composition now install
  six lazy tenant-bound readers plus separately instantiated local re-verifiers
  over the AI-governance loop, consent outbox, data-use mutation ledger, signed
  privacy audit, TRC continuous-evidence audit and third-party lifecycle audit.
  Deployments can still replace both maps with independently operated source
  authorities. Closed normalized records use stable projection IDs;
  every page, including an empty terminal page, requires a signed receipt from
  the canonical tenant-bound strict sink before its cursor advances. Exact
  source-authority, schema, verifier-identity, trust-root and source-key
  rotation pins, generation sweeps, CAS lease renewal/reclaim, bounded retry,
  replay, revocation, immutable history, local integrity linkage and
  read/admin APIs are covered locally. The scheduler consults only active
  tenant-registry records and makes one fixed-six run per enabled tenant per
  cycle. Stock histories cover one canonical authority per suite rather than
  every suite module, and explicitly retain `source_history_incomplete` and
  `source_retention_unverified`; no independently operated signed/anchored
  verifier deployment ships here. SQLite linkage is not external WORM or an
  accepted cross-day/off-host anchor; off-host signing-key custody and live
  scheduler crash/recovery, concurrency, scale, full provider/source
  completeness, customer operation and SIEM acceptance remain unproven.
- **Configuration and installation:** config lint, migration recognition and
  installer/wizard prompts now cover
  `[consent_preferences.experiments]`,
  `[consent_preferences.profile_data]`,
  `[consent_preferences.trust_center]`,
  `[consent_preferences.scanner.notice_proposals]`,
  `[privacy_automation.regulatory_intelligence]`,
  `[privacy_automation.data_discovery.adapters]`,
  `[privacy_automation.data_discovery.remediation]`,
  `[privacy_automation.data_discovery.remediation.data_map]`,
  `[third_party_management.intelligence]`,
  `[third_party_management.lifecycle]`,
  `[privacy_automation.rights_orchestration]`,
  `[privacy_automation.rights_consumer_delivery]`,
  `[privacy_automation.notice_governance]`,
  `[tech_risk_compliance.scoping]` and
  `[tech_risk_compliance.scope_actions]`,
  `[tech_risk_compliance.continuous_evidence]` and its closed
  `[tech_risk_compliance.continuous_evidence.stock_collectors]` source
  manifests,
  `[data_use_governance.provider_audit]`,
  `[third_party_management.intelligence.providers]`,
  `[customer_sandbox_lifecycle]`, `[audit.unified]`,
  `[consent_preferences.native_sdk]` and `[governance_inventory]`. Enabling a local
  control plane does not
  provision content licenses, provider credentials, destinations or live
  operating approval.

### Focused local validation for this increment

| Surface | Result | Boundary |
|---|---:|---|
| Entire repository, clean-provider current-tree matrix | 23,145 passed, 292 skipped, 3 expected xfails, 0 failed in 43m45s | `maverick.__file__` resolved to this checkout. Live LLM-provider variables were removed, so the credential-gated real-provider self-learning benchmark skipped; this is local repository coverage, not provider/customer/legal/scale acceptance. |
| Consent-experiment core, API and page | 30 passed | Local deterministic fixtures; no live traffic, distributed runtime or legal/privacy acceptance |
| Privacy-intelligence core, delivery, API and page | 32 passed | Local fixtures and injected transport behavior; no licensed corpus or live SMTP operation |
| Third-party-intelligence core | 21 passed | Provider-neutral local fixtures; no commercial feed or provider acceptance |
| Third-party alert-delivery worker | 11 passed | Local transport doubles; no destination enrollment or live SMTP operation |
| Third-party dashboard/API and mount | 13 passed | Strict route/page contracts, explicit delivery-run boundary and main OpenAPI/visibility mount; no commercial feed or live SMTP operation |
| Config lint, migration and installer/wizard subset | 20 passed | Local configuration generation/recognition only |
| Named consent-delivery destinations, shared dispatch composition, operator API/UI, config/installer and Copilot | 300 selected Python checks demonstrated passing across the focused run and corrected single-test rerun; 2 consent-delivery Node response-contract checks passed | Closed Salesforce, Marketo, Adobe Experience Platform and Snowflake request translations; exact tenant/destination/public-origin/DNS/signing-key/credential-revision/schema authority; deployment-held credential references; no redirects; same-store bridge/dispatch; idempotency/no-change/retry/ambiguity/revocation; minimized receipts; fail-closed readiness; mounted API; liquid-glass UI and Copilot guidance were exercised locally. No credential value or provider session was opened by readiness. No fake reconciliation authority was installed, so production readiness remains false until a deployment supplies an independently operated provider-read authority. No live provider tenant, OAuth scope, schema provisioning, interoperability, outage/recovery, continuous operation, scale or customer acceptance was tested. One separate pre-existing/concurrent `privacy_data_discovery.html` source-level JSON-contract selector remained red because that page moved its client into a static file without updating the template-only test; it is not consent-delivery evidence. |
| Named third-party provider contracts, production composition, durable feed runtime, scheduler, dashboard readiness, config, installer and Copilot | 38 focused provider/runtime tests plus 255 focused integration checks passed in the current tranche | Closed normalized reference contracts for the RiskRecon, SecurityScorecard, HackNotice, SupplyWisdom, ISS Corporate Solutions and approved-generic concepts; exact enrolled hosts; bundled DNS-pinned redirect-free GET transport; deployment-held credential references; tenant/source/monitor/key/revision pins; schema/byte/page/time/completeness bounds; leases, retry/no-change/ambiguity/dead-letter/replay/revocation; minimized receipts; app lifespan wiring; status/admin credential readiness; liquid-glass UI and closed Copilot guidance were exercised locally. Provider names describe normalized reference-contract shapes only. No licensed sandbox or live feed was contacted; no vendor-native interoperability, commercial corpus, provider write, automatic risk conclusion, continuously operated external deployment, multi-node recovery/scale drill or provider/customer acceptance is claimed. |
| Native Data Use provider audit plus Snowflake/Databricks adapter regressions | 230 passed in the current broader focused rerun | Exact operation/binding/IR/control/read/key pins, built-in bounded native transports, normalized audit events, pagination/chunk/metadata/watermark checks, explicit reconciliation outcomes, leases/recovery/retry/dead-letter/replay/revocation/retention/integrity and no-raw persistence; no live provider/customer acceptance, production completeness certification or scale proof |
| OTT/CTV rights bridge plus identity/orchestration/config/app regressions | 182 passed | Fresh redeemed pairing and exact service-client/Trust-Center authority, access/deletion case creation, approval/legal-hold preservation, revocation, recovery, expiry/purge and digest-only status with local fixtures; no compiled/deployed native host package, provider retrieval/deletion, delivered response or live estate/customer acceptance |
| Managed customer sandbox core, API, composition, config and wizard | 137 passed, including 83 core lifecycle tests | Six closed safe projections, immutable snapshot/source authority, four-eyes lifecycle, disabled remediation, exact idempotency/replay, collision and quota enforcement, retention, and injected crash windows across provisioning/resync/expiry/destroy/abandon passed locally. This is not a registered tenant or execution/network sandbox; workspace integrity, keyed audit, hosted/live operation, production recovery/concurrency/scale/usability/SLA and customer acceptance remain unverified. Two Windows link/reparse tests skip when the host cannot create the required link type. |
| Unified cross-suite audit core, strict sink, adversarial cases, six-source adapters, API, composition, scheduler, config and wizard | 204 passed in the integrated focused slice; 24 focused reader/capture/composition tests passed after final hardening | Local/synthetic fixtures cover the fixed-six projection core plus shipped lazy tenant-exact canonical readers, a reusable digest-only capture contract, separately instantiated local re-verifiers, signed privacy-chain verification, source drift/tamper refusal and stock/injected composition boundaries. The final tenant-exact custody composition added later orchestrates checkpoint/anchor/WORM/SIEM primitives after projection and through the scheduler. The stock readers still cover only one canonical history per suite. No independently operated verifier service, whole-suite completeness, real Object Lock/SIEM estate, accepted off-host key custody, crash/recovery/concurrency/scale exercise, provider completeness or customer operation was performed. |
| Shared governance inventory core, stock composition/API and installer/config | 8 focused core/composition/API tests plus 4 installer/config tests passed | Tenant-private fixed-six normalized read model, explicit completeness/availability, minimized HMAC provenance, CAS/idempotency, bounded query/export, retention/recovery and integrity were exercised locally. Do not add these overlapping slices into a repository-wide unique-test total; no live source completeness, scheduler/recovery/scale or customer acceptance was performed. |
| Native consent protocol/security and reference-adapter contracts | 13 focused Python tests plus 5 Node tests passed | Canonical Ed25519 envelope/receipt handling, exact authority, counters/replay/idempotency, offline renewal, rotation/revocation, fresh-attestation refresh and bounded SDK schema behavior passed locally. The Swift/Kotlin/JavaScript/BrightScript sources were not compiled, packaged or operated on physical devices. |
| Native consent production composition, admin/operator UI, app/config/Copilot integration | 274 Python tests plus 2 Node admin-client tests passed; targeted Ruff clean | Separate focused slice over composition, admin API/page, app mount, wizard consent config, Copilot context and UI visibility. Local/synthetic only: no external platform verifier, store/device, HSM/KMS, multi-replica rate limit, customer/regulator/conformance or recovery/scale acceptance; missing required callbacks fail readiness. |
| Stock privacy-discovery adapters, digest-only target, composition and operational API | 31 distinct focused tests passed: 14 adapter, 6 target, 9 composition and 2 API; Ruff clean across 9 touched files | Six exact-source readers, pinned bounded transport, lazy tenant composition, approved-review outbox/target, admin work list, worker/reconcile/maintenance and fail-closed side-effect attestation were exercised with local doubles. The managed scheduler was added and validated in the separate row immediately below. No credentials/live estate, provider writes, broad formats/database values, complete RoPA semantics, live recovery/concurrency/scale or customer acceptance. |
| Managed privacy-discovery remediation scheduler, lifecycle, API/UI/Copilot and config/installer | The one 306-check focused slice reported 305 passed and one failed assertion; the production parser correctly rejected that invalid parent-gate combination, the assertion was corrected, and its exact selector passed. One subsequently added app-owned start/stop selector also passed, so 307 distinct focused checks were demonstrated individually without rerunning the full slice. Targeted Ruff initially exposed four integration issues; the exact affected files passed after correction, the subsequently changed scheduler test also passed Ruff, final compile checks passed, and targeted diff-check was clean apart from line-ending warnings. | Local doubles exercised explicit global plus tenant opt-in, bounded active-tenant/worker/reconciliation/lease limits, tenant/principal-derived identities, exact built-in digest-target reconciliation before retry, durable interrupted-cycle recovery, interval/jitter/backoff, process and strict cross-process exclusion, app-owned start/stop, tenant-gated automatic-retry truth, operator status and Copilot/config/wizard reachability. No discovery provider, credential value, provider write or raw finding was used. No continuously operated deployment, live provider interoperability, estate completeness, multi-host outage/recovery/scale or customer acceptance is claimed. |
| TRC continuous-evidence core, API, scheduler and dashboard attachment | 29 passed | Local digest-only collector, exact authority, tenant allowlist enforcement, incomplete/truncated evidence, recovery, retention/integrity, tenant/job failure isolation and bounded lifespan operation; external collector/credential injection seams exist but no external runtime is installed, and no compliance conclusion, broad live estate or customer/auditor acceptance is claimed |
| Seven stock TRC collectors, production composition, scheduler/app/API, operator UI, config/installer and Copilot | A 312-check focused local slice initially reported 310 passed and 2 failed. One assertion incorrectly depended on provider tuple order; the other exposed an unrelated shared consent-page token typo. Both were corrected, and the exact two failed selectors then passed (2 passed). The entire 312-check slice was intentionally not rerun, so this row does not claim a fresh aggregate green run. | Local doubles and closed manifests exercised AWS Security Hub, AWS Config, Azure Defender, Azure Policy, Google Cloud Security Command Center, GitHub security streams and Okta posture composition, tenant/source/definition/enrollment/key/capability/scope/credential/control pins, bounded transport, lazy registry, scheduler injection, status/run-once API, liquid-glass operator page, Copilot and config/wizard contracts. No credential value or provider session was opened by readiness. No live provider tenant, provider-native interoperability, licensed corpus, estate completeness, continuous operation, outage/recovery, concurrency, scale or customer/auditor acceptance was tested. |
| AI Evidence Analysis authority lifecycle, tenant media status, API, config and installer | 59 focused tests passed in one run; targeted Ruff passed across the eight touched Python/test files | Local SQLite fixtures exercised live-only artifact case capacity, bounded authority-history admission with terminal revocation reserve, expiry plus full retention before compaction, minimized digest-only replay tombstones, second-interval tombstone folding into a rolling anchor, active-file VACUUM/WAL truncation, composed core/authority maintenance, tenant-enabled media/byte-limit status, readiness-bound legacy XLS availability and closed config/wizard output. This is not external WORM/off-host anchor custody, backup/crash/concurrency/scale acceptance, live parser operation, calibrated extraction/analysis quality, OneTrust wire compatibility or customer/provider acceptance. |
| Parity-runtime installer and config gates | 22 passed | Explicit default-off provider-audit, continuous-evidence/scheduler and named third-party-provider/scheduler settings; configuration only, with no credential, provider license or live deployment provisioned |
| Public consent experiment core/runtime | 43 passed | Default tenant-local web runtime and closed DOM contract; no live traffic, accessibility or scale acceptance |
| Third-party lifecycle core, API and page | 23 passed | Local provider-neutral control plane; callback intent only, no provider writes |
| Privacy-rights core, API, page, config and wizard | 31 passed | Local source-orchestration intent; no provider operation, identity verification, delivery or completeness claim |
| Compliance scoping core, API, page, config and wizard | 33 passed | Local deterministic questionnaire/scope/overlap authority; no licensed content, legal advice or certification |
| Privacy-notice governance core, API and page | 19 passed | Content-free local publication intent; no consent-provider/site/app write or live acceptance |
| Lifecycle/rights/notice lease-token regression | 36 passed | Overlapping local core suites; forced URL-safe leading-symbol edge cases, no external delivery |
| Consent profile-data core | 14 passed | Closed-schema local authority; no free text, protected-trait inference or live acceptance |
| Consumer Trust Center core | 40 passed | Publication-pinned safe color/copy branding, strict alternate-resolver validation and signed export; no production identity, brand assets/custom domains or live acceptance |
| Persisted Trust Center authority core, API and page | 38 passed | Digest-only local context/grant/case authority plus mounted authenticated workbench; no production resolver/signing custody or live identity/customer operation |
| Privacy-rights consumer-delivery core | 18 passed | One-time local capability/grant lifecycle; no built-in object/source provider or live operation |
| Privacy-rights delivery operator API/page | 18 focused passed | Server-derived tenant/principal and no capability readback/redeem/object route; no external provider acceptance |
| Public consent/profile/Trust Center/rights-delivery API | 37 passed | Server-bound local web routes; injected production authorities/adapters and live traffic remain absent |
| Progressive-profile, Trust Center and rights-download SDK runtime | 5 Node tests passed | Safe closed-control rendering, WebCrypto digest-chain verification and bounded binary contract; no live browser/accessibility/customer acceptance |
| Earlier focused reconciliation across profile, notice, rights, lifecycle, scoping, config, installer and UI | 185 passed | Pre-later-increment overlapping local suites; not the full repository matrix and not live/external acceptance |
| Notice publishing/withdrawal bridge, compliance scope actions and profile admin surfaces | 111 passed | Local stores, callbacks and dashboard contracts; no provider/site/app/native operation or live acceptance |
| Scope-actions/config/shared-app integration | 59 passed | Includes fail-closed scope-to-scoping dependency and mounted route visibility; no production apply callback |
| Reviewed scan-to-disclosure proposal and inventory adapter | 44 passed | Public inventory APIs and deterministic local fixtures; no legal-copy generation, publication or live-estate completeness claim |
| Progressive-profile SDK continuation rerun | 2 Node tests passed | Safe closed-control renderer; no live browser/accessibility/customer acceptance |
| Governed consent-publishing/Trust Center branding core, API and page | 78 passed | Exact theme-version pins, rollback, strict copy/contrast/digest validation and legacy-unbranded compatibility; no live accessibility/customer acceptance |
| Privacy discovery plus durable remediation outbox | 53 passed | Earlier local digest-only lease/retry/recovery/reconciliation slice with an injected callback; the later stock provider/target composition is covered separately above, and neither run is live-estate evidence |
| Privacy discovery-to-Data-Use bridge plus both source authorities | 49 passed | Exact reviewed observed-classification CAS/reconcile/rollback only; no inferred business/regulatory/consent authority or live estate |
| Current combined focused reconciliation across consent publishing, Trust Center core/authority/public/operator paths, discovery/remediation, Data Use bridge and shared visibility | 248 passed | One 2026-08-30 current-tree invocation; focused local coverage only, not the full repository suite or external acceptance |
| Trust Center/DSR preparation composition, safe public-profile discovery and third-party collaboration regression | 241 passed plus 12 Node tests | One 2026-08-30 current-tree invocation covering exact credential/profile/revision binding, registry migration and tamper detection, server-derived rights-intake context preparation, production composition seams, distinct approvals and the external collaboration portal. Local/injected fixtures only; no live IdP, browser, vendor, provider, estate, recovery, concurrency, scale or customer acceptance. |
| Current consolidated consent-identity, Data Use provider-audit, TRC continuous-evidence, third-party provider-runtime, config/installer and app regression | 284 passed plus 6 Node SDK tests | One 2026-08-30 current-tree invocation across the newly reconciled runtime surfaces. Local/injected fixtures only; it is neither a full-repository pass nor live provider, customer, estate, privacy, scale, auditor or certification acceptance. |

The original consent experiment, privacy-intelligence, third-party-intelligence
and configuration set (the first six rows) passed together as **127 tests**.
The later lifecycle, public-runtime, privacy-rights, compliance-scoping,
privacy-notice and profile-data rows were rerun as separately focused sets and
overlap some core files, so their counts must not be arithmetically summed into
a unique-test total. These are focused increment results and should not be
presented as an additional sum on top of the separate 23,145-pass repository
matrix recorded above.
Before the later bridge, withdrawal, scope-actions, profile-admin and inventory-
adapter increments, the broader affected consent, privacy, Data Use Governance,
policy-publication and third-party test families passed **732 tests with two
skips** in one combined run. The continuation rows above were run on
2026-08-30; the **248-passed** row is the consent/Trust Center/discovery/Data
Use combined focused invocation. Other rows overlap and are evidence sets, not
a sum or a replacement for the later full-suite result.

For the earlier final current-tree gate, `python -m ruff check .`,
`python -m vulture`, package `compileall`, JavaScript syntax checking, the then
current 138-row OneTrust matrix verifier and `git diff --check` all passed. The first
repository-wide attempt exposed dated test evidence, Windows command quoting,
UTC/local-day bucketing and host-provider isolation defects; those were repaired
without weakening production freshness, sandbox, tenancy or audit controls.
The complete clean-provider matrix was then rerun from zero and passed.

For the 2026-08-31 documentation correction, the expanded 149-row verifier
passed separately, including exact status locks for the corrected and newly
tracked rows plus the five-stage legend and official-source gates. This
docs/gate validation did not rerun the 23,145-test repository matrix and must
not be presented as a new full-suite result.

For the current continuation, Ruff passed across 21 targeted Python files;
`py_compile` passed eight runtime/integration modules; Vulture at 80% confidence
passed the three newly added/integrated feature modules; Node syntax plus five
SDK tests passed; and `git diff --check` passed with expected Windows line-ending
notices. Ruff format checks passed across the 18 feature-specific files. The
three large shared integration modules (`api.py`, `app.py` and
`ui_visibility.py`) remain outside whole-file Ruff format conformance; they were
not mechanically reformatted because that would create broad unrelated churn.

For the final Trust Center/DSR and third-party-collaboration increment, Ruff
lint passed across the 17 selected runtime and test files; `compileall` passed
the nine selected runtime modules; Vulture at 100% confidence reported no dead
code in the eight feature modules; and the focused 241 Python plus 12 Node tests
passed. A whole-file Ruff format check still flags five selected files:
`consent_public_api.py`, `consent_public_admin_api.py`,
`third_party_collaboration_api.py`, `app.py` and `test_consent_public_api.py`.
They were not mechanically reformatted in this dirty handoff tree because doing
so would mix broad unrelated formatting churn with the functional increment.

### Live, licensed and external residuals

1. Extend the approved web consent assignment to native app/TV runtimes, then
   perform privacy/legal, causal-validity, accessibility, recovery, concurrency
   and production-scale acceptance with consenting live traffic.
2. License and enroll current authoritative privacy content, validate legal
   provenance and jurisdiction coverage with counsel, enroll destinations, and
   operate alert delivery through outage, retry, acknowledgement ambiguity,
   dead-letter recovery and multi-node failover.
3. Build and accept named commercial sanctions/PEP/watchlist/adverse-media,
   cyber-rating, breach and compliance feed adapters with real credentials and
   permitted test data.
4. Operate third-party screening, EDD, guarded autoapproval, continuous
   monitoring, alert delivery, revocation, exchange withdrawal, recovery and
   portfolio-scale workflows with consenting vendors and risk/compliance
   owners.
5. Complete the wider residuals already listed in this handoff: external
   discovery estates, identity/GRC/billing/gateway providers, production C2PA
   custody, independently reviewed benchmark privacy properties, external
   vendor participation, customer-scale usability/recovery, certifications and
   commercial ecosystem acceptance.

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
- Consent-gated deployable JSONL network-egress, browser-extension and OAuth
  sensor processes with fixed-origin transport, hot-reloaded Ed25519 keys,
  private normalized SQLite spools, bounded retry/recovery and explicit
  coverage limitations. A local consenting estate exercises key lifecycle,
  privacy and 1,000-observation concurrency without claiming completeness.
- Read-only Entra, Okta and Purview discovery adapters through a pinned-host,
  redirect-disabled runtime transport with secret references and pseudonymous
  normalized snapshots.
- An opt-in identity-governance scheduler with provider leases, retry/backoff,
  digest-only snapshot history, bounded hash deltas and safe change callbacks.
  The stock app starts the process-default scheduler without a change callback,
  so normalized changes do not reach dynamic risk/alerts through that path.
- A default-off identity-governance control plane for digest-only risk/alert
  callback fanout, pseudonymous agent-principal federation through an
  independently injected enrolled subject-binding resolver, identity-to-
  capability mapping, fresh conditional-access decisions and CAS/idempotent
  Purview writeback/compensation. The stock app does not install the control
  runtime, so its endpoints fail closed rather than becoming configured/ready.
  These paths are locally tested, not accepted against live identity tenants.
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
- A restart-safe runtime-policy pilot client and local FastAPI gateway estate
  covering approval, no-change, retry, response-loss recovery, after-effect
  ambiguity, current-policy re-evaluation, rollback and compensation. Typed
  canonical state digests and approval-crash reconciliation prevent false
  no-change results and orphaned approval replays.
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
- A consent-gated local vendor-portal pilot with representative synthetic
  documents, strict document contracts/drift handling, exact retry/replay,
  invitation revocation, cross-tenant denial and digest-only evidence.
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
- A restartable local benchmark coordinator and reviewed trust-root-rotation
  ceremony with five synthetic contributors. Admission requires explicit
  consent/inclusion/privacy-review evidence; the report does not infer
  anonymity or differential privacy from transport. Durable state is separately
  byte-bounded and mutations roll back atomically on capacity/save failure.
- Media-provenance control plane with typed C2PA interfaces and a concrete,
  optional official-`c2patool` verifier/signer implementation, transient media
  processing, digest-only receipts, exact asset/deployment/context bindings and
  four-eyes release. Configuration can select the verifier; signer custody is
  still an injected deployment responsibility. `c2patool` 0.27.16 is installed
  and development-fixture signer/verifier interoperability passed; an external
  signer boundary with mandatory executable-digest pinning and a production
  HSM/KMS custody design are documented.
- A closed, consent-gated live provider contract harness for Hugging Face
  inventory, Entra discovery, one ServiceNow sandbox write/replay and OpenAI
  Costs. ServiceNow mutation requires an exact match against a closed origin
  allowlist; hostname markers and the environment label cannot grant authority.
  Readiness-only mode performs no I/O and fails closed on missing credentials or
  origin authorization.

### Product surface and configuration

- Strict request schemas and tenant/admin authorization for the new API paths.
- One merged governance operations workbench covering the new workflows.
- Feature configuration recognition, migration and installer/wizard support.
- Focused core, API, authorization, scheduler/runtime, UI and configuration
  tests.
- Updated feature catalog, the earlier focused comparison in
  [ONETRUST_CAPABILITY_GAP_ANALYSIS_2026-08-28.md](./ONETRUST_CAPABILITY_GAP_ANALYSIS_2026-08-28.md),
  and the broader 149-outcome
  [OneTrust full-platform public capability matrix](./ONETRUST_FULL_PLATFORM_CAPABILITY_MATRIX_2026-08-30.md).

## G1-G27 status matrix

| Gap | Status | Implemented on this branch | Still not done or not proven |
|---|---|---|---|
| G1 AI estate discovery | **Partial; deployable local sensor pilot passed** | Inventory service, ten registry adapters, scheduler, six-class control plane, Ed25519 ingress, deployable consent-gated network/browser/OAuth agents and local key/privacy/recovery/1,000-event estate | External-estate deployment; SaaS/identity/catalog agents; enterprise completeness and production privacy/scale proof |
| G2 native connectors | **Complete-local; live acceptance blocked** | Ten adapters plus a closed live-contract harness; Hugging Face readiness is explicit | Valid consenting provider credentials, live namespace operation, drift and scale acceptance |
| G3 portfolio risk board | **Complete-local; product acceptance outstanding** | Hierarchy, transparent formula, snapshots, trend/delta, drill-through, API/UI/tests | Production data quality and CRO/customer usability acceptance |
| G4 policy packs | **Complete-local bounded library** | Signed/versioned starter packs, activation, subscriptions, exact safe diffs, staleness and re-attestation API/UI | OneTrust-scale maintained content, licensed standards text, legal/certifier acceptance |
| G5 AI vendor risk | **Complete-local product surface and synthetic pilot; live acceptance outstanding** | Questionnaire, portal, human-review import and consent-gated two-vendor synthetic pilot covering document contracts, exact retry/replay, revocation and isolation | Crash/restart recovery, consenting external vendors, representative live documents and ecosystem workflow acceptance |
| G6 drift monitoring | **Complete-local; live telemetry outstanding** | PSI/KL, prediction, embedding and performance drift to alert/evidence/finding | Calibrated production telemetry, alert delivery and operations acceptance |
| G7 explainability | **Complete-local core** | Closed explanation receipts, exact input/model provenance, review, API/UI | Native provider adapters and adverse-action/legal acceptance |
| G8 red-team assurance | **Complete-local core/dispatcher** | Schedules, leases, dispatcher, bounded suites, commitments, evidence/promotion gate | Live enrolled-agent end-to-end and comprehensive legal bias audit |
| G9 agent/MCP topology | **Complete-local core** | Transitive reachability, trust boundaries, reverse blast radius, MCP inventory, API/UI | Live enterprise product adoption and operator usability acceptance |
| G10 agent identity federation | **Partial; production composition exists, provider/consumer last mile absent** | Read-only Entra/Okta/Purview discovery; leased tenant-specific scheduler; mounted lazy per-tenant control runtime; governed federation/capability/Purview authority registry; fresh conditional-access decisions; CAS/idempotent writeback/compensation; durable digest-only dynamic-risk/alert request handoff. | Exact Entra/Okta/Purview transports and trusted enrollment; AI-asset-bound governed downstream risk/alert consumer; real test tenants, provider policy semantics and customer operating acceptance |
| G11 inline LLM control plane | **Complete-local enforcement and pilot; external acceptance outstanding** | Runtime evaluate/execute plus restart-safe local gateway pilot covering retry, outage ambiguity, no-change, re-evaluation, rollback and compensation | A consenting real external/customer gateway and broad agent/tool/MCP/Copilot acceptance |
| G12 regulatory tracking | **Complete-local bridge; external feed operations outstanding** | Impact mapping, review, stale-evidence resolution, notices, API/UI/config/tests | Licensed/current sources, counsel validation and delivered-notice operations |
| G13 Article 73 incidents | **Complete-local workflow** | Serious-incident triage, clocks, routing record, four-eyes approval, export/receipt | Filing transmitter, maintained authority directory, legal calendar and legal determination |
| G14 business intake | **Complete-local** | Inventory/declaration/review creation and linking, API/UI/tests | Fresh-customer usability and under-three-minute field acceptance |
| G15 GRC outbound sync | **Complete-local adapters; live readiness blocked** | ServiceNow/Archer flows plus an exact-origin-allowlisted ServiceNow write-and-replay harness; environment/hostname labels are not mutation authority | Independently reviewed sandbox-origin enrollment, valid credential/tenant, live mutation acceptance, production credentials and broader GRC targets |
| G16 enterprise AI cost/ROI | **Complete-local adapters; live readiness blocked** | AWS/Azure/GCP/OpenAI collectors plus a bounded OpenAI organization-Costs contract harness | Valid admin credential and consenting billing tenant, finance acceptance; OpenAI model-level allocation remains unsupported by the Costs contract |
| G17 AI literacy register | **Complete-local** | Role requirements, pseudonymous assignments, completion receipts, waivers, gaps and export | HRIS/LMS contracts and customer operating acceptance |
| G18 EU conformity workflows | **Complete-local readiness** | FRIA, Annex IV, Article 43 route, post-market, declaration/CE readiness workflow | Legal determination, conformity assessment, notified/certification body acceptance, signed declaration or CE mark |
| G19 C2PA provenance | **Development interoperability complete; production acceptance outstanding** | Concrete `c2patool` verifier/signer, installed 0.27.16 binary, real development-fixture interop, digest-pinned external-signer boundary, digest receipts, transient media, four-eyes release and production custody design | Production certificate/HSM/KMS ceremonies, ACL/application-control preflight, revocation/outage operation, trusted timestamp/certificate ecosystem and relying-party/customer acceptance |
| G20 benchmark network | **Complete-local coordinator/ceremony; privacy and live acceptance outstanding** | Aggregate lifecycle/client plus restartable synthetic coordinator, copy-on-write bounded durable state, five-contributor admission review, signed report and trust-root rotation/revocation | Live contributors/cross-tenant pooling; independent inclusion/anonymity/DP/privacy-budget proof and acceptance |
| G21 certification/discovery | **External** | Internal AIMS, SoA, evidence and audit primitives can support an application | ISO 42001 certification, analyst review and directory/profile listings |
| G22 insurability | **Complete-local packet; external outcome outstanding** | Underwriter-oriented evidence profile, limitations, guide and four-eyes export | Insurer integration, underwriting acceptance, coverage and changed quote/pricing |
| G23 certification-body channel | **External** | Evidence shape can be reviewed | Accredited certification-body relationship and joint acceptance |
| G24 systems-integrator channel | **External** | Deployment and governance artifacts exist | Named partner kit, commercial/support/margin model, certification and active SI relationship |
| G25 regulated verticals | **Complete-local decision support** | Credit reasons, aggregate fair-lending intervals, LL144 readiness, NAIC mapping, governed lifecycle | Individual decisions, legal conclusions, audits, filings, certifications and regulator acceptance |
| G26 discovery ecosystem | **Partial ecosystem; three deployable sensor agents locally accepted** | Six identities/control plane/ingress plus network-egress, browser-extension and OAuth agent processes with privacy, key lifecycle, recovery and bounded-scale estate | Deployed SaaS/identity/catalog agents, external-estate operation, discovery completeness and production privacy/scale acceptance |
| G27 bank MRM package | **Complete-local package** | Nine review sections, missing/unverified states, exact evidence, lifecycle and four-eyes integrity | Model approval, validation-function adoption, committee workflow acceptance and bank/examiner/regulator acceptance |

## Explicit OneTrust gaps after this build

The 149-outcome matrix now has 75 rows classified Implemented, 67 Partial, zero
Missing and seven External / unverifiable. OneTrust's publicly named product
still has advantages Lightwork has not matched in code-plus-proof:

1. Deployed broad discovery, including shadow and embedded AI collection.
2. A much larger set of live, maintained enterprise integrations.
3. Continuous operations and customer-scale implementation history.
4. A deeper maintained regulatory/privacy/GRC/data-governance content library.
5. Bundled and operated privacy-intelligence content plus commercial
   third-party screening/cyber/breach data feeds.
6. Live consent-experiment delivery and optimization across production web,
   app and consent-runtime traffic.
7. Mature external vendor, regulator and implementation collaboration.
8. A hosted full-product customer sandbox/tenant-copy service; the new bounded
   local isolated-workspace lifecycle is not that outcome.
9. Product usability, support, recovery, deployment and scale evidence.
10. Live/customer-accepted operation of the local packaged Privacy AI and
    Third-Party Risk agents, Copilot, knowledgebase and multimodal AI Evidence
    Analysis, including licensed content and off-host token/signing custody.
11. Credentialed, durable and accepted named agent detection/enforcement for
    AWS Bedrock, Azure AI Foundry and Google Vertex, plus maintained/licensed
    Databricks AI Security Framework content and externally reviewed mappings.
12. Live California DROP authority/registered-broker interoperability, subject
    matching, deletion/permanent-opt-out semantics and legal/regulator/customer
    acceptance. The local control plane cannot prove any of those outcomes.
13. Live/migrated customer acceptance for the now-built AI-entity tasks,
    consent bulk jobs/indexes/publish settings/Global Opt-Out, assessment
    projections, evidence-assisted assessment proposals, TPRM Contract/
    Engagement lifecycle and role-aware executive PDF reporting.
14. External object-store, malware/privacy, backup, off-host key-custody,
    recovery/scale and relying-workflow acceptance for the now-built encrypted
    local document vault, administration tokens and incident attachments.
15. Whole-suite canonical source-history coverage for the shared inventory and
    unified audit; independently operated verifiers, a real S3 Object Lock
    account, accepted off-host key custody and live shared-inventory/agent/audit
    custody/SIEM operation. The current stock readers cover one bounded history
    per suite, while the local checkpoint/anchor/WORM/SIEM composition is built.
16. Compiled, packaged and operated native consent host integrations with
    accepted platform attestation/key/outbox/transport custody, a certified
    TCF/GPP CMP, downstream AdTech and full device/household/rights delivery.
17. Privacy discovery beyond the six current readers: broad formats/database
    values, complete purpose/transfer/subject/recipient/vendor semantics, a
    complete RoPA target and live scheduler/estate completeness operation.
18. Exact identity-provider transports and live operation of the AI-asset-bound
    governed downstream risk/alert consumer.
19. Installed/attested OCR and legacy-XLS deployment dependencies, general
    visual-semantic image analysis beyond OCR, and representative extraction
    quality/calibration/privacy/customer acceptance.

Lightwork's strongest differentiation is the governed execution substrate:
runtime action-path enforcement, signed delegation, capability attenuation,
agent/MCP topology and containment, exact artifact/evidence/evaluator bindings,
raw-transient/digest-only paths, and promotion interlocks. These are technical
differentiators, not parity or readiness proof.

## Fable's remaining work queue

The repository implementations and bounded local pilots for the seven requested
workstreams now exist. The following work cannot be truthfully completed on this
host without credentials, consenting external participants or production
infrastructure:

1. Deploy the three sensor agents into an approved external test estate, record
   the privacy/DPIA decision and source coverage, and repeat key, recovery and
   production-volume exercises. Build the remaining SaaS, directory and catalog
   agent packages.
2. Supply a consenting external gateway and run the same runtime pilot against
   it. Record retry/outage/rollback/no-change results and external operator
   acceptance; the completed local FastAPI pilot is not a substitute.
3. Populate the four secret references in
   `scripts/acceptance/fixtures/provider-contract-readiness.json`, replace all
   placeholder test-tenant identifiers, and run without `--readiness-only` only
   after the exact consent and ServiceNow mutation acknowledgements are true and
   the exact ServiceNow write origin is independently reviewed in the allowlist.
4. Install exact Entra/Okta/Purview transports and trusted enrollment into the
   now-mounted identity scheduler/control composition. Bind its durable
   dynamic-risk/alert request to an exact AI asset and separately governed
   downstream consumer, then operate federation, conditional access and
   writeback in approved tenants before enabling provider mutation.
5. Provision production C2PA HSM/KMS custody, certificate chain, timestamping,
   rotation/revocation and on-call recovery following
   `docs/security/c2pa-signing-custody.md`; obtain relying-party acceptance.
6. Enroll real benchmark contributors only after independent inclusion,
   anonymity, differential-privacy and privacy-budget review. The local run
   explicitly does not claim those properties.
7. Enroll consenting external vendors and representative documents, perform
   human review, and record vendor/customer ecosystem acceptance.
8. Repeat the five-suite platform runner in deployment-like infrastructure and
   add moderated human usability acceptance before readiness claims.
9. Operate the registry-backed Trust Center/DSR composition with a
   production IdP, separately approved identity-assurance resolver, protected
   signing-key custody and at least three distinct operator identities for the
   proposer/two-approver ceremony. Exercise stale revision, key rotation,
   revocation, outage/recovery, per-subject and tenant quotas, concurrency and
   scale. Operate the shipped governed logo/media authority and custom-domain
   DNS/TLS lifecycle, then run accessibility and brand acceptance across
   representative web/native channels. Compile, package and deploy the existing
   native reference adapters, add any remaining host integrations, and operate
   the OTT/CTV rights bridge through real device identity, provider retrieval/
   deletion, response delivery, household/device transitions and customer
   acceptance.
10. Supply a production IdP/reauthentication mapping and accepted KMS/HSM or
    external object-store custody to the shipped rights-delivery composition.
    Separately review a safe protocol for failures after one-time server-grant
    claim, then operate end-to-end identity, redaction, delivery, revocation,
    recovery and customer acceptance exercises.
11. Operate the shipped discovery scheduler, worker/reconcile/maintenance
    paths, six stock source readers and digest-only target in a consenting
    estate, then add broad
    formats/database values, the missing RoPA semantics and any required source/
    destination adapters. Exercise revocation, outage, ambiguity, manual
    redelivery, recovery, concurrency, scale and continuous-completeness limits
    in a consenting enterprise estate.
12. Operate the built-in Snowflake and Databricks audit readers against
    consenting test tenants and independently accept query-tag availability,
    permissions, source latency/retention, result pagination/chunks, delivery-
    watermark authority, continuous completeness, recovery and production
    scale. The local 230-test result is not live-provider evidence.
13. Operate the managed sandbox-workspace lifecycle in a hosted control plane
    with consenting source tenants and representative safe configuration from
    all six suites. Independently accept source/target boundaries, exclusion of
    data and secrets, disabled-state remediation, recovery, concurrency, scale,
    usability, audit export and SLA behavior. A true product-tenant copy needs
    separately designed identity, secret, data, provider and rollback authority;
    do not infer it from the local 137-test result.
14. Extend the shipped tenant-exact stock readers from one canonical authority
    per suite to lawful-cursor coverage of every material suite history. Deploy
    separately controlled signed/anchored verifiers with accepted trust-root
    and off-host key custody; operate the scheduler through kill, restart,
    outage, recovery, concurrency and production scale; configure and accept the
    shipped cross-day anchor, real S3 Object Lock WORM and exact-tenant SIEM
    paths plus whole-database rollback protections; then obtain customer and
    SIEM completeness/operations acceptance. Do not infer any of those results
    from the local focused runs.
15. Extend the shipped shared-inventory and packaged-agent context readers from
    one canonical history per suite to all material suite authorities, then
    deploy the agent contracts with independently operated adapters/dispatchers.
    Move the encrypted one-time token vault to accepted HSM/KMS custody and
    complete rotation/revocation, ambiguous-outcome recovery, adversarial
    evaluation, licensed knowledge content and customer usability/operations
    acceptance.
16. Replace the cloud-agent control plane's process-local canonical authority
    with durable tenant storage, install credentialed transports, broaden only
    independently verified safe provider writes, and run Bedrock/Azure/Vertex
    completeness, outage, retry, reconciliation, recovery and scale acceptance.
    License/maintain the Databricks framework content and obtain external
    mapping review; the content-free local pack is not certification.
17. Operate the California DROP path with a consenting registered broker and
    external authority, independently validate subject/request bindings and
    all status transitions, and complete retry, revocation, privacy, legal,
    regulator and customer acceptance. Separately contract-test assessment
    comments and the evidence-result object in their real relying workflows.
18. Operate the now-mounted Governance Agents, AI Evidence Analysis, DROP and
    identity compositions with production authority. Preserve fail-closed
    revoked/drifted behavior and record startup, recovery, concurrency, scale
    and customer/provider acceptance without converting local readiness into a
    live claim.
19. Migrate and usability-test the now-built AI-entity tasks; exercise the
    consent bulk export/action/index/settings/Global Opt-Out products at
    representative volume and through native device channels.
20. Contract-test the new assessment score/record/AI-metadata projections,
    reviewed evidence-to-assessment proposals and first-class Contract/
    Engagement lifecycle in real relying workflows, including mapping/model
    calibration, migration, privacy review and customer/vendor acceptance.
21. Operate the encrypted local document vault, administration tokens,
    incident attachments and deterministic executive PDF reports with
    representative records and relying workflows. Independently accept
    external object-store/malware/backup/key custody, source completeness,
    brand governance, recovery, concurrency, scale and customer usability.
22. Deploy and attest exact Bubblewrap, Tesseract/language-data and legacy-XLS
    parser dependencies on a supported POSIX host; execute the OS-network-
    denied sandbox/readiness path and independently evaluate adversarial
    extraction quality, privacy boundaries and score calibration.

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

## 2026-08-31 production-composition and parity rebaseline continuation

This section is the authoritative continuation for current capability counts
and supersedes earlier same-day statements that Governance Agents, AI Evidence
Analysis, DROP or identity production compositions were absent. Those earlier
statements accurately described the browser-audit checkpoint before the later
build, but they are not the current working-tree state.

Current composition outcome:

- **Governance Agents:** the app installs the stock production composition,
  including deterministic offline analysis, internal-intent dispatch, an
  encrypted rotatable one-time token vault, receipt verification and recovery/
  readiness controls. It now also installs authoritative tenant-exact
  cross-suite context projections with revision, digest, completeness and
  source-availability pins, bounded projections and double-read drift
  rejection. The packaged common-context row is Implemented locally; the
  Privacy AI and Third-Party Risk Agent rows remain Partial for equivalent
  maintained/licensed content, commercial feeds and live external operation.
- **AI Evidence Analysis:** the app installs its tenant store, authority,
  adapter, readiness and retention composition. Its authority registry now has
  closed capacity, live-only artifact quotas, retention-delayed digest
  tombstones, a rolling compaction anchor and composed core/authority
  maintenance; tenant status no longer advertises disabled media or hardcodes
  legacy XLS unavailable. Adapter compaction cannot silently recreate revoked
  or expired local authority. The durable-result row is
  Implemented locally. The broader format-analysis row remains operationally
  Partial: optional OCR and legacy-BIFF8 workers require exact-pinned POSIX
  Bubblewrap network isolation plus attested Tesseract/language data/runtime
  libraries/Pillow/`xlrd`/`olefile`. Unsupported Windows fails closed, and this
  host could not execute the required sandbox. OCR is not general visual-
  semantic understanding, and no extraction-quality, calibration, privacy or
  customer acceptance is claimed.
- **California DROP:** the app installs the authority registry, store factory,
  scheduler and production composition. The locally owned DROP row is
  Implemented; registered-broker, legal, regulator and customer acceptance are
  explicitly not claimed.
- **Identity governance:** the app installs a lazy exact-tenant control runtime,
  tenant scheduler composition, exact tenant/provider/revision/digest AI-asset
  authority and leased, retryable, idempotent dynamic-risk and alert outcomes,
  with closed config/wizard controls and a fixed-authority deployment transport
  boundary. The row remains Partial because real Entra/Okta/Purview credentials,
  trusted enrollment, provider-side conditional-access enforcement, writeback
  interoperability and operated customer acceptance are absent.
- **Shared governance inventory and unified audit:** the app installs a tenant-
  private fixed-six shared inventory with canonical readers, explicit source
  completeness/availability, minimized HMAC provenance, strict API and liquid-
  glass/Copilot workbench. This closes local `PX-01`. Unified audit now also
  installs an exact-tenant custody runtime invoked by projection runs and the
  scheduler for signed chain/anchor verification, checkpoint publish/verify,
  optional sealed-day S3 Object Lock export and durable at-least-once exact-
  tenant SIEM delivery with replay/backoff. `PX-10` remains Partial because the
  stock surface covers only one narrow history per suite and has no independently
  operated verifier, whole-suite audit completeness, real Object Lock account,
  accepted off-host key custody or live scheduler/SIEM operation.
- **Native consent:** the application scanner, device-bound Ed25519 core,
  backend, public/admin/operator composition and Swift/Kotlin/JavaScript/
  BrightScript reference adapters now exist. A 13-Python/5-Node protocol slice
  and separate 274-Python/2-Node composition/UI slice passed locally, with
  targeted Ruff clean. `CP-16` through `CP-19` remain Partial: the adapters were
  not compiled, packaged or run on devices, and accepted attestation/key/outbox/
  transport custody, certified TCF/GPP, AdTech/rights delivery and live device/
  recovery/scale/customer operation are absent.
- **Privacy discovery:** six exact-source S3/Azure Blob/GCS/Snowflake metadata/
  ServiceNow CMDB/Entra readers, a tenant-pinned composition, approved-review
  outbox, digest-only data-map target, admin worker/reconcile/maintenance
  surfaces and a bounded active-tenant scheduler now ship. `PA-04` remains
  operational-proof Partial because no live continuously operated estate was
  available. `PA-05` and `PA-06` remain material-code Partial because coverage
  is enrolled-resource and supported-text/metadata only and the target is not a
  complete RoPA.
- **No-code GRC Studio:** the app installs a guided liquid-glass/Copilot visual
  builder for closed objects, fields, forms, rules, collector bindings and
  report views. Three-principal approval, CAS publish/rollback, authenticated
  portability, integrity, retention/recovery and server-authorized runtime
  projections close the local UI-driven configuration row. Broad named-
  connector coverage and fresh-tenant live usability remain separate outcomes.
- **Governed document custody:** the app installs an encrypted tenant-private
  vault and wires its exact document resolver and opaque one-time delivery-grant
  authority into administration tokens and incident attachments. The two local
  rows are Implemented; external object-store, malware/privacy, backup, off-host
  key custody, relying-workflow, recovery and scale acceptance are not claimed.
- **Evidence-assisted assessments:** the app installs the general
  evidence-analysis-to-questionnaire proposal composition, API and workbench.
  Exact evidence/template/criteria/mapping pins, citations, independent review
  and CAS application to still-empty answers close the two local rows; vendor
  attestation, source completeness, model/parser calibration and customer
  acceptance are not claimed.
- **Third-party executive reports:** the app installs role-aware deterministic
  PDF reporting over exact selected lifecycle, intelligence, Contract/
  Engagement and collaboration source manifests. Rendered branding is limited
  to closed plain-text/color tokens; optional brand-asset reference/digest
  fields are non-rendered provenance metadata, and no asset/logo bytes are
  resolved, fetched or embedded. Truthful unavailable states, immutable report
  content, revocation/retention, one-time principal-bound downloads, audit and
  integrity close the local row; live portfolio completeness, migrated data,
  external custody and executive/customer recovery/scale acceptance are not
  claimed.

The rebaselined 149-row matrix is **75 Implemented, 67 Partial, 0 Missing and
7 External / unverifiable**. The 67 Partial rows split into **52 with material
code/product breadth remaining** and **15 whose primary residual is live or
operational proof**. The twenty-two promotions from the earlier
53/86/3/7 checkpoint are `PX-16`, `AG-27`, `CP-21` through `CP-25`, `PA-08`,
`PA-21` through `PA-24`, `TRC-25`, `TPM-06`, `TPM-18`, `TPM-22`, `PX-15` and
`TRC-16`, `PX-01`, `CP-03`, `PA-11` and `PA-14`. No row was
promoted merely from a file or class name, and no live/provider/customer/legal
evidence was inferred from local tests. The optional parser paths remain
default-off until their exact deployment dependencies are installed and
attested. `TRC-24` remains operational-proof Partial because this Windows host
cannot execute its required OS-enforced, network-denied parser sandbox.

The detailed status definitions, all 149 IDs split into implemented,
code/product-Partial, live/operational-Partial, Missing and External groups, and
focused deep dives are in
[`ONETRUST_PARITY_BREAKDOWN_2026-08-31.md`](./ONETRUST_PARITY_BREAKDOWN_2026-08-31.md).
This continuation is documentation of the local working tree; it is not proof
that the changes are committed, pushed, merged or deployed.

## 2026-09-01 final local closure

This section supersedes any earlier statement in this handoff that the privacy-
notice publisher lacked a stock callback/composition, privacy discovery lacked
a scheduler, privacy-rights fulfillment lacked source adapters, rights package
delivery lacked production wiring, Trust Center branding/custom domains were
only a theme placeholder, or unified-audit checkpoint/WORM/SIEM primitives were
not orchestrated. Those were accurate intermediate checkpoints, not the final
working-tree state.

Final classification changes and boundaries:

| Row | Final local result | What remains |
|---|---|---|
| `CP-03` | **Implemented.** Reviewed encrypted/versioned PNG/JPEG custody, independent activation/revocation, exact custom-domain challenge plus DNS/TLS verification, Host-independent projection and digest-bound public asset delivery now ship. | Live DNS/TLS/CDN issuance, production key/IdP custody, accessibility/brand/customer and external-fulfillment acceptance. |
| `CP-13` | **Operational-proof Partial.** Exact-CAS unlink, fresh-evidence relink/account transition, immutable history, pairing-proof revocation, legacy backfill and interrupted-operation recovery close the material local identity-lifecycle gap. | Production IdP/JWKS custody, continuous multi-device transport and live recovery/scale/customer operation. |
| `PA-04` | **Operational-proof Partial.** The six stock readers and digest target now run through a bounded active-tenant scheduler with durable leases, cross-process exclusion, recovery and backoff. | Credentialed continuous-estate operation, completeness, outage/recovery/concurrency/scale and customer acceptance. |
| `PA-11` | **Implemented.** Exact notice/property/rule/resource bindings, double-preview resolution, release/target re-read, CAS publication, verified rollback, retry/recovery and a bounded active-tenant scheduler now form the stock notice-publication runtime. | The runtime intentionally does not mutate a provider, website, tag manager or SDK deployment; live rollout and legal/privacy/accessibility/customer acceptance remain external. |
| `PA-14` | **Implemented.** Exact Microsoft Graph, Google Workspace, Salesforce and ServiceNow retrieval/deletion/verification contracts, atomic pre-mutation reservation, ambiguity reconciliation, authority rechecks, encrypted exact-target custody and governed-vault retrieval now ship. | Live providers/credentials, estate completeness, accepted KMS custody and provider/legal/customer operation. |
| `PA-15` | **Operational-proof Partial.** The stock rights-delivery composition binds exact case/package authority, independent public subject identity, deployment-owned reauthentication proof, digest/key pins and governed-vault authority checks before grant, consume and byte return. | Production IdP/reauth mapping, accepted KMS/HSM or object-store custody, live browser/customer/load evidence, and a separately reviewed post-claim/pre-response crash protocol; current behavior deliberately fails closed. |
| `PX-10` | **Material-code Partial.** The tenant-exact custody runtime now runs with projection/scheduling and performs signed chain/anchor verification, checkpoint publish/verify, optional sealed-day S3 Object Lock export, durable at-least-once exact-tenant SIEM delivery, replay/backoff, authority drift rejection and cross-process exclusion. | Every material suite history, independently attested completeness, separately operated verification, a real Object Lock/SIEM estate, accepted off-host keys and live recovery/scale/customer operation. |

`CP-01`, `CP-04` and `CP-06` remain material-code Partial even though their
evidence descriptions are stronger: the complete UCPM outcome still needs
operated communications/native channels; Adobe Experience Platform still needs
a deployment-specific asynchronous query/readback authority; and native CMP
hosts remain uncompiled/unoperated with no live geolocation/accessibility
acceptance.

The three production compositions named in the user review are code-complete at
the local product-composition layer:

- **Governance Agents** installs tenant/principal/RBAC authority, five packaged
  kinds, exact shared context, deterministic local analysis, internal-intent
  dispatch, encrypted one-time token custody, receipts, approval, recovery,
  readiness, API, liquid-glass UI and Copilot.
- **AI Evidence Analysis** installs private tenant stores, parser/adapter
  authority, revocation/rotation, bounded parsing and analysis, durable review/
  release results, retention, API, liquid-glass UI and Copilot. Optional OCR/
  BIFF8 execution still requires an attested supported POSIX sandbox.
- **California DROP** installs broker/principal/key/request authority, the
  privacy-rights bridge, four-eyes changes, exact status transitions, scheduler,
  retry/reconciliation/revocation/recovery, API, liquid-glass UI and Copilot.

Here, **production composition** means deployable, configuration-backed,
fail-closed application wiring that does not rely on test-only injection. It
does not mean a California authority, cloud provider, licensed content owner,
customer, auditor or regulator supplied credentials or accepted an operation.
The final current matrix is therefore **75 Implemented, 67 Partial, 0 Missing
and 7 External / unverifiable**, not literal OneTrust parity. The historical
**53/86/3/7** checkpoint remains only as provenance for the 22 local promotions.

## 2026-09-01 integrated UI and validation closure

The dashboard was started twice on loopback from the repository `.venv` with an
isolated `MAVERICK_HOME`: first with every new capability gate off, then with a
closed local acceptance configuration enabling Governance Agents, AI Evidence
Analysis, DROP, consent identity, notice governance/publication, Trust Center
branding/custom-domain authority and privacy-rights consumer delivery. No live
provider credential, customer data or external mutation was used.

Browser acceptance covered `/governance-agents`, `/ai-evidence-analysis`,
`/privacy/drop`, `/privacy/notice-governance`,
`/consent/trust-center-authority`, `/consent`,
`/privacy/rights-consumer-delivery`, `/privacy` and `/audit`:

- all pages returned HTTP 200, rendered their closed operator controls, exposed
  the current navigation path and showed no browser console warning/error;
- the off-state disabled controls and explained the exact configuration gates;
- the enabled-state removed those disabled banners and exposed the bounded
  operations without introducing browser-selected tenant, credential, adapter
  or identity authority;
- Governance Agents status returned its five packaged kinds and callable stock
  seams, while candidly reporting the missing authenticated principal and live
  provider/content proof;
- AI Evidence status enabled PDF/XLSX/text operation attempts and truthfully
  reported image OCR unavailable on this host;
- DROP reached local `Ready` through its real stock composition, but continued
  to disclaim California/regulator/legal/customer interoperability;
- notice publication exposed its local runtime/status/run-once controls and
  corrected stale page text: the scheduler can publish to Lightwork's exact-
  tenant Consent Publishing store, not a website, SDK, tag manager or provider;
- Trust Center exposed the complete asset review/activate/revoke/custody and
  custom-domain challenge/DNS/TLS/activate/reverify/revoke operation inventory;
- consent showed current identity links, governed unlinks and immutable
  link/unlink/relink/account-transition evidence; rights delivery showed its
  exact case/package/vault gates and operator-to-consumer identity fail-closed
  seam;
- Audit now has a read-only liquid-glass Unified Audit Custody panel backed by
  `/api/v1/audit/unified/status`, with checkpoint/WORM/SIEM readiness and an
  explicit separate-production-proof indicator; and
- page-aware Copilot was opened on Governance Agents and rights delivery. Its
  module-specific suggestions loaded successfully and remained guidance-only.

The standard dark theme's panel material was verified in the browser as a
translucent gradient with `blur(20px) saturate(1.6)`, layered borders and
shadows. High-contrast mode deliberately replaces transparency/blur with an
opaque surface and stronger border; that accessibility exception is not a
liquid-glass regression.

Validation was intentionally not repeated wholesale. One consolidated changed-
test Python run completed with three integration failures: a mis-nested AI-
evidence config-lint block and an outdated explicit public-route allowlist.
Those defects were corrected; the exact failures plus adjacent notice/audit UI
checks passed `8/8`. The JavaScript sweep passed `137/139`, exposing an outdated
test harness that still assumed privacy discovery was inline after the client
moved to a bounded external script; the exact corrected contract file then
passed `52/52`. Native consent contracts passed `5/5`. Ruff passed all 321
changed Python files, Vulture returned clean, the matrix verifier returned
`verified_structure_only` with `parity_claimed=false`, and the browser checks
above completed with no console errors. These are local/synthetic validation
results, not live provider, customer, regulator or certification acceptance.

---

# Historical Origin/Main G1-G27 Handoff Snapshot (Superseded)

The remainder of this file preserves the earlier `origin/main` handoff as merge
history. Its branch, worktree, counts and completion statements describe the
2026-08-23 baseline and are superseded by the 2026-09-01 handoff above. Retaining
it preserves the original implementation evidence and continuation rationale;
it must not be read as the current branch status.

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
