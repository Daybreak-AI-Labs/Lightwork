# Production migration safety

This runbook governs RUN-14 for the first production profile. The machine
authority is `release/production-migration-contract.json`; regenerate and
review it whenever the Postgres ladder or `migrations.lock.json` changes. The
contract is repository enforcement, not evidence that a customer database was
successfully migrated or restored.

## Scope and compatibility

Postgres is the only admitted production world-model backend. SQLite remains an
evaluation backend: it migrates forward when a local world is opened and its
pre-migration copy is best effort. That local copy is useful for development,
but it is not managed backup, PITR, restore, or production migration evidence.

For Postgres, a release accepts only these states:

1. An empty, dedicated `public` schema may use `bootstrap` and apply the full
   reviewed ladder.
2. The exact adjacent predecessor migration prefix may use `upgrade` and apply
   exactly the new tail version.
3. The exact current migration set may use `verify`, or may be reconciled with
   the same still-valid bootstrap/upgrade authorization after a commit/report
   ambiguity.

An arbitrary older prefix, a missing middle version, duplicate/out-of-order
versions, a version newer than the image, an unexpected public object, or an
object owned outside the dedicated schema authority is refused before DDL. A
reviewed live-catalog fingerprint additionally detects missing or altered
columns, types/defaults/nullability, constraints, indexes, sequences,
functions, and user triggers even when the version ledger is exact.
Multi-version jumps are not admitted. Upgrade each release boundary in order
and retain one report per boundary.

## Authority and custody

Serving pods never receive the migration-owner DSN. The Helm hook uses a
separate pre-provisioned Kubernetes Secret with only
`MAVERICK_PG_MIGRATION_DSN` and `MAVERICK_MIGRATION_AUTHORIZATION`. An init
container turns one read-only projected generation into private regular files
in a memory-backed `emptyDir`; the Job receives no service-account token.
Its separate default-deny NetworkPolicy allows DNS only to the pinned cluster
resolver and non-DNS traffic only to `migration.allowedEgressCIDRs`. Set that
list to the exact Postgres endpoint CIDR(s), never the serving workload's
broader dependency allowlist or an all-address range. Set
`migration.databasePort` to the exact TCP port in the migration-owner DSN.
The dedicated `public` schema must be owned by the migration role and must not
grant `CREATE` to `PUBLIC` or any other role. The runner replaces a
DSN-controlled search path with transaction-local `public` before any DDL.

The authorization is strict, duplicate-free JSON, at most 16 KiB, valid for at
most 24 hours, and names exactly two distinct approval principals. The runner
validates those identifiers but does not authenticate the people behind them;
authorization authenticity remains an external GitOps/RBAC and change-control
gate. The document binds:

- the first production profile, operation ID, mode, source and target heads;
- the chart app version and immutable image digest;
- the canonical SHA-256 of the packaged migration lock;
- a drain evidence identifier and SHA-256 no more than one hour old;
- for an upgrade, a completed managed snapshot or continuous-PITR marker no
  more than 24 hours old and bound to the source head;
- a live restore-drill evidence identifier and SHA-256 no more than 90 days
  old; and
- the exact fail-closed recovery policy.

Compute SHA-256 over the authorization file's exact bytes and set
`migration.authorizationSha256` to that lowercase digest. This public digest
binds the mounted Secret generation to the reviewed Helm release. The marker's
evidence IDs and digests are references: the runner validates their shape,
freshness, and binding, but it does not query the backup provider. In
particular, a backup marker does not prove that a backup can be restored.
Freshness is evaluated again at execution, not only when the authorization is
issued, and an upgrade backup must complete after the recorded writer drain.

## Drained deployment procedure

1. Rehearse the exact adjacent transition on a representative copy at the
   expected data volume. Record elapsed time, locks, disk headroom, integrity
   checks, and application verification.
2. Deny new admissions, stop schedulers and remote writers, let in-flight work
   settle, scale the serving control plane to zero, and retain the drain report.
   The marker is an operator attestation; cluster-level zero-writer proof is a
   required external release record.
3. For `upgrade`, create a source-head managed snapshot or confirm a PITR point
   after the drain. Retain the provider-native completion and retention record.
   For `bootstrap`, verify the database is dedicated and empty.
4. Confirm that the exact managed Postgres profile has a successful independent
   full-restore and integrity drill within the contract window. Reference that
   separately from the new backup marker.
5. Create the short-lived two-approver authorization, compute its raw SHA-256,
   and provision the dedicated migration Secret. Do not place either DSN in
   Helm values, command-line arguments, logs, or the serving Secret.
6. Install or upgrade with the production values overlay and site values. The
   site values must pin `migration.allowedEgressCIDRs` and
   `migration.databasePort` to the database endpoint.
   The chart's `pre-install,pre-upgrade` Job runs:

   ```text
   python -m maverick.production_migration apply \
     --dsn-file /run/secrets/lightwork-migration/MAVERICK_PG_MIGRATION_DSN \
     --authorization-file /run/secrets/lightwork-migration/MAVERICK_MIGRATION_AUTHORIZATION \
     --authorization-sha256 <reviewed-raw-file-sha256> \
     --image-digest sha256:<release-image-manifest> \
     --release-version <chart-app-version>
   ```

7. The runner verifies `sslmode=verify-full` and the trusted CA, requires a
   `NOSUPERUSER NOBYPASSRLS` table/schema owner with no foreign schema-create
   grant, takes the migration advisory lock and access-exclusive locks on the
   admitted table set, rechecks the exact version/table/owner state and the
   head-specific live-catalog fingerprint, and applies all pending DDL plus
   version rows in one transaction. The same fingerprint is checked again
   after DDL and before a success report can be emitted.
8. While writers remain drained, the runner invokes the existing exact RLS
   installer in its own idempotent transaction. It refuses unscoped rows,
   foreign ownership, missing tables, or any unexpected/permissive policy.
9. If any legacy rows can predate strict application sealing, run the
   whole-database `maverick encryption migrate-postgres` inventory/rewrite from
   a separately reviewed drained maintenance Job with the required KMS custody.
   Its DSN is also required to use `sslmode=verify-full`, and the command attests
   live TLS. It acquires the schema runner's advisory lock before its table
   locks, validates the exact catalog/ledger/owner/RLS/trigger posture under
   those locks, and repeats that posture check immediately before commit.
   Retain a final `ready=true`, zero-plaintext report. The serving role's
   tenant-scoped startup check is not a whole-database attestation.
10. Collect the canonical value-free JSON report from the schema Job log and bind/sign
    it in the external release evidence system. The report contains only
    digests, schema versions, timestamps, and a hash of the migration role; it
    never contains the DSN or role name. Collect it before the one-hour
    `ttlSecondsAfterFinished` deadline. The Kubernetes TTL controller then
    removes the completed Job and Pod, bounding how long a finished Pod can
    retain mounted migration authority.
11. Start the non-owning runtime. Startup takes the same advisory lock and
    refuses an exact ledger whose live-catalog fingerprint is missing or
    altered. Retain exact-release TLS, schema, RLS,
    encryption, cross-tenant adverse, readiness, and application smoke results
    before reopening admissions. Remove/revoke migration-owner Secret access.
    After terminal evidence collection, explicitly delete the hook Job and its
    migration NetworkPolicy if the TTL controller has not already removed the
    Job. Helm does not manage hook resources as ordinary release resources, and
    the NetworkPolicy has no TTL:

    ```bash
    kubectl delete job,networkpolicy <release>-maverick-migration --ignore-not-found
    ```

    The Job uses its own empty affinity by default instead of inheriting serving
    pod affinity that could require a not-yet-created serving pod. Qualify the
    target cluster's TTL controller, scheduler, RuntimeClass, and CNI behavior;
    a successful chart render is not that evidence.

## Failure and rollback

There are no SQL down migrations. Released migration text is immutable and new
migrations are additive-only. A DDL failure before commit rolls back the whole
schema transaction. Do not edit `schema_migrations`, remove a column/table, or
attempt a hand-written reverse migration.

If the schema commit completed but RLS installation or report collection did
not, keep serving drained and rerun the same operation while its authorization
is still valid, using the identical authorization bytes. The schema transaction
stores that public authorization SHA-256 as a table comment; a bootstrap or
upgrade retry on a current schema is refused unless the receipt matches. The
exact-current path performs no DDL and reconciles the RLS policy set. This is
the supported response to commit-before-report ambiguity. A separately issued
authorization must use `verify` mode.

If application verification fails after the database reached the target head,
either deploy a reviewed forward fix that understands that head or perform a
full managed restore to the source recovery point while all writers remain
stopped. Never start the older binary against the newer schema. A restore must
include provider completion evidence, integrity checks, schema/RLS checks, and
application verification before traffic returns; repairing only the migration
ledger is forbidden and is mechanically detected by the live-catalog check.

## Evidence boundary

Repository tests exercise authorization parsing, exact state classification,
transaction rollback, idempotent current-state reconciliation, adverse
column/index/trigger catalog drift, Helm wiring, and contract drift. The
committed contract pins the empty, adjacent-predecessor, and current catalog
fingerprints plus the catalog-query digest, and normalized SHA-256 digests of the
migration Job, both values profiles, and active Helm validation helpers; the
verifier removes comments and refuses literal constant Go-template branches
before checking safety wiring. Its release CLI also requires Helm 3, renders a
complete valid production fixture, verifies the semantic Job and NetworkPolicy
output, and proves representative weakened migration values are rejected. CI
pins that render to Helm 3.17.3. Tests refresh a mutated template's accepted
source digest before proving that literal and value-dependent dead branches are
still refused; source pinning is not used as a substitute for semantic
validation. These controls do not
create an exact managed Postgres service, drain a
real fleet, take a provider snapshot, interrupt a live migration, or restore a
database. Therefore live qualification remains open until every item in the
contract's `external_qualification_required` list is retained for the exact
approved release and customer deployment profile.
