# Supported Python interpreter qualification

Native Windows AMD64 Python **3.10.19 and 3.11.14** each collected the same
**40,660 executable cases** at `3640fcf7906d3f7129df1b34bc1718492a164bcf`:
40,653 ordinary and seven heavy cases, with eight optional collection skips
and no collection errors. After the test-only correction below, the same
**405 distinct cases pass in all four critical cohorts**, with seven explicit
database-service skips per cohort. Collection is not full-suite runtime proof.

| Complete 18-file cohort | Passed | Skipped | Failed/errors | Helper time |
| --- | ---: | ---: | ---: | ---: |
| Python 3.10 ordinary | 405 | 7 | 0 | 107.270 s |
| Python 3.10 secure | 405 | 7 | 0 | 113.306 s |
| Python 3.11 ordinary | 405 | 7 | 0 | 112.120 s |
| Python 3.11 secure | 405 | 7 | 0 | 133.425 s |

The critical runs use test source
`f1f41a3e13a8d3f574546e8a577a48555e3ec8bc` and unchanged product wheels from
`3640fcf`. Each selects the identical 412 case IDs across 18 complete files:
companion storage/model/API/installer, employee privacy and secondary surfaces,
knowledge reindexing/cost controls, tenant lifecycle, workflow credentials,
workspace records/feedback, SQL execution, shared hero, theme and localization.
The [machine-readable receipt](supported-python-qualification-3640fcf.json)
lists every file, final selected ID, skip, source boundary and raw evidence hash.
It identifies the two newly added admission cases separately from the earlier
full collection. Repeated modes and focused runs are not additional distinct tests.

## Installation and source evidence

All eight first-party wheels passed ZIP/hash checks and **4,827 payload files**
matched the exact Git archive. Its SHA256 is
`ee91f88c1705b09b47a7ce9a651c7e312a5d7645f48545ea5722cfc728c4d0b5`.
The eight hashes and payload counts are in the receipt. The final build passed
two static gates, 16 offline contracts and eight wheel builds.

The two task-local environments use 100/96 verified binary wheels totaling
81,922,852/81,854,152 bytes respectively. Every third-party wheel came from the
official PyPI file host with its resolver SHA256 verified; all eight first-party
requirements were direct local wheels. Dependency declarations and CI constraints
were unchanged from the retained resolution plan. `pip check`, four CI tool pins,
41/40 active first-party requirement checks and exact constraint closure for
92/88 external distributions all passed. Package origins resolve inside each
private environment; no Python 3.12 native extension was reused.

Runs used isolated home/temp/configuration, an OS-only inherited environment,
no service credentials, disabled model auto-fetch and a Python audit guard
rejecting external DNS/socket connections in the test process. Both secure flags
were set before imports for secure runs. Source hashes and clean tracked state
were checked before and after every run. Native file access was necessary to
read the private wheel artifacts after a default-sandbox read was denied; no
ACLs were weakened. Native Python 3.10 also parsed all 754 changed Python files.

## Retained failure and test correction

The initial Python 3.10 critical run at `3640fcf` finished with **402 passed,
one failed and seven skipped**. The SQLAlchemy SQLite deadline case expected
`sqlite_progress_and_interrupt` but observed `unsupported`. Its 50 ms absolute
deadline covers setup as well as execution. A deadline error before binding a
driver is consistent with that result, but the original substage was not traced.

An instrumented isolated run of the unchanged source then passed. It recorded
32 ms before SQLite binding and SQLAlchemy's `before_cursor_execute` event at
approximately 47 ms of the 50 ms allowance. This is a pre-driver observation,
not an independent SQLite VM trace, and does not prove the original failure's
exact timing. Both the initial failure and later passing trace are retained.

Commit `f1f41a3` performs a real `SELECT 1` warmup before the unchanged 50 ms
measurement and requires SQLite's own trace callback to observe the recursive
statement. It retains the two-second completion bound, error, finished-handle,
cancellation-support and connection-release assertions. The measured call still
owns a fresh engine/connection. Two deterministic SQLAlchemy engine-initialization
expiry cases assert zero connection/dispatch, engine disposal, no watcher,
`not_started`, completed cleanup and unchanged rows; these differ from the prior
raw-SQLite admission regressions. Product deadline and cancellation code did not
change. The complete affected module passed **25 cases with seven service skips**
on each interpreter in each mode before the four larger runs. Independent static
review found no weakened cancellation contract.

## Remaining boundaries

The seven runtime skips require PostgreSQL (four) or MySQL/MariaDB (three);
those services were not started for this interpreter slice. Separate native
database results are recorded in [the database qualification](remaining-database-qualification.md).
The eight collection skips are Telegram, Z3, DuckDB, observability, pandas and
three official SWE-bench harness gates. Their absence does not become a pass.
The Starlette/AnyIO deprecation warning is retained in each critical run.

**Full Python 3.10 ordinary and serial-heavy runtime phases remain scheduled
separately after the parent-owned Python 3.12 phases.** This record does not
claim those phases, full Python 3.11 runtime, release-host or live-provider
qualification. Raw logs, JUnit, exact collection receipts, dependency provenance
and the diagnostic trace remain under `audit/compat-interpreters` in the task
workspace; the checked-in JSON pins their hashes.
