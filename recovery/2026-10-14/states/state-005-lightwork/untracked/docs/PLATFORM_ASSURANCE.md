# Platform, model, and product assurance

Lightwork's platform-assurance layer turns production checks, AI-asset and
agent-estate metadata, evaluation evidence, release SBOMs, and product-security
decisions into reviewable, content-bound records. It is intentionally
conservative:

- Collection is not approval.
- A successful check is not certification.
- A deterministic model-risk screen is not a legal classification.
- A readiness result is not authority to promote or deploy a DGM candidate.
- A vulnerability feed match is not a legal reportability decision.
- Recording a submission receipt is not an automated filing capability.

The operator console is at `/security/assurance`; the REST surface is below
`/api/v1/security/assurance`.

## Configuration

All new surfaces are independently off by default:

```toml
[evidence_graph]
enable = true

[evidence_factory]
enable = true

[agent_trust_auditor]
enable = true
stale_after_seconds = 604800

[cra_ops]
enable = true
# Ed25519 keys owned by trusted filing connectors; private keys never enter Lightwork.
filing_receipt_trusted_public_keys = ["<64-hex-character-public-key>"]

[model_risk_assurance]
enable = true
# A separate, fail-closed interlock over DGM/self-improvement promotion.
gate_promotions = true

[authority]
mode = "strict"

[connectors.certification]
certified_only = true
allow_uncertified = false
```

The installer asks separately about each capability. `enable = true` turns on
the Model Risk & AI Assurance Officer; promotion behavior remains compatible
unless `gate_promotions = true` is also explicit. A malformed or unreadable
global configuration does not enable any capability, and malformed policy for
an explicitly enabled promotion gate denies promotion rather than bypassing it.

## Continuous evidence graph

`maverick.evidence_graph` stores bounded metadata and cryptographic bindings,
not raw telemetry or credentials. Nodes are stable per source authority
revision, carry explicit typed links, and public ingestion always begins in
`pending_review`. Secret detection covers every caller-controlled string,
including values under otherwise innocuous attribute names. An inherited
approval is available only to source-specific projections that re-read a
persisted governed record and bind its schema, revision, and exact human
decision digest. Graph reads, coverage calculations, and signed pack rendering
revalidate that exact source authority. A rejected, missing, invalid, or newer
source revision makes the projection `authority_stale` without deleting its
historical review; a later source approval produces a new revision-bound node.
Only current, human-approved evidence contributes to control coverage; expired
or authority-stale evidence remains visible as stale.

Supported projections currently include reviewed Security/GRC evidence,
finalized production-evidence campaigns, Agent Trust Auditor observations,
reviewed CRA product/VEX metadata, and Model Risk asset/evaluation evidence.
Every mutation uses revision compare-and-swap and the governed audit outbox.

## Production Evidence Factory v1

`maverick.evidence_factory` creates a campaign manifest bound to:

- Git commit and immutable container image digest;
- deployment identity and a redacted configuration hash;
- migration and dependency versions;
- declared SLO, RTO, and RPO;
- bounded collector results and sanitized artifact hashes; and
- named witness statements for operator-attested checks and drills.

Collectors have explicit offline, integration, live, operator-attested, or
drill modes and report `PASS`, `FAIL`, `ERROR`, `NOT_RUN`, or
`INSUFFICIENT_DATA`. Each collector callback runs across a killable spawned-process
boundary with a bounded response, a supervisory timeout, and deep secret
redaction. Timeout terminates the child before the campaign can proceed.
This is process isolation, not an OS sandbox: a callback may still perform an
effect before it is terminated, so v1 refuses collectors declared destructive
and operators must treat collector code as trusted. Missing witnesses produce
`NOT_RUN`; they never become an implicit pass.

Collection and drill ownership is persisted as a CAS-fenced claim with an
explicit lease. The lease covers the declared callback timeouts plus bounded
process and result-sanitization settlement time. Shared governed-record
backends use Postgres time for creation and recovery decisions, avoiding
cross-replica application-clock skew; local compatibility mode uses the local
clock. An operator may explicitly abort a campaign or drill, while stale
recovery aborts orphaned drills and reopens only the campaign whose persisted
lease expired. Either transition changes the revision and attempt identity, so
a late owner cannot commit. Abort fences persistence but cannot synchronously
kill a callback running in another host process, which is another reason the v1
destructive-collector prohibition remains mandatory.

Finalization passes the canonical manifest to the existing proof-pack signer.
If cryptography or a signing key is unavailable, that signer intentionally
returns an unsigned manifest and the campaign records `signature_status` as
`unsigned`; callers must not treat it as signed evidence. When the evidence
graph is enabled, the finalized campaign is projected as `pending_review`, not
automatically approved, with explicit signed/unsigned metadata.

The factory proves what its collectors actually ran. Real managed-database or
Redis failover, KMS custody, IdP behavior, WORM retention, penetration testing,
and customer SLO/ROI claims still require the relevant external environment and
a human witness.

## Agent Trust Auditor

`maverick.agent_trust_auditor` maintains separate declared and observed views of
the agent estate. Local projection reads the active trust registry, configured
MCP specifications, the built-in core-tool catalog, and configured provider
names. Catalog entries are labelled `catalogued`, never `available`, unless the
caller supplies the live `ToolRegistry`; only registered and currently exposed
tools then receive `configured` or `available` status. Projection does not read
the installable MCP catalog and never calls a trust-registry mutation API.

OpenTelemetry ingestion is allowlist-only. Prompts, messages, bodies, tool
arguments, tool results, arbitrary attributes, credentials, and URLs are
dropped before persistence. Stable source-namespaced identities prevent two
collectors from silently merging unrelated assets.

Deterministic findings cover:

- unowned agents, MCP servers, or providers;
- unpinned external identity;
- allow-all or declaration-exceeding tool authority;
- stale observations; and
- observed tools absent from the declaration.

Every finding cites the exact evidence-graph observation IDs that support it.
Human owners, purpose, risk tier, allowed tools, and lifecycle are held in a
separate CAS declaration store. Discovery cannot grant or widen authority.
Only declarations in the internally consistent `approved` or `production`
lifecycle confer owner/tool policy; proposed, rejected, suspended, and retired
records remain visible but cannot suppress findings. Evidence packs bind
declarations, observations, findings, and graph nodes to an Ed25519 audit
receipt. Verification requires an externally provisioned `key_id -> Ed25519
public key` registry. The public key disclosed inside a pack is never a trust
anchor and must exactly match the selected registry entry.

## Model Risk & AI Assurance Officer

`maverick.model_risk_assurance` keeps six authority families separate:
observed AI assets, accountable-human declarations, assurance evidence,
incidents, human decisions, and deployment lineage. The inventory accepts
bounded observations for models, agents, tools, datasets, and providers. A
stable source identity plus an exact version digest distinguishes a changed
artifact from an unchanged name. Observations cannot approve themselves, and a
new observed revision makes an earlier declaration stale until a human reviews
the new binding.

Declarations record owner, purpose, intended use, operational risk tier, risk
context, lifecycle, and a dated EU AI Act assertion. EU category/applicability
defaults to `undetermined`; a non-default assertion requires a
named human reviewer, rationale, `as_of` date, and source reference. The
deterministic screen may flag declared high-impact, prohibited-use,
transparency, human-oversight, and third-party signals, but it never converts
those inputs into a legal verdict. Approval is refused while applicability is
undetermined or when the human assertion is `prohibited`.

The generated mappings are versioned, advisory metadata:

- [NIST AI RMF 1.0](https://www.nist.gov/itl/ai-risk-management-framework)
  is mapped to GOVERN, MAP, MEASURE, and MANAGE and is identified as under
  revision;
- [ISO/IEC 42001:2023](https://www.iso.org/standard/42001) is identified as an
  AI management-system requirements standard; and
- [Regulation (EU) 2024/1689](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)
  is mapping context whose applicability and current implementation timeline
  require human legal review.

These mappings are not licensed standards text, a conformity assessment,
certification, or legal advice.

Evidence is immutable, content-digested, expiring, and separately reviewed.
Supported kinds cover evaluation, red-team testing, drift monitoring, model
cards, data and vendor assessments, incident analysis, and human-oversight
tests. Each record binds its scope and artifact digest, and may also bind the
evaluator digest. The officer recomputes deterministic findings from current
authority: undeclared or stale assets, missing legal-applicability review,
missing or stale evaluation/red-team evidence, missing third-party assessment,
failed or inconclusive drift monitoring, unresolved incidents, and divergence
between observed bytes and governed deployment lineage. Repeated observation
and evidence ingestion supplies continuous monitoring; the module does not
pretend that a connector or provider was queried unless a trusted integration
actually supplied that observation.

Humans approve or reject declarations and evidence, decide legal
applicability, accept a specific non-critical finding for a bounded time, and
authorize deployment. A risk acceptance is bound to the exact finding digest;
changed or expired findings reopen automatically, and critical findings cannot
be accepted for promotion. Assurance packs bind the current inventory,
declaration, evidence, findings, incidents, decisions, and deployment lineage;
signature integrity does not prove the underlying assertion true.

### Exact-payload DGM promotion interlock

When both `enable = true` and `gate_promotions = true`, the common
self-improvement controller adds a fail-closed model-risk gate. It computes the
approval request from the candidate's actual payload and checks the resulting
SHA-256 commitment; a stale or self-reported candidate digest cannot replace
that computation. Human promotion authority binds all of the following:

- candidate id, promotion rung, and exact payload digest;
- current artifact observation and version digest;
- current approved human declaration and non-undetermined, non-prohibited EU
  applicability assertion;
- current evaluator observation and digest;
- current, approved, passing, unexpired evaluation and red-team evidence over
  the exact artifact bytes; and
- current evidence-graph authority. Deterministic findings and any exact-digest
  risk acceptances are re-evaluated separately at the gate.

The gate re-reads every bound CAS revision at promotion time. A changed
artifact, evaluator, declaration, evidence record, graph projection, review,
expiry, or revocation denies. New or changed unaccepted high/critical findings
also deny; critical findings are never risk-acceptable. Recording deployment
lineage reruns the same gate and requires the expected predecessor digest,
preventing a promotion approval from floating to different bytes or a different
release.
This is a governance interlock, not evidence that a model is safe, compliant,
or suitable for a particular use.

### Standalone boundary

`demo/model-risk-ai-assurance-officer/` is a self-contained reduced SKU. It
stores user-declared metadata and unsigned human attestations in a local JSON
file, runs deterministic findings, and produces a non-executing DGM
promotion-readiness report. It does not import Lightwork, discover provider
accounts, collect live telemetry, use the governed evidence graph or signed
audit, authorize a promotion, deploy, roll back, change policy, or call an
external system. Use the integrated officer when tenant authority, signed
evidence, shared approvals, continuous provider integrations, deployment
lineage, or an enforcing DGM gate is required.

## Mandatory authority plane

`[authority] mode = "strict"` removes unsigned internal bus messages and shared
or synthetic principals from authority-bearing paths. Enterprise mode forces
strict behavior even if configuration requests compatibility. Informational
agent messages are signed, replay checked, and receive a deny-all, effect-free
grant; they cannot replace an agent's active capability. Delegations remain
signed, scoped, and attenuated. Strict authority also forces capability
enforcement, so the signed `delegate_to_agent` path remains present even when
`[capabilities] enforce` was omitted or set false.

Strict federation requires pinned signing identity. Strict A2A, MCP, and gRPC
require attributable per-agent credentials. Governed Postgres now supplies the
shared handoff issuer state, database-time lease decisions, and globally atomic
nonce/replay claims; compatibility deployments retain process-local defaults.
`HandoffAuthority` accepts a non-exporting Ed25519 signer callback, so a provider
can keep private material inside KMS/HSM custody, but Lightwork does not yet ship
a vendor-specific KMS adapter. Multi-host qualification still requires that
external signer custody plus witnessed rotation, revocation, partition, and
recovery drills; it no longer requires inventing a separate nonce store.

For MCP stdio, strict mode requires `[mcp] stdio_principal` (or
`MAVERICK_MCP_STDIO_PRINCIPAL`) to name an active inbound Agent Trust entry.
Startup fails before reading client input when that identity is missing or
invalid. One immutable identity/capability snapshot is propagated through
tools, resources, subscriptions, prompts, and task creation; queued execution
rechecks current policy and can only narrow or revoke the frozen grant.

## Effect-safe external actions

Tools may declare an `EffectSpec` describing whether an operation is a read or
write, its external system and operation, native idempotency semantics, typed
status resolver, and optional compensation callback. The declaration is
mandatory for every write-capable governed connector in enterprise/strict
posture; registry assembly fails closed when metadata is missing or incorrectly
declares a write surface as read-only. The first-party connector inventory is a
regression-tested boundary. Standard, non-governed legacy tools remain
compatible. The kernel derives a stable logical key from tenant, principal,
run, node, connector, and operation; the request digest is checked separately
so key reuse with different content is rejected.

Selecting `[authority] mode = "strict"` activates this effect-governance floor
even when `[governed_connectors]` is omitted or an environment override tries
to disable it. Compat community deployments retain the historical opt-in. A
strict deployment must select the Postgres effect backend and application
at-rest encryption before any governed write can reach its provider.

The original effect advances through:

```text
PREPARED -> SENT -> COMMITTED | ABORTED | INDETERMINATE
                         COMMITTED -> COMPENSATED
```

Compensation has a separate durable lifecycle:

```text
NOT_STARTED -> COMPENSATING -> COMPENSATED
                    |
                    +-> INDETERMINATE
```

A callback error is quarantined as `INDETERMINATE`; a process death remains
`COMPENSATING`. Neither condition is replayed automatically. After the active
claim staleness window, a typed provider resolver may prove that the undo was
not started (which safely reopens it), confirm compensation with its own
receipt, or leave it indeterminate. Original and compensation receipts remain
separate.

Generic REST receives a kernel-owned `Idempotency-Key` that model-supplied
headers cannot override. It is classified as keyed correlation, not proven
native idempotency. HTTP `202 Accepted` and all refused redirects remain
`INDETERMINATE`; neither can advance a durable write to `COMMITTED`. Ambiguous
writes are quarantined instead of automatically retried.

Provider response bodies are returned only on the first live execution. Durable
effect state and governed COMMIT lineage retain a SHA-256 digest plus a bounded,
internally-authored non-content summary, never the arbitrary body. Provider
receipts are bounded and secret-sanitized. Enterprise/strict execution proves
at-rest encryption can seal and reopen effect payloads before approval or
provider invocation; an explicit encryption opt-out refuses the write. Opening
a legacy effect database scrubs old raw results and seals safe payloads so typed
reconciliation remains available through the posture upgrade. Cached success
returns transaction/digest evidence rather than replaying the original body.

The effect ledger has a tenant-scoped shared Postgres backend with forced RLS,
exact schema/policy verification, and sealed receipts. Provider-specific status
resolvers and compensation operations, live provider idempotency qualification,
and witnessed crash/partition exercises still remain before claiming multi-host
exactly-once effect handling.

## Certified connector core

The initial certification catalog contains eight read-only security telemetry
connectors: CloudTrail, GuardDuty, Entra ID, Sentinel, Okta, Splunk, GitHub Audit
and Security, and Elastic. Each has versioned auth, scope, host, port, API,
pagination, cursor, rate-limit, deadline, response-size, and deterministic
contract-test declarations.

`contract-tested` is not `live-certified`. Live status requires a fresh,
trusted Ed25519 v2 receipt produced by a bounded metadata-only probe and an
exact `TransportBinding` supplied independently by trusted startup. The signed
binding covers connector, canonical endpoint and port, API/auth mode, the exact
manifest read-scope set (extra write or admin scope fails), canonical tenant,
and a deterministic digest of the registered callable implementation. The
digest excludes defaults, closure values, partial arguments, and instance
fields so credentials never enter the receipt. Registration recomputes it; a
different callable, tenant, endpoint, mode, API version, or scope set fails
closed. Legacy unbound v1 receipts remain verifiable artifacts at the
`contract-tested` tier but can never authorize a certified-only runtime.

The production dashboard registration hook accepts the tenant-scoped receipt,
trusted public key, operator-pinned hosts, and binding alongside the callable,
then injects that complete bundle into each fresh `ConnectorFactoryRegistry`.
With `certified_only = true`, connector factories refuse to expose tools
without that exact bundle. A configured break-glass exception still requires
an individual actor, a reason whose digest is audited, and an audit sink that
accepts the record. Actual enterprise deployment posture is a floor: an
embedding cannot disable certified-only enforcement by injecting
`enterprise=False`.

## CRA Product Security Officer

`maverick.cra_ops` provides a governed product and release inventory, bounded
CycloneDX/SPDX normalization, reviewed VEX records, vulnerability/incident
cases, report packets, statutory clock projections, and signed product-security
packs.

Product applicability starts `undetermined`. A release must have an immutable,
normalised SBOM before a case can be opened, and the exact release/SBOM binding
is checked again before reportability and drafting. A case starts with no active
CRA clock. An accountable human must record both product applicability and case
reportability before clocks can appear.

For a human-determined reportable actively exploited vulnerability, the module
projects:

- early warning: awareness + 24 hours;
- full vulnerability notification: awareness + 72 hours; and
- final report: corrective or mitigating measure availability + 14 calendar
  days.

For a human-determined reportable severe incident, it projects:

- early warning: awareness + 24 hours;
- full incident notification: awareness + 72 hours; and
- final report: one calendar month after the recorded full-notification
  submission.

These rules are metadata from [Regulation (EU) 2024/2847 Article
14](https://eur-lex.europa.eu/eli/reg/2024/2847/oj), whose reporting duties
[apply from 11 September
2026](https://digital-strategy.ec.europa.eu/en/policies/cra-reporting). Human
legal review is required. Cases whose awareness predates that date remain
`not_yet_effective` with no statutory clock unless counsel persists an explicit,
rationaled effective-date override through the governed API.

Each report packet is bound to the exact product, release artifact, case,
content, and evidence-node set. Every supplied evidence node must exist in the
active tenant, be current and human-approved, and explicitly identify the case,
release, or product it supports. The packet binds each node's id, revision,
payload digest, and review digest. An approved packet cannot be edited.

The compatibility `submission-receipt` endpoint records only an
`unverified_local_attestation`: it neither marks the report submitted nor starts
a clock. Submission authority requires a v1 external filing-receipt envelope
signed by a connector key pinned in deployment-global `[cra_ops]` policy. The
signature binds the report id, exact approved packet digest, stage, case,
tenant, external timestamp, channel, and receipt id. Request bodies cannot
supply a trust key. Only a verified envelope marks the report submitted and can
anchor the severe-incident final-report clock. There is no network submission
function in `cra_ops`.

SBOM ingestion accepts bounded CycloneDX 1.4-1.6 and SPDX 2.2-2.3 JSON. It
rejects duplicate JSON keys, duplicate identities, broken dependency references,
and hash/license/external-reference arrays beyond their explicit limits rather
than silently truncating them. It retains the raw document hash without storing
raw content and labels dependency edges as a declared graph unless the producer
supplies the explicit Lightwork resolution assertion.

## Current HA boundary

Several bounded consistency families now have transactionally shared Postgres
backends: the world model/approvals, integrated Security/CRA/Agent Trust/Model
Risk and evidence governance, A2A claims, effect receipts, flow definitions/releases/runs,
the promotion/rollback journal, platform and environment hunter records plus
response/lease coordination, canonical audit append, selected replay and
revocation stores, dashboard settings and role
overrides, configuration proposals, fleet roster, desired adapter
release/health observations, calibration freeze state, holdout query budgets,
form submissions, tenant namespace/lifecycle authority, tenant policy and
wrapped-key authority, tenant usage and spend reservations, identity
federation/session/role state, event/SaaS trigger registries, deployment-global
public-form routing and epoch-bound submission claims, learned-tool admission,
verifier confirmation claims, and desired verifier/correction/DGM artifact
releases plus per-node health observations. The explicitly selected
self-harness state is shared separately. A securely configured Redis queue adds
network job dispatch and poll leases; otherwise that family remains local.
Holdout query budgets require a valid deployment-stable
`self_harness.holdout_authority_id`, and harness learning requires
`self_harness.store = "world"`. Tenant cap, usage, reservation, and
settlement changes share one per-tenant CAS document;
namespace claims use a persisted provisioning saga, and unknown/incomplete
claims remain denied. Database time governs daily windows and bounded replay/
late-settlement retention. Shared deletion preserves tombstones and refuses a
misleading single-replica synchronous purge.
The holdout budget is reported shared only when governed Postgres is selected
and every replica declares the same valid
`[self_harness] holdout_authority_id`; a missing or malformed id is an explicit
readiness blocker rather than a path-derived fallback.
These adapters use canonical tenant binding, atomic CAS or claims, bounded
application-sealed payloads, a least-privilege non-`BYPASSRLS` role, forced RLS,
and per-transaction schema and policy verification. Local compatibility remains
for a standard single-replica deployment; shared selection refuses to hide
nonempty legacy state. Every replica must resolve the same KMS/key authority.

This is not the whole control plane. Enterprise or multi-replica admission
remains blocked while audit private-key custody, channel-federation delivery
and replay, in-process agent-bus delivery and acknowledgement, work-discovery
enrollment/leases/revocation epochs, or any other active authority is local or
unregistered. Identity families are shared only after the deployment policy
digest and external SCIM bearer-set digest bind successfully; failure keeps
them local and blocks admission. Artifact bytes remain node-local, but cannot
authorize serving without an exact shared desired digest and healthy node
observation. The readiness inventory distinguishes formerly over-broad entries:
PBAC decisions are derived from frozen,
content-digested inputs; transient evaluator slots and callback-only learning
rollouts are not claimed as built-in stores (their explicit capability seams
reactivate local blockers when a deployment backend is added); supply-chain
pins and public-form routing appear only when their feature combination may be
active; and configured offline approver keys plus the fleet encryption key are
immutable external trust inputs that remain readiness blockers until their
exact deployment digests verify. Inactive evidence is emitted in
`inactive_stores` and cannot override a store independently discovered by an
integration.

When both event triggers and tenant-by-user isolation are active, the anonymous
token-to-tenant registry is a deployment-global, digest-only governed document.
Route lifecycle and epoch-bound submission claims linearize rotation/revocation
against appends; raw bearer tokens never enter that registry. Unknown or
inactive links remain indistinguishable, and unavailable or ambiguous authority
fails closed. Do not lift the Helm replica guard
until every authority and replay boundary has a tested shared backend, positive
live service probes, and witnessed contention, failover, partition, and restore
drills. Deterministic transaction doubles and migration tests are repository
evidence; a live-service exercise is deployment evidence, not a repo claim.
