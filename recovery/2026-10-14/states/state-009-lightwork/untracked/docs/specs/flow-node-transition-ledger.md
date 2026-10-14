# Flow node transition ledger

**Status:** Implemented repository contract for governed flow execution

This contract closes the gap between a tool's own consequential receipt and a
flow run's aggregate final row. Every agent or action work-node occurrence is
checkpointed synchronously after the executor returns and before the
interpreter may enter another work node.

## Atomic production record

One immutable `lightwork.flow-node-transition.v1` document binds:

- the run, flow, pinned definition digest, publication identity, and definition
  revision;
- a monotonic occurrence sequence and execution segment;
- the node ID, node kind, tool name, and authoritative node status;
- a SHA-256 commitment to the bounded, redacted input state observed before
  dispatch;
- the exact typed consequential receipt when one exists, plus its receipt
  digest (or an explicit zero digest when no receipt applies);
- a SHA-256 commitment to the bounded, redacted result and post-node data;
- the next cursor; and
- the preceding transition digest and this transition's digest.

In the production Postgres flow authority, insertion of that immutable record
and advancement of the mutable run projection and chain head use one database
transaction while the per-run session lock remains held. The transition cannot
be published without its run head, and the run head cannot advance without the
transition. Duplicate or contradictory immutable records, missing predecessors,
head regression, malformed receipts, and digest or cursor tampering are refused.

The local JSON backend is a development fallback. It uses the same record and
verification format under the run lock, but it is not described as providing a
cross-file transactional commit.

## Failure behavior

Input commitment is computed before dispatch. If it cannot be represented as
strict bounded JSON, execution stops before the external boundary. If the
executor has returned but transition persistence or verification fails, the run
becomes typed `indeterminate_effect` with checkpoint origin and does not enter
the next work node. A production consequential action cannot advance as `done`
without a typed receipt.

A crash after a successful transition but before final aggregate run save does
not cause automatic replay. Queue redelivery sees an in-flight row and
quarantines it; an operator can inspect the immutable transition and its receipt
when reconciling the external system.

## Scope and limits

The ledger proves repository transition durability and preserves exact typed
receipt evidence. It does not turn an at-least-once external provider into an
exactly-once provider, prove a managed Postgres deployment, or authorize blind
replay. Live failover, provider reconciliation, retention, and recovery evidence
remain deployment gates.

