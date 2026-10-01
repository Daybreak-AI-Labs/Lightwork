# Source-bound learning fixture qualification

Recorded 2026-10-01 00:41 UTC. Isolated product source:
`f50e64acd5c7d4a2836537be3708e9827daaaf52`. Test-only correction:
`4ae7bd6d8fe32f11a3a3d358d819a0d5b27adc42`.

Four failures from the ordinary qualification stream reproduced in fresh
processes. They were fixture failures, not audit-key cache failures:

- `test_dream_harvests_corpus_when_configured` mocked a harvested candidate with
  no current source lineage. It now creates a real unowned goal and carries
  its exact identity and privacy schema through staging and the read assertion.
- `test_self_harness_apply_audit_uses_the_committed_outbox` supplied an empty
  proposal source list. It now uses a real unowned goal. The actual promotion
  gate, refused audit sink, durable outbox, restart, same-event retry, and
  acknowledgement checks remain intact.
- Two `test_cortex_tenant_isolation` positive controls seeded legacy addenda
  without mandatory provenance and closed the process-cached tenant world.
  Seeding now uses the native addendum writer with a real tenant-local source
  and `close_world_if_owned`. The record title also matches the test's exact
  query, and the positive control separately requires both lesson and Operating
  Record hits. Cross-tenant negative assertions remain unchanged.

No production privacy, source-ownership, tenant, encryption, learning, or audit
code changed. No permissive authority mocks or source ID zero shortcuts were
introduced.

| Local evidence label | Actual result |
| --- | --- |
| `learning-baseline-01` | Two initial files: 12 passed, 2 failed |
| `cortex-tenant-baseline-01` | Tenant file: 1 passed, 2 failed |
| `learning-cortex-final-ordinary` | All three complete files: **17 passed**, 2.52 seconds pytest |
| `learning-cortex-final-secure` | Same files: **17 passed**, 2.84 seconds pytest |

Both final invocations ran the exact working-tree patch later committed as
`4ae7bd6`; their recorded HEAD is the immutable parent `f50e64a`. The secure
launcher explicitly sets **both** `MAVERICK_SECURE_DEFAULT=1` and
`MAVERICK_TEST_SECURE_DEFAULT=1` before starting pytest. The ordinary launcher
scrubs inherited values and uses the repository's ordinary fixture posture.
These are focused replacement results, not a rerun of the entire ordinary
suite. Python 3.10 AST parsing, Ruff, and diff whitespace checks pass for all
three changed test files.

Each evidence label has a task-local `audit/<label>.log`, `.xml`, and
`.result.json` containing the exact command, immutable base, timing, and exit
code. The source-matching private Python 3.12.12 interpreter imports all eight
packages from `slice-learning-regressions`. The runner uses `python -m pytest
-vv --tb=short --showlocals --timeout=180 --timeout-method=thread`, a fresh
`--basetemp`, and JUnit output. Provider credentials are scrubbed; only
synthetic local stores and existing dependencies are used. No provider,
production, deployment, or shared dependency changes occurred.
