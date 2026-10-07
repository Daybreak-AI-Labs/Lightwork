# Unresolved transport timeout from the final-07 ordinary run

The original full-suite failure remains **unresolved**. The focused runs below pass, but they do not explain or close the failure. No product change, timeout increase, scheduling change, or weakened assertion is included in this investigation.

The failing test was `packages/maverick-channels/tests/test_transport_supervision.py::test_bounded_queue_overflow_is_durable_serial_and_in_intake_order` at source `2a765aa3e93cc7775090f574f027bd855b33aef5`. Its existing five-second wait for eight handler starts expired; the call phase lasted 12.090 seconds. The completed full ordinary run reported 40,072 passes, this one failure, 590 skips, and 22 warnings in 3,281.79 seconds.

## What the retained original fixture establishes

A read-only inspection of the failed synthetic fixture found all eight durable admissions. The final admission reached `received` about 1.538 seconds after the first receipt was created. Message 0 reached `execution=completed`, `delivery=sending`, revision 5; messages 1 through 7 remained `received/not_ready`, revision 2. The last corresponding audit event was about 1.736 seconds after the first receipt was created. Receipt and audit metadata hashes are preserved in the [machine-readable evidence](transport-timeout-investigation-95aa61e.json); no original fixture files were changed.

This establishes the last durable transition, before any later handler began;
it does not identify the worker's active operation or receipt. The worker catches
processing exceptions and continues, so an exception leaving the first receipt
in `sending` followed by a later pre-claim stall is also consistent with these
rows. A persisted `sending` revision does not prove the awaited journal mutation
returned: `GovernedRecordStore.update` also flushes its audit outbox before
returning. The retained row has a cleared audit outbox and the matching audit
event, but no original coroutine or native stack identifies the active operation.
A native lock/rename stall and earlier process-global contamination remain
hypotheses, not established causes.

The transport worker, delivery journal, governed records, privacy records, file locking, world model, root test fixtures, and affected test file have identical Git blobs between the previously passing source `500fc87363a492def69722ef19d2cc518d171286` and `2a765aa`. The receipt records each compared blob.

## Focused checks

The unchanged complete transport module passed 11/11 in ordinary mode and 11/11 with both secure-default flags enabled. A further ordinary run under the original runner's host identity also passed 11/11. External task-local instrumentation recorded eight handler starts in order, durable transitions, recovery scans, and delivery confirmation stages without changing application behavior, deadlines, or assertions. These runs did not reproduce the original stall.

The original streamed reports omitted worker IDs. Same-worker predecessors were instead established from the retained `popen-gw1` fixture-directory creation timestamps and matched test names: the installer Copilot operations module, then the channel base module, immediately preceded the transport module. Their ordered whole-module cohort passed **36/36 ordinary and 36/36 secure**, including the two new diagnostic regression cases. This recreates the immediate predecessor sequence, not the entire long-lived full-suite worker process.

The ordered cohorts' pytest summaries took 9.83 and 9.84 seconds. Each focused module/cohort emitted the existing bare-string channel-handler deprecation warning. Source hashes, commands, flags, JUnit durations, instrumentation hashes, and all local artifact hashes are in the JSON receipt. Configuration used fresh temporary homes and no inherited service credentials; there were no provider calls.

## Diagnostic change for the next integrated run

Test-only commit `95aa61e3e9c00e5ca902a5ca905fcd3164530172`, followed by the precise counter-label correction `9bf17003a997098164ced948509a65d17a06cb0d`, adds a snapshot only when the same five-second waiter expires. It records `handler_started_count`, pending count, generation/queue flags, and coroutine/thread stack file basenames, function names, and line numbers. It does not read frame locals, include message content, mutate storage, or replace the original timeout. All original queue-bound, durability, serial-execution, and ordering assertions remain.

Two diagnostic tests verify that the identical timeout is re-raised even if diagnostic collection fails, message text is absent, and queue state remains unchanged. The label-only correction was followed by another two-case pass. Ruff, AST parsing, and whitespace checks passed. All owned diagnostic runtime has stopped. A fresh integrated run with these diagnostics is still needed to investigate a recurrence; these focused passes are not an all-green full-suite claim.
