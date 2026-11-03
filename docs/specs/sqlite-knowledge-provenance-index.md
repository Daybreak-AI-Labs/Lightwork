# SQLite knowledge provenance indexing

Status: design prerequisite for a persistent index. The SQLite knowledge store
scans metadata for deletions and cold exact reads and counts. A bounded,
process-local index can reuse a previously complete scan for repeated reads or
counts, but only under a pinned read snapshot with matching SQLite data and
connection change versions; uncertainty falls back to the scan. Those
operations support provenance verification and erasure, so an index must never
silently omit a matching chunk. The scan remains the authority until the
migration and compatibility checks below are implemented.

## Required behavior

The current comparison is Python's `json.loads(meta or "{}").get(key) == value`.
An index must preserve that comparison, including missing keys, nulls, unusual
Unicode, duplicate object keys, non-string values, and corrupt metadata. A
failed parse must keep the current operation from reporting successful erasure;
no previously selected chunk may be deleted before every relevant row has been
validated. Exact reads retain their ordered `limit + 1` overflow sentinel.
Collections remain isolated and all deletion and count results are derived from
the same collection as the query.

## Migration and compatibility

1. Inventory all deployed readers and writers, including older Lightwork
   versions and direct SQLite clients. Establish which SQLite versions provide
   JSON functionality before making any persisted trigger depend on it. A
   writer without that functionality must either remain compatible or fail
   closed with documented recovery; it must not bypass index maintenance.
2. Introduce a versioned index with a transactional rebuild of existing rows.
   Build and publish its version marker in the same `BEGIN IMMEDIATE`
   transaction as the data and maintenance triggers. A crash must leave either
   the prior usable schema or a complete index; missing or incompatible version
   markers must force a rebuild or the authoritative scan.
3. Maintain the index transactionally on every insert, update, replacement, and
   delete, including direct writes by other processes. In particular, account for
   `INSERT OR REPLACE` without assuming recursive delete triggers are enabled.
   Test an older direct writer against the upgraded database.
4. Use the index only for a precisely defined representable subset. Every row
   outside that subset must have an indexed fallback marker, and a query that
   could match or encounter such a row must use the authoritative scan. SQL
   JSON extraction alone is insufficient: its duplicate-key and Unicode
   behavior need not match Python's JSON decoder and equality rules. Recheck
   candidate metadata with the current Python comparison before returning or
   deleting it.
5. Compare indexed and scanned answers across real existing databases before
   enabling the indexed path. Persisted index records contain provenance data;
   include them in deletion, secure-removal, backup, and restore behavior.

## Qualification

Exercise empty and legacy databases, old direct writers, concurrent processes,
replacement and reindex transactions, malformed/non-object JSON, duplicate
keys, escaped Unicode and embedded nulls, mixed value types, interrupted
rebuilds, rollback, restore, and exact-read overflow. Include adversarial
tests proving a partial or stale index cannot cause a false zero count or an
incomplete deletion to be acknowledged as complete. Benchmark representative
collection sizes separately from these correctness checks.
