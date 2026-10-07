# Message storage upgrade

Schema 49 retires the unused message-search indexes on both supported database
backends. With the normal encryption setting, those indexes held encrypted text
that could not serve a meaningful message search. Removing them prevents future
index writes and duplicate derived storage. Saved messages, conversation turns,
encryption, current task search and tenant permissions remain in place.

The installed product applies this upgrade through its normal database opening
path. Customers do not edit database statements or configuration files. Treat the
application update as an offline maintenance window: stop older application
instances before opening the upgraded database. A schema-48 release refuses the
schema-49 database; restarting an older binary is not a downgrade procedure.
Existing backup and recovery controls remain separate from this migration.

SQLite removes `messages_fts` and its three maintenance triggers. The immutable
historical schema remains unchanged; the current opening path omits its exact
retired block so reopening cannot recreate the index. PostgreSQL removes only
`idx_pg_messages_fts`; existing row-level policies are unchanged.

Both migration ladders append version 49. Versions 1–48 retain their original
checksums. Migration governance permits only the reviewed backend, version and
complete SQL digest for this retirement. An extra destructive statement, altered
SQL or the same statements at another version still refuses. This does not
relax the general additive-only or released-migration rules.

Actual local SQLite and cached PostgreSQL 16 upgrades preserved authoritative
message and conversation rows byte for byte, including legacy plain text and
sealed Unicode. Native append, history, task search, tenant isolation and current
reopens passed. A newer schema was refused. These checks establish the named
local upgrade paths, not compatibility with custom SQL, mixed old binaries or
every customer database. Removing derived objects does not itself shrink the
physical database file; no automatic vacuum is added.
