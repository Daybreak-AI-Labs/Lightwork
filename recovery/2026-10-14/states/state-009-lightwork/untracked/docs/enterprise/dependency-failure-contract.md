# First-profile dependency failure contract

This document defines what Lightwork does when a dependency fails in the
`enterprise-single-control-plane-v1` profile. The machine-readable authority is
[`release/dependency-failure-contract.json`](../../release/dependency-failure-contract.json),
validated by `scripts/verify_dependency_failure_contract.py`.

It is intentionally not an availability claim. Every admitted dependency still
requires a live outage and recovery drill against the exact release, cluster,
configuration, credentials, and provider offering before go-live.

## Probe semantics

- `/livez` proves only that the process accepts traffic. Dependency failure must
  not create a restart loop.
- `/healthz` checks Postgres access and the presence of a model credential; it
  does not make a live vLLM, IdP, DNS, or public-certificate request.
- `/readyz` adds fixed fail-closed production-assurance signals, including KMS
  custody, exact Postgres TLS/RLS posture, audit integrity/backlog, policy
  integrity, queue state, and kill-switch posture. A failed or unknown required
  signal returns HTTP 503.
- A green readiness response is therefore not proof that vLLM, the IdP, DNS, or
  the external ingress certificate is reachable. Monitor those from outside the
  pod and halt traffic on their defined signals.

## Failure matrix

| Dependency | Profile status | Startup/readiness behavior | In-flight and retry rule |
|---|---|---|---|
| Postgres | Required | Startup and readiness fail closed on missing authority, wrong schema/RLS, or unverified TLS. | Preserve the stable operation identity. Retry only after recovery when the operation is idempotent; reconcile any ambiguous external effect first. |
| Vault KMS | Required | Startup validates off-host custody; readiness performs a remote custody check. | Cached tenant DEKs may work only until the bounded TTL. Never extend the cache or downgrade to local keys during an outage. |
| vLLM | Required | Configuration is checked, but live reachability is not a readiness probe. | Provider calls time out/fail. Control denials, partial streams, cost-authority failures, and ambiguous charges are not permissive failover signals. |
| DNS | Required infrastructure | Not probed by the app. | The owning dependency fails; no alternate destination may bypass the approved egress inventory. Retry only reconciled/idempotent work. |
| Ingress TLS | Required infrastructure | Exact chart configuration is checked; live certificate validity is externally monitored. | The app can remain live while clients cannot connect. Restore the pre-provisioned certificate and retain rollback evidence. |
| OIDC or trusted proxy | Exactly one admitted path required | Configuration is checked; IdP/JWKS/proxy reachability is not a readiness probe. | New login/key refresh can fail. Do not fall back to a shared token; preserve session expiry and revocation policy. |
| Audit storage/signer | Required | Startup/readiness fail closed for required custody and authority backlog. | Consequential work is refused without its signed durable audit. Ordinary telemetry failure grants no authority. Retry with the original stable identity after repair. |
| Audit anchor | Required | Missing required ledger or excessive lag removes readiness. | Treat missing history as integrity/tamper state, quarantine it, and reconcile without rewriting evidence away. |
| SIEM collector | Operationally configured | Delivery backlog/failure removes production assurance readiness. | The confidential outbox retains stable delivery IDs, leases, backoff, and dead-letter state; drain or redrive without inventing a new identity. |
| State PVC | Required | Missing/unusable custody state stops startup or required readiness checks. | Disk-full/read-only failures refuse durable writes. Restore the volume and reconcile checkpoints, claims, and audit before retry. |
| Vector store | Excluded | No supported runtime behavior. | Knowledge/RAG must remain disabled until a new manifest and qualification admit it. |
| Runtime object store | Excluded | No supported runtime behavior. | Backup-object storage is an external DR responsibility, not an application failover claim. |
| Connectors/channels | Excluded | Configuration is rejected by the default-deny profile. | No connector outage behavior is claimed; each future connector needs its own delivery and rotation qualification. |

## Required outage evidence

For each non-excluded row, retain the exact release and image digest, production
manifest/runtime-contract digests, Helm values digest, dependency/provider
identity, start and recovery timestamps, injected fault, client-visible status,
readiness/metrics samples, stable operation IDs, durable state before/after,
reconciliation outcome, data-loss/duplicate-effect result, operator, approver,
and expiry/rehearsal date.

The minimum drill set is: startup unavailable; outage before dispatch; outage
after durable prepare; outage after remote acceptance but before local commit;
recovery with the same idempotency identity; prolonged outage across lease and
cache expiry; credential/certificate expiry; partial/corrupt response; and
restoration from the approved backup path. An unrun drill remains an external
production blocker even when repository tests pass.
