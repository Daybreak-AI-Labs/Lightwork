# Local qualification checkpoints

Local testing is authorized. These results replace the earlier testing deferral;
they do not certify an installed release or live provider integration. All
provider, identity-provider and billing boundaries use synthetic data or mocks.
No deployment configuration or credentials were changed.

## Current evidence snapshot, 2026-10-01

This snapshot was recorded while the final07 Python ordinary phase was running.
The register has **190 implemented entries awaiting acceptance and five
qualification entries**. LW-052's 125-task regression reproduced a missing
page-boundary task and retained deleted content. The scoped cursor/window repair
is integrated; all 108 mobile tests and the 22-case backend group pass locally.
Seven real-PostgreSQL cursor variants and final integrated checks remain queued.
The five separately approved security repairs remain implemented with final
integrated qualification pending.

| Evidence | Exact execution source | Current result and limit |
| --- | --- | --- |
| Full Python 3.12 ordinary | `2a765aa3e93cc7775090f574f027bd855b33aef5` (`qualification-final-07`) | Ordinary is active. Later cursor/mobile fixes are outside this frozen run; subsequent secure, serial-heavy and floor-interpreter phases require a new source binding. No complete pass is claimed. Source/process receipts are retained under `audit/qualification-final-07-*`. |
| [Static gates and package builds](static-build-qualification-3640fcf.md) | `3640fcf7906d3f7129df1b34bc1718492a164bcf` | All 26 checks passed: Ruff, Vulture, 16 offline contracts and eight wheels. All 4,827 payload files match the exact Git archive. Later product changes require renewed build/static verification. |
| [Complete browser and JavaScript](browser-quiet-qualification-2026-10-01.md) | `11c30996c98a0ad0c9583005223019ad0d75b8b4` | All 90 distinct Chrome journeys across default/setup profiles and all 1,695 JavaScript tests passed. [Exact source comparison](browser-source-relationship-2a765aa.md) proves the unchanged dashboard/frontend relationship to `2a765aa`; it does not claim a browser rerun there or cover the active mobile change. |
| [Current-client Qdrant repair](qdrant-current-client-qualification-e22ffbc.md) | `e22ffbcca2af43887ed4c142b2633fd86a51471c` | 75 distinct passing IDs across native adapter and real loopback-server checks; both postures include actual offline ONNX inference. One intentional parameter skip remains. Repeated executions are not additional unique tests. |
| [Secure PostgreSQL follow-up](postgresql-secure-gap-qualification.md) | `aa37a5a58d81940af4ab87434f6c33e6af96c75e` | All 363 cases in 29 complete files passed with both secure flags, zero skips/errors/failures. This covers all 270 identities previously lacking secure PostgreSQL evidence. The private server stopped; twelve pgvector extension cases still require the absent native compiler prerequisites. |
| [Python 3.10 / 3.11 compatibility](supported-python-qualification-3640fcf.md) | Full collection at `3640fcf`; critical test source `f1f41a3e13a8d3f574546e8a577a48555e3ec8bc`, unchanged `3640fcf` product wheels | Each interpreter collected the same 40,660 executable IDs before the two later admission tests. All four ordinary/secure critical cohorts passed the same 405 cases, with seven explicit database-service skips each. Full Python 3.10 ordinary/heavy runtime remains scheduled after Python 3.12; no full Python 3.11 runtime is claimed. |
| [Mobile pagination](mobile-pagination-qualification-4d8dedb.md) and [scoped backend cursor](mobile-task-cursor-qualification.md) | Mobile `4d8dedb11b940e98875867021b70f151e74c7fcb`; backend `737ba45287df2c09370d5f83056889f2e5be16d0` | Complete mobile suite: 108 passed; TypeScript passed. Backend: 22 passed in each posture with seven native-PostgreSQL variants pending. Original failing reproductions are retained. Native device scrolling/accessibility remains separate. |

The dated sections below preserve earlier failed, incomplete and focused runs,
including their then-pending work. Their historical present-tense wording does
not supersede this snapshot. Final acceptance still requires the fresh integrated
results and any repair follow-ups; counts from overlapping cohorts must not be
summed. Signed releases, unavailable native platforms, real provider/IdP journeys,
distributed deployment failures and human language/accessibility review retain
their explicit qualification boundaries.

## Full-suite checkpoints

| Checkout | Result | Meaning |
| --- | --- | --- |
| `b0a7eea` | 36,262 passed, 3,102 failed, 195 errors, 551 skipped | Failed initial ordinary Python qualification. Retained as failure evidence, not acceptance. |
| `d822db99ee48aab4fdded47fc799486e8e5ea44f` | All 1,594 dashboard JavaScript cases passed | Complete JavaScript run at this checkpoint. Later controller changes require integrated requalification. |
| `9bea7d4e4c751b022cf5f9d6b261c8af6f025d6c` | Ordinary run stopped during collection | Its long native temporary path was replaced after Windows file tests demonstrated a bounded environment requirement. No pass/fail total is claimed. |
| `cfa16bfc6659b59d4e855beb024914409cad44a7` | Incomplete; stopped after worker crash and scheduler stall at99% | Read-only stack inspection showed all workers waiting, not executing tests. No final JUnit or pass/fail total exists; progress dots are not acceptance. The actual assertion failures remain unresolved. |
| `f036cef90a6d6fd1ddaa3f76bd857b9ca9bd4c3e` | All1,680 dashboard JavaScript tests passed across176 files | Zero failures/skips/cancellations. Exact manifests and source boundaries: dashboard-javascript-qualification.md. |
| `12ff6b8ad9e42f73a13467c3514f9687ba57465f` | Incomplete: 5,669 passed, 10 skipped, two worker crashes | Run ended after 837.37 seconds with both settings catalog cases losing their workers. Remaining cases were not executed. Streamed reports and JUnit retain the partial result; this is not a full-suite pass. |
| `29aee7e75b6d31012f087ec725456eec20e206af` | 39,763 passed, 219 failed, 555 skipped | Complete failed ordinary run; no worker losses or collection errors. Both former crash cases passed unchanged. Later repairs require fresh qualification. |
| `2c1e8a888eee011a2851836079b57c9929b8d745` | Ordinary: 39,987 passed, 17 failed, 558 skipped; secure incomplete | Ordinary completed in 2,736.35 seconds, with zero errors or worker losses. Its 17 failures belong to two test modules repaired in `a726d51`; the complete two-module cohort passed 46 cases with two POSIX skips per posture. Secure ended after 7,581 passes, 108 skips and one worker crash during the timed stack dump in the 1,000-round self-harness model test. Both secure switches were explicit. No complete secure pass is claimed. |

The initial failures are not dismissed as a single fixture problem. Production
repairs include exact ownership for local schedules, employee content reads in
independent reviews, complete private-record erasure, immediate learning stop,
explicit recovery-authority classification, restored safe governance metadata,
and private UI cleanup after access loss. Updated tests exercise the actual
current revision/review/owner boundaries and preserve denial assertions.

## Focused evidence

- All 70 distinct installed-Chrome journeys passed across the complete browser
  run and targeted repairs. `browser-qualification.md` records the five-role
  invitation, approval, export and departure journey and the exact limits.
  This is not a single final-commit 70-case run.
- Core privacy: all 205 cases passed; two existing symbolic-link cases require
  Windows link-creation privilege and are explicitly skipped. The full harness
  and targeted follow-ups are recorded in the identity qualification documents.
- Dashboard controls: the 17-family run passed 498 cases; its one outdated owned
  approval-history expectation was corrected and passed separately. The
  independent content-read and employee-privacy regressions passed 17 cases.
- Reviewed task/schedule operations passed 186 distinct cases at their source
  checkpoint; conversation qualification records overlap with earlier runs.
- Native regression group: 259 cases passed in each of ordinary and secure
  default modes. Two link-privilege skips remain explicit in each run.
- The 75 native-file cases covering spreadsheets, document processing, fair
  queues and retained-custody recovery pass with native scratch under the short
  workspace `.t` directory. External scratch reproduced `lxml` access errors
  under pytest; excessively long workspace paths reproduced Windows cleanup
  limits. No library or file-safety guard was bypassed.
- Ruff and Vulture pass at `0ac794e`; subsequent changes still require the final
  static gate. Earlier offline contract checks and eight package wheels passed
  at their documented checkpoint and will be repeated after integration.

Focused counts may overlap and must not be summed as a unique test total. Full
ordinary, serial heavy-concurrency, secure-default, browser, contract and build
results remain pending on the consolidated final source.

## Qualification boundaries

Real provider/IdP consent and accounting, native Apple/Android SDKs and devices,
signed release trust and clean-machine installation, installed IDE behavior,
dedicated PostgreSQL/distributed failure scenarios, and human language review
remain separate evidence requirements. Local JetBrains and Zed builds are
recorded in `native-build-evidence.json`; they are not distribution approval.
The five independently traced PR2530 findings were separately approved.
`additional-security-fixes.json` records implementation, regression evidence and
tenant restore-boundary regression evidence, separate from the195-entry register.

The tenant restore repair preserves namespace claims and deletion tombstones
through publication, rollback, older interrupted journals and concurrent claims.
Its eight-file secure-default cohort passed 219 tests with two Windows
symbolic-link privilege skips. All 13 new public-restore models passed. The
ordinary cohort's one inventory omission was corrected and its four-case gate
passed again; independent replay of the original restore bypass remains part
of final qualification. Failed restores may conservatively retain newly
published identity barriers; they never release those identities.

The two settings worker losses were investigated separately from assertion
failures. An unchanged isolated governance case passed in 196.03 seconds,
including 135.656 seconds rendering settings groups. Repeated authenticated
credential-key permission checks are a measured hot path. The native access
violation seen during a diagnostic dump has not been independently reproduced;
its cause remains unresolved. The batch-local decryption repair passed 88
configuration/crypto cases with two Windows privilege skips. Both unchanged
exhaustive settings cases passed serially and in parallel; the secure parallel
run passed those two and four new regressions. Timeouts and access assertions
were retained. Details: settings-config-read-qualification.md.

At cfa16bf,15 of16 offline contract gates and all8 package wheels passed. The
remaining constraint check required pip26.2.1 rather than the installed25.0.1.
After the incomplete suite was stopped, only that pinned local tool was upgraded;
constraint closure then passed with91 exactly pinned external distributions.
Final-source contracts/builds remain pending.

At `29aee7e`, Ruff, Vulture, all 16 offline contracts and all eight wheels passed.
Every wheel passed archive integrity checks, and the core configuration source
matched the exact Git export. Source and wheel hashes plus individual check
results are in static-build-qualification.json. Later source changes and the
full runtime phases remain separately tracked; these are not release approval.

Optional DuckDB qualification found a remaining LW-095 raw SQL content-read
side path. Commit `59ab6ea` retains global query permission while restricting
materialized task rows to the exact request owner. The same three real-DuckDB
regressions failed on the prepatch source, returning other employees' rows,
and passed after repair in ordinary and secure modes. This belongs to the
original approved privacy policy; it is not a sixth PR2530 finding.

## Complete ordinary checkpoint, 2026-10-01

The frozen `29aee7e75b6d31012f087ec725456eec20e206af` ordinary run
completed in 2,892.21 seconds: **39,763 passed, 219 failed, 555 skipped**,
with no collection errors or worker losses. Both previously crashing Settings
cases passed unchanged. This is a failed qualification result, not acceptance.
The exact source, JUnit, streamed failure details and process result are retained
under `audit/qualification-final-02-ordinary.*` in the local workspace.

Repairs made after that frozen source include SQL analytics owner filtering,
bounded translation delivery, named identity dispatch for code notes, and a
Preferences rendering fix for the `source` interpolation placeholder. Each
has native regressions; no response-size, access-denial or revocation assertion
was relaxed. Standalone page fixtures now assemble the same template context as
the installed application. Historical migration fixtures explicitly construct
the historical schema instead of writing it through the current schema writer.
Current-revision save, reviewed erasure and source-lineage tests use their real
native contracts. Remaining failures continue through focused review and a
fresh consolidated run; counts are not inferred from patch inspection.

Dashboard-only secure cohorts must explicitly set both
`MAVERICK_SECURE_DEFAULT=1` and `MAVERICK_TEST_SECURE_DEFAULT=1`. The latter is
consumed by the core test fixture and alone does not establish dashboard-only
production posture. The harness now sets both. Earlier dashboard-only labels
without this evidence are ordinary/posture-unspecified checks; corrected
cohorts and the final full secure run are tracked separately.

Final per-criterion review reopened LW-122 at that checkpoint because the
local-only bounded reindex implementation did not complete the existing hosted
embedding path. The subsequent implementation in `3dd047f` supports priced
standard Voyage/OpenAI routes with native cost admission and durable ambiguous
paid-outcome recovery. Its complete focused group passed 137 tests in each
posture; unpriced routes fail closed. See
`hosted-reindex-qualification-2026-10-01.md` for exact boundaries.
The same review reproduced omitted custom-theme semantic colours and hidden
mobile Close-button focus after desktop resize; their repairs are in `5bc069b`,
after the `2c1e8a8` runtime checkpoint. New local and browser regressions qualify
those changes separately before another consolidated freeze.

## Frozen checkpoint `500fc873`, 2026-10-01

Ruff, Vulture, all 16 offline contracts and all eight wheels passed. All 4,826
wheel payload files match the exact Git archive. The complete JavaScript run
passed 1,695 cases across 179 files. The default and setup browser profiles
executed all 90 unique journeys: 87 passed and three timed out. Two failures
exhausted the navigation scenario's overall deadline; one accounting-summary
assertion remained in its loading state. A later unchanged-deadline quiet run passed six of seven selected journeys;
only cold navigation failed. The first `/start` response took 25.8 seconds,
while warm navigation and accounting passed. Native route profiling and a
measured onboarding repair are active; contention alone is not a sufficient
explanation. The original failures and all quiet-run traces remain retained.

The ordinary Python run ended incomplete after 12,170 passes, 133 skips and two
failures: one outdated complete-theme expectation and a lost worker in the
large-corpus knowledge search case. No fatal traceback or timeout diagnostic
appeared in that log, so the worker-loss cause is not established. The complete
417-test theme module passes after its expectation repair in both postures,
with two POSIX-only skips each. The complete secure run finished with
**40,054 passed, three failed and 558 skipped**, zero errors or worker losses.
Its three failed expectations were the theme palette, implicit unsigned replay
and plaintext streaming under inherited encryption. The latter two corrected
complete modules pass 27 and nine cases in each posture, including real signature
and GCM-tag tamper rejection. See [the complete secure receipt](complete-secure-500fc873.md),
[signing qualification](control-plane-signing-qualification.md) and
[attachment qualification](attachment-posture-qualification.md). Original
failed/incomplete results remain retained; focused reruns do not convert them
into complete-suite passes.

Additional private native-vector qualification passed 41 cases in each posture
with one explicit live-Qdrant-server skip. Optional SDK and actual synthetic CPU
training qualification passed 181 cases in each posture, with one intentional
missing-LangChain fallback skip. These source-pinned supplemental records are
linked from their individual qualification files and overlap integrated tests.

Native database testing also reproduced an expired SQLite admission escaping
the public tool's error contract before dispatch. The narrow repair in
`dd82ce5` preserves budget, halt and cancellation propagation and reports
`transaction_status=not_started`; deterministic prepatch regressions fail and
pass after repair. Its seven complete database files passed 210 cases in each
posture, with real loopback PostgreSQL and MariaDB and no skips. This later
product change requires renewed integrated qualification; the frozen wheels and
runtime runs above do not include it.

## Later source and qualification follow-up

The SQLite repair is included in `7d38f76`, where Ruff, Vulture, all 16 offline
contracts and all eight wheels pass. Every one of 4,826 wheel payload files
matches the exact source archive; [the receipt](static-build-qualification-7d38f76.md)
records commands and hashes. Later test/documentation repairs do not alter that
product payload. Active Qdrant and onboarding changes still require a new source
freeze and consolidated validation.

A private native Qdrant server reproduced the removed `.add` text-helper failure
with qdrant-client 1.19.1. The compatibility repair is being qualified with
explicit vector/model identity, reset preservation, invocation budgets and
no automatic resend. Public model assets may be prefetched into a private cache
for bounded offline CPU inference; no live provider inference is authorized.

The [skip reconciliation](skip-reconciliation-7d38f76.md) maps every one of the
558 earlier skipped entries to supplemental execution or an explicit remaining
gate. Counts overlap integrated cases and collection placeholders are not
reported as passed tests. Additional secure PostgreSQL coverage and supported
Python 3.10/3.11 compatibility qualification are prepared. These jobs are held
while the browser performance investigation needs a quiet machine.

The [knowledge worker investigation](knowledge-worker-exit-500fc873.md)
reproduced the timeout plugin's silent-exit signature with a controlled trigger.
The original failure lacks a timer receipt, so its cause remains an inference.
The unchanged case passed focused execution and the complete secure run. Future
full-run diagnostics record timer invocation before calling the original timeout
handler; deadlines and failure behavior remain unchanged.

## Complete browser and JavaScript checkpoint `11c3099`

The integrated onboarding repair and later SQL/test corrections are included in
`11c30996c98a0ad0c9583005223019ad0d75b8b4`. All **90 distinct installed-Chrome
journeys** passed across the complete default/setup profiles, with zero retries
or unexecuted cases. All **1,695 JavaScript tests across 179 files** passed, with
zero failures, cancellations or skips. Source and tracked tree remained unchanged.
The [dated qualification record](browser-quiet-qualification-2026-10-01.md)
retains original failed runs, case reconciliation, hashes and fixture posture.

The measured repair avoids more than 2,000 unnecessary bundled-agent reads for
activated owners or workspaces with no parseable overrides. Thirteen focused
regressions pass in each Python posture. This improvement and the green quiet
rerun do not fully attribute the earlier 25.805-second outlier. Native platform,
live-provider and human accessibility/translation limits remain explicit.
Qdrant compatibility, native supplementary checks and fresh complete Python
qualification are the remaining active local work.
