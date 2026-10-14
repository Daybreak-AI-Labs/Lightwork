# Production operations, incident, and support runbook

This is the source procedure for RUN-10 and OPS-02/04/05/06/07/08/09/11 in the
first production profile. It does **not** claim that a pager route, responder
roster, dashboard, capacity baseline, customer SLA, or drill has been exercised.
Those deployment records remain release qualifications.

## Verifier failure policy

`MAVERICK_VERIFIER_REJECTION_POLICY` is `refuse` for production, code, and any
tool-bound/consequential run. After one revision, a second verifier rejection
returns typed `verification_refused` and withholds the candidate output.
Production cannot lower this to `warn`; an unknown value fails closed. The
development-only warning behavior applies only to informational answers without
tools or consequential effects. Preserve both verifier decisions and the typed
refusal when investigating.

## Telemetry and correlation

Use JSON logs with deep secret-key and inline-secret redaction. Goal execution
binds goal ID, conversation ID, and channel through context variables. Signed
audit events are the authoritative correlation plane for tenant/principal,
agent, flow/run, action, approval/signoff, receipt, provider, and connector
references; those fields are not promised on every log line. Never place raw
secret, prompt, result, customer ID, path, key ID, exception text, or destination
in a Prometheus label. Tenant quota labels are fixed-length pseudonyms and
production assurance uses a fixed signal vocabulary.

`/metrics` accepts only the dedicated current/previous metrics service bearer.
Bad, missing, or out-of-scope credentials increment bounded reason counters.
The ServiceMonitor bearer is not proof of target TLS; prove ingress/mesh TLS and
the actual scrape path in the live cluster.

## Alert intake and paging

Every alert in `deploy/observability/prometheus-rules.yaml` declares severity,
owner, runbook, evidence to preserve, and customer impact. Route critical alerts
to a continuously staffed human and warnings to the owning queue. Deduplicate
the generic production-assurance alert when a signal-specific alert fires, but
retain both series. A page is not closed by process restart: reconcile the
underlying signed state and external effects.

Import `deploy/observability/grafana-production-overview.json` as the source
dashboard template. It covers target/assurance state, concurrency, durable
queue, pseudonymous tenant quota, spend, goal outcomes, authentication, audit
pipeline, disk, metrics-service authentication, and firing alerts. Importing it
does not prove datasource wiring, retention, page routing, or a usable on-call
path; retain screenshots/query results and a witnessed test page from the live
deployment.

The severity targets below are internal response design targets, not customer
SLA commitments; `release/service-objectives.json` remains unapproved.

| Severity | Internal acknowledgement | Update cadence | Authority |
| --- | ---: | ---: | --- |
| SEV1 | 15 minutes | 30 minutes | Incident Commander, Security, SRE, Comms |
| SEV2 | 30 minutes | 2 hours | Incident Commander, Security, owning engineer |
| SEV3 | 4 business hours | daily | Owning team |
| SEV4 | 1 business day | as material | Ticket owner |

Every page record must identify incident, severity, UTC time, detector, affected
tenant pseudonyms, customer impact, commander, evidence locations, next update,
and escalation. Live qualification must prove notification delivery and backup
escalation when the primary responder does not acknowledge.

## Authentication or credential compromise

Signals include authentication spikes, metrics-service credential failures,
unexpected identity-provider events, or KMS/key-custody failure. Declare at
least SEV2 until scope is known. Stop affected ingress or engage `maverick halt`;
revoke the exact IdP/provider/metrics/KMS generation; preserve provider audit
records and signed Lightwork evidence without copying credentials; rotate to a
distinct generation; restart workers to clear cached material; prove the old
generation fails and the new generation works. Review all actions signed during
the exposure window before restoring service.

## Suspected cross-tenant exposure

Remove service from readiness and halt effects. Preserve RLS catalog state,
connection TLS attributes, tenant canary results, audit tips, deployment digest,
and relevant Postgres logs. Do not query customer bodies broadly during triage.
Identify exact principals, tables, rows, backups, and downstream evidence sinks;
engage legal/comms; repair policy through migration-owner change control; rerun
negative two-tenant probes and residual scans before canary recovery.

## Audit or evidence pipeline outage

Required audit persistence/signing failures refuse the dependent action. Do not
retry an ambiguous action. Preserve chain files, anchors, independent
checkpoints, outbox delivery IDs, collector acknowledgements, and key identity.
Repair custody/TLS/schema/capacity, verify all chains, reconcile the durable
outbox by stable delivery ID, and issue a signed evidence bundle with the
out-of-band publisher key. A local green check does not replace independently
retained checkpoint or collector evidence.

## Sandbox, guardrail, or policy escape

Arbitrary command execution, plugins, connectors, channels, attachments, and
RAG are excluded from this profile. Any observation of them is a configuration
violation and at least SEV2. Halt effects, isolate the pod/network, preserve the
image digest, policy digest, signed shield/policy events, and read-only
workspace evidence, then rebuild from the admitted image/profile. Never enable
an excluded surface as an incident workaround.

## Malicious plugin or excluded integration

Third-party plugins, generated connectors, channels, notification egress, and
attachments are absent and default-denied in this profile. Discovery of one in
a serving image/process is at least SEV2: halt effects, isolate ingress/egress,
preserve image/SBOM/profile digests and signed denial events, revoke any
associated credential, and determine whether customer data reached the
integration. Rebuild from the narrow admitted artifact and repeat exclusion,
network, secret-mount, and tenant-negative checks. Do not investigate by
executing the plugin or copying unrestricted customer payloads.

## Provider incident

Keep provider failure typed and bounded; do not silently route to an unadmitted
provider or model. Preserve vLLM health, TLS, model-policy decision, latency,
queue, quota, and deployment evidence. Refuse/queue work within published
limits, communicate impact, and recover only on the exact admitted vLLM/model
combination. Hosted-cloud fallback is outside the profile.

## Stuck, duplicate, or indeterminate action

Freeze retries. Preserve action and idempotency IDs, lease generation and
heartbeat, approval/signoff, receipts, audit rows, and an independently obtained
external-target observation. Classify the result as committed, not committed,
or indeterminate. Only the reviewed reconciliation workflow may authorize a
replacement; never edit queue/receipt state or reclaim a lease by hand.

## Database loss or control-plane authority failure

Remove readiness, halt mutations, preserve lease and Postgres evidence, and
follow the reviewed backup/migration-owner lifecycle. Prove authenticated
backup identity, restore to an isolated target, apply exact migrations and RLS,
run tenant-negative probes, reconcile audit/queue/checkpoint tips, and canary
before ingress. The single-control-plane profile is not HA; measured RTO/RPO and
durability remain live qualifications.

## Bad release

Stop rollout and preserve old/new image digests, manifest/profile bytes,
migration report, readiness/alerts, and canary results. If schema changed, use
the forward recovery plan; do not downgrade the database blindly. Restore only
the exact prior application/config combination that remains schema-compatible,
or ship a reviewed forward fix. Complete change-control exception and AAR.

## Emergency halt and credential-revocation drill

Use `maverick halt --reason "DRILL <id>"`, capture generation and signed receipt,
prove `/readyz` is 503 and a harmless action receives a typed halt, then use
`maverick unhalt` and prove generation-bound recovery. Never delete HALT files
or call arm/disarm/clear primitives directly. In a separate non-customer drill,
revoke one purpose-scoped credential, prove old-key denial and alert delivery,
activate the new generation, remove overlap, restart/cache-clear, and reconcile
all audit records. Record UTC times, actors, evidence hashes, observed detection,
page delivery, acknowledgement, containment, recovery, defects, and owner/due
date. A template or unit test is not a completed drill.

## Capacity and cost

Monitor concurrency, queue depth/age/dead letters/stale leases, disk, token and
spend rate, and authoritative quota used/reserved/limit/remaining/over-limit.
Page quota-authority failure; warn below 20 percent remaining. Operators must
establish per-resource baselines, forecasts, lead times, and budgets from live
traffic. Database connection saturation, storage IOPS/latency, backup growth,
and observability ingestion capacity are infrastructure signals not supplied by
the application and must be added to the live dashboard before launch.

## Customer and auditor evidence export

Use `maverick audit evidence` with exactly one day/range mode, one exact tenant,
and optional exact goal, incident, or action selector. The command refuses
unreadable, malformed, unsigned, untrusted, or unanchored completed evidence;
publishes atomically with private permissions; and records authorization and
completion. Give the auditor the publisher public key independently, then run
`maverick audit evidence-verify BUNDLE --key KEY`. The packet proves included
chain integrity and exact selector results, not that every real-world action
entered the audit system.

## Support, maintenance, communication, and review

Maintain one customer case with severity, entitlement, tenant, region,
deployment/profile/version, safe reproduction, impact, evidence hashes, owner,
next update, and escalation history. Never request secrets or unrestricted data
dumps. Announce maintenance through the contractually approved channel and
status surface; publish incident updates at the severity cadence; legal/comms
approve breach or regulatory notices. SEV1/SEV2 reviews occur within five
business days with timeline, detection/containment/recovery measures, control
failures, customer impact, corrective owners/dates, and evidence links.

Live release evidence must include named on-call rotations, reachable backups,
tested page routes, customer support intake, status ownership, maintenance and
breach templates, and one end-to-end escalation exercise.

## Telemetry pipeline failure

Treat `MaverickDown`, scrape-auth failures, stale/missing rule evaluation,
audit-forwarder failures/backlog, or observability storage drops as monitoring
incidents. Use an external black-box signal so the application cannot declare
its own observability healthy. Preserve Prometheus target/rule state, secret
generation, ingestion/drop/cardinality/storage metrics, and audit outbox state.
Restore authentication/TLS/capacity and replay only durable audit deliveries.
External OTel tracing is excluded, so dropped-trace claims are not applicable
to this profile; logs, metrics, and signed audit are the admitted planes.
