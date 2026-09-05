# Run accounting and recovery

`run_goal` owns the lifecycle boundary for the existing goal and episode
records. Completion, waiting for input, Shield refusal, budget exhaustion,
setup/provider exceptions, and asyncio or dashboard cancellation close any
started episode. Final episode spend and quota settlement happen after paid
finalization work. The in-run outcome branches stage the episode result;
the outer boundary persists its final spend once and always attempts quota
settlement and trace-context cleanup.

Execution completion (`goal.status = done`) does not confer human acceptance,
validation of an external effect, or permission to release a deliverable.
Existing review, signoff, and external-action records remain authoritative for
those decisions. A model answer is never a human approval.

## Durable resumption

Durable execution remains opt-in (`MAVERICK_DURABLE=1` or `[durable] enabled`).
Its supported boundary remains the existing linear depth-zero agent loop,
using the SQLite world model's `checkpoints` table. This change does not add
swarm-tree or in-flight external-action checkpointing.

Resumption restores counters and elapsed execution time into the caller's
existing `Budget`. Current caps, capability, execution principal, tenant
reservation, and resource owner are not imported from history. The existing
execution-principal binding and current tenant/quota checks run first. For
the standard orchestrator checkpoint, restored caps are checked before any
briefing/planning call; root discovery also covers domain-agent keys, and the
root agent's own restoration is idempotent.
An explicitly selected episode must belong to the requested goal.

Existing accounting plus checkpoint history is conserved. A successful quota
write annotates the existing checkpoint budget JSON with `_settled` counters.
On a subsequent invocation with a fresh Budget, cumulative episode totals
include historical usage, while the quota ledger receives only the usage
not already acknowledged. Failed quota writes retain unsettled counters for
recovery and retain the existing conservative tenant reservation behavior.
Historical token, dollar, tool, and elapsed counters must be finite,
nonnegative and complete. Corrupt or unavailable requested resume history
blocks execution instead of silently starting over. A missing checkpoint
still permits the existing warm restart. Optional inspection APIs keep their
best-effort defaults.

Checkpoints survive interrupted and blocked results. They are removed only
after successful run finalization. No released world-model migration changes;
the additional accounting field is optional JSON in the existing checkpoint
budget column. Older valid snapshots remain readable and count their saved
usage as unsettled because they carry no ledger acknowledgement.

## Limits and reconciliation

The quota JSON ledger and SQLite checkpoint are separate stores. A process
crash after the ledger write but before the checkpoint acknowledgement can
charge historical usage again on resume. This is conservative overcounting,
not a claim of transactional exactly-once billing. Operator reconciliation is
required for that window and for legacy checkpoints whose prior settlement
cannot be established. A hard process loss can also lose usage since the
last persisted boundary; provider receipts remain necessary for reconciling
unreturned or uncheckpointed usage. These tests do not prove live provider
billing or crash-atomic settlement across stores.

Resuming messages does not undo tools or establish whether a timed-out
external mutation succeeded. The governed external-action operation and
reconciliation records, rather than an agent retry, must resolve that state.

Required Shield policy blocks absent or unavailable input/output scans,
including a detector verdict carrying `scan_error`. Intentionally optional
Shield installations retain the existing warned fail-open behavior. Rendered
code diffs retain their pre-existing scan exemption. A final prose refusal
persists only the refusal, closes the episode as blocked, and preserves the
existing prohibition on exporting the rejected final through success webhooks.

## Offline verification

`python3 -m pytest -q packages/maverick-core/tests/test_run_lifecycle_reliability.py`
covers the lifecycle and accounting boundaries with synthetic provider
responses, isolated SQLite records, the real local usage ledger, and no live
customer mutations. The durable-checkpoint, orchestrator, output/webhook,
quota and tenant-budget suites provide compatibility coverage.
