# Open items

This is the current list of findings that are still open after the 2026 audits
(`PLATFORM_IMPROVEMENT_AUDIT.md`, `USER_EXPERIENCE_AUDIT.md`,
`CODE_QUALITY_AUDIT.md`). Those audits are point-in-time records and do not
track status per item, so use this file for what is still open.

It was compiled on 2026-10-14 from commit `d8376beb`. Each row was checked in
that pass with one grep or file read. The evidence column gives `file:line` in
this repository; line numbers are as of that commit and drift as files change.
This is not an issue tracker: rows have no owners or dates. Items that were
checked and are already fixed are listed at the end and are not in the tables.

## Open: owner decision

| Item | Evidence | Why open |
|---|---|---|
| Off-host audit signing is off by default. Without enterprise mode, `[audit] require_offhost_key`, or `MAVERICK_AUDIT_REQUIRE_OFFHOST_KEY`, a same-host Ed25519 key is generated on disk. That gives integrity only against accidental or non-privileged edits. | `packages/maverick-core/maverick/audit/signing.py:487-511` (`require_offhost_signing`), `:619` and `:677` (local key path and custody warning) | Turning it on by default would break local installs that have no external key. Owner decision. |
| No release is published. The releases API lists none; tags run from `v0.1.0` to `v0.1.6`; `main` is at 0.1.7. | `release-cohort.toml` (`version = "0.1.7"`); `SECURITY.md` (Supported versions) | Cutting the first release is an owner decision. |
| Mutable `latest` image tag. The default `image_tag` is `latest` and the ECR repository allows tag overwrite, so a service follows re-pushes of the tag. `image_digest` now pins the task definition to `repository@digest` when set (`deploy/aws/terraform/variables.tf`, `ecs.tf`; NEXT_STEPS.md step 4b). | `deploy/aws/terraform/ecr.tf:3` (`MUTABLE`); `deploy/aws/terraform/variables.tf` (`image_tag`, `image_digest`) | Immutable tags need a versioned push from the release pipeline; the release process is an owner decision. Until then, set `image_digest` after each build. |
| ALB has no access logs, no WAF association and no deletion protection. The database has deletion protection; the load balancer does not. | `deploy/aws/terraform/alb.tf:1` (`aws_lb.main`); `deploy/aws/terraform/rds.tf:36` (the only `deletion_protection` in the stack) | Log bucket, WAF rules and their cost are owner decisions. |

## Open: separate change

| Item | Evidence | Why open |
|---|---|---|
| Database URL uses `sslmode=require`, not `verify-full`. The `pg_dsn` secret now carries `?sslmode=require` (encrypted, server certificate not verified); `deploy/postgres/README.md` asks for `verify-full`. | `deploy/aws/terraform/secrets.tf` (`pg_dsn` secret version) | `verify-full` needs the RDS CA bundle shipped in the image and pinned; separate change. |
| AWS deployer identity keeps unscoped create actions. Infrastructure the stack creates has no ARN before it exists, so EC2, ELB, RDS, EFS, ACM and SES actions stay on `resources = ["*"]`; they are now bound to the deploy region (`aws:RequestedRegion`), and a Deny (`NeverDeleteDataStores`) stops the identity deleting the database, its snapshots or the state volume. S3, CodeBuild, ECR, ECS, Logs and Secrets Manager actions were already resource-scoped. | `deploy/aws/terraform/deployer.tf` (`Infra`, `NeverDeleteDataStores`) | Tag-based conditions on the create actions would need every resource tagged consistently first; separate change. |
| ECS task without container hardening. The container definition sets no read-only root filesystem and no capability drop. | `deploy/aws/terraform/ecs.tf:26-28` (container definition); `deploy/aws/terraform/ecs.tf:119` (writable state mount) | Read-only root needs the writable paths listed and a test deploy; separate change. |
| Helm pods mount the default service-account token. The pod spec sets no `automountServiceAccountToken: false` and names no ServiceAccount. | `deploy/helm/maverick/templates/deployment.yaml:26` (pod spec); no `automountServiceAccountToken` anywhere in `deploy/helm` | Confirm the app never calls the Kubernetes API, then set it false; separate change. |
| Helm ingress values are not rendered. `values.yaml` defines an `ingress` block (disabled by default), but `templates/` has no Ingress resource. | `deploy/helm/maverick/values.yaml:154-160`; `deploy/helm/maverick/templates/` (no ingress template); `NOTES.txt:11` tells the operator to add one | Separate change. |
| Queue side effects on the reclaim path are at-least-once. A worker frozen past its lease, or one that dies after its effect and before `complete()`, has its job run again. Handlers must be idempotent. | `packages/maverick-core/maverick/worker.py:526-548` (`_reclaim_stale` caveat); `packages/maverick-core/maverick/job_queue.py:718` (`reclaim_stale`) | Needs idempotency keys at each side-effect boundary, handler by handler. The soak metric `exactly_once_under_contention` measures claim atomicity only. |
| CSP still allows inline styles: `style-src 'self' 'unsafe-inline'`. Inline scripts are closed (see the fixed list); the templates' inline `<style>` blocks and `style=""` attributes still need it. | `packages/maverick-dashboard/maverick_dashboard/app.py` (`_DEFAULT_CSP` and `_DOCS_CSP`, `style-src`) | Moving every inline style into stylesheets or nonce-stamped `<style>` blocks touches most templates; separate change. |
| `app.py` is about 15,700 lines. | `packages/maverick-dashboard/maverick_dashboard/app.py` | Splitting it is a refactor with a broad test surface; separate change. |

## Open: unused code (owner decision: keep or remove)

| Item | Evidence | Why open |
|---|---|---|
| `prompt_dsl` has no production importers. Only `packages/maverick-core/tests/test_prompt_dsl.py` imports it. | `packages/maverick-core/maverick/prompt_dsl.py` | Keep it as a documented library or remove it. |
| `migrate.REWRITES` is an empty list, so the rewrite loop never runs. | `packages/maverick-core/maverick/migrate.py:252` (empty list), `:257` (loop) | Populate the rewrite table or remove the mechanism. |
| `DigestIndex` and `recall_relevant_digests` have no production caller. Only `packages/maverick-core/tests/test_compaction_rag.py` uses them. | `packages/maverick-core/maverick/compaction/__init__.py:501` (class), `:563` (function) | Wire them into compaction or remove them. |

## Checked and already fixed (not open)

- Terraform version floor. `required_version` is now `>= 1.10`, matching the S3
  backend's `use_lockfile = true` (`deploy/aws/terraform/versions.tf`).
- Personal identifiers in a tracked Terraform var file. `oidc.auto.tfvars` now
  holds only non-personal configuration; `dashboard_admins` and `email_from`
  are read from the untracked `local.auto.tfvars` (template:
  `deploy/aws/terraform/local.auto.tfvars.example`, ignored by `.gitignore`).
  The git history still carries the earlier values.
- Secrets Manager recovery window. Every entry in `deploy/aws/terraform/secrets.tf`
  uses `recovery_window_in_days = 7`; none can be deleted immediately.
- EFS backup. `aws_efs_backup_policy.state` enables AWS Backup for the state
  volume (`deploy/aws/terraform/efs.tf`: daily, 35-day retention, default vault).
- Postgres world-model login bypassing row-level security. On open,
  `PostgresWorldModel._check_database_role` reads the login's `rolsuper` and
  `rolbypassrls`. With `[world_model] rls` on, such a login is reported by
  `/healthz` and `/readyz` as degraded (`checks.database_role`), and enterprise
  mode refuses to open; with RLS off it is only logged. The governed-records
  store has its own check (`packages/maverick-core/maverick/governed_records.py`,
  `_schema_status_on`), which refuses such a login.
- CSP inline scripts. `script-src` is `'self' 'nonce-…'` with a fresh nonce per
  response, and every inline `<script>` in the dashboard templates carries it;
  `'unsafe-inline'` is gone from `script-src` (`style-src` keeps it, see above).
- SAML signatures. `want_response_signed` is now `not want_assertions_signed`
  (`packages/maverick-dashboard/maverick_dashboard/saml.py`), so at least one
  signature is always required.
- Message search index tokenizing ciphertext. Migration v49 retired the SQLite
  full-text index and its triggers (`packages/maverick-core/maverick/world_model.py:455-461`)
  and the Postgres GIN index (`packages/maverick-core/maverick/world_model_backends/postgres.py:753-755`).
- Dashboard automation described as exactly-once. The wording now says atomic
  claims with at-least-once delivery (see the queue row above for the open part).

## Not verified, so not listed

- "Six record tables without a retention path" was not checked in this pass.
