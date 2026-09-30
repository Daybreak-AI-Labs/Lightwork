# Customer-scoped vendor support intake

The connected support endpoint takes customer identity from the authenticated
serve token. Neither a correlation id nor customer fields inside an uploaded
bundle can select another customer's ticket. Active intake identity is the
customer plus correlation id. An absent correlation id creates a new ticket.

The repository's `intake_ticket` operation owns a dedicated SQLite connection
and `BEGIN IMMEDIATE` transaction for lookup, create/append, comment, active-key
maintenance and chained audit event. It commits before returning a ticket.
Exceptions, cancellation/control-flow exceptions and process death before
commit roll back the transaction. Other handlers' commits on the shared app
connection cannot publish its work. Standalone audit appends use the same
database writer serialization, including across local worker processes.
Acquisition uses SQLite's five-second busy timeout and has no automatic write
retry. This does not turn other existing console business workflows into atomic
transactions.

Each successful resend to an active ticket adds one deterministic note and one
audit event. A resolved/closed ticket remains historical; a later bundle starts
a new active ticket. Staff can reopen an old ticket only when its customer and
correlation id have no other active owner. Otherwise the operation returns an
explicit conflict and leaves status, comments and audit unchanged. Unassigned
staff intake is a separate namespace and requires `allow_unassigned=True` from
a trusted internal caller. Connected customer intake never sets that flag.

Schema version 3 adds a customer-scoped lookup and active-key table maintained
by ticket insert/status/ownership triggers. Migration selects the newest active
legacy duplicate as the intake destination and preserves every historical row.
The authenticated Support desk lists up to 100 duplicate groups with ticket
links for staff reconciliation. No tickets are automatically deleted, closed or
merged. `store.intake_conflicts()` also provides the bounded report. If the
selected ticket closes, intake reconciles a remaining canonical active legacy
ticket before creating another one. A stale mapping to another customer's ticket
fails closed with an explicit reconciliation response.

Local validation covers two authenticated customers with the same correlation
id, 24 concurrent resends, three independent writer processes, process death,
audit/commit failure injection, migration rollback, visible legacy duplicates,
reopen policy and tamper-evident audit verification. This is local console
validation; no customer bundle was sent to a deployed service.
