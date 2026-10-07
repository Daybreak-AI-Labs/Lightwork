# Memory and knowledge implementation checkpoint

This checkpoint extends existing features and their existing configuration:
`[dreaming] user_notes`, `[memory] temporal`, Memory Guard, and
`[knowledge_access] enable` (already wired into the installer). It does not
introduce an unconfigured standalone service or change model/provider setup.

Personal preference consolidation is bounded per exact channel/user, not across
coworkers. It uses newest source turns, records source turn/time/content version,
rewrites after source changes, and retains the last complete store on a failed
source scan. Tenant path resolution fails closed. Cross-channel identity sharing
and personal suppression controls are still unfinished.

Shared facts now have an optimistic concurrency contract. The current SQLite
AUTOINCREMENT ID and PostgreSQL write sequence are durable revisions; no migration
is needed. Reads return the revision, writes and deletes compare it under the
backend write lock before touching live values or temporal history. A recreate
receives a new revision. Agent memory remembers its own last observed revision
and refuses to overwrite another writer. Conversational priority writes also
compare their observed revision. Trusted internal callers can still explicitly
make unconditional writes by omitting the optional revision.

The Facts page renders at most 50 business facts, searches names/source/age, and
offers explicit selection for read-only bulk review. Details show provenance,
source run reference, age, trust, sensitivity, available history and retention
consequences. A historical value can become a reviewed edit draft; saving still
checks the current revision. The editor preserves a stale draft alongside the
current value and requires explicit review before rebasing. Fact tags and an
individual expiry policy are not implemented. Current policy-controlled retention
is described without claiming that removal erases recorded history.

Shared Facts UI and API exclude reserved `goal:` and `user:` namespaces before
pagination, and reject those names on shared writes, reads and deletes. Exact
workspace ownership also applies to displayed PostgreSQL history, including
legacy compatibility mode. Unscoped business facts remain shared; provenance
does not imply a new private classification. The dedicated task memory tool
retains exact goal scope. Broader private-memory recall policy is owned by the
identity/privacy workstream.

Knowledge purposes traverse all registry pages and recheck source, purpose,
consent, lineage and actor authority before displaying a purpose. A backend
failure is unavailable, with explicit retry and preserved query. Employee
feedback binds an exact currently visible source/version, returns only the
requester's submission/status, and leaves independent administrator decisions,
derivative handling, legal holds and training permission unchanged. Search does
not disclose counts of denied records. The metadata finder links the separate
governed-content surface but is not yet unified discovery.

The recent chat field is explicitly a filter of 25 recent titles, with a link to
the existing full task search for older and content matches.

Validation: Python 3.10 AST, JavaScript syntax and whitespace review only. New and
updated regression tests cover quiet-person retention, preference replacement,
source edits/failure, fact conflicts/delete-recreate/concurrent connections,
PostgreSQL parity/tenant boundaries, 10,000-fact bounded lookup, private-scope
exclusion, purpose pagination/revocation/outage and operator feedback authority.
No test suites, provider calls, migrations, benchmarks, browser qualification or
production actions were run, following the user's explicit testing deferral.
