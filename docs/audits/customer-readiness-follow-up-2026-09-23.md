# Customer readiness: pre-CI follow-up

This follow-up addresses the seven additional defects reproduced against
`072c09dbf197085b35829fcfc069f623b08f04a2`. It supplements the
[consolidated readiness report](customer-readiness-consolidation-2026-09-23.md);
that report's test counts and packaged-image evidence describe its earlier
snapshot, not these subsequent changes. Publication remains in draft PR #2519.

## Corrections and customer impact

| Finding | Correction | Evidence exercised |
| --- | --- | --- |
| DC-01: first-run setup generated obsolete task caps | Initial settings no longer write a $1 or 600-second task cap. Metering, strict pricing, explicit saved limits and organization controls remain active. Setup copy matches the saved policy. | Actual setup HTTP request, saved file, runtime Budget resolution, repeat initialization, explicit Settings overlay. |
| DC-02: cancelling a goal left its current await running | The execution guard checks owner cancellation before starting a coroutine and while awaiting model/tool work. It cancels and drains its owned coroutine. An admitted tool retains an unknown-effect warning. | Actual HTTP cancellation with cooperative pending work, uncapped and capped task budgets, cancellation cleanup, durable goal and episode outcomes. |
| DC-03: a known allowance refusal lost workflow continuation | The runner emits a typed refusal only before orchestration starts. The flow binding preserves a budget hold and prior node outputs only for that newly created child goal. Conditional status publication preserves a racing terminal outcome. | Actual flow-to-runner-to-Agent refusal and resumed execution; completed prior action runs once; a zero-cost stop after an effect remains indeterminate. |
| DC-04: simultaneous ingestion could replace immutable provenance | SQLite and PostgreSQL validate provenance, chunk layout and embedding identity inside the publication transaction. Conflicts do not partially publish. Matching retries and partial repairs retain the first admission timestamp. | Independent connections, both callers reading absence, conflicting/matching concurrent writers, partial repair, immutable readback and winning subject erasure; native PostgreSQL as well as SQLite. |
| DC-05: interrupted retention stranded attachment files | The receipt records bounded pending cleanup before file deletion. A restart resumes remaining cleanup; completion follows confirmed absence. Legacy compacted receipts recover valid owned orphan files. A failing receipt advances the rotating cursor instead of starving later records. | Failures at each attachment unlink, failed final receipt write, stale/concurrent transitions, restore hold, bounded legacy validation, restart, dedup preservation and two-record starvation regression. |
| DC-06: SSH setup saved no destination | Interactive setup collects and validates the SSH destination. Configuration publication and smoke checks reject a missing or malformed destination. Remote workspace defaults use POSIX spelling. | Wizard-to-file-to-loader-to-factory roundtrip, invalid values preserving existing configuration, smoke rejection before any SSH call. |
| DC-07: VPS installation provisioned an SSH path locally | SSH deployments give the local service its own state workspace. The remote work directory remains remote; local/container path checks retain their existing behavior. | Remote tilde and absolute paths, unrelated local collision, rendered service paths and local write probe. |

The same compatibility review corrected one adjacent defect: `get_knowledge()`
now carries the saved Qdrant URL through to its adapter. A regression checks that
the configured destination reaches construction without contacting that service.

## Qdrant compatibility limitation

This release supports atomic governed ingestion with SQLite and PostgreSQL.
Qdrant's existing adapter has no shared atomic fence for document provenance and
collection embedding identity. It now refuses **new or partial governed
publication before embedding or vector/document writes**, with an explicit error
directing ingestion to a supported backend. Local accounting receipts can still
be recorded. There is no silent backend substitution.

Existing Qdrant reads, subject erasure and complete matching read-only retries
remain supported. The installer and knowledge documentation disclose this
limitation. This is a material restriction for a deployment expecting new Qdrant
ingestion; the adapter is not qualified for that use. A distributed publication
implementation and its failure tests remain future work.

## Qualification

The full suite ran at `4acec337575dfe2c06fc088b47f6168cc194f838`. Runtime source remains identical to that
snapshot. Three test files subsequently received fixture-only corrections,
preserving every existing assertion; their complete modules passed in both
modes. Source digests and the exact correction are recorded separately. The
following results are local; overlapping cohorts must not be added together.

| Check | Result |
| --- | --- |
| Full secure-mode Python suite, excluding heavy concurrency | 31,128 passed; 480 skipped; 5 failures/errors |
| Corrected runner-fixture modules, ordinary and secure | 61 passed; 0 skipped; 0 failures/errors in each mode; resolves every full-suite failure |
| Full-suite cases qualified by the full run plus corrected-module rerun | 31,133 passed; 480 skipped; 0 failures/errors |
| Heavy concurrency, secure and serial after the ordinary suite | 7 passed; 8 skipped; 0 failures/errors |
| Setup/SSH/VPS affected cohort, ordinary and secure | 108 passed; 0 skipped; 0 failures/errors in each mode |
| Knowledge plus core/dashboard callers, ordinary and secure | 292 passed; 3 skipped; 0 failures/errors in each mode |
| Final knowledge configuration/wizard/publication follow-up | 30 passed; 0 skipped; 0 failures/errors in each mode |
| Channel package, ordinary | 378 passed; 8 skipped; 0 failures/errors |
| Final retention regressions, secure | 31 passed; 0 skipped; 0 failures/errors |
| Cancellation, workflows, runner and world backends, secure | 340 passed; 0 skipped; 0 failures/errors |
| Same runtime cohort, ordinary | 340 distinct cases passed after correcting four new PostgreSQL test assertions and rerunning its complete 96-case module |
| Required static/contract/governance checks | All 16 passed, including whole-repository Ruff and Vulture |
| Fresh setup in real Chromium | One journey passed at 320px and 1280px, including actual save, reload and saved-policy inspection |
| Signed governance and harness benchmark evidence | Refreshed for this source and both tracked-artifact verifiers passed |
| Local secret delta scan | No new findings; baseline unchanged; no network verification |

Skipped tests are not passes. The separate native PostgreSQL cohorts execute
backend cases that the default full-suite run skips. Three parser-dependent
knowledge cases lack optional native document libraries in this Python runtime.
The initial channel-wide invocation was interrupted without a final receipt;
the bounded rerun above is the qualification result. Initial failures in newly
added PostgreSQL assertions were test cursor-access errors, corrected before
the final focused secure run. The full run then exposed stale runner fixtures
that pre-labelled goals completed or supplied an object without the Budget
contract. Their correction uses pending-to-done goals and real uncapped Budgets;
production checks and existing assertions are unchanged. Failed and incomplete
attempt logs are retained. A second complete repository run was not performed.

The evidence directory is `consolidation-evidence-20260923/pre-ci-fixes` beside
the working checkout. `qualification.json` records exact JUnit counts, source
and artifact digests, review receipts and limitations. No fixture credentials
are published. Hosted CI is intentionally deferred.

## Boundaries

- Cancellation is cooperative. A connector that delays cancellation cleanup can
  retain execution capacity; a remote effect is not reversed by cancelling its
  local await. Interrupted work with a possible effect remains non-replayable.
- PostgreSQL qualification uses an owned disposable local service and synthetic
  records. Live model providers and customer messaging services are not exercised.
- SSH destination validation and service rendering do not prove remote
  connectivity or a deployed Linux/systemd installation.
- Existing container, desktop and browser evidence is not silently relabelled as
  a new packaged acceptance test. New evidence is identified separately.
- Hosted CI remains deferred. Passing local checks does not establish that every
  defect in the repository has been found or that a customer deployment is ready
  without environment-specific acceptance.
