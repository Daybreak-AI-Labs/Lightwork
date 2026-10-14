# Production data boundary

`release/production-data-boundary.json` is the machine-readable DATA-14 map for
`enterprise-single-control-plane-v1`. `scripts/verify_production_data_boundary.py`
binds it to the exact source version, production manifest exclusions, processor
inventory, and existing implementation paths.

The map inventories nine admitted flows: identity/session state; goals,
conversations, prompts, results, and tool content; governed flow/checkpoint
state; signed audit/approval/receipt evidence; operational telemetry;
quota/cost/capacity data; secrets/key material; read-only workspace source; and
authorized support/evidence artifacts.
It also default-denies connectors, channels, plugins, attachments, RAG,
notification egress, application-driven external telemetry, command execution,
hosted model providers, and automated retention.

This is a source contract, not a DPA, RoPA, legal subprocessor schedule, or live
deployment attestation. Before serving a customer, the operator must name and
approve actual vendors and regions, approve every retention/deletion schedule,
and capture live TLS, at-rest encryption, access, residency, backup/replica
deletion, restore, credential-revocation, and tenant-erasure evidence. Any live
path absent from the JSON is a release blocker, not an implied extension.

Run the source verifier locally:

```text
python scripts/verify_production_data_boundary.py
```

Passing means the checked-in inventory matches the checked-in narrow profile.
It does not mean a deployment or legal control has passed qualification.
