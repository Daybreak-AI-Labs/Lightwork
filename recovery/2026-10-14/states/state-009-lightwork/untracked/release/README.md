# Release authority

This directory separates facts the repository can prove from facts only a
registry owner can prove. Public publishing is intentionally fail closed until
both sets of facts agree.

## Current state

`release-state.json` is the reviewed machine-readable snapshot. At the time of
the snapshot:

- the source and eight-package Python cohort were at `0.1.7`;
- the latest canonical repository tag was `v0.1.6` at
  `86760df9882bdb48692a9776058e8013eed05300`;
- GitHub returned no Releases, so neither `0.1.7` nor an older tag is a
  supported production release;
- all eight PyPI JSON endpoints and the npm endpoint for
  `@maverick/plugin-sdk` returned HTTP 404.
- the independent `maverick-native` PyPI and `mvk-scan-wasm` npm distributions
  are experimental, excluded from the production surface, and explicitly
  `publish_authorized: false`; no registry ownership is claimed for either.

The registry 404 item is only a public observation, preserved in
`registry-observation-2026-08-03.json`. A 404 does **not** establish that a name
is available, reserved, controlled by Daybreak AI Labs, or connected to a
Trusted Publisher.

Run the deterministic local reconciliation with:

```bash
python -I scripts/verify_release_state.py --root .
```

To compare a fresh read-only GitHub observation, first export the primary
source response and then pass it as an explicit verifier input:

```bash
gh release list --repo Daybreak-AI-Labs/Lightwork --limit 100 \
  --json tagName,isDraft,isPrerelease > github-releases.json
python -I scripts/verify_release_state.py --root . \
  --github-releases-json github-releases.json
```

## Production support matrix

`production-support-matrix.json` publishes the first profile's exact admitted,
qualification-required, evaluation-only, and excluded combinations. It keeps
repository coverage separate from product support: for example, PostgreSQL 16
is the pinned live-test target, but an exact managed offering is not qualified
until its TLS, migration, RLS, backup, restore, and failover evidence is bound
to the release. The same rule applies to Kubernetes, Helm, gVisor, vLLM,
Vault, identity, and browser versions.

Verify the matrix against the source manifest, chart, package metadata, and
pinned repository targets with:

```bash
python -I scripts/verify_production_support_matrix.py --root .
```

See `docs/enterprise/production-support-matrix.md` for the human-readable
operator view. Release state binds the matrix, and the release workflow stages
and signs the exact versioned JSON bytes.

## Production API contract

`production-api-contract.json` and `production-openapi-schema.json` are one
indivisible release pair. The contract binds the full production-visible
OpenAPI document by raw and canonical SHA-256, all 69 unique operation IDs,
the startup drift gate, the API lifecycle, and the minimum deprecation window.
The release workflow stages both exact byte sequences and downstream release
verification rejects a missing, substituted, or digest-mismatched half.

```bash
python -I scripts/verify_production_api_contract.py --root .
```

This is a source and release-candidate compatibility boundary. It does not
claim that an API version has been publicly released or that external client
journeys have been qualified. An intentional schema change requires a reviewed
contract-version decision and migration/release documentation before the
generated pair is refreshed.

## Production onboarding and QA assurance contracts

`production-identity-posture-contract.json` is the provider-neutral source
boundary for the exact-one-provider identity/RBAC posture. It fixes the OIDC
and trusted-proxy mapping inputs, canonical digest coverage, credential
exclusions, static RBAC inputs, and the separate live-requalification boundary
for mutable authorization rosters. Release state binds its exact path, and the
release workflow stages, checksums, and signs the byte-identical versioned
asset; that source contract does not claim that a customer identity provider or
mutable role assignment has been qualified.

`production-onboarding-contract.json` defines 11 required deployment checks
covering provider/model identity, authentication, isolation, safety, signer
custody, Postgres/migrations, connectivity/TLS, secrets, quotas, observability,
and operational evidence. The ordinary setup wizard is not production
authority: omitted, expired, or malformed live observations remain explicit
`not_checked`/failed blockers.

A `pass` is accepted only from canonical evidence-manifest bytes signed by an
out-of-band pinned Ed25519 authority. The manifest must bind the exact contract
digest, release-state digest, nonzero trusted source revision, freshness
window, out-of-band pinned deployment ID and configuration digest, and unique
evidence for every named requirement. A plausible URN and hex digest, an
evidence-carried target/key, duplicated artifact, or substituted
contract/release state/deployment cannot self-attest readiness. Duplicate
evidence is rejected within one manifest; cross-evaluation nonce consumption
remains the responsibility of an external deployment-authority ledger.

`production-qa-assurance.json` maps the admitted parser/protocol fuzz,
hostile-input, critical durability/crash-injection, and state-machine property
inventories to exact source and test symbols and to the unconditional pytest
job. It also binds the release-critical supply-chain
steps and records SOC 2, ISO 27001, ISO 42001, incident response,
vulnerability management, and disclosure as repository processes only. It
does not claim hosted-run results, certification, a staffed rotation, a
completed drill, or live penetration evidence.

```bash
python -I scripts/verify_production_onboarding_contract.py --root .
python -I scripts/verify_production_qa_assurance.py --root .
```

Both files are signed release assets, but an operator still must run the
separate `maverick production-onboarding-readiness` evaluator with current,
digest-bearing deployment observations. A repository contract alone cannot
make the deployed environment ready.

## Data, operations, and runtime source contracts

The release also signs three exact source-derived boundaries:

- `production-data-boundary.json` inventories admitted data flows and retains
  explicit live approval/qualification requirements for processors, residency,
  retention, encryption, access, deletion, and recovery evidence.
- `production-operations-contract.json` binds source-owned telemetry, alerts,
  incident scenarios, emergency controls, and internal support targets while
  remaining explicitly live-operations-unqualified.
- `production-runtime-contract.json` is generated byte-for-byte from the
  production runtime code and keeps live-cluster network proof required,
  public egress denied, arbitrary command execution excluded, and the root
  filesystem read-only.

```bash
python -I scripts/verify_production_data_boundary.py --root .
python -I scripts/verify_production_operations_contract.py --root .
python -I scripts/verify_production_runtime_contract.py --root .
```

These assets make the reviewed source boundary portable and tamper-evident;
they do not replace live deployment qualification, staffing, drills, or
customer-specific legal/operational decisions.

## Production code inventory and claim ledger

`production-code-inventory.json` is generated from the checkout rather than
maintained as marketing copy. It inventories workspace packages, declared test
files/functions, constructed dashboard routes and their production policy,
server-wired channels, MCP tools, connector identifiers, ports, installer
questions, feature surfaces, and historical proof freshness. Regenerate only
as an intentional reviewed change; the verifier refuses stale counts, identities,
routes, hashes, or documentation assertions:

```bash
python -I scripts/verify_production_code_inventory.py --root . --write
python -I scripts/verify_production_code_inventory.py --root .
```

`claim-evidence-ledger.json` binds narrow buyer-facing statements to hashed
evidence and binds every reviewed buyer document by its whole-file SHA-256.
Only `repository_verified` and explicitly limited `source_inventory_only` rows
may be buyer-claimable. Live, external, historical, draft, and not-claimed rows
must remain false. The checked-in ledger is deliberately draft:

```bash
python -I scripts/verify_claim_evidence_ledger.py --root . --write
python -I scripts/verify_claim_evidence_ledger.py --root .
python -I scripts/verify_claim_evidence_ledger.py --root . --release-candidate
```

The last command additionally requires a current external approval reference
and two distinct approvers. A new top-level enterprise document, changed buyer
copy, changed evidence, or removed production disclaimer invalidates the ledger.
See `docs/enterprise/claim-evidence-ledger.md`.

## Dependency failure contract

`dependency-failure-contract.json` binds the first profile's Postgres, Vault,
vLLM, DNS, ingress, identity, audit, SIEM, and state-volume behavior, while
explicitly excluding vector stores, runtime object stores, and connectors. It
also records the important negative facts: liveness is dependency-independent,
and readiness does not make live provider, IdP, DNS, or public-certificate
requests. Every non-excluded row remains `live_drill_required`; repository tests
alone cannot turn it into an availability claim.

```bash
python -I scripts/verify_dependency_failure_contract.py --root .
```

See `docs/enterprise/dependency-failure-contract.md` for the operator matrix and
the exact evidence that must be retained from each outage/recovery exercise.
Release state binds and the release workflow signs the versioned JSON bytes.

## Migration, tenant-quota, and secret-rotation contracts

The first profile now has three additional fail-closed source contracts:

- `production-migration-contract.json` binds the checksum-locked migration
  ladder, adjacent/bootstrap/current state machine, separate owner credentials,
  authorization and evidence freshness, drain/backup/restore markers,
  transactional DDL/RLS behavior, Helm hook isolation, and recovery semantics.
- `tenant-quota-contract.json` binds one required monotonic policy generation,
  exactly one complete JSON or scalar policy source, six ceilings, PostgreSQL
  authority, durable reservations/settlement/expiry, and explicit coverage
  limits.
- `secret-rotation-contract.json` binds the exact secret inventory, external
  custody, pod-recreation trigger, ordered overlap/rollback/revocation phases,
  and per-secret validation.

```bash
python -I scripts/verify_production_migration_contract.py --root .
python -I scripts/verify_tenant_quota_contract.py --root .
python -I scripts/verify_secret_rotation_contract.py --root .
```

These commands prove repository contracts, not completed production drills.
Release change control separately requires representative managed-PostgreSQL
migration/restore, quota saturation/failover, and provider-specific secret
rotation evidence. See the corresponding enterprise runbooks.

## Model admission gate

`production-model-policy.json` binds the first production profile to exact,
reviewed model-serving facts rather than treating a provider name as approval.
Each admitted model must name its canonical `provider:model` specification,
artifact SHA-256, credential-free HTTPS endpoint, serving region, retention
posture, mounted-file credential source, spend ceiling and period, and exact
failover chain. The policy is bound to the production-surface manifest with an
explicit `utf8-lf-sha256` digest (strict UTF-8 with line endings normalized to
LF), expires within one year, and requires distinct model-risk and security
owners.

Verify the checked-in contract with:

```bash
python -I scripts/verify_production_model_policy.py --root .
```

The checked-in policy is intentionally `draft` with no model admitted. Before
a release candidate can pass, evidence owners must replace the placeholders
with observed deployment facts, obtain the required independent approvals,
set a bounded validity window, and retain external proof of the artifact,
endpoint, locality, retention, and credential-custody assertions. Then run:

```bash
python -I scripts/verify_production_model_policy.py --root . --release-candidate
```

Repository validation cannot prove what a remote or self-hosted model server
actually loaded. Runtime admission independently checks the deployed model,
endpoint, artifact assertion, region, retention mode, custody source, cap, and
failover chain against the exact policy bytes embedded in the production image.

## Service-objective gate

`service-objectives.json` is the machine-readable contract for availability,
API latency, recovery time and point, durability, severity-one response, and
off-host audit-anchor lag. Draft or null targets are not commitments. Approval
requires numeric targets and measurement sources for all objectives, an
error-budget freeze and incident-review policy, immutable evidence references,
separate service-owner and site-reliability approvers, and a bounded expiry.

Verify the draft structure locally with:

```bash
python -I scripts/verify_service_objectives.py --root .
```

The release-candidate form fails closed until business and reliability owners
approve real targets backed by measured production-like exercises:

```bash
python -I scripts/verify_service_objectives.py --root . --release-candidate
```

## Pytest-exception gate (QA-03)

`qa/pytest-exceptions.json` is the reviewed inventory for every committed
pytest skip, conditional skip, import-based skip, and expected failure. The
production surface includes `feature:pytest-exception-governance`; release
state binds the exact inventory path; and change control protects the
inventory, verifier, runbook, focused tests, and read-only CI enforcement lane.
The verifier rejects unreviewed or moved exceptions, stale source
fingerprints, missing owners/reasons, expired or non-strict xfails, and
production-critical dependency exceptions without an unconditional CI lane.

Run the deterministic local gate with:

```bash
python -I scripts/verify_pytest_exceptions.py --root .
```

The 2026-08-09 local snapshot validates 444 exceptions, including 227 in
production-critical test paths, 164 records bound to enforced dependency
lanes, and two strict temporary xfails. QA-03 is recorded as satisfied for the
repository-level implementation and adverse regressions only. That does not
claim a hosted CI run or make the overall readiness packet ready: it remains
`draft`, with the other unresolved gates and external release evidence still
blocking production. The release workflow re-runs the verifier and stages the
exact inventory as `lightwork-pytest-exceptions-<version>.json`; downstream
release verification requires that signed, checksummed asset.

## Postgres data-boundary gates (DATA-09 and DATA-17)

The repository now contains the code-level controls and adverse regressions for
the first profile's Postgres tenant boundary and sealed-field migration. Root
and parent-derived child tables receive exact enabled-and-forced RLS policies;
the runtime role must be a non-owning `NOSUPERUSER NOBYPASSRLS` role; and
`maverick tenant rls-apply --confirm-drained` installs the complete policy set
atomically from a separate migration-owner job. Production readiness rechecks
every policy through the fixed `postgres_rls_verified` signal, and a failed or
unavailable signal removes the instance from readiness.

`maverick encryption migrate-postgres` is the separate whole-database path for
inventorying and rewriting every reviewed sealed column across all tenants. It
requires the exact schema and table-owning migration role, authenticates
existing ciphertext in tenant context, refuses unscoped or tampered data,
restores forced RLS and immutable triggers, and commits only after a second
zero-plaintext pass. Production startup separately requires at-rest and
per-tenant encryption, strict reads, and refuses visible plaintext. The local
`tenant delete --purge` command refuses shared Postgres rather than issuing a
misleading complete-erasure receipt.

DATA-09 and DATA-17 remain `pending`, not satisfied. Repository-local tests
prove implementation and adverse behavior, but this worktree did not run a
live Postgres migration or produce a deployed RLS/readiness report. Before an
exact approved release can become ready, retain both external evidence records
required by `production-change-control-policy.json`:

- a signed exact-release RLS apply report, least-privilege role evidence,
  enabled/forced root-and-child policy inventory, deployed readiness result,
  and live cross-tenant read/write adverse report;
- signed pre/post whole-database encryption inventories showing zero plaintext
  with strict mode enabled, bound schema/column-manifest identities, a managed
  snapshot or PITR marker, and successful restore plus key-recovery drills.

The executable migration sequences and custody boundaries are documented in
`deploy/postgres/README.md`, `docs/encryption.md`, and the enterprise deployment
playbook. None of those runbook instructions are evidence that a deployment
actually completed them.

## Production change-control gate

`production-change-control-policy.json` is the machine-readable minimum for
the repository-controlled portion of production change management. It binds
the GHCR staging write, release draft, signed Python-evidence attachment, PyPI
publication, and final GitHub Release/container promotion jobs to their
authority jobs, exact `needs` dependencies, allowed write permissions, and the
`production-release`, `pypi`, or `npm` GitHub Environment. It also requires
explicit CODEOWNERS routing for the policy, verifiers, release state, readiness
evidence, service objectives, the QA-03 contract, the read-only CI workflow,
the DATA-09/DATA-17 implementations, regressions, and runbooks, and all three
release workflows.

Verify those source-controlled invariants with:

```bash
python -I scripts/verify_production_change_control.py --root .
```

The command deliberately reports
`external_evidence_status=required-not-verified-by-this-tool`. Repository files
cannot prove GitHub administrator settings or that a particular deployment was
independently approved. Before production release, retain external evidence for
all twelve policy entries: the `main` branch/ruleset configuration, the protected
`v*` and `native-v*` tag rulesets, and settings plus an approval/deployment record for the
`production-release`, `pypi`, and `npm` Environments, plus the exact-release
Postgres RLS deployment and whole-database encryption-migration records, plus
production migration, secret rotation, tenant quota/capacity, and claim-ledger
approval records. The
evidence must demonstrate the
requirements listed in the policy, including independent approval, prevention
of self-review, blocking checks, and allowed deployment refs. Do not treat
CODEOWNERS entries, a green verifier result, or an Environment name in YAML as
proof that those GitHub settings are configured.

The downstream `publish.yml` workflow validates its own default-branch source
revision before signature attachment, registry publication, or finalization
can mutate state. GHCR staging, draft creation, evidence attachment, and final
publication/container promotion use `production-release`; PyPI publication
uses `pypi`. The native npm path names `npm` but remains statically disabled and
contains no token-based publish command. Removing an authority dependency,
expanding job write permissions, enabling a disabled native registry job, or
changing an Environment name makes the verifier fail.

## Registry ownership gate

Public release jobs must not infer namespace authority from availability
checks or a successful first upload. Before enabling registry publication, an
authorized registry owner must do all of the following outside this repository:

1. Reserve or otherwise establish control of every Python cohort name and the
   npm `@maverick/plugin-sdk` namespace/package. Before any separate native
   release is enabled, also establish control of PyPI `maverick-native` and npm
   `mvk-scan-wasm`.
2. For each eight-package cohort PyPI project, configure the GitHub Actions Trusted Publisher as
   owner `Daybreak-AI-Labs`, repository `Lightwork`, workflow `publish.yml`,
   environment `pypi`. If `maverick-native` is later authorized, its reviewed
   identity is workflow `native.yml`, environment `pypi`.
3. Keep the npm SDK publication unauthorized while it is experimental. If it is
   promoted later, configure an npm Trusted Publisher and change its reviewed
   release policy before any npm publish job is enabled. The same rule applies
   to both experimental native distributions; their jobs are statically
   disabled and the token-based npm mutation was removed.
4. Copy `namespace-ownership-attestation.template.json` to
   `namespace-ownership-attestation.json`, record the authenticated registry
   account and an auditable internal evidence reference for each namespace,
   set every ownership result to `true`, and set the eight PyPI publisher
   results to `true`. Use a validity window no longer than 31 days.
5. Add the reviewed signer's public key as
   `namespace-attestors.allowed-signers`, then sign the exact JSON bytes:

   ```bash
   ssh-keygen -Y sign -f PATH_TO_REVIEWED_PRIVATE_KEY \
     -n lightwork-release-namespace-v1 \
     release/namespace-ownership-attestation.json
   ```

   The matching allowed-signers identity must be
   `lightwork-release-namespace-owner`. Never commit the private key.

6. Verify the production gate locally:

   ```bash
   python -I scripts/verify_namespace_ownership.py --root .
   ```

The gate verifies the SSH signature before parsing the attestation, requires an
exact package inventory, rejects expired statements, limits validity to 31
days, and checks the exact repository/workflow/environment publisher identity.
Missing files, malformed evidence, false flags, extra packages, and operational
signature failures all block publication.

PyPI documents the required GitHub owner/repository/workflow/environment
identity in its [Trusted Publisher setup guide](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).
npm documents the equivalent OIDC identity and supported actions in its
[Trusted Publishing guide](https://docs.npmjs.com/trusted-publishers/).

## Release transition

The current source is deliberately marked `unreleased`, the reviewed
production-surface manifest is deliberately release-ineligible, and the
readiness, model-policy, and service-objective contracts are drafts. A public
publication attempt must remain blocked until evidence owners update the full
governance packet to the exact assessed source content and the signed
namespace gate passes. Creating a tag or setting a repository variable is not
sufficient authority.

The readiness verifier inventories 219 gates (37 P0, 157 P1, and 25 P2).
Every packet, including a draft, must explicitly enumerate all 194 P0/P1 gates;
an omitted gate is invalid rather than silently treated as pending. A ready
release must record each one as `satisfied` or, only for a conditional gate,
`excluded`; each record must resolve to implementation, regression,
adverse-result, and owner/runbook evidence in the checked-out source tree. It
also binds a canonical, commit-independent identity of tracked-present and
nonignored untracked source content. The recorded Git revision is diagnostic
but must resolve to an ancestor whose non-generated source is byte-equivalent
to the tagged checkout. The exact source identity, assessed revision,
production surface, gate records, and update timestamp are covered by a
domain-separated Ed25519 approval signature. The signature is verified only
against a public key pinned in `production-readiness-trust.json`; there is no
environment-key fallback or automatic key generation, and the repository
intentionally configures no production readiness key. The approval envelope
and readiness packet are source-excluded to avoid a cryptographic fixed point,
but the packet must exactly match its committed `HEAD` bytes. A single valid
signature proves only approval by that pinned key, not reviewer quorum or
independent custody; those controls and any retroactive key-revocation policy
remain external release requirements. Its production-surface binding uses
`utf8-lf-sha256`, so LF and CRLF checkouts attest the same reviewed UTF-8
content without making arbitrary byte normalization part of the contract. The
following command shows the current unresolved count:

```bash
python -I scripts/verify_production_readiness.py --root .
```

The Release workflow repeats that release-candidate check before any build,
uses `Dockerfile.production` for the promoted GHCR digest, and publishes the
exact production surface, readiness evidence, model policy, service objectives,
support matrix, dependency failure contract, change-control policy,
pytest-exception inventory, production code inventory, claim/evidence ledger,
migration contract, secret-rotation contract, tenant-quota contract, the exact
API/OpenAPI pair, onboarding contract, QA assurance map, data boundary,
operations contract, runtime contract, and release-state bytes under versioned
`lightwork-*.json` names. Every asset is included in the release checksum file,
receives its own keyless Sigstore signature/certificate, and is parsed again by
downstream exact-asset verification. The evidence must bind the exact manifest
digest and canonical source-content identity. A manifest marked ineligible, a draft contract, an
omitted P0/P1 gate, or an unresolved blocker cannot be staged. The
draft-creation and finalization jobs both use the `production-release` GitHub
Environment, and the PyPI job uses `pypi`. Administrators must configure the
independent reviewers, self-review prevention, and protected deployment refs
required by the machine-readable change-control policy, then retain the
external evidence described above.

After a release is published, refresh the GitHub observation and security
support block in the same reviewed change that advances the cohort. The
verifier rejects version drift, a stale support block, a different latest-tag
commit, unrecorded noncanonical release-like tags, and unclassified
distribution surfaces.
