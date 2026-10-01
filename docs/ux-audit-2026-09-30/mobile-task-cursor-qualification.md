# Mobile task continuation: backend qualification

LW-052's 125-task acceptance review identified a real gap in offset-based mobile
pagination when the first page refreshes. The mobile refresh/window repair is
tracked separately. Backend source
`737ba45287df2c09370d5f83056889f2e5be16d0` supplies an additive immutable-ID
continuation contract for that repair; this record alone does not close LW-052.

`GET /api/v1/goals` accepts an optional positive signed-64-bit `before_id` with
offset zero. Both native stores filter `id < before_id` in the same query as
current owner/status/domain/project predicates and the existing PostgreSQL
tenant predicate. The existing API order is unique ID descending, so no tuple
tie-breaker or anchor lookup is needed. A deleted or inaccessible anchor remains
only a numeric boundary and grants no content authority. Invalid or mixed cursor
requests refuse before opening the world database. Native ascending callers
retain ascending output with the same exclusive upper-ID filter.

The scoped `limit + 1` query determines `Pagination-Has-More`. When another page
exists, `Pagination-Next-Before-Id` names the last returned ID. Existing offset
requests retain their array response and offset headers. Cursor requests omit
`Pagination-Next-Offset`, which could otherwise misleadingly point into the head
of the list. Administrator policy authority still does not broaden content reads.

| Synthetic local cohort | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Pre-repair new-test checkpoint at `7b6d69d` | 1 | 12 | 8 |
| Repaired ordinary, expanded cohort | 22 | 0 | 7 |
| Repaired secure, same expanded cohort | 22 | 0 | 7 |

The baseline exercised the newly authored cursor contract against unchanged
product source and retained its failures. The final cohort adds negative-offset
and PostgreSQL-query predicate controls, exercises workspace isolation on SQLite
instead of its earlier parameter skip, and includes six existing API/ownership/
PostgreSQL query controls. These are not identical selections; the machine-readable
[receipt](mobile-task-cursor-qualification.json) identifies every executed case.
The separate mobile reproduction is the evidence for the original missing-row
interaction, rather than these backend contract failures alone.

Both repaired runs cover the same 22 distinct passing cases. They include reaching
the oldest of 125 tasks after insertion and anchor deletion, current-owner
revocation, scoped filter composition, legacy offsets, ascending filter semantics,
admin privacy, foreign/missing anchors, accurate continuation headers and invalid
request refusal before database access. The seven remaining variants require a
real PostgreSQL service. The SQL-recording control verifies bound predicates, but
does not substitute for executing them against PostgreSQL.

Each historical synthetic run used the isolated matching-source Python 3.12 environment, OS-only
inherited environment allowlist, temporary home/configuration, synthetic identity
and local SQLite fixtures, serial execution and unchanged 180-second per-test
timeouts. Secure execution set both secure-default flags before collection.
No provider or native database service was contacted. The existing Starlette/
AnyIO deprecation warning remains visible. Before/after hashes match for all five
new test and product files captured by the runner; the additional OpenAPI
expectation change is separately bound by its committed Git blob.

Ruff, Python 3.10 AST parsing of all six changed Python files and whitespace checks
passed. Native PostgreSQL execution was subsequently completed below; final
integrated qualification remains pending. Source-candidate runs used base `7b6d69d` plus the recorded working-tree
changes, subsequently committed unchanged as `737ba45`; they are not reported as
an already-complete repository-wide run at that commit.

## Native PostgreSQL follow-up

Both complete new cursor test files subsequently passed against a private
PostgreSQL 16.15 loopback service: **23 passed, zero failed/errors/skips** in
ordinary mode (18.78 seconds) and with both secure-default flags (21.81 seconds).
The immutable clean execution source was
`a0c1da8b9005a15fc80882ce286c75d786a48e57`; all six changed Python file blobs
are identical at review source `0d0755b0ba4389d49357a4f6354cd758ab145e4e`.
The [JSON receipt](mobile-task-cursor-qualification.json) includes every case,
all seven native PostgreSQL identities, source comparisons and artifact hashes.

This resolves the seven service-gated variants above, including deleted cursor
anchors, insertion while paging, owner revocation, scoped filters, ascending
filter semantics and tenant isolation. The 23-case native cohort is the two new
files; the historical 29-case cohort additionally selected six existing API,
ownership and PostgreSQL-query controls. Repeated posture executions are not
additional distinct cases.

Runs were serial with synthetic users/data, a temporary local database/home,
an explicit inherited-environment allowlist and unchanged 180-second per-test
timeouts. Source was unchanged after each run. Both owned servers stopped and
the port had no listener; no provider or production service was used. The
existing Starlette warning remains visible. These backend receipts accompany
the [108-case mobile suite](mobile-pagination-qualification-4d8dedb.md), while
fresh integrated checks and installed Android/iOS scroll/focus qualification
remain explicit boundaries.
