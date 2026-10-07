# Remaining native database qualification

Recorded 2026-10-01T02:10:21.256646+00:00. Final source: `c30affc712adfbb30464eb8929128a3beb314a68` on baseline
`500fc87363a492def69722ef19d2cc518d171286`. **210 distinct cases pass in ordinary
mode and the same 210 pass in secure mode**, with no failures, errors, or skips.
Both secure flags are set before collection. The native MariaDB cancellation
cases that were previously skipped now pass. **Twelve pgvector cases remain
unrun** because the required native extension build tools are absent.

| Final serial run | Passed | Helper elapsed | Scope |
| --- | ---: | ---: | --- |
| `remaining-database-final-ordinary-02` | 210 | 53.22 s | Seven complete test files |
| `remaining-database-final-secure-02` | 210 | 82.74 s | Same files; both secure flags |

The files are `test_query_execution.py`, `test_query_results.py`,
`test_database_tool.py`, `test_database_connection_hosts.py`,
`test_database_connection_timeouts.py`, `test_q3_2026_batch18.py`, and
`test_capability_path_enforcement.py`, all under `packages/maverick-core/tests`.
These executions include the real PostgreSQL/psycopg/psycopg2 cases in the
query-execution file. They are not a rerun of the earlier 580-case PostgreSQL
selection, and the two security modes are not added together as distinct tests.

## Repair found by the native run

The first baseline run finished with 23 passes and two failures. Under load,
SQLite's 50 ms query deadline expired inside `QueryExecution.__enter__`, outside
the helper's error handler. This exception escaped `sql_query().fn` without
the public tool's normal error result. Commit
`5fd1f8f938630f4ae968d88e50efb65b3855a737` handles this precise admission error,
finishes the unused execution handle, and returns the canonical
`QueryDeadlineExceeded` error with `transaction_status=not_started`.
It does not change any deadline, dispatch a query, or hide budget, halt, or
async cancellation signals.

Two deterministic public read/write regressions fail before the repair in each
security mode, and the identical regressions pass after it. They assert the
full error shape, no database connection attempt, no watcher, a finished
handle, and no inserted row. Three additional regressions preserve the exact
BudgetExceeded, Halted, and CancelledError objects before database dispatch.
The first broad run caught a missing `source` argument in the new Halted test;
`c30affc712adfbb30464eb8929128a3beb314a68` fixes that fixture. The fresh final
cohorts above include all five regressions and the original 50 ms SQLite case.

The initial MySQL combined-cancellation case returned an error before binding
the cancellation driver, with `cancellation_support=unsupported` and a 0.59 s
call duration. This is consistent with cold initialization consuming the
300 ms admission allowance; its original error string was not separately
captured. Later observation of the unchanged code recorded
native slow-statement dispatch, a real `KILL QUERY`, and the independent
server-side timeout. The test now performs a real `SELECT 1` warmup before its
**unchanged 300 ms deadline and under-five-second bound**, matching the existing
psycopg2 fixture approach. Each measured call still opens its own connection;
a native SQLAlchemy cursor event additionally proves that the slow statement
was actually sent. Combined cancellation, KILL-only, and server-limit-only
cases all pass, followed by a fresh successful query. No driver mock or
cancellation bypass substitutes for those native cases.

## Private fixture and provenance

The [official portable Windows ZIP workflow](https://mariadb.com/docs/server/server-management/install-and-upgrade-mariadb/installing-mariadb/binary-packages/installing-mariadb-windows-zip-packages)
was used with [MariaDB 11.4.8 Windows x64](https://archive.mariadb.org/mariadb-11.4.8/winx64-packages/mariadb-11.4.8-winx64.zip),
92,533,641 bytes. Its SHA-256
`ed86e93157af46317bb49161451c2ec258498a6fa8e68ca821ef1d780d855e6b`
matches the [upstream checksum file](https://archive.mariadb.org/mariadb-11.4.8/winx64-packages/sha256sums.txt).
The archive was checked for path traversal before private extraction.

Only a task-local data directory was initialized; no Windows service was
created. The owned server binds to `127.0.0.1:61688`, with named
pipes disabled, 64 MB InnoDB buffers, and 20 maximum connections. Root and test
passwords are generated synthetic values. The test account has SELECT only on
its fixture database; 40 empty tables with 20 columns create sufficient
synthetic metadata for the existing information-schema query. Wrong-password
rejection and the server's actual bind address, port, data directory, and named
pipe setting were verified before tests.

[PyMySQL 1.1.2](https://pypi.org/project/PyMySQL/1.1.2/), a 45,300-byte pure-Python
wheel, was downloaded from official PyPI and its published SHA-256 verified:
`e6b1d89711dd51f8f74b1631fe08f039e7d76cf67a42a323d3178f0f25762ed9`.
It was extracted into a database-only private target used by the source-matched
interpreter. The already verified private psycopg2 wheel and existing
PostgreSQL 16.15 runtime were reused. Shared dependencies were not changed.

Every invocation verifies the exact MariaDB process executable against the
retained process handle before shutdown, and verifies PostgreSQL's tracked
PID/start time/data directory/executable through its existing helper.
Both servers stop in `finally`, including failed test and fixture attempts.
Final state: **both servers stopped; neither selected port has a listener**.
No remote database, live provider, host service, production setting, or
deployment credential was used or changed.

## Exact pgvector limitation

The [first-party Windows build instructions](https://github.com/pgvector/pgvector#installation)
require Visual Studio C++ support and `nmake`. `vswhere -all -products '*'
reports no installed Visual Studio instances. `cl`, `nmake`, `clang`,
`clang-cl`, `gcc`, `cmake`, and `ninja` are absent from PATH, and Windows Kits
10 is absent. The existing Rust GNU LLVM toolchain supplies a linker but no
C compiler/Windows SDK. The official PostgreSQL archive contains 1,303 include
entries, so missing PostgreSQL headers are not the claimed blocker. No host
SDK installer or unverified third-party extension DLL was used.

The unresolved twelve cases remain the two collection-isolation cases, two
live knowledge cases, six document-publication cases, and two native reindex
cases listed in the earlier PostgreSQL ledger. MariaDB qualification is not
MySQL Server or Linux/managed-database qualification.

## Evidence and reproduction

`remaining-database-qualification.json` records every final testcase, exact
source, counts, timings, official download provenance, and artifact hashes.
Task-local `audit/` retains the final `.log`, `.xml`, `.source.json`, and
`.result.json`, pre/post deterministic regressions, original failures, and
read-only observation logs. The earlier observation helper's non-MySQL yield
error and the fixture's unsupported `shared_memory` query are explicitly
recorded as setup failures, not product passes. Credentials, database state,
binaries, and environments are not committed.

From the prepared task workspace, the serial source-pinned helper reproduces
the seven-file cohort and always shuts down its owned servers:

```powershell
& .\.venv-slice-remaining-database\Scripts\python.exe .\run_remaining_database.py remaining-database-recheck
& .\.venv-slice-remaining-database\Scripts\python.exe .\run_remaining_database.py remaining-database-secure-recheck --secure
```

These are reproduction commands, not additional claimed runs. Python 3.10 AST
parsing, Ruff for both changed Python files, and whitespace checks passed.
Native process execution requires the host-approved Windows route used by the
existing PostgreSQL qualification; it does not install a service or alter ACLs.
