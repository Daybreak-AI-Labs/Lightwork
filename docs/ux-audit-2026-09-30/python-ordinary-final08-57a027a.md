# Final08 ordinary Python run: completed with one failure

The complete native Windows Python 3.12.12 ordinary run at
`57a027adfdd8df955d33aaea88ec7089aafc6c60` finished with **40,098 passed,
one failed, 597 skipped and 23 warnings** in 3,049.04 seconds. JUnit records
zero errors. Pytest and its controller both returned exit code 1. This remains
a failed full run; separate focused passes and the subsequent test correction
do not rewrite its outcome.

The [machine-readable receipt](python-ordinary-final08-57a027a.json) binds the
JUnit, complete log, streamed phase reports, failure log and start/result
receipts by SHA-256. The source was clean before and after execution, and its
head did not change. The controller recorded no phase timeout, cleanup error,
source-verification error or remaining pytest process. JUnit's suite duration
is 3,048.206 seconds; the complete controller duration is 3,053.92 seconds.

Execution used four workers, file-level scheduling, no worker restarts, the
original 180-second per-test limit and 7,200-second phase limit. The selector
was `not heavy_concurrency`; neither secure-default flag was enabled. The
Windows account and fresh temporary-directory requirements are explained in
the separate [account diagnosis](final08-windows-account-diagnosis.md).
That interrupted earlier sandbox-account attempt is retained separately.

## The one failure

`packages/maverick-core/tests/test_flow_payloads.py::test_unavailable_or_mismatched_artifact_never_falls_back_to_preview[bytes]`
failed with `DID NOT RAISE FlowPayloadError`, on worker `gw1`, after a
0.1790315-second call. Its setup and teardown passed.

The [retained-fixture investigation](flow-payload-fault-injection-57a027a.md)
establishes that this fault injection did not corrupt the artifact. The
35,159-byte encrypted blob already ended in `0x58` (`X`), so replacing its final
byte with literal `X` left both the bytes and SHA-256 unchanged. The resulting
digest still matched the stored reference. Accepting that unchanged artifact
is consistent with the existing production integrity check.

Test-only correction `2decc28d5a2709ec1d362bdfb8499aaa13aacfce` flips one bit
and explicitly asserts that the bytes changed. It preserves the original
`FlowPayloadError` expectation, production code and test budgets. The JSON
receipt binds the investigation as committed at that correction. Results from
the corrected source are recorded separately; this document makes no claim
that its fresh full qualification has finished.

## Seven additional skips

The preceding final07 run at `2a765aa3e93cc7775090f574f027bd855b33aef5`
had 40,072 passes, one failure, 590 skips and 22 warnings. All 590 skipped
identities remain skipped here. There are 33 additional JUnit identities:
26 passed and seven skipped. No prior identity was removed.

All seven new skips are PostgreSQL variants in
`packages/maverick-core/tests/test_goal_list_cursor.py`, with the same explicit
reason: `MAVERICK_PG_DSN not set (no dedicated Postgres test service)`.

| New skipped case | Separate ordinary PostgreSQL run | Separate secure PostgreSQL run |
| --- | --- | --- |
| `test_cursor_reaches_oldest_of_125_after_insert_and_deleted_anchor[postgres]` | Passed | Passed |
| `test_cursor_keeps_filters_and_current_owner_after_revocation[postgres]` | Passed | Passed |
| `test_cursor_is_id_bound_even_in_ascending_order_and_preserves_legacy_offset[postgres]` | Passed | Passed |
| `test_invalid_cursor_refuses_before_query[postgres-0]` | Passed | Passed |
| `test_invalid_cursor_refuses_before_query[postgres--1]` | Passed | Passed |
| `test_invalid_cursor_refuses_before_query[postgres-9223372036854775808]` | Passed | Passed |
| `test_cursor_retains_current_workspace[postgres]` | Passed | Passed |

Those supplementary outcomes come from the two complete cursor files, each
**23 passed, zero failed/errors/skipped**, on private PostgreSQL 16.15 at
`a0c1da8b9005a15fc80882ce286c75d786a48e57`. Their
[existing receipt](mobile-task-cursor-qualification.json) identifies every
case and both postures. All 16 referenced raw artifact hashes were reverified,
and all six recorded test/product Git blobs match final08. The seven identities
match exactly. These separate local service runs provide evidence for the
service-gated cases while preserving this full run's 597-skipped result.

Six prior skip messages differ only in generated temporary-directory and
worker paths; each still reports Windows symlink privilege error 1314. The JSON
retains both versions. Streamed reports reconcile the current skips as 494
setup skips plus 95 call skips plus eight collection placeholders. They also
record 40,098 passing calls, one failing call and 40,688 passing teardowns,
with no duplicate call reports or setup/teardown failures.

## Historical transport failure and remaining boundaries

The formerly failing
`test_bounded_queue_overflow_is_durable_serial_and_in_intake_order` passed in
this full run on `gw2`; its call lasted **4.9408556 seconds**, with the original
deadline and assertions intact. This establishes a passing integrated
execution. It does not establish the cause of the
[historical final07 timeout](transport-timeout-investigation-95aa61e.md), which
remains unexplained in the retained evidence.

The 23 warnings remain visible in the log: Starlette/AnyIO, `msilib`, plugin
API v1, bare-string channel replies and Click deprecations. This document
covers only the completed ordinary run. Fresh ordinary, secure-default,
serial heavy-concurrency and supported-interpreter qualification on the
corrected source are tracked separately.
