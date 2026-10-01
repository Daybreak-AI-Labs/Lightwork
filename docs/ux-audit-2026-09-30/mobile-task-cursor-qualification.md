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

Each run used the isolated matching-source Python 3.12 environment, OS-only
inherited environment allowlist, temporary home/configuration, synthetic identity
and local SQLite fixtures, serial execution and unchanged 180-second per-test
timeouts. Secure execution set both secure-default flags before collection.
No provider or native database service was contacted. The existing Starlette/
AnyIO deprecation warning remains visible. Before/after hashes match for all five
new test and product files captured by the runner; the additional OpenAPI
expectation change is separately bound by its committed Git blob.

Ruff, Python 3.10 AST parsing of all six changed Python files and whitespace checks
passed. Native PostgreSQL execution and final integrated qualification remain
pending. Source-candidate runs used base `7b6d69d` plus the recorded working-tree
changes, subsequently committed unchanged as `737ba45`; they are not reported as
an already-complete repository-wide run at that commit.
