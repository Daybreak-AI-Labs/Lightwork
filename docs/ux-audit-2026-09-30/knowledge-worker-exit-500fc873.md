# Knowledge worker exit investigation

Source: `500fc87363a492def69722ef19d2cc518d171286`. No product code,
test source, assertion, permission check, or timeout was changed for this
investigation. [Machine-readable evidence](knowledge-worker-exit-500fc873.json)
records source and local artifact hashes.

The ordinary full04 run stopped after 12,170 passes, 133 skips, one theme
expectation failure, and one lost worker. The lost worker was executing
`test_ranked_search_covers_older_assigned_sources_in_large_corpus`. That
incomplete full run remains failed/incomplete; the focused results below do
not convert it into a complete successful qualification.

## What the exit message establishes

Installed versions are pytest 9.1.1, pytest-timeout 2.4.0, pytest-xdist 3.8.0,
and execnet 2.1.2 on Python 3.12.12/Windows. The installed
`pytest_timeout.timeout_timer` dumps its diagnostics and then calls
`os._exit(1)` in its `finally` block. Under captured xdist execution, that
intentional deadline exit can appear as `node down: Not properly terminated`
without a `Timeout` or fatal-error line in the controller's captured log.

A separate synthetic test that sleeps for two seconds with a 0.3-second cap
reproduced exactly that reporting pattern. An observer recorded entry into
the original timeout callback immediately before the worker disappeared.
This expected failure is a test of the diagnostic mechanism, not a product
regression. Both the simple observer and the proposed final-run reporter
produced a direct timer receipt; the latter recorded 0.312 seconds elapsed
for the 0.3-second deadline.

The original failed worker had no such receipt or retained native exit code.
Its deadline termination is therefore a supported inference, not a proven
cause. Absence of fatal-error text does not establish either a native crash
or a timeout.

## Unchanged case and source evidence

The focused regression used the same source, matching-source environment,
one xdist worker, original 180-second per-test cap, and the original full
306-row synthetic registry. It passed in **86.21 seconds of test execution**
(0.26 seconds setup; 91.22 seconds total pytest time). Real registration and
approval of those 306 rows took 54.69 seconds. All eight labeled queries,
four-source coverage, tenant exclusions, current-rights checks, and fresh
job materialization assertions passed. Forty content resolutions occurred,
as required by the unchanged test.

The secure full04 run independently completed all **66 cases in the entire
knowledge access module** on this same source. The large-corpus case passed
in 108.26 seconds and the pagination/revocation inventory case in 96.33
seconds, with the same 180-second cap. Both secure-default environment flags
were set by that runner. The module's fixture explicitly selects local
governed storage and disables at-rest encryption, so these results do not
claim encrypted-store or PostgreSQL qualification.

The failed ordinary test's retained temporary directory was created at
01:57:51.4587908 UTC. Its final registry row was written at 01:59:50.345666
UTC, about 118.89 seconds later, before its search phase. The focused test
registered the same rows in 54.69 seconds. The original ordinary run shared
the host with the secure suite, Chromium, and JavaScript qualification.
These observations support a contention-related deadline explanation, but
do not prove when or why the original worker exited.

The focused run still shared the host with four secure-suite workers. Its
recorded retrieval median of 3.25 seconds and maximum of 5.68 seconds cover
eight local synthetic queries; they are not a quiet-host performance
benchmark or a model-quality claim.

## Next full-run diagnostics

The private candidate `audit/audit_local_failure_reporter_timeout_candidate.py`
preserves existing controller reporting and wraps only the worker's installed
timeout callback. Immediately before invoking the original callback it
writes a per-worker receipt containing test ID, process ID, configured
deadline, method, and elapsed time. Diagnostic write failure cannot suppress
the original callback; a separate check forced an unavailable output path
and verified that the original callback still ran and returned unchanged.
The candidate's synthetic timeout probe retained the same worker-exit
behavior.

`audit/knowledge-timeout-reporter.patch` contains the reviewed change for the
private runner. It was not applied to the in-progress secure run. The next
complete run can use it without raising timeouts, skipping cases, or changing
product behavior. No independently justified product performance defect was
established in this investigation, so no speculative optimization was made.

All probes used isolated local temporary data and scrubbed provider
credentials. No live provider, production, or deployment operation occurred.
