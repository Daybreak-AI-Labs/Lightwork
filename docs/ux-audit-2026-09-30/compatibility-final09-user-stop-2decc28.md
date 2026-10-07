# Compatibility testing stopped at the user's request

The user said **“okay stop testing it.”** The active Python 3.10 ordinary suite was stopped at approximately **36%**, and all successor phases were cancelled. There is **no full Python 3.10 or 3.11 suite pass claim** for this checkpoint.

Both native collections had already completed successfully on frozen source `2decc28d5a2709ec1d362bdfb8499aaa13aacfce`. Each collected the same **40,695 unique ordered test IDs**: 40,688 ordinary and seven heavy. Each recorded eight identical optional-module/harness collection skips, zero collection errors and one Starlette/AnyIO deprecation warning. These are collection results, not executed-test passes.

Root terminated the verified owned pytest tree rooted at PID 51528. Controller session 65808 then exited with code 1, reported `pytest_still_running=false`, and confirmed unchanged source. The failure-diagnostic file was empty at cancellation. The raw `interrupted:false` field reflects an external process-tree stop rather than the controller's own keyboard-interrupt path; it does not mean the run completed. Raw receipts and logs remain unchanged. Root subsequently confirmed that all named task-owned processes and qualification processes were absent; the confirmation receipt is included in the JSON evidence.

The Python 3.10 ordinary remainder, its serial-heavy phase, and both Python 3.11 execution phases are cancelled. They will not resume automatically. No fixes or further tests were performed after the stop request; only stopping, receipt preservation and this documentation followed.

The [machine-readable evidence](compatibility-final09-user-stop-2decc28.json) records the stop reason, original receipt hashes, terminated PIDs reported by root, and the independently reconciled collection summary. Its SHA-256 is `ad67925390de093ec762964d236c58ce22514a9fe2d02270fc7453c28d7fbe98`. Completed [final09 installation proofs](compatibility-final09-installation-2decc28.md) remain valid and separate from runtime coverage. Parent-owned Python 3.12 results are documented separately.
