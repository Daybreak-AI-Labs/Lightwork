# Native PostgreSQL and erasure qualification

A later [native MariaDB and SQL follow-up](remaining-database-qualification.md) resolves the three MySQL/MariaDB skips below and repairs a SQLite admission-timeout boundary. The twelve pgvector cases remain unrun with the exact build-tool blocker recorded there. This document retains its original cohort and timings.

Recorded 2026-10-01 00:31 UTC. Product source: `de699529b01a87f371ebcfe96c57bd3c022d91f9`.
The isolated `slice-postgres-final` checkout adds only test corrections in
`7ae68f57a8ec4f79709223b058508d6bb0124c18`,
`281097174c74b3a10f88bbc7894e6bc209c32890`, and
`3828114be41dcccbed3ceeaa537eeca006a7dba9`. It does not change database,
credential, cancellation, tenant, erasure, encryption, or migration behavior.

## Results and scope

The original 528-case selection is resolved to **513 passed, 15 skipped** by
combining its initial results with focused reruns. This is **not** a fresh,
single all-green invocation of all 528 cases. Replacing results by exact JUnit
`classname` and `name`, including 52 additional tenant-migration unit cases,
gives **580 distinct cases: 565 passed, 15 skipped**. Secure-default results
are separate executions and are not added to that distinct-case count.

| Local run label | Source or patch | Actual invocation result |
| --- | --- | --- |
| `postgres-de699-01` | `de699529` | 528 collected: 503 passed, 9 failed, 8 skipped, 8 errors |
| `postgres-corrections-01` | `7ae68f5` | 204 collected: 196 passed, 8 skipped; all nine initial failures pass |
| `postgres-secure-01` | `7ae68f5`, secure defaults | 122 passed; four database-tool/runtime CI files |
| `postgres-psycopg2-03` | `2810971` plus the exact test patch committed as `3828114` | One native psycopg2 deadline case passed |
| `postgres-psycopg2-secure-02` | `3828114`, secure defaults | The same native psycopg2 case passed |
| `erasure-ordinary-01` | `2810971` | 33 passed; complete SQLite erasure-receipt and coverage files |
| `erasure-secure-01` | `2810971`, secure defaults | The same 33 SQLite cases passed |

The initial eight errors were the eight new knowledge cases that require the
unavailable pgvector server extension. Capability-aware fixtures now skip those
cases only when using a general PostgreSQL DSN whose server does not advertise
the extension. An explicit `MAVERICK_KNOWLEDGE_DSN` still requires its promised
extension; connection, authentication, and other errors are not caught as skips.

Remaining environmental skips are exactly:

- **12 pgvector cases:** two collection-isolation cases, two existing live
  knowledge cases, six document-publication cases, and two native reindex cases.
- **Three MySQL/MariaDB cancellation cases:** no `MAVERICK_MYSQL_TEST_URL` or
  dedicated local MySQL instance. A second database server was not installed.

No psycopg2 skip remains in this combined native coverage. Initial attempts to
enable it failed before query dispatch: the first private wheel target was
owned by the sandbox account and was unreadable to the account launching the
native fixture. Installing the same verified wheel into a second private
target fixed that environment issue without global ACL or shared dependency
changes. The first readable-driver run then expired its 150 ms deadline during
cold dialect initialization; the test correction below addresses its intended
statement-level measurement. Those unsuccessful attempts remain in the local
logs; they are not counted as successful qualification.

## Corrections and retained assertions

- PostgreSQL and SQLite complete-erasure fixtures now create a human edit
  through `goal_mutations.edit_result` before signoff. This populates the new
  `goal_mutation_receipts` store through the real transaction. Diagnostics
  confirmed it was the only empty receipt store. Full-scope enumeration,
  descendant closure, export, deletion, and completion-proof assertions remain.
- PostgreSQL attachment submission uses the real `GoalIn` request schema,
  including current context and connection-selection fields, instead of a stale
  partial namespace.
- Flow operator-review fixtures explicitly enable audit signing, matching the
  existing SQLite counterpart. Strict signature verification remains enabled.
- The pool-exhaustion fixture opens and returns a real connection before timing
  an exhausted warm pool. Its 100 ms lease budget and bounded-wait assertion
  remain unchanged; it no longer measures first Windows socket/process startup.
- The learning-store migration fixture checks the current declared head and
  every actual `schema_migrations` version instead of a stale hard-coded 40.
- The goal-insert unit contract verifies the `connection_selection` column,
  exact parameter ordering, empty default, and placeholder/parameter parity.
- The dashboard overview fixture inspects rendered headings. An inert
  translation-catalog string no longer falsely identifies an error page.
- The psycopg2 statement test first performs a real `SELECT 1` through the tool
  to initialize its native dialect. The measured call still owns a fresh
  connection, retains its **150 ms deadline** and **under 2.5 seconds** bound,
  asserts `SELECT pg_sleep(5)` reaches the native cursor, checks cancellation
  support and completion, and then verifies a fresh successful query. No
  provider mock, cancellation bypass, or increased deadline is used.

All 11 changed Python test files pass Python 3.10 AST parsing, Ruff, and diff
whitespace checks. No runtime source changes were needed for these failures.

## Native fixture and provenance

The disposable PostgreSQL **16.15-4 Windows x64** runtime comes from the
[official PostgreSQL Windows page](https://www.postgresql.org/download/windows/)
and its linked [EDB binary distribution](https://www.enterprisedb.com/download-postgresql-binaries).
The selected archive is
[`postgresql-16.15-4-windows-x64-binaries.zip`](https://get.enterprisedb.com/postgresql/postgresql-16.15-4-windows-x64-binaries.zip),
371,449,528 bytes. Its locally computed SHA-256 is
`f5f55b03bd54ce0dd1c51d524b54c7e015abd4d620af27d6971288a2dbe4a8f8`.
That value is a local download fingerprint, not a separately verified upstream
checksum. The official archive's `postgres.exe` is not Authenticode signed;
provenance is the official HTTPS download chain. Only `bin`, `lib`, and `share`
were extracted after checking archive paths for traversal.

The cluster binds exclusively to **127.0.0.1:60165**, uses generated synthetic
SCRAM-SHA-256 credentials, retains `fsync` and `synchronous_commit`, and limits
shared buffers to 64 MB and connections to 40. A helper records and rechecks
the exact executable, PID, start epoch, data directory, and port before stopping
the owned process. Every native test invocation stops that server in `finally`.
Final state: **stopped; no listener**. Readiness, bad-password rejection, and
actual create/drop-database checks also passed. The installed optional native
extensions include `pgcrypto` 1.3 and `uuid-ossp` 1.1; pgvector is absent from
both the archive and `pg_available_extensions`.

The test interpreter imports all eight packages from the isolated checkout and
reads existing dependency packages without processing the shared environment's
editable hooks. Existing psycopg 3.3.5/libpq 18 support was reused. The optional
driver is [official PyPI psycopg2-binary 2.9.13](https://pypi.org/project/psycopg2-binary/2.9.13/),
`psycopg2_binary-2.9.13-cp312-cp312-win_amd64.whl`, 2,767,784 bytes, with
PyPI-published and locally verified SHA-256
`376ebf7d8aee4b7386b2bac31fdc27911e7e57cd0a88f1e038b8b149398ac008`.
It resides only in a task-local target on the private test interpreter's path.
The shared interpreter still cannot import psycopg2. No deployment credentials,
remote databases, live providers, system services, global environment changes,
Docker, WSL, compilers, or third-party pgvector binaries were used.

## Reproduction and evidence

Task-local evidence is under `audit/`: each run label above has its `.log`,
`.xml`, and `.result.json`; PostgreSQL runs additionally have `.source.json`
with the exact immutable head and arguments. The 38-entry initial selection is
`postgres-de699-targets.json`; it includes all ten core CI PostgreSQL files,
24 additional DSN-backed core files, dashboard boards, and the three knowledge
selections. The correction and secure selections are
`postgres-corrections-targets.json` and `postgres-secure-targets.json`.
The single-driver selector is `postgres-psycopg2-targets.json`.
Machine-readable committed counts and target inventory accompany this note in
`postgresql-qualification.json`. Raw logs, disposable credentials, downloaded
binaries, virtual environments, and database state are not repository files.

From the prepared task directory in PowerShell, the local runner starts the
tracked server, scrubs inherited credential/provider settings, supplies only
the synthetic DSN, creates an isolated test home, runs sequentially, and stops
the server even on a pytest failure:

```powershell
& .\.venv-audit\Scripts\python.exe .\run-postgres-qualification.py postgres-de699-targets.json postgres-recheck
& .\.venv-audit\Scripts\python.exe .\run-postgres-qualification.py postgres-secure-targets.json postgres-secure-recheck --secure
& .\.venv-audit\Scripts\python.exe .\run-postgres-qualification.py postgres-psycopg2-targets.json postgres-driver-recheck
& .\.venv-audit\Scripts\python.exe .\pg-qualification\manage.py status
```

These are reproduction commands for the prepared fixture, not claims that the
new `*-recheck` labels were run. The underlying invocation is source-matched
`python -m pytest -vv --tb=short --durations=30 --timeout=180
--timeout-method=thread` with isolated `--basetemp` and JUnit output. Secure
runs add `MAVERICK_TEST_SECURE_DEFAULT=1`. Host-approved native process
execution is required on this Windows machine; its restricted sandbox cannot
launch PostgreSQL's restricted-token child. The helper does not install a
service or weaken the sandbox.

All secure invocations in this ledger select **core** tests. Their autouse
`_isolate_maverick_home` fixture consumes `MAVERICK_TEST_SECURE_DEFAULT=1` and
sets `MAVERICK_SECURE_DEFAULT=1` before each test. The launchers did not also
set the latter flag before collection; these results therefore describe the
core fixture's per-test secure posture, not pre-collection secure imports or
dashboard-only secure coverage. Future launchers should set both flags.

This qualifies the recorded Windows/source cohort. Linux CI with pgvector,
managed PostgreSQL, MySQL/MariaDB, and later untested product changes remain
separate qualifications.
