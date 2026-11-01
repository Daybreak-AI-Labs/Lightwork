# Roadmap

This page lists the themes the maintainers are working on and the near-term
work under each. It describes direction, not a schedule: items carry no dates,
and they move as priorities change or contributors take them on.

How it is maintained:

- **Shipped work lives in [`FEATURES.md`](./FEATURES.md).** When an item ships
  it moves there and comes off this page; nothing belongs in both.
- **Recorded decisions**, including proposals that were declined, are in
  [`docs/specs/`](./specs/) (`*-decision.md` and
  [`jit-consideration.md`](./specs/jit-consideration.md)).
- **Significant changes go through an RFC** in [`docs/rfcs/`](./rfcs/), as
  described in [`GOVERNANCE.md`](../GOVERNANCE.md). Where an item below has an
  RFC or design document, it is linked.
- **To propose an item**, open a
  [feature request](https://github.com/Daybreak-AI-Labs/Lightwork/issues/new/choose)
  describing the problem it solves. Any item here can be picked up by a
  contributor; say so on the related issue, or open one, before starting so
  work is not duplicated.

## Platform and scale

- **Multi-replica control plane.** Postgres centralizes the world model, but
  workflow releases and runs, A2A durable claims, audit and learning ledgers,
  and tenant-local policy files are not yet transactionally shared, so the
  dashboard/serve tier runs as one replica. See the gap table in the
  [deployment playbook](./enterprise/deployment-playbook.md#10-gap-status).
- **Postgres tenancy runbook.** Row-level security, the `maverick.tenant`
  session setting and connection pooling have shipped. The operator guidance in
  [`deploy/postgres/README.md`](../deploy/postgres/README.md) still needs the
  last step: connect the application as a non-superuser role and apply RLS as
  the table owner.
- **Queue dispatch at scale.** The `QueueDispatcher` (arq) is wired; it still
  needs to be run against a real Redis broker with an out-of-process worker
  pool.
- **Unified dispatch.** Make the queue path the default execution contract,
  with the in-process dispatcher as one backend of it, so behavior is
  identical with or without a broker
  ([2.0 RFC proposal](./proposals/2.0-rfc.md)).
- **Sandbox pool.** The explicit `sandbox/pool.py` API transfers only
  immutable policy into a fresh backend; automatic cross-run parking and its
  startup savings are not yet qualified
  ([sandbox policy handoff](./operations/sandbox-pool.md)).

## Safety and governance

- **Trained shield members.** The shield's ensemble framework and explainable
  reason codes ship with heuristic injection, exfiltration and PII members.
  Trained small-model members need training data and GPU runs.
- **Confidential compute attestation.** `maverick confidential-compute`
  detects and reports the platform posture; attestation needs SEV-SNP or TDX
  hardware.
- **Plugin runtime isolation.** Granted plugins run in-process with load-time
  permission grants only; there is no runtime syscall or network sandbox for
  them yet.
- **Per-tenant MCP gating.** A shared MCP bearer grants full tool access;
  per-tenant gating is available only through the opt-in Agent Trust Plane
  (`[agent_trust] enforce = true`).
- **Firecracker backend.** The local Firecracker scaffold is experimental:
  its exec path does not yet mount the workspace, and it is not a supported
  execution backend.
- **Live identity-provider certification.** SAML SSO and SCIM provisioning are
  tested against the protocol suites in this repository but not yet against a
  live identity provider ([security overview](./enterprise/security-overview.md)).

## Model improvement

The governance and evaluation substrate has shipped; no trained specialist
weights exist yet. The planned sequence (first real-data corpus, base-model
bakeoff, per-tenant specialist adapters, canary serving) is described in
[Governed model improvement](./MODEL_IMPROVEMENT_PLATFORM.md#status-and-planned-work)
and [`specs/model-improvement-decision.md`](./specs/model-improvement-decision.md).
Fleet learning across tenants is explicitly deferred.

## Ecosystem and interop

- **Live-service validation.** The IRC, glasses/wearable and LangChain
  connectors and the queue/KMS backends have protocol and logic unit tests.
  End-to-end runs against a live IRC server, a real Even Realities G2 device,
  a LangChain install and a Redis broker are still to do.
- **Voice channel v2 adapters.** The streaming-ASR and barge-in session layer
  (`maverick_channels.streaming_voice`) has shipped; real streaming-ASR and
  playback adapters still need to be plugged into its seams.
- **Language-bindings measurement.** See [Decisions](#decisions) below.

## Packaging and release

- **First published release.** The release workflow builds single-file
  binaries, a container image, standalone demo archives, checksums, SBOMs and
  Sigstore material, but no release has been published yet. PyPI publishing of
  the eight-package cohort stays disabled until every package name and its
  trusted publisher are verified. See [`DISTRIBUTION.md`](./DISTRIBUTION.md).
- **Platform code signing.** Release executables carry Sigstore signatures but
  are not yet Apple-notarized or Authenticode-signed.
- **Native installers.** The Tauri desktop shell and the Windows MSI candidate
  still need a compiled payload, release attachment, platform signing and
  real-machine validation before they are attached to releases.
- **Update delivery.** `release_update` verifies, plans, applies and rolls back
  signed bundles; the concrete per-target install hooks, the release-feed
  publisher and channel configuration are not built yet
  ([product operations](./enterprise/product-operations.md)).
- **Lightwork 2.0.** [RFC 0001](./rfcs/0001-maverick-2.0.md) proposes config
  schema v2 (with the renames carried by `maverick migrate`), an async channel
  SDK with a structured reply object, moving the long tail of SaaS connectors
  out of the core package (per the
  [breadth-vs-depth decision](./specs/breadth-vs-depth-decision.md)), and a
  documented streaming tool contract. The release gate is
  [`release-checklist-2.0.md`](./release-checklist-2.0.md), and the upgrade
  path is [`migration-2.0.md`](./migration-2.0.md). The plugin API v1
  compatibility window (deprecation `plugins.api_v1`, removal in 0.3.0) closes
  on the same path.

## User experience

- **Trust registry in the dashboard.** Peers and keys are managed with
  `maverick trust`; a dashboard view of the registry is not yet available.
- **Desktop updates.** Wire a Tauri updater to `release_update.apply_bundle`.
- **Support ticket bridge.** Route `support_bundle.ticket_summary()` output into
  a ticketing system, and close the loop from a ticket to a hotfix release.

## Decisions

### Language bindings

Lightwork is exposed to other languages **over MCP**, not by porting
`maverick-core`. Thin API clients port well; opinionated frameworks do not, and
Lightwork is the second kind. The MCP server and quickstarts for the top five
target languages — **TypeScript**, **Go**, **Rust**, **C#** and **Java** — have
shipped (see [`docs/clients/`](./clients/)).

The opt-in analytics on MCP-client language headers ship with a consent step in
the setup wizard (off by default). The decision gate: if more than **15%** of
active installs drive Lightwork from non-Python MCP clients, fund **one** thin
TypeScript client package. Under 15%, the MCP surface remains the answer.

Constraints that hold either way: no port of `maverick-core` to a second
language; sandbox backends stay Python process glue and are not part of the
cross-language contract; the multi-agent topology (orchestrator, proposer,
verifier, revisor, reflector) stays in Python. Other languages drive Lightwork;
they do not re-implement it.

### Other recorded decisions

The general in-kernel trajectory-learning loop is parked until real volume and
operator demand exist ([decision](./specs/learning-substrate-decision.md));
A2A's Agent Card is adopted in place of the homegrown ACD format
([decision](./specs/a2a-vs-acd-decision.md));
breadth is frozen in favor of depth
([decision](./specs/breadth-vs-depth-decision.md)); the hot path is not
compiled with mypyc or Cython ([consideration](./specs/jit-consideration.md));
Redis is not used as the primary world-model store
([decision](./specs/redis-world-model-decision.md)); and browser anti-bot
evasion is declined ([decision](./specs/anti-bot-evasion-decision.md)).

## Standing constraints

Every item above keeps the project's house rules (see
[`CONTRIBUTING.md`](../CONTRIBUTING.md#house-rules)): self-hosting stays the
default, with a self-hostable path for anything that would otherwise need a
hosted service; the kernel runs without the shield; budget caps are never
bypassed; all shell execution goes through `sandbox.exec()`; and a new
capability arrives with a configuration knob and a setup-wizard step.
