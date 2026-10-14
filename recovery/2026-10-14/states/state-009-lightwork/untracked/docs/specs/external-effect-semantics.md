# External-effect execution semantics

**Status:** Authoritative for the model-visible tool boundary in the first
production profile

This contract applies to model-visible consequential tools and flow handlers
admitted through the production tool registry. It does not claim that every
supported operator CLI administration command returns this typed receipt; CLI
commands retain their separately documented exit, structured-output, audit, and
change-control requirements.

Lightwork does not claim universal exactly-once execution. Queue ownership,
model execution, connector delivery, and an external system's mutation are
different boundaries. A single-winner queue claim does not prove that a
handler ran once, and a successful local return does not by itself prove that
an external effect committed.

The first production profile keeps consequential mutation tools disabled. Any
future reviewed mutation tool must implement the typed receipt protocol below
before it can be admitted to that profile.

## Receipt states

| State | What is proven | Required behavior |
| --- | --- | --- |
| `PREPARED` | The exact request, tenant, principal, policy decision, target, operation ID, and idempotency key were durably recorded before dispatch. No external effect is asserted. | Dispatch at most from this recorded intent. Preserve the same idempotency key across recovery. |
| `COMMITTED` | An authenticated provider response or authoritative reconciliation read proves the effect, and a durable signed receipt binds the provider reference to the prepared intent. | Return success. A replay returns or reconciles the same operation; it must not create a second effect. |
| `REFUSED` | Policy or the provider affirmatively refused the operation. No commit is asserted. | Do not retry automatically. A later attempt requires a newly authorized operation when policy permits it. |
| `INDETERMINATE` | Dispatch may have reached the provider, but no authenticated terminal evidence proves commit or refusal. | Stop automatic execution and retry. Preserve evidence and route the operation to reconciliation and operator adjudication. |

Display text, an HTTP status alone, a timeout, connection loss, or an exception
message can never upgrade an operation to `COMMITTED`. The signed typed receipt
and its authenticated provenance are the authority.

## Transition rules

The normal transition is `PREPARED` to one of `COMMITTED`, `REFUSED`, or
`INDETERMINATE`. A terminal receipt must bind to its predecessor receipt,
request digest, operation ID, idempotency key, tenant, principal, tool, action,
target, and reconciliation method. A mismatched, stale, unsigned, replayed, or
malformed receipt is refused.

`INDETERMINATE` is intentionally sticky. It may become `COMMITTED` or `REFUSED`
only after an authoritative reconciliation read or a documented operator
adjudication. Re-running the original mutation to discover its state is not
reconciliation.

For flow runs, the run API exposes an `indeterminate_case` containing bounded
evidence commitments, response and escalation deadlines, an escalation target,
and the permitted operator actions. Only an authenticated tenant admin on the
explicit adjudicator allowlist may publish the immutable resolution. A required
signed audit event is written first. Only a `not_committed` resolution may
explicitly authorize a fresh replacement run; all other dispositions remain
quarantined from retry. The operational procedure and request fields are in
[`../operations.md`](../operations.md#resolve-an-indeterminate-flow-run).

Flow execution also persists an immutable, hash-chained work-node transition
after an executor returns and before the interpreter can enter another work
node. The production Postgres authority atomically commits that transition with
the run projection. It binds the pre-dispatch input revision, node status,
exact typed receipt when applicable, output commitment, and next cursor. The
record schema, refusal behavior, and limits are defined in the
[flow node transition ledger](flow-node-transition-ledger.md).

## Delivery and recovery guarantee

`JobQueue.claim()` provides a single winner for one ready row revision under
the measured contention path. It is not an end-to-end execution guarantee.
After a worker crash or lease expiry, the job can be delivered again. Handler
execution and external delivery are therefore at-least-once under recovery.

Every admitted model-visible consequential handler must consequently do one of
the following:

1. use a provider-enforced idempotency key whose retention window covers every
   Lightwork retry and recovery window;
2. reconcile an existing operation by stable external reference before any
   repeat dispatch; or
3. enter `INDETERMINATE` and require operator resolution.

The system must never describe a cancelled or timed-out call as not executed
unless authenticated provider evidence proves that fact.

## What current tests prove

The control/data-plane soak proves zero loss, clean drain, and a single winner
for concurrently claimed ready rows in the tested SQLite topology. It does not
exercise lease-expiry redelivery, a process failure during an external call,
provider idempotency, or external-effect reconciliation. Those cases require
separate adverse-path and live-provider evidence.

The production release contract may cite only the precise guarantee supported
by the retained evidence. Broader delivery, durability, or commit claims need
deployment-specific proof.
