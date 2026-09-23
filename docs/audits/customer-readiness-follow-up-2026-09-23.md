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

Final combined local qualification is being recorded before publication. The
evidence directory is `consolidation-evidence-20260923/pre-ci-fixes` beside the
working checkout. It contains JUnit results, commands, review notes and the
source/evidence digests; fixture credentials are excluded from published files.

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
