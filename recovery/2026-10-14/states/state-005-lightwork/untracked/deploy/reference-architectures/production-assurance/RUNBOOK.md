# Production assurance operations runbook

This runbook preserves the single-writer boundary and produces reviewable
evidence. Adapt commands to the volume snapshot, identity, and secret
systems approved by your organization. A successful command is not itself a
service-level or compliance claim.

## 1. Release and readiness gate

For every release, record the change ticket, source commit, verified OCI
digest, signature/attestation verifier and trust anchor, SBOM digest, config
file SHA-256, migration versions, PVC snapshot identifier, encryption-key
version/identifier, and named approver. Confirm:

- the Deployment requests exactly one replica and no HPA;
- the running `imageID` contains the approved digest;
- `/livez` and `/readyz` succeed through the authenticated/operator path;
- the Postgres schema/RLS, application seal/unseal, and migration-governance
  probes succeed under the non-superuser runtime role;
- the deployment's `maverick.authority-readiness/v1` report is retained with
  its exact shared, local, inactive, and unregistered family inventories and
  service-probe results;
- the audit chain verifies with the separately controlled public key;
- the evidence-factory deployment ID, Git commit, image digest, and config hash
  match the change record;
- `[model_risk_assurance] enable = true` and `gate_promotions = true` match the
  approved deployment policy; and
- every in-scope production model/agent and its evaluator has a current
  version-digested observation, reviewed declaration, and named human owner.

Do not translate a failed, missing, timed-out, or `INSUFFICIENT_DATA` collector
into a pass. Roll back to a previously verified digest when the release gate
fails, and retain both the failure evidence and the rollback record.

## 2. Backup procedure

The shared Postgres stores and residual local authorities on the PVC do not
share one application transaction. To obtain a conservative, quiescent planned
backup pair:

1. Stop new goals, webhook intake, approvals, and connector actions at the
   external gateway. Record the quiesce time and responsible operator.
2. Wait for in-flight work to finish or explicitly record each abandoned or
   indeterminate effect. Never replay an ambiguous external effect merely
   because the local receipt is absent.
3. Scale the single control-plane Deployment to zero. Verify that no dashboard
   or serve pod still owns the PVC.
4. Capture the exact `maverick.authority-readiness/v1` report for the quiesced
   deployment. Create a Postgres snapshot or point-in-time recovery
   marker that is provider-consistent, then a CSI VolumeSnapshot (or
   storage-provider equivalent)
   of the quiesced PVC. Record both provider IDs, timestamps, database
   WAL/recovery point, storage-encryption key versions, retention policies, and
   immutable hashes where exposed. The database snapshot must cover **every**
   family that report marks shared on Postgres, rather than a hard-coded subset.
   The PVC snapshot must cover every report-local family plus attachments and
   any other current files. Bind the report and both snapshot IDs into one
   recovery-set record.
5. Resume exactly one control-plane replica and run readiness, Postgres
   schema/RLS, audit-chain, migration, and effect-reconciliation checks before
   reopening ingress.

Do not substitute an uncoordinated live database/PVC copy for the quiesced
pair; Postgres and the local stores do not share one transaction. Back up the
external signing/encryption keys in a separate trust system and bind only their
key identifiers/versions to the snapshot record. Never copy plaintext keys
into the evidence pack. ConfigMap source and secret definitions must be
recoverable through their separate approved configuration/secret-management
systems.

## 3. Isolated restore drill

Never test a restore over the live environment.

1. Create an isolated namespace, network boundary, Postgres restore target,
   Secret set, and PVC. Block outbound connector effects until reconciliation
   is complete.
2. Restore the bound Postgres recovery point and PVC snapshot plus the exact
   matching encryption/signing key versions through the secret manager. Deploy
   the exact OCI digest and non-secret config fingerprint recorded with that
   recovery set.
3. Run migrations only through the governed migration process. Regenerate the
   `maverick.authority-readiness/v1` report and compare every machine-reported
   shared and local family with the inventory bound to the recovery set; missing,
   newly local, unregistered, or backend-changed authority fails the drill. Then
   check Postgres integrity, forced RLS, encryption round-trip, tenant isolation,
   audit-chain continuity, pending approvals, effect receipts, evidence records,
   the residual local stores, and the latest signed packs.
4. Reconcile every effect in `SENT` or `INDETERMINATE` state against the remote
   provider before permitting a retry. Exercise representative read-only goals;
   keep destructive drills outside Evidence Factory v1.
5. Measure recovery time from the declared start point and data loss against a
   documented source-of-truth watermark. A named operator witness records the
   method and exceptions. Only compare the measurements to declared RTO/RPO;
   never infer them from a successful restore alone.
6. Destroy the isolated copy under the organization's data-destruction policy
   after evidence retention requirements are met.

## 4. Signed evidence campaign

Use the authenticated assurance API under
`/api/v1/security/assurance/evidence-factory`. The dashboard bearer token is a
secret; inject it into the HTTP client from a secret store and prevent shell
history or debug logs from printing it.

Start a campaign with the exact reviewed commit, digest, deployment ID, declared
SLOs, RTO/RPO, and mapped control IDs. The create response returns a campaign
ID and revision. Collect using that revision, then finalize using the new
revision returned by collection. The default collectors run readiness,
audit-chain, migration-governance, and reliability checks. They store redacted
structured results and sanitized artifact hashes, not raw backup contents.

If a collector host exits after reserving a campaign, read the current campaign
revision and persisted lease before using the admin-only `.../{id}/recover`
endpoint with a recorded reason. Recovery refuses a live lease, uses Postgres
time in this shared reference, fences the old revision/attempt, aborts orphaned
drills, and reopens the campaign for an explicit retry. Use `.../{id}/abort`
for a deliberate terminal cancellation. The corresponding drill recover/abort
endpoints are available under `evidence-factory/drills`; abort is a persistence
fence and does not claim to kill a callback still running on another host.

For recovery exercises, add the named operator witness through the
Evidence Factory library/integration path; v1 intentionally refuses destructive
collectors. Finalization attempts to sign the canonical manifest and projects a
node into the evidence graph as `pending_review`. Stop the production-assurance
workflow if `signature_status` is not `signed`; an unsigned finalized manifest
is reviewable output, not signed production evidence.
A different authorized human must:

1. verify the manifest canonical hash and Ed25519 signature against the
   separately distributed trusted public key;
2. compare image, commit, config, migration, dependency, and deployment
   provenance to the release record;
3. inspect every explicit collector status and the backup/restore witness;
4. approve, reject, or later revoke the evidence-graph node with a rationale.

Only current, unexpired, human-approved evidence contributes to graph coverage.
After successful trusted-key verification, archive the signed manifest,
public-key identifier, backup/restore metadata, and review decision in the
external evidence repository. Lightwork's copies in Postgres and on the single
PVC cannot be their own independent backup.

## 5. Model-risk change and DGM promotion gate

Treat inventory refresh, evidence review, change authorization, and deployment
lineage as separate authority steps. For every model, agent, tool, dataset, or
provider change in scope:

1. Record the new observation from a trusted integration, including the exact
   artifact/version digest. Confirm the stable source identity is correct. An
   observation is a fact claim, not approval, and a new revision invalidates an
   older declaration binding.
2. Have the accountable human review owner, purpose, intended use, operational
   risk tier, and the dated EU AI Act category/applicability assertion. The
   default is `undetermined`; the deterministic screen cannot make the legal
   decision. A prohibited or undetermined assertion cannot authorize
   promotion.
3. Collect current evidence over the exact artifact bytes. Promotion requires
   separately reviewed, passing, unexpired evaluation and red-team evidence;
   review applicable drift monitoring, human-oversight tests, data/model cards,
   incident analysis, and vendor assessment for third-party assets. Record the
   evaluator version digest independently from the candidate.
4. Review every deterministic finding and active incident. A human may accept
   one exact non-critical finding digest only for a bounded period and with a
   rationale. Critical findings cannot be accepted for promotion. A changed
   finding or expired acceptance has no authority.
5. Authorize only the named DGM candidate and promotion rung. The controller
   recomputes the payload SHA-256 from the actual candidate; never copy a digest
   reported by candidate-controlled metadata into the approval without
   independently binding the underlying bytes. Bind the current artifact,
   declaration, evaluator, evidence revisions, and evidence-graph authority.
6. Run promotion through the common self-improvement controller. Any payload,
   bound revision, digest, review, expiry, revocation, or graph mismatch denies;
   a new unaccepted high/critical finding or qualifying active incident also
   denies. Do not bypass the gate by calling a lower-level deployment mechanism.
   Publish deployment lineage only after the gate passes; the lineage write
   reruns the check and verifies the expected predecessor digest.

Exercise negative paths before production use: changed candidate bytes with a
stale self-reported digest, evaluator rotation, expired evidence, revoked human
approval, unresolved incident, drift-detection failure, unavailable governed
storage, and rollback to the recorded predecessor. Retain the denial and
rollback evidence. Framework mappings for NIST AI RMF 1.0, ISO/IEC 42001:2023,
and the EU AI Act are versioned decision-support metadata; schedule qualified
human review against current primary sources. A passing gate is neither a
certification nor proof that the model is safe or legally deployable.

## 6. Recovery and residual-risk record

After each drill, record actual RTO/RPO, gaps, owners, due dates, and whether the
next release is blocked. Keep these standing residual risks visible:

- one Lightwork control-plane pod means maintenance and node/PVC recovery cause
  control-plane downtime;
- this reference selects Postgres to satisfy strict authority's shared effect
  ledger and governed-record requirements, but several authority families
  remain local, so it is still not a highly available control plane;
- a quiesced Postgres/PVC backup pair is not an application-wide transaction,
  so every restored local store and ambiguous effect still needs validation;
- loss of the separately managed encryption key makes sealed data unrecoverable;
- attachments and the current audit day are not application-sealed, so the PVC
  and backup target require infrastructure encryption and access controls;
- evidence signatures prove integrity and key possession, not factual truth;
- evidence review, model-risk acceptance, EU AI Act applicability, DGM
  authorization, CRA reportability/filing, and connector exceptions remain
  human decisions;
- continuous model-risk coverage is only as current as the trusted inventory,
  provider, evaluation, drift, and incident observations supplied to it;
- digest pinning does not replace publisher-signature or attestation checks.
