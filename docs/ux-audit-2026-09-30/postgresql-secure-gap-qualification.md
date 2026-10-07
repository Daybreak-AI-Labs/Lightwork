# Secure PostgreSQL qualification follow-up

Recorded 2026-10-01T05:02:36.720683+00:00. Exact clean source:
`aa37a5a58d81940af4ab87434f6c33e6af96c75e`. **363 passed, zero failures, errors, or skips** across 29 complete
files in the PostgreSQL qualification cohort (including their existing non-PostgreSQL parameters). Every one of the **270 exact testcase identities**
previously supported only by ordinary-mode PostgreSQL evidence now has a
passing secure-mode receipt. Both `MAVERICK_TEST_SECURE_DEFAULT=1` and
`MAVERICK_SECURE_DEFAULT=1` were set before collection.

The source matched the prepared inventory's 29 test-file Git blobs exactly;
working-file SHA-256 hashes were captured before the run and verified afterward.
No test or product files were changed for this qualification. The final tree
remained clean at the same SHA. Runtime was 692.06 seconds
for the serial helper including PostgreSQL startup; JUnit records individual
case timings. Pytest elapsed time was 683.20 seconds. Raw failures remained empty.
The sole warning is Starlette `testclient.py:53`: `The anyio.abc.BlockingPortal
alias is deprecated, use anyio.from_thread.BlockingPortal instead.` No warning
was suppressed. Some read-only Git commands also reported access warnings for
the native account's global ignore file; the explicit source/cleanliness checks passed.

## Interrupted first attempt

Attempt `postgres-gap-secure-final06-01` stopped during the second parameter
of the workflow-choice recovery case after 33% progress. It has no JUnit or
helper completion receipt and is recorded as **incomplete**, with no inferred
product failure or hang cause. All original artifact hashes are preserved in
the recovery receipt. The later run repeats all 29 files with the same
assertions, both secure flags, and unchanged 180-second test / 2,700-second
helper limits; it does not resume after the interruption or exclude that case.

Recovery first verified the exact synthetic fixture's recorded PID was absent,
the loopback port could be bound, and native `pg_ctl status` returned stopped.
Two worker native-tool requests timed out before execution in automatic approval
review; the coordinator reported two additional review timeouts. The
[historical blocker record](postgresql-secure-gap-blocked-aa37a5a.md) retains
that distinction. These were review timeouts, not safety rejections. An authorized default-sandbox `pg_ctl start` reached PostgreSQL but
failed to create its additional restricted token (Windows error 87). That
failed attempt and the exact sandbox identity remain separately recorded.

The bundled `postgres.exe --help` documents direct server startup with `-D`.
An approved default-sandbox preflight launched that exact owned executable
directly, with no elevation or token manipulation. PostgreSQL recovered its
own stale lock and reached readiness. Native `pg_ctl` fast stop then succeeded,
leaving no PID file or listener. Attempt 02 used that same foreground startup
method in a hidden owned `Popen`, with a 45-second readiness bound and cleanup
in `finally`. No data/PID file was manually deleted, authentication/network
settings were unchanged, and no ACL was relaxed.

## Coverage and interpretation

The selection is `audit/pg-secure-gap-7d38f76-files.json`, expanded from the
original 270 exact JUnit identities in `pg-secure-gap-7d38f76.json`. The full
files add 93 cases beyond that specific skip inventory. The accompanying
`postgresql-secure-gap-qualification.json` records all 363 final identities,
every one of the 270 recovered identities, their previous source-tagged
ordinary receipts, and the matching source/blob/hash evidence.

The earlier [PostgreSQL ledger](postgresql-qualification.md) remains the record
of ordinary executions. **No ordinary rerun is claimed here.** The four
query-execution PostgreSQL cases and three MariaDB cancellation cases already
qualified in both modes in the [SQL/MariaDB follow-up](remaining-database-qualification.md)
were excluded from this gap selection. Unrelated SDK cases were not rerun.
The twelve pgvector server-extension cases remain unrun because the documented
MSVC C++/nmake/Windows SDK prerequisites are absent; no substitute implementation
or third-party extension binary was used.

This cohort covers PostgreSQL-backed migrations and interrupted migration
recovery, attachment/channel admission, chat/history, consent, control-plane
authority, event/federation admission, workflow lineage and learning, governed
records/pools/RLS, facts/history, deadlines, the native world model, and dashboard
boards. These are existing complete test files, with no changed assertions or
relaxed thresholds.

## Runtime isolation and shutdown

The existing official PostgreSQL 16.15-4 portable Windows x64 runtime was reused
with its private data directory, generated synthetic SCRAM credentials, 64 MB
shared buffers, 40 maximum connections, and **127.0.0.1:60165 only**. The original
[distribution provenance](postgresql-qualification.md#native-fixture-and-provenance)
and download SHA-256 remain unchanged. No service, SDK, binary package, global
environment, ACL, shared dependency, or deployment credential was changed.

The raw default-sandbox receipt records its actual Windows execution identity;
the committed record retains the identity category and hashes while omitting
the account/SID. It is
not a native-Cstep or production-service identity qualification. The source
receipt also hashes the startup-only private runner, direct-launch helper, and
failure reporter. The updated task-local runner allowlists basic OS environment variables rather
than passing inherited provider/database credentials. It supplies only the
synthetic PostgreSQL DSN, uses the private source-matched interpreter, enforces
the existing **180-second per-test cap**, and disables the optional timed
traceback diagnostic while retaining fatal traceback reporting. Tests run
serially. The helper verifies source SHA and a clean tree before and after,
retains failures as they occur, and stops its exact owned server in `finally`.

Final cleanup verified the tracked executable, PID, start epoch, data directory,
and port before shutdown. **PostgreSQL is stopped; the postmaster PID file is
absent and no listener remains on 127.0.0.1:60165.** The Qdrant worker's independent
fixture was not inspected or altered.

## Evidence and reproduction

Task-local artifacts are prefixed `audit/postgres-gap-secure-final06-02`: `.log`, `.xml`,
`.source.json`, `.result.json`, `.failures.jsonl`, `.failures.jsonl.reports`,
and `-test-blobs.json`. Their SHA-256 hashes are committed in the accompanying
machine-readable record. No credentials, database contents, binaries, virtual
environments, or full console logs are repository files.

The prepared task environment can reproduce this source-pinned cohort with:

```powershell
& .\.venv-audit\Scripts\python.exe .\run_postgres_secure_gap_direct.py slice-postgres-final06 aa37a5a58d81940af4ab87434f6c33e6af96c75e pg-secure-gap-7d38f76-files.json postgres-gap-secure-recheck --secure
```

That is a reproduction command, not an additional run. It requires the same
approved default-sandbox direct PostgreSQL launch used for this run.
These results do not establish Linux, managed PostgreSQL, pgvector, or live
provider behavior.
