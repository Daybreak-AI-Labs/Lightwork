# Production tenant quota and backpressure contract

This contract defines repository-enforced tenant capacity admission for
`enterprise-single-control-plane-v1`. The canonical machine-readable document
is `release/tenant-quota-contract.json`; verify it with:

```bash
python -I scripts/verify_tenant_quota_contract.py --root .
```

This is code and configuration evidence, not proof that a customer's managed
Postgres service or Kubernetes cluster sustained the intended limits. A live
Postgres concurrency, failover, recovery, and load qualification remains
required before release.

## Required production configuration

Production sets `tenantQuota.enforce=true` and requires
`tenantQuota.policyGeneration`. The generation is a canonical positive decimal
integer from 1 through 999999999 (`^[1-9][0-9]{0,8}$`). Increment it for every
change to a default limit or tenant override. A stale replica, or different
policy bytes/limits at the same generation, is refused by the shared Postgres
row lock. Reusing a generation is not a rollout shortcut.

Configure exactly one policy source:

1. Set all six `tenantQuota.limits.*` scalar values. Partial scalar vectors are
   refused.
2. Set one `tenantQuota.limitsJSON` string and leave every scalar empty. The
   Helm environment entry is limited to 64 KiB (the generic runtime parser has
   a 256 KiB defense-in-depth bound), has the exact top-level fields `default` and
   optional `tenants`, and must contain all six resource keys in `default`.
   Helm requires JSON numbers, non-negative values, integer resource values no
   greater than 2^53-1 (the exact JSON/Go range), and spend no greater than
   999999999.999999. Use scalar mode when a deployment-wide integer ceiling
   must be larger. Runtime parsing additionally refuses duplicate keys, non-finite values,
   excess depth/nodes/tenants, unknown fields, malformed tenant identities, and
   values outside the durable numeric range.

Example scalar configuration:

```yaml
tenantQuota:
  enforce: true
  policyGeneration: "17"
  limitsJSON: ""
  limits:
    concurrentRuns: "8"
    queuedJobs: "250"
    dailyTokens: "10000000"
    dailySpendDollars: "250.000000"
    toolCalls: "50000"
    storageBytes: "10737418240"
```

Equivalent JSON mode:

```yaml
tenantQuota:
  enforce: true
  policyGeneration: "18"
  limitsJSON: >-
    {"default":{"concurrent_runs":8,"queued_jobs":250,"daily_tokens":10000000,"daily_spend_dollars":250.0,"tool_calls":50000,"storage_bytes":10737418240},"tenants":{"tenant-priority":{"concurrent_runs":12}}}
  limits:
    concurrentRuns: ""
    queuedJobs: ""
    dailyTokens: ""
    dailySpendDollars: ""
    toolCalls: ""
    storageBytes: ""
```

The chart emits `MAVERICK_TENANT_QUOTA_ENFORCE=1`,
`MAVERICK_TENANT_QUOTA_POLICY_GENERATION`, and either
`MAVERICK_TENANT_QUOTA_LIMITS_JSON` or these six scalar variables:

| Resource | Scalar environment | Production scalar grammar | Meaning |
|---|---|---|---|
| Concurrent runs | `MAVERICK_TENANT_QUOTA_MAX_CONCURRENT_RUNS` | `0` to 6 canonical digits | Active governed goal-run occupancy. |
| Queued jobs | `MAVERICK_TENANT_QUOTA_MAX_QUEUED_JOBS` | `0` to 9 canonical digits | Active durable local job-queue occupancy. |
| Daily tokens | `MAVERICK_TENANT_QUOTA_MAX_DAILY_TOKENS` | `0` to 18 canonical digits | UTC-day uncached input plus output tokens. Provider cache buckets are spend-accounted but not included in this predictable token reservation. |
| Daily spend | `MAVERICK_TENANT_QUOTA_MAX_DAILY_SPEND_DOLLARS` | canonical decimal below 1 billion, at most 6 fractional digits | UTC-day governed provider spend. |
| Tool calls | `MAVERICK_TENANT_QUOTA_MAX_TOOL_CALLS` | `0` to 18 canonical digits | UTC-day admitted run and direct governed-flow action calls. |
| Storage | `MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES` | `0` to 18 canonical digits | Cumulative UTF-8 bytes for structured deliverable artifacts only. |

Zero is an intentional deny-all ceiling, not unlimited capacity. Missing is a
configuration error. The JSON runtime accepts the wider signed-64-bit integer
range, while the production scalar grammar is deliberately narrower and
canonical.

## Authority and failure behavior

`tenant_quota_usage` contains cumulative use plus the monotonic policy fence;
`tenant_quota_reservations` contains active and terminal leases. The production
runtime role is tenant-scoped under forced RLS. Reservation admission, policy
sync, usage comparison, and lease creation occur in the same Postgres
transaction under a row lock. There is no file or process-local fallback.

An exact replay returns the original lease. Reusing its request ID with a
different principal or vector is refused. Reservations live for at most seven
days. Normal completion settles actual cumulative usage and releases occupancy;
actual use cannot exceed the reserved projection. An explicit pre-dispatch
cancel releases an unused hold. If a process crashes, expiry charges the
projected token/spend/tool/storage values exactly once before releasing run or
queue occupancy. This is intentionally conservative: an authority outage does
not reopen unmetered capacity.

Queue lease identity is stored on the durable job row and renewed across poll,
claim, heartbeat, and restart. Cap-blocked tenants are skipped so one noisy
tenant does not head-of-line block another. Terminal queue transitions release
the persisted hold and stale callbacks cannot resurrect a terminal job.

Readiness parses and validates the complete base policy before enumerating any
tenant, so an empty roster cannot hide malformed policy. It then verifies each
active tenant's six-dimensional vector, generation, and current standing.
Missing policy, unavailable Postgres authority, policy drift, and already-used
or reserved capacity above a lowered ceiling all make the deployment not ready.
Metrics use stable SHA-256 prefixes rather than raw tenant identifiers.

## Exact coverage boundary

Storage enforcement covers only structured deliverable artifact writes that
reserve the exact UTF-8 length before persistence and settle afterward.
Attachment bytes are outside the first production surface and are not counted.
`connector_calls` and `vector_store_bytes` are also unavailable. A zero or
missing metric must not be presented as enforcement for any unavailable
resource. Network ARQ/Redis dispatch, connectors, vector retrieval, and
attachments remain excluded from this profile.

## Required release evidence

Before claiming this control in production, retain target-deployment evidence
for:

- Helm lint/template and admission-policy validation for scalar and JSON modes,
  including negative cases for missing generation, partial scalars, mixed
  sources, malformed JSON, and oversized policy;
- live Postgres simultaneous admission at every ceiling, noisy-neighbor tenant
  isolation, same-generation conflict, stale-replica refusal, UTC rollover,
  crash expiry, settlement replay, queue restart, and primary failover;
- load and soak measurements showing bounded database lock time, readiness and
  metric continuity, queue fairness, and no admission overshoot;
- artifact storage accounting and explicit negative evidence that attachment,
  connector, and vector-store usage is not claimed;
- an operator-approved starting generation, six ceilings, tenant overrides,
  alert thresholds, recovery owner, and a tested policy-increase/decrease
  procedure.

Repository tests establish the grammar, cross-file wiring, fail-closed runtime
behavior, SQLite parity, and guarded live-Postgres test path. They do not replace
the customer-specific deployment and load evidence above.
