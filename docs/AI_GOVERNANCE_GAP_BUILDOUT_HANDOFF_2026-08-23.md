# Lightwork AI Governance Gap Buildout: Fable Handoff

Updated: 2026-08-30

Repository: Daybreak-AI-Labs/Lightwork

Branch: claude/aigov-gap-buildout

Publication target: origin/claude/aigov-gap-buildout

Commit message: wip: AI governance gap buildout (G1-G27) - handoff state

Current state: the earlier handoff snapshot is committed and pushed at
`ffa309a0990ecd216ee995a24bd53ece6bee16ac`, which is also the current
`origin/claude/aigov-gap-buildout` tip. The 2026-08-28 operational-acceptance
additions and the 2026-08-30 OneTrust full-platform increment described below
are currently **local, uncommitted and unpushed** on
`claude/aigov-gap-buildout`; main remains untouched.

## Executive result

This branch contains a substantial, locally tested AI-governance buildout
covering every G1-G27 recommendation that can reasonably be represented as
repository code. It is a WIP handoff, not a finished product, a OneTrust-parity
claim, a certification, or proof of operation in a customer environment.

The remaining gaps are now concentrated in external operation and acceptance:

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
- authoritative readers for all six domain stores, independently operated
  signed/anchored unified-audit verifiers, external WORM/cross-day anchors,
  off-host signing-key custody and live scheduler/recovery/scale/customer/SIEM
  acceptance;
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
- The remote branch tip is the earlier snapshot. The working tree described by
  this revision is not durable on GitHub until the user authorizes a new commit
  and push.
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

## 2026-08-30 OneTrust full-platform increment

The new
[OneTrust full-platform public capability matrix](./ONETRUST_FULL_PLATFORM_CAPABILITY_MATRIX_2026-08-30.md)
maps 138 publicly advertised outcomes across shared platform capabilities, AI
Governance, Consent & Preferences, Data Use Governance, Privacy Automation,
Tech Risk & Compliance and Third-Party Management.

| Matrix status | Count |
|---|---:|
| Implemented | 59 |
| Partial | 72 |
| Missing | 0 |
| External / unverifiable | 7 |
| **Total** | **138** |

Zero rows classified **Missing** does not mean OneTrust parity. A **Partial**
row may contain substantial local code while still lacking a material part of
the advertised outcome, live interoperability, content, operations, recovery,
scale or customer acceptance. The seven **External / unverifiable** rows depend
on licensed content, third parties, commercial relationships, certifications
or market evidence that repository code cannot create. These are
capability-level classifications, not proof that every feature is complete.

A same-day official-site refresh added six previously implicit named outcomes:
packaged shared-platform AI Agents; AWS Bedrock, Azure AI Foundry and Google
Vertex agent detection; the Databricks AI Security Framework; the Privacy AI
agent/Copilot/Knowledgebase suite; multimodal AI Evidence Analysis; and the
early-access Third-Party Risk Agent. The official
[2026-07-10 changelog](https://developer.onetrust.com/onetrust/changelog/onetrust-20260710-released)
adds Assessment Comments APIs, California DROP request/status APIs and the
`aiEvidenceAnalysisResult` evidence-task result. Seven of these nine named
outcomes now have coherent local paths classified **Implemented**; the named
cloud-provider enforcement and Databricks framework outcomes remain
**Partial**. The sources mix preview, early-access and GA language without a
feature-level entitlement map, so this handoff does not promote a marketing
mention to a generally available baseline.

### Current continuation addendum

The current tree adds the following locally coherent, product-mounted paths:

- `governance_agents.py` and the strict workbench/API package five closed agent
  kinds: breach response, privacy risk assessment, program Copilot,
  knowledgebase governance and third-party risk. Policies are immutable and
  default-off; context, adapters, dispatchers and execution credentials are
  server-resolved; findings/actions are closed and bounded; consequential
  execution requires human approval and verified receipts; quotas, replay,
  recovery, revocation, retention and integrity are explicit.
- `ai_evidence_analysis.py` and its workbench/API implement bounded PDF, JPEG,
  Excel and text analysis against immutable criteria with independently
  attested adapters, transient raw content, closed cited facts, deterministic
  summaries, transparent justification components, gaps/remediation,
  provenance, distinct human release and a durable result object corresponding
  to the newly public `aiEvidenceAnalysisResult` outcome.
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
  data.

This addendum does **not** claim literal OneTrust parity. Final integrated
repository verification against this checkout passed **23,145 tests**, with
**292 skipped**, **3 expected xfails** and **0 failures** in **43m45s**. The run
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
  targeted Ruff, compileall and Vulture passed. No live IdP/JWKS fetch, native
  SDK integration, production key custody, customer synchronization, recovery
  or scale acceptance is claimed.
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
  audit and integrity evidence. It is network-free by default and includes no
  built-in provider/data-map target or live-estate acceptance.
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
  evidence. In this orchestration module, callbacks remain worker intent only:
  no source adapter, identity verification, response delivery or estate-
  completeness claim is implemented. The separate delivery path below does not
  convert that local intent into provider or customer acceptance.
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
  failures. The built-in collector observes only local SOC 2 technical state;
  external collector/credential injection seams exist but no external runtime
  is installed by default, and no compliance
  conclusion, certification, broad live-estate completeness or customer/
  auditor acceptance is claimed.
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
- **Unified cross-suite audit projection:** `unified_audit.py` plus the
  dashboard source adapters, composition, strict API and bounded lifespan
  scheduler now provide a default-off, signed-only projection over the fixed
  AI Governance, Consent & Preferences, Data Use Governance, Privacy
  Automation, Tech Risk & Compliance and Third-Party Management sources.
  Deployments inject tenant-bound authoritative readers separately from
  independent verifiers. Closed normalized records use stable projection IDs;
  every page, including an empty terminal page, requires a signed receipt from
  the canonical tenant-bound strict sink before its cursor advances. Exact
  source-authority, schema, verifier-identity, trust-root and source-key
  rotation pins, generation sweeps, CAS lease renewal/reclaim, bounded retry,
  replay, revocation, immutable history, local integrity linkage and
  read/admin APIs are covered locally. The scheduler consults only active
  tenant-registry records and makes one fixed-six run per enabled tenant per
  cycle. No authoritative readers for the six domain stores or independently
  operated signed/anchored verifier deployment ships here. SQLite linkage is
  not external WORM or a cross-day/off-host anchor; off-host signing-key
  custody and live scheduler crash/recovery, concurrency, scale, provider
  completeness, customer operation and SIEM acceptance remain unproven.
- **Configuration and installation:** config lint, migration recognition and
  installer/wizard prompts now cover
  `[consent_preferences.experiments]`,
  `[consent_preferences.profile_data]`,
  `[consent_preferences.trust_center]`,
  `[consent_preferences.scanner.notice_proposals]`,
  `[privacy_automation.regulatory_intelligence]` and
  `[third_party_management.intelligence]`,
  `[third_party_management.lifecycle]` and
  `[privacy_automation.rights_orchestration]`,
  `[privacy_automation.rights_consumer_delivery]`,
  `[privacy_automation.notice_governance]`,
  `[tech_risk_compliance.scoping]` and
  `[tech_risk_compliance.scope_actions]`,
  `[tech_risk_compliance.continuous_evidence]`,
  `[data_use_governance.provider_audit]` and
  `[third_party_management.intelligence.providers]`, and
  `[customer_sandbox_lifecycle]`, and `[audit.unified]`. Enabling a local
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
| Named third-party provider contracts, durable feed runtime and scheduler plus existing intelligence/delivery regressions | 64 passed | Closed RiskRecon, SecurityScorecard, HackNotice and approved-generic normalized contracts; tenant/source/monitor/key/revision pins; leases, retry/dead-letter/replay/revocation, concurrency, retention/integrity and lost-acknowledgement recovery were exercised with injected local doubles. No bundled HTTP client or credential, licensed/live feed, SupplyWisdom contract, provider write, automatic risk conclusion, continuously operated deployment, portfolio-scale operation or provider/customer acceptance. |
| Native Data Use provider audit plus Snowflake/Databricks adapter regressions | 230 passed in the current broader focused rerun | Exact operation/binding/IR/control/read/key pins, built-in bounded native transports, normalized audit events, pagination/chunk/metadata/watermark checks, explicit reconciliation outcomes, leases/recovery/retry/dead-letter/replay/revocation/retention/integrity and no-raw persistence; no live provider/customer acceptance, production completeness certification or scale proof |
| OTT/CTV rights bridge plus identity/orchestration/config/app regressions | 182 passed | Fresh redeemed pairing and exact service-client/Trust-Center authority, access/deletion case creation, approval/legal-hold preservation, revocation, recovery, expiry/purge and digest-only status with local fixtures; no native SDK, provider retrieval/deletion, delivered response or live estate/customer acceptance |
| Managed customer sandbox core, API, composition, config and wizard | 137 passed, including 83 core lifecycle tests | Six closed safe projections, immutable snapshot/source authority, four-eyes lifecycle, disabled remediation, exact idempotency/replay, collision and quota enforcement, retention, and injected crash windows across provisioning/resync/expiry/destroy/abandon passed locally. This is not a registered tenant or execution/network sandbox; workspace integrity, keyed audit, hosted/live operation, production recovery/concurrency/scale/usability/SLA and customer acceptance remain unverified. Two Windows link/reparse tests skip when the host cannot create the required link type. |
| Unified cross-suite audit core, strict sink, adversarial cases, six-source adapters, API, composition, scheduler, config and wizard | 89 passed | Local/injected fixtures cover signed-only default-off configuration, fixed-six completeness, stable record and mandatory page receipts, tenant-bound chain-verified sink receipts, rotation pins, sweep generations, leases/retry/replay/revocation, history/integrity and active-tenant scheduling. No live authoritative domain reader, independent verifier service, external WORM/cross-day anchor, off-host key custody, crash/recovery/concurrency/scale exercise, provider completeness, customer operation or SIEM acceptance was performed. |
| TRC continuous-evidence core, API, scheduler and dashboard attachment | 29 passed | Local digest-only collector, exact authority, tenant allowlist enforcement, incomplete/truncated evidence, recovery, retention/integrity, tenant/job failure isolation and bounded lifespan operation; external collector/credential injection seams exist but no external runtime is installed, and no compliance conclusion, broad live estate or customer/auditor acceptance is claimed |
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
| Privacy discovery plus durable remediation outbox | 53 passed | Local digest-only leases/retry/recovery/reconciliation with injected callback; no built-in provider/target or live estate |
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

For the final current-tree gate, `python -m ruff check .`,
`python -m vulture`, package `compileall`, JavaScript syntax checking, the
138-row OneTrust matrix verifier and `git diff --check` all passed. The first
repository-wide attempt exposed dated test evidence, Windows command quoting,
UTC/local-day bucketing and host-provider isolation defects; those were repaired
without weakening production freshness, sandbox, tenancy or audit controls.
The complete clean-provider matrix was then rerun from zero and passed.

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
- A default-off identity-governance control plane for digest-only risk/alert
  callback fanout, pseudonymous agent-principal federation through an
  independently injected enrolled subject-binding resolver, identity-to-
  capability mapping, fresh conditional-access decisions and CAS/idempotent
  Purview writeback/compensation. These paths are locally tested, not accepted
  against live identity tenants.
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
  and the broader 138-outcome
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
| G10 agent identity federation | **Complete-local control paths; live acceptance outstanding** | Read-only Entra/Okta/Purview discovery; leased scheduler; digest-only risk/alert coupling; pseudonymous federation with authoritative enrolled subject resolution; capability mapping; fresh conditional-access decisions; CAS/idempotent Purview writeback/compensation | Real Entra/Okta/Purview test tenants, agent-principal enrollment contracts, policy semantics and customer operating acceptance |
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

The 138-outcome matrix has no rows classified Missing, but 72 are Partial and
seven are External / unverifiable. OneTrust's publicly marketed product still
has advantages Lightwork has not matched in code-plus-proof:

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
4. Operate the identity scheduler/control plane against approved Entra or Okta
   and Purview tenants. Review provider-specific agent-principal, conditional-
   access and writeback semantics before enabling any mutation.
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
9. Operate the new registry-backed Trust Center/DSR composition with a
   production IdP, separately approved identity-assurance resolver, protected
   signing-key custody and at least three distinct operator identities for the
   proposer/two-approver ceremony. Exercise stale revision, key rotation,
   revocation, outage/recovery, per-subject and tenant quotas, concurrency and
   scale. Extend the reviewed color/plain-copy theme contract to governed
   logo/media assets and custom domains; run accessibility acceptance across
   representative web/native channels. Build native mobile/TV SDKs and operate
   the OTT/CTV rights bridge through real device identity, provider retrieval/
   deletion, response delivery, household/device transitions and customer
   acceptance.
10. Supply governed object-store/decryption and source-system adapters for
    rights packages, define recovery for failures after one-time server-grant
    claim, and operate end-to-end identity, redaction, delivery, revocation and
    customer acceptance exercises.
11. Compose the discovery remediation outbox and discovery-to-Data-Use bridge
    into deployed workers, supply approved source and data-map/RoPA target
    adapters, and exercise revocation, outage, ambiguity, recovery, concurrency,
    scale and continuous-completeness limits in a consenting enterprise estate.
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
14. Implement authoritative, lawful-cursor readers for the AI Governance,
    Consent & Preferences, Data Use Governance, Privacy Automation, Tech Risk &
    Compliance and Third-Party Management stores. Deploy separately controlled
    signed/anchored verifiers with accepted trust-root and off-host key custody;
    operate the scheduler through kill, restart, outage, recovery, concurrency
    and production scale; add external WORM/cross-day anchors and whole-database
    rollback protections; then obtain customer and SIEM completeness/operations
    acceptance. Do not infer any of those results from the local 89-test run.
15. Deploy the closed packaged-agent contracts with independently operated
    analysis/dispatch adapters and a durable encrypted one-time execution-token
    vault. Complete HSM/KMS custody, rotation/revocation, ambiguous-outcome
    recovery, adversarial evaluation, licensed knowledge content and customer
    usability/operations acceptance. Do not infer those results from local
    policy, receipt and recovery tests.
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
