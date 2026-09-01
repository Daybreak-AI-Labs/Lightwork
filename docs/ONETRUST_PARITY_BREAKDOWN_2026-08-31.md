# Lightwork / OneTrust Capability-Status Breakdown

Date: 2026-09-01

Branch inspected: `claude/aigov-gap-buildout`

Authoritative row-level source:
[`ONETRUST_FULL_PLATFORM_CAPABILITY_MATRIX_2026-08-30.md`](./ONETRUST_FULL_PLATFORM_CAPABILITY_MATRIX_2026-08-30.md)

## Bottom line

The earlier **53 Implemented / 86 Partial / 3 Missing / 7 External or
unverifiable** statement is a stale point-in-time snapshot. Reinspection of the
current shared working tree, including the newly mounted production
compositions and focused tests, produces this conservative 149-row result:

| Status | Rows |
|---|---:|
| Implemented | 75 |
| Partial — material code/product breadth remains | 52 |
| Partial — locally coherent; primary residual is live/operational proof | 15 |
| Missing | 0 |
| External / unverifiable | 7 |
| **Total** | **149** |

This is not a literal-parity score and it is not a percentage-complete claim.
One row may normalize a broad product outcome while another names a narrow API.
`Implemented` means a coherent locally owned product path is mounted,
configuration/readiness capable and focused-test-covered. It does not prove
live provider interoperability, customer-scale operation, licensed content,
legal or regulator acceptance, certification, commercial ecosystem breadth or
OneTrust wire compatibility.

The exact promotions from the stale snapshot are:

- `AG-27`: AI-entity review/remediation task lifecycle.
- `CP-21` through `CP-25`: consent bulk export, custom indexes, bulk subject
  actions, web publish settings and application Global Opt-Out.
- `PA-21` through `PA-23`: California DROP, assessment score/record projections
  and persisted answer-AI/record-person metadata.
- `TRC-25`: durable `aiEvidenceAnalysisResult`-shaped local result lifecycle.
- `TPM-22`: first-class Contract and Engagement lifecycle.
- `PX-16` and `PA-24`: encrypted tenant-private document custody now closes
  the administration-token and incident-attachment relying paths locally.
- `PA-08` and `TPM-06`: governed evidence-analysis-to-assessment proposals,
  independent review and CAS application now cover general supported
  questionnaires rather than only the AI-vendor path.
- `TPM-18`: role-aware deterministic executive PDF reporting over exact
  third-party source manifests with closed text/color branding, non-rendered
  brand-asset provenance metadata and governed downloads.
- `PX-15`: packaged Governance Agents now receive authoritative, tenant-exact
  cross-suite context with revision, digest, completeness and availability pins.
- `TRC-16`: a mounted no-code GRC studio now governs visual objects, fields,
  forms, rules, collector bindings, report views and runtime projections.
- `PX-01`: the mounted tenant-private six-suite governance inventory now
  supplies a single normalized local read model with stock canonical readers,
  explicit completeness/availability state and minimized entity provenance.
- `CP-03`: reviewed encrypted brand assets, exact custom-domain authority and
  Host-independent public projection close the locally owned branded web Trust
  Center outcome.
- `PA-11`: a stock notice-publication composition and managed scheduler now
  bridge approved releases to exact active consent configurations with
  double-preview validation, CAS publication and verified rollback.
- `PA-14`: stock Microsoft Graph, Google Workspace, Salesforce and ServiceNow
  retrieval/deletion/verification contracts plus encrypted target custody and
  governed-vault retrieval close the locally owned source-operation outcome.

`TRC-24` also gained governed optional OCR and legacy-BIFF8 parser code, but is
not promoted: this Windows host cannot execute the required OS-enforced,
network-denied parser sandbox, so deployment readiness and adversarial parser
operation remain unproven.

No row was promoted merely because a similarly named file exists. `PA-19`,
`TPM-21` and `AG-20` remain Partial for the concrete
boundaries explained below.

## What each status means

### Implemented

The locally owned outcome has a coherent core, a shipped mount (API, page,
scheduler or worker as appropriate), a production configuration/composition
path whose readiness can pass without a test-only dependency, and focused
automated coverage. Remaining live or customer acceptance is recorded but does
not erase completed local code.

### Partial — material code/product breadth remains

Useful code exists, but at least one substantive piece of the marketed outcome
is still absent: a source/destination adapter, compiled/deployed native host
integration,
maintained content, background composition, parser, downstream consumer,
document authority, no-code builder, or equivalent product breadth. These are
the rows where more repository/deployment engineering can materially change the
classification.

### Partial — locally coherent; primary residual is live/operational proof

The relevant local core is coherent, but the advertised outcome intrinsically
requires a real provider/customer estate, continuously operated workload,
calibration, recovery/scale exercise or relying-party acceptance that was not
available on this host. These rows must not be promoted from mocks, local
transports or synthetic tenants.

### Missing

No coherent implementation of the advertised outcome exists. The current
working tree has **zero** rows in this category. Zero Missing still does not
mean parity because 67 Partial rows and seven External rows remain.

### External / unverifiable

The row is fundamentally about proprietary content, commercial ecosystem,
certification, live scale or third-party acceptance that repository code alone
cannot establish.

## All 149 capability IDs by current status

### Implemented (75)

- Shared platform (7): `PX-01`, `PX-03`, `PX-05`, `PX-09`, `PX-13`, `PX-15`,
  `PX-16`.
- AI Governance (12): `AG-01`, `AG-02`, `AG-05`, `AG-07`, `AG-08`, `AG-09`,
  `AG-10`, `AG-12`, `AG-16`, `AG-17`, `AG-18`, `AG-27`.
- Consent & Preferences (10): `CP-02`, `CP-03`, `CP-10`, `CP-12`, `CP-14`,
  `CP-21`, `CP-22`, `CP-23`, `CP-24`, `CP-25`.
- Data Use Governance (8): `DUG-01`, `DUG-03`, `DUG-04`, `DUG-05`, `DUG-06`,
  `DUG-07`, `DUG-08`, `DUG-09`.
- Privacy Automation (12): `PA-03`, `PA-07`, `PA-08`, `PA-09`, `PA-11`,
  `PA-14`, `PA-17`, `PA-20`, `PA-21`, `PA-22`, `PA-23`, `PA-24`.
- Tech Risk & Compliance (16): `TRC-01`, `TRC-03`, `TRC-04`, `TRC-07`,
  `TRC-08`, `TRC-09`, `TRC-12`, `TRC-13`, `TRC-14`, `TRC-15`, `TRC-17`,
  `TRC-16`, `TRC-18`, `TRC-21`, `TRC-22`, `TRC-25`.
- Third-Party Management (10): `TPM-01`, `TPM-02`, `TPM-05`, `TPM-06`,
  `TPM-07`, `TPM-09`, `TPM-11`, `TPM-17`, `TPM-18`, `TPM-22`.

### Partial — material code/product breadth remains (52)

- Shared platform (7): `PX-02`, `PX-04`, `PX-06`, `PX-07`, `PX-10`, `PX-11`,
  `PX-12`.
- AI Governance (6): `AG-04`, `AG-06`, `AG-19`, `AG-20`, `AG-25`, `AG-26`.
- Consent & Preferences (12): `CP-01`, `CP-04`, `CP-05`, `CP-06`, `CP-07`,
  `CP-09`, `CP-11`, `CP-15`, `CP-16`, `CP-17`, `CP-18`, `CP-19`.
- Data Use Governance (1): `DUG-02`.
- Privacy Automation (9): `PA-02`, `PA-05`, `PA-06`, `PA-10`, `PA-12`,
  `PA-13`, `PA-16`, `PA-18`, `PA-19`.
- Tech Risk & Compliance (6): `TRC-02`, `TRC-05`, `TRC-06`, `TRC-10`,
  `TRC-19`, `TRC-20`.
- Third-Party Management (11): `TPM-03`, `TPM-04`, `TPM-08`, `TPM-10`,
  `TPM-12`, `TPM-13`, `TPM-14`, `TPM-15`, `TPM-16`, `TPM-19`, `TPM-21`.

The highest-value code work in this bucket is broader canonical source-history
coverage and independently operated audit verifiers; compiled, packaged native
host integrations, an Adobe Experience Platform query/readback authority and
the remaining consent/AdTech lifecycle; provider breadth beyond the six
discovery readers, broader formats/database values and complete RoPA semantics;
maintained compliance/legal content; broader continuous-evidence collectors;
and third-party feed/content integrations.

### Partial — locally coherent; primary residual is live/operational proof (15)

- Shared platform (1): `PX-08`.
- AI Governance (8): `AG-03`, `AG-11`, `AG-13`, `AG-14`, `AG-15`, `AG-21`,
  `AG-22`, `AG-24`.
- Consent & Preferences (1): `CP-13`.
- Data Use Governance (1): `DUG-10`.
- Privacy Automation (2): `PA-04`, `PA-15`.
- Tech Risk & Compliance (2): `TRC-11`, `TRC-24`.

These rows need consenting provider/customer estates, continuously operated
workloads, calibration, completeness, recovery, concurrency, scale, usability
or relying-party acceptance. Repository tests can strengthen their safety but
cannot independently supply the missing evidence.

### Missing (0)

None in the current working tree.

### External / unverifiable (7)

- `PX-14`: broad certified integration and partner ecosystem.
- `AG-23`: enterprise-scale integration, support and jurisdiction breadth.
- `CP-08`: OneTrust's proprietary tracker-classification corpus.
- `CP-20`: deployed regional consent integrations and operating review.
- `PA-01`: DataGuidance expert network and maintained content corpus.
- `TRC-23`: actual certification/auditor acceptance and equivalent maintained
  content.
- `TPM-20`: live vendor intelligence ecosystem and market scale.

## What “production composition” means here

A production composition is the deployable wiring that turns a domain core
into a real Lightwork module. It includes a shipped installer/config path,
application mount, authenticated tenant-derived API, RBAC, tenant-private
persistence and key authority, readiness that fails closed, maintenance and
recovery seams, audit/integrity/retention controls and the operator UI/Copilot
context where the outcome needs one. It must be usable without injecting a
test-only dependency.

The phrase does **not** mean that an external provider, California authority,
licensed content owner, customer, auditor or regulator has supplied access or
accepted the result. Those are separate live-interoperability and
operated-acceptance stages. Lightwork can build the composition and adapters;
it cannot honestly manufacture third-party credentials, content rights, users
or acceptance evidence.

### The three production compositions called out explicitly

| Product surface | Production code that now ships | Local matrix result | What still requires a real external estate |
|---|---|---|---|
| Governance Agents | Stock tenant/principal/RBAC composition; five packaged agent kinds; exact cross-suite context; deterministic local analysis; internal-intent dispatch; encrypted rotatable one-time token custody; signed receipts; approval, retry, reconciliation, revocation, retention, readiness; strict API; liquid-glass/Copilot workbench | `PX-15` Implemented. `PA-19` and `TPM-21` stay Partial only where their broader marketed outcomes require licensed knowledge/content and commercial critical-event feeds. | Independently operated external adapters/dispatchers, accepted HSM/KMS custody, licensed corpora/feeds, live provider/customer operation and acceptance |
| AI Evidence Analysis | Stock tenant composition; private stores; exact adapter/parser authority; rotation/revocation; PDF/XLSX/text parsing; optional sandboxed OCR/legacy XLS workers; bounded criteria/extraction/analysis; durable result lifecycle; review/release; retention maintenance; strict API/authority API; liquid-glass/Copilot workbench | `TRC-25` Implemented. `TRC-24` is operational-proof Partial because its optional sandbox cannot run on this Windows host. | Attested Bubblewrap/Tesseract/legacy-XLS deployment, representative quality/calibration/privacy/adversarial testing and customer workflow acceptance |
| California DROP | Stock tenant composition; rights-intake bridge; principal/key/broker/request authority; four-eyes changes; bounded scheduler; exact status transitions; retry/reconciliation/revocation/recovery/retention/integrity; strict API; liquid-glass/Copilot workbench | `PA-21` Implemented locally | Live California authority and registered-broker interoperability, production subject matching and delivery, legal/regulator/customer acceptance |

So the answer to “can Lightwork build those production compositions?” is
**yes**: they are now repository code and application wiring. The word
“production” describes a deployable, fail-closed composition; it does not turn
an unperformed external pilot into evidence that the composition has operated
successfully in somebody else's tenant.

## Focused deep dives

### Shared governance inventory is locally Implemented; unified audit remains Partial

Affected rows: `PX-01` is Implemented; `PX-02` and `PX-10` remain Partial.

What is built now:

- A mounted tenant-private shared inventory normalizes a fixed contract across
  AI Governance, Consent & Preferences, Data Use Governance, Privacy
  Automation, Tech Risk & Compliance and Third-Party Management. Stock lazy
  readers preserve exact source revision/head, completeness, retention and
  availability state instead of pretending that a failed or truncated source
  is complete.
- The inventory keeps HMAC references and minimized entity/relationship
  provenance rather than copying raw source actors, identifiers or names. It
  includes CAS/idempotent refresh, bounded query/export, retention/recovery and
  integrity, plus a mounted strict API and liquid-glass/Copilot operator page.
- The separate unified-audit composition ships six canonical readers and local
  re-verifiers, signed page receipts, cursor/CAS leases, generation sweeps,
  retry/replay/revocation and a bounded active-tenant scheduler.
- Unified-audit projection events flow into the same tenant signed audit log as
  the rest of the platform. That audit subsystem already ships independently
  runnable checkpoint publication/verification, signed cross-day anchors,
  retention-aware S3 Object Lock WORM export with exact version/retention
  readback, and SIEM forwarding. A tenant-exact custody runtime now orchestrates
  those primitives after projection runs and through the existing scheduler:
  signed chain/anchor verification, checkpoint publish/verify, optional sealed-
  day WORM export and at-least-once exact-tenant SIEM delivery with durable
  cursor, replay and backoff. Authority drift/rollback, cross-process overlap
  and interrupted-operation state fail closed.
- Focused local evidence includes 8 shared-inventory core/composition/API tests
  and 4 installer/config tests, plus 24 final reader/capture/composition tests
  for unified audit after its earlier overlapping 204-test integrated slice.

Why only `PX-01` moves:

- The normalized inventory is a coherent local shared read model, so the local
  `PX-01` outcome no longer lacks its common inventory/composition surface.
- Each stock reader still covers only one canonical history per suite. Source
  permissions, retention and history determine completeness; the inventory is
  not a replacement system of record and does not create missing source facts.
- `PX-02` remains far short of a maintained 200-plus connector/classification
  estate. `PX-10` still lacks whole-suite history coverage and independently
  attested completeness. Its integrated custody composition was not exercised
  here against a separately operated verifier, real Object Lock account,
  accepted off-host key custodian or live recovery/scale/SIEM estate; automatic
  sealing also remains an explicit operator responsibility.

### Native mobile and OTT consent: substantial source contracts, still Partial

Affected rows: `CP-16`, `CP-17`, `CP-18` and `CP-19` remain material-code
Partial.

What is built now:

- The application scanner parses enrolled Android, FireOS, iOS, tvOS, Roku,
  Tizen and webOS manifests/bundles for permissions, detected dependencies/SDKs
  and Apple privacy manifests under a reviewed integrity/retention lifecycle.
- The native core, public/admin APIs and production composition bind an
  application to an existing public-consent client, admit a device key only
  through fresh injected platform attestation, and require an exact Ed25519-
  signed tenant/application/platform/device/client/publication/policy/profile
  envelope with a monotonic counter. Receipts, idempotency, offline renewal,
  ambiguous outcomes, key rotation/revocation and fresh application-authority
  refresh fail closed.
- Eight closed operations cover configuration, profile bootstrap/current
  preferences, preference/signal writes, profile link/revoke and rights
  handoff. Swift, Kotlin, JavaScript and BrightScript source adapters map iOS/
  tvOS, Android/Fire TV, Tizen/webOS and Roku; TCF/GPP remains an opaque value
  from an injected certified verifier rather than an invented local codec.
- The protocol/security slice passed 13 focused Python tests and 5 Node
  adapter/schema tests with targeted Ruff clean. A separate final composition,
  admin API/page, app mount, wizard/config, Copilot and visibility slice passed
  274 Python tests plus 2 Node admin-client tests; targeted Ruff was clean.

Why all four rows remain Partial:

- The reference adapters were not compiled, packaged, notarized/store-reviewed
  or exercised on physical devices. Production hosts still owe accepted
  platform attestation, hardware/OS key and profile custody, transactional
  outbox/transport integration, receipt-root custody and native accessibility/
  localization operation.
- Application registration is create-only: authority changes require a new
  globally unique application reference, while device authority refresh uses
  fresh attestation and exact revision CAS. The bounded rate limiter is per
  process and requires upstream fleet-wide enforcement; standards/profile/
  rights callbacks remain explicit deployment injections and required missing
  callbacks fail readiness.
- Manifest analysis is not runtime IDFA/permission collection. No certified
  local TCF/GPP CMP, downstream AdTech integration, complete household/device
  ownership lifecycle, estate retrieval/deletion, retrieved-data delivery or
  physical-device/recovery/scale/customer acceptance exists.

### Privacy discovery: named readers, target and scheduler now ship; breadth remains Partial

Affected rows: `PX-02`, `PA-05` and `PA-06` remain material-code Partial;
`PA-04` is operational-proof Partial.

What is built now:

- Six closed stock readers cover exact enrolled AWS S3, Azure Blob and Google
  Cloud Storage text objects, Snowflake column metadata, ServiceNow CMDB and
  Microsoft Entra asset metadata. The production transport resolves named
  credentials only for a bounded request, enforces the enrolled HTTPS origin
  and enterprise egress policy, pins DNS, refuses redirects and limits bytes.
- The mounted tenant-exact composition pins configuration and provider version,
  creates coordinators lazily and connects only fresh independently approved
  findings to the existing durable outbox and a built-in tenant-private digest-
  only data-map target. Admin work-list, worker, reconcile and maintenance
  routes expose ambiguous work without exposing provider bodies.
- A global-plus-tenant opt-in scheduler now bounds the active-tenant set,
  workers and reconciliation, uses durable leases with in-process and
  cross-process exclusion, applies jitter/backoff, recovers interrupted work
  and exposes lifecycle/API/UI/Copilot status.
- The stable slice passed 31 distinct focused tests: 14 adapter, 6 data-map
  target, 9 composition and 2 operational API tests. Ruff passed all 9 touched
  core/dashboard/test files.

Why no row becomes Implemented:

- Only explicitly enrolled resources are read, provider permissions may hide
  assets and pagination is not a transactional estate snapshot. The managed
  scheduler closes the local background-composition gap, but no credentials,
  live provider estate, outage/recovery/concurrency/scale exercise or customer
  acceptance was available.
- Object content is limited to supported text media and Snowflake coverage is
  metadata, not table values. PDF/OCR/archive/proprietary formats, maintained
  risk taxonomy and broad connector breadth remain absent.
- The digest target is evidence input, not a complete Article 30 RoPA. It does
  not infer purpose, transfers, data subjects, recipients or vendors, performs
  no provider write/deletion, and an enqueue failure still requires manual
  approved-review redelivery.

`PA-04` therefore moves from the material-code subgroup to operational-proof
Partial; its remaining gap is live continuous-estate evidence. The broader
connector, format and semantic outcomes in `PX-02`, `PA-05` and `PA-06` still
need material product breadth.

### Document custody, assessment evidence and executive reporting: locally complete, externally unaccepted

Affected rows: `PX-16`, `PA-08`, `PA-24`, `TPM-06`, `TPM-18`.

What is built now:

- An encrypted tenant-private document vault provides exact revision/digest
  authority, opaque one-time delivery grants, administration tokens, incident
  attachment projections, streamed downloads, revocation/replay/expiry,
  retention/tombstones, recovery, integrity and digest-only audit.
- Evidence-assisted assessment auto-population converts independently released
  AI Evidence Analysis records into deterministic, cited proposals pinned to
  exact assessment/template/criteria/mapping authority. A different human
  reviews a selected subset before CAS application to questions that remain
  empty; missing and indeterminate evidence stay explicit.
- Third-party executive reporting resolves selected lifecycle, intelligence,
  Contract/Engagement and collaboration records twice around generation, binds
  exact revision/digest manifests and truthful empty/unavailable states, then
  emits bounded deterministic PDF 1.4 bytes. Tenant/principal/role are
  server-derived, rendered branding is closed plain text/colors, optional
  brand-asset reference/digest fields are non-rendered provenance metadata, and
  downloads are principal-bound, one-time and revocable.
- Each path has a mounted strict API, production configuration/composition,
  liquid-glass page and Copilot context, plus focused lifecycle, isolation,
  idempotency/CAS, retention and integrity coverage.

What is not claimed:

- The local vault is not accepted external object-store, malware-scanning,
  backup, HSM/KMS or relying-workflow custody.
- Evidence proposals are not vendor attestations, certifications, legal
  conclusions or proof of parser/model quality, source completeness or live
  assessment usability.
- The PDF path does not resolve, fetch or embed asset/logo bytes and accepts no
  arbitrary HTML, CSS or URLs. Optional brand-asset reference/digest fields are
  bound as metadata only. No complete live portfolio, migrated-data, executive/
  customer, recovery or scale acceptance was inferred.

These residuals remain deployment and acceptance work, but they no longer
represent missing local code for the five normalized outcomes.

### Governance Agents: packaged local path is Implemented; two suite outcomes stay Partial

Affected rows: `PX-15`, `PA-19`, `TPM-21`.

What is built now:

- Five packaged kinds: breach response, privacy risk assessment, program
  Copilot, knowledgebase governance and third-party risk.
- Mounted strict API and liquid-glass/Copilot workbench.
- A stock production composition with tenant/principal/RBAC binding,
  deterministic offline analysis, internal-intent dispatch, signed receipt
  verification, an encrypted rotatable one-time execution-token vault,
  replay/recovery/reconciliation/revocation and explicit readiness.
- Immutable policies, bounded transient context, closed findings/actions,
  independent human approval, quotas, retention and integrity checks.
- Authoritative tenant-exact projections over privacy, assessments, governed
  documents/evidence analysis and third-party lifecycle, Contract/Engagement,
  intelligence and monitor histories. Projections bind source revision, digest,
  completeness and availability, reject double-read drift and omit raw/private
  source content.

Current classification:

- `PX-15` is Implemented for the coherent local packaged-agent/common-context
  outcome.
- `PA-19` and `TPM-21` remain Partial because the marketed outcomes also
  advertise equivalent maintained/licensed Knowledgebase content, commercial
  critical-event feeds and live operated external adapters that are not bundled.
- External mutations remain deliberately disabled; off-host HSM/KMS custody,
  independently operated adapters/dispatchers and customer operation are not
  accepted.

What can be built next is concrete: licensed-corpus enrollment and revocation,
additional named source adapters, and a production external dispatcher with
ambiguous-outcome recovery. Live users, feeds and acceptance remain a separate
stage.

### AI Evidence Analysis: result lifecycle is implemented; hardened optional parsers remain operationally unproven

Affected rows: `TRC-25` is Implemented locally; `TRC-24` is Partial pending
deployment and operation of its OS-enforced parser sandbox.

What is built now:

- Mounted workbench/API and authority-administration API.
- Stock tenant production composition with private stores, config/readiness,
  authority enrollment/rotation/revocation and retention maintenance.
- Bounded PDF, XLSX, JPEG/PNG, legacy BIFF8 XLS and UTF-8 text intake;
  immutable criteria;
  attested deterministic extraction/analysis; cited facts, summaries,
  transparent justification components, gaps/remediation, provenance, human
  release and digest-only persistence.
- Optional OCR and legacy XLS workers require an exact path/SHA/version-pinned
  POSIX Bubblewrap launcher, pass bounded no-input readiness probes and run in
  an OS-enforced network namespace with pre-exec resource limits and
  whole-process-group termination.
- Authority binds both parser implementations plus Tesseract, language data,
  runtime libraries, Pillow, `xlrd` and `olefile`. The conservative BIFF8
  allowlist refuses formulas, macros, encryption, external references, error
  cells and unknown records.
- A durable result object with exact artifact/criteria/adapter pins and no raw
  artifact content, which closes the locally owned `TRC-25` outcome.

What remains unproven:

- OCR is text extraction, not general visual-semantic image understanding.
- The optional parsers are default-off. This Windows host fails closed because
  Bubblewrap is unavailable; Docker was installed but its daemon was
  unavailable. No live OS-network-denied execution or production readiness was
  demonstrated here.
- Representative adversarial extraction quality, privacy review, score
  calibration and customer workflow acceptance have not been performed.

The parser implementation now exists, so this is no longer a material-code
gap. `TRC-24` remains operational-proof Partial because neither installing
dependencies nor synthetic tests prove the sandbox, extraction quality or
customer workflow on this host.

### California DROP: locally Implemented, externally unaccepted

Affected row: `PA-21`.

What is built now:

- Strict API, liquid-glass/Copilot workbench, authority registry, scheduler and
  stock production composition.
- `DataDeletion` and `OptOut` creation through the existing privacy-rights
  intake; evidence-bound `PENDING`, `EXEMPTED`, `DELETED`, `OPTED_OUT` and
  `NOT_FOUND` transitions.
- Independent broker, subject and request authority; opaque/digest-only
  persistence; replay, key failure, rotation/revocation, reconciliation,
  retention and integrity controls.
- A shipped config path that can report ready without test-only app-state
  injection.

What is not claimed:

- No consenting registered broker or California authority was connected.
- Subject matching, production delivery, legal decisions, regulator acceptance,
  retry/recovery at external boundaries and customer operation remain external
  evidence.

This distinction is why `PA-21` is Implemented locally without being described
as a live or regulator-accepted DROP service.

### Identity governance: production composition exists, but the outcome remains Partial

Affected row: `AG-20`.

What is built now:

- A mounted lazy per-request/per-tenant production composition and closed
  default-off installer/config table.
- Tenant-exact authority/federation/capability/Purview-asset registry with CAS,
  integrity and retention; cross-tenant rejection and fresh runtime creation.
- Exact provider/tenant/DNS-pin/no-redirect transport gating, capability
  mapping, conditional-access decisions and authority-gated Purview writeback.
- Tenant-specific scheduler composition, exact tenant/provider/revision/digest
  AI-asset binding and idempotent, leased, retryable durable dynamic-risk and
  alert outcomes with dead-letter/replay/revocation and minimized receipts.
- A fixed-authority Entra/Okta federation and Purview transport boundary for
  deployment-owned credentials and provider endpoints.

Why `AG-20` remains Partial:

- No real Entra/Okta/Purview tenant, credentials or trusted enrollment was
  available for operation; those provider authorities remain deployment-owned.
- Conditional access is a governed evidence decision, not proof that a provider
  policy was mutated or enforced.
- Live federation, conditional-access enforcement, writeback interoperability,
  outage/recovery and provider/customer acceptance remain untested.

The remaining stage is deployment configuration plus a consenting test tenant
for live federation, enforcement, writeback, outage/recovery and acceptance.

### 2026-09-01 consent and privacy-automation closure

The final local closure changed three row statuses and moved three other rows
between the two Partial subgroups. The row-level matrix remains authoritative;
this table makes the engineering boundary easier to inspect.

| Row | Current local code | Classification and remaining boundary |
|---|---|---|
| `CP-01` | The web Trust Center now combines exact-origin publication, consent history, volunteered profile data, independently authorized rights summaries, signed export, reviewed brand assets, custom-domain authority and the current-link identity lifecycle. | Material-code Partial: a complete UCPM outcome still needs operated communications, compiled/deployed native channels, production IdP/JWKS custody and continuous cross-channel operation. |
| `CP-03` | Encrypted/versioned PNG/JPEG custody, independent review/activation/revocation, exact custom-domain challenge and DNS/TLS verification, Host-independent projection and digest-bound safe public asset delivery now ship. | Implemented locally. Live DNS/TLS/CDN issuance, external fulfillment, accessibility/brand review and customer acceptance remain deployment evidence. |
| `CP-04` | Salesforce, Marketo and Snowflake have closed stock write/readback contracts, live metadata-authority checks where applicable, exact-origin/DNS/schema/credential pins and a destination-authority re-read before commit. | Material-code Partial because Adobe Experience Platform still requires a deployment-specific asynchronous Query Service/dataset authority; all four destinations also lack live provider acceptance. |
| `CP-06` | Branded structured web renderers, localized rule/templates, reviewed media authority and exact mobile bundle/platform contracts exist. | Material-code Partial until native hosts are compiled/deployed, IP geolocation is operated and accessibility/localization/property acceptance is completed. |
| `CP-13` | Exact OIDC/PKCE pairing plus a migration-safe identity overlay now provide legacy backfill, immutable lifecycle history, exact-CAS unlink, fresh-evidence relink/account transition, pairing-proof revocation and interrupted-operation recovery without caller-selected public identity. | Operational-proof Partial: the material local lifecycle gap is closed; production IdP/JWKS custody, multi-device transport and live recovery/scale/customer evidence remain. |
| `PA-04` | Six exact-source discovery readers, tenant composition, approved-review outbox, digest-only target and a bounded global-plus-tenant scheduler with durable leases, cross-process exclusion, recovery and jitter/backoff now ship. | Operational-proof Partial: the local continuous runtime exists, but no credentialed continuously operated estate or accepted completeness/recovery/scale evidence exists. |
| `PA-11` | The stock notice-publication composition and scheduler bind exact tenant/notice/property/rule/resource authority, double-resolve consent previews, derive CAS revisions, re-read release/target authority and perform verified rollback/retry/recovery. | Implemented locally. It intentionally does not mutate a website, tag manager, provider or SDK deployment; live rollout and legal/privacy/accessibility/customer acceptance remain external operation. |
| `PA-14` | Stock Graph, Google Workspace, Salesforce and ServiceNow retrieval/deletion/verification contracts include pinned transport, atomic pre-mutation reservation, ambiguity reconciliation, legal-hold/config/credential rechecks, encrypted exact-target custody and governed-vault retrieval. | Implemented locally. Coverage is limited to enrolled sources; live credentials, estate completeness, accepted KMS custody and provider/legal/customer operation remain external evidence. |
| `PA-15` | A stock rights-delivery composition now binds exact orchestration case/package authority, independent public subject identity, deployment-owned reauthentication proof, digest/key pins and governed-vault CAS/revocation checks before grant, consume and byte return. | Operational-proof Partial: real IdP/reauth mapping, accepted KMS/HSM and browser/customer recovery/load evidence remain. A post-claim/pre-response crash stays intentionally fail-closed pending a separately reviewed one-time-delivery protocol. |
| `PX-10` | Projection and custody now run as one exact-tenant composition with chain/anchor verification, checkpoint publish/verify, optional sealed-day WORM export, at-least-once SIEM cursor/replay/backoff, cross-process exclusion and authority-drift rejection. | Material-code Partial because one canonical history per suite is not whole-suite audit coverage or independently attested completeness; external verifier/Object Lock/key-custodian/SIEM operation is also unaccepted. |

## How to read the counts safely

- `75 Implemented` means 75 local outcomes satisfy this matrix's repository
  bar; it does not mean 75 OneTrust services have been cloned or commercially
  accepted.
- `67 Partial` combines material build work with outcomes whose last mile is a
  real external estate. The two subgroups above make that distinction explicit.
- `0 Missing` means every normalized row has at least a coherent partial
  implementation; it does not mean every material component is
  present.
- `7 External / unverifiable` should not be converted into engineering tickets
  that promise a code-only result. They need content rights, providers,
  customers, auditors, certification bodies or market evidence.

This breakdown and the matrix describe the current local working tree. They do
not by themselves establish that the changes are committed, pushed, merged or
deployed.
