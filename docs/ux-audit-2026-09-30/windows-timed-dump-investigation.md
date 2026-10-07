# Windows timed traceback investigation

Date: 2026-10-01. Reviewed product source: `2c1e8a888eee011a2851836079b57c9929b8d745`. Runtime: CPython 3.12.12, February 3 2026 standalone Windows AMD64 build. This investigation changes no application source, ACL enforcement, test assertion or dependency.

**The standalone experiment did not reproduce the access violation. The full secure run's crash remains an observed failure, not a passing result or a proven application defect.** An asynchronous diagnostic-runtime fault fits the trace, but the evidence below does not prove it or exonerate every application/native path.

## Observed failure and source inspection

The full secure run stopped after 7,581 passing cases and 108 skips when a worker crashed during `test_self_harness_model_based`. Its additional 120-second faulthandler diagnostic began an all-thread dump, printed two complete thread stacks and part of the application stack, then stopped immediately after `  File ` before emitting a nested Windows access-violation dump. Neither dump identified a current Python thread. The visible application stack was in `Path.is_junction`/`_unsafe_lock_alias` during audit-file verification, rather than inside a ctypes call. A sampled Python frame is not itself a native fault address.

The matching [CPython 3.12.12 traceback implementation](https://github.com/python/cpython/blob/v3.12.12/Python/traceback.c#L1169) prints that prefix immediately before reading the frame's code metadata. Its current-thread label comes from the faulting thread's Python thread state. The [watchdog implementation](https://github.com/python/cpython/blob/v3.12.12/Modules/faulthandler.c#L523) executes the delayed dump from a native watchdog thread. These details support, but do not prove, a watchdog fault while another thread changes frames.

Upstream [issue 140815](https://github.com/python/cpython/issues/140815) and [PR 140921](https://github.com/python/cpython/pull/140921) document invalid/freed-frame failures in this diagnostic path and later defensive checks. The inspected 3.12.12 source retains direct code/frame accesses. The newer [issue 158200](https://github.com/python/cpython/issues/158200) supplies additional code-metadata-churn evidence on another runtime/platform; it is related context, not a reproduction of this Lightwork failure.

Read-only inspection of the relevant Win32 helpers found no concrete pointer truncation, premature release or duplicate release. `GetSecurityInfo` retains its descriptor while the borrowed owner SID is converted; separately allocated strings are copied into Python values before each allocation is freed once. The token-information buffer stays live through SID conversion. Handles use pointer-sized types and successful CRT conversion has one descriptor close. These lifetimes match the [GetSecurityInfo ownership contract](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo) and [GetTokenInformation buffer contract](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-gettokeninformation). This review is not a heap-corruption proof.

## Bounded standalone experiment

Exactly three sequential children ran the same CPython 3.12.12 executable with `-I -S -X faulthandler`. Each had a 30-second external hard limit, an isolated task-local home/temp directory and an eight-second workload. The pure-standard-library workload repeatedly made, invoked and discarded code objects/functions with fresh filename/name metadata. Assertions verified that neither ctypes nor Lightwork modules were imported. No external calls, custom native extension, package/site initialization or application file-permission helper ran.

Two children scheduled repeated one-microsecond `dump_traceback_later` diagnostics to the Windows null device, following the general upstream churn approach. Fatal-handler output remained captured in a task-local log. The third ran the same churn without scheduling dumps. The diagnostic dump count is not asserted because repetitive output was directed to the null device.

| Attempt | Configuration | Completed function calls | Wall time | Outcome |
| --- | --- | ---: | ---: | --- |
| 1 | Repeated timed dump | 12,123,162 | 8.266 s | Exit 0; no crash or external timeout |
| 2 | Repeated timed dump | 12,628,380 | 8.296 s | Exit 0; no crash or external timeout |
| 3 | Untimed control | 12,436,968 | 8.297 s | Exit 0; no crash or external timeout |

The [machine-readable receipt](windows-timed-dump-investigation.json) preserves the exact reproducer/controller source, interpreter commands, log hashes and outcomes. No additional trials were run beyond these bounds.

## Application differential and remaining limit

The owner separately reran the exact secure 1,000-round application case with its existing 900-second test cap. With the additional 120-second diagnostic it passed in 201.97 seconds, despite a partially rendered diagnostic dump. Without the timed diagnostic it passed in 197.74 seconds. Both isolated executions passed, so this comparison establishes intermittent behavior, not causal separation.

Restoring the repository's normal test-runner behavior by omitting an additional asynchronous 120-second diagnostic does not remove the fatal handler, the existing 900-second test deadline or any application assertion. Nevertheless, a successful full secure rerun under those settings must be recorded independently. A native exception address/thread stack or a reliable minimized reproduction would be needed for stronger attribution. No ACL weakening or speculative application patch is justified by the current evidence.
