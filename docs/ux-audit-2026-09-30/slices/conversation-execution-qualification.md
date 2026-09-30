# Conversation and execution regression qualification

This checkpoint was built from integrated commit `6456e66375984bc9e2e53fe6c854319b136eb9ee`, with identity metadata checkpoint `9244cd9e361f55eaf39a3a79e54b47f203df6245` included for the oversight projection contract.

The fleet dispatch, replacement, add-agent, and deletion routes now require exact fleet ownership. A named administrator cannot mutate another employee's fleet, and anonymous local operation is limited to unowned fleets. Denial tests verify the roster and run history remain unchanged and the provider readiness gate is never reached.

Code feature admission now carries the authenticated owner and execution user into the background worker, proposal task, and native runner. The worker rechecks current authorization before creating work. Approval creation uses the current saved proposal's task context while holding its existing renewal lock, then restores the caller's prior context. Tests cover two named administrators' isolated tasks and approval queues, revoked background authority, and stale proposal input within another task's ambient context.

Updated fixtures preserve the intended privacy contract: caller-owned event polling, owner-scoped upcoming work, private external-agent runs omitted from personal boards, missing fleet tasks omitted from history, and rule names retained without private generated tool labels. No content authorization bypass was added to make these assertions pass.

## Local validation

The latest result for each of 164 distinct test cases is passing:

| Test module | Passing cases |
| --- | ---: |
| Dashboard external agents API | 44 |
| Dashboard event admission recovery | 24 |
| Dashboard code and immune actions | 21 |
| Core codebase | 20 |
| Dashboard fleet dispatch | 15 |
| Dashboard fleet CRUD | 12 |
| Dashboard immune console | 9 |
| Dashboard fleets console | 7 |
| Dashboard workforce operations | 7 |
| Dashboard fleet run history | 5 |

Matching-checkout `PYTHONPATH` was used for all eight local packages. Test reports are in the executor's `audit/conversation-execution-01.xml`, `conversation-code-02.xml`, `conversation-fleet-crud-01.xml`, and `conversation-code-binding-03.xml`. The initial run exposed three incorrect approval-binding calls; the later code run and exact binding regression passed after correction. Counts above deduplicate those reruns.

Python AST parsing, Ruff, and Git whitespace validation passed. Tests used local temporary repositories and mocked provider boundaries; no live provider, production, merge, or deployment actions ran. Integrated repository-wide qualification remains the consolidation owner's responsibility.
