# Durable channel delivery

Configured messaging adapters use separate durable intake, execution and reply-delivery states. A provider event is bound to the active tenant, configured transport account and external message identity. Input content and routing are sealed by the governed record store. Source attachments use private content-addressed files, checked against their manifest before execution. Raw credentials and arbitrary provider exception text are not stored in delivery receipts.

The inbox is committed before an acknowledgement that would remove the provider's retry opportunity. Each message has one execution identity. A compare-and-swap transition owns execution; existing legacy processed-message claims are treated conservatively and never reopened automatically. Once execution begins, a crash or exception may have left business effects behind. The receipt remains `execution_started_unconfirmed` or `execution_unknown`; an error does not authorize another agent run. A generic error notification can be delivered without changing that unresolved execution state.

The completed reply is committed before sending it. Delivery retries use that saved reply and per-chunk receipts. A typed definitive rejection can become `retryable`; a transport timeout or missing acknowledgement becomes `delivery_unknown`. Confirmed chunks are skipped on retry. Adapters recheck current sender authorization before execution, before delivery and before each chunk. SMTP connection loss after possible acceptance is unknown, not exactly-once delivery. Stable SMTP Message-ID values support investigation. RCS uses stable provider message IDs for repeated attempts.

Recovery scans a bounded rotating metadata window, loads only that window, then advances a persisted cursor after processing. An empty window does not imply an empty inbox. Webhook transports own a recovery task for their lifetime; email saves UIDVALIDITY/UID-bound intake before marking it seen and also performs recovery each poll. Acknowledging intake does not mean an agent has completed or the recipient has received an answer.

Current bounds are 10,000 receipts per tenant namespace, 1,000 account recovery cursors, 1,000,000 characters per durable text/reply, 10 source files under the existing attachment size/aggregate rules, 512 reply chunks, and a normal recovery window of 16 records. Capacity refusal backpressures admission. Unknown execution/delivery records are never removed to make room. Explicit retention compaction after 30 days clears content only for confirmed completed-and-delivered records; the deduplication tombstone remains. This does not automatically erase all old claims or permit uncertain effects to replay.

Administrators can inspect a tenant-scoped, bounded status window at `GET /api/v1/channels/delivery-receipts?limit=50&cursor=...`. Pass `receipt_id=chd-...` for one receipt's chunk states and provider identifiers. These responses contain status metadata, not message/reply text or credentials. There is deliberately no endpoint that automatically resends an uncertain reply or reexecutes an uncertain run.

Validation is local. Actual local filesystem/SQLite and least-privilege PostgreSQL authorities are exercised with synthetic provider/agent transports. Successful SDK import and local adapter tests do not qualify live provider delivery.

Adapter specifics are in [Gateway recovery](channel-gateway-recovery.md) for
Slack, Telegram, Discord, Matrix and iMessage, and [Bounded social polling](channel-polling.md)
for Mastodon, Bluesky and Threads.
Signal and IRC use [owned transport supervision](channel-transport-supervision.md).
Voice and wearable response semantics, the CLI scope exception and conservative
bootstrap restore handling are described in [Live interaction boundaries](channel-live-interfaces.md).
