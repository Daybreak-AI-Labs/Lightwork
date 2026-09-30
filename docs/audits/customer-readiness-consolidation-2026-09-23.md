# Customer-readiness consolidation — 23 September 2026

This report records the repair and local qualification of the 54 tracked audit findings, the task-limit redesign and the integration of PRs #2519–#2523. It is a bounded engineering assessment, not a claim that every defect, exploit or customer-specific integration has been found.

Implementation and generated benchmark evidence baseline: `2a1368a8e1145188827bd98c0dd084881eb6788e`. The original audit checkout is preserved separately; all repairs were integrated in an isolated checkout. The qualification artifacts are retained in the accompanying `consolidation-evidence-20260923` directory. Raw logs and private fixture state are not shipped as customer documentation.

## Customer behavior

- Ordinary tasks no longer receive generated dollar, time, token, tool-call or 25-step limits. Explicit invocation/operator limits and organization spending controls remain enforced. Existing numeric configuration is retained until an operator deliberately disables task defaults; malformed explicit policy does not silently become unlimited.
- Interrupted workflows retain the correct nested invocation and completed work. HALT and allowance signals cannot be swallowed by business-error handlers, and incomplete model responses are not accepted as finished answers.
- Knowledge ingestion fences embedding identity changes and stale writes; long-document retrieval uses the searchable content rather than its display preview. Reindexing and tenant separation were exercised against native PostgreSQL/pgvector.
- Collection receipts, provider holds, channel delivery and restored execution authority retain their reconciliation requirements. Unknown external outcomes are not guessed or automatically replayed.
- Client fixes cover stale assessment actions, recoverable consent/PIA submissions, bounded polling and desktop waits, visible input errors and a shared script needed by four dashboard pages.
- Deployment fixes create and preserve the selected workspace, write configuration atomically, validate relay requests and make failed demo startup visible.
- Imported branching and operational parameters retain their meaning or require explicit repair when the source expression cannot be translated safely.

See [task limits](../task-limits.md), [provider spending](../provider-spending.md), [channel delivery](../channel-delivery.md), [billing](../billing.md) and [knowledge](../knowledge.md) for the operating behavior and limits.

## Local qualification

| Area | Evidence and result |
| --- | --- |
| Full ordinary Python discovery run | 30,990 passed; 41 failed/error; 470 skipped. Failures were retained and investigated. |
| Every module containing an ordinary-run failure | 333 passed; 0 failed/error; 1 skipped. Includes all 41 initial failing cases and their neighboring tests. |
| Full secure-mode Python suite | 31,075 passed; 0 failed/error; 470 skipped. |
| Serial concurrency, ordinary mode | 7 passed; 0 failed/error; 8 skipped. The eight skips are collection-time optional dependencies. |
| Serial concurrency, secure mode | 7 passed; 0 failed/error; 0 skipped. Same seven workload cases, selected directly. |
| Native PostgreSQL/pgvector | 349 of 350 cases passed initially; the remaining test assumed no DSN while the native fixture intentionally supplied one. Its complete 54-case knowledge module passed after isolating the missing-DSN fixture. The 350-case cohort therefore has no unresolved failure; overlapping reruns are not added to the distinct count. |
| Native document worker | Nine passed: actual OCR, office/PDF and LaTeX engines, recalculation, failure handling, opaque macro-byte preservation and container cleanup. This does not certify customer VBA execution. |
| Frontend unit/contracts | 909 distinct tests passed across dashboard, desktop JavaScript, client contracts, consent web, TypeScript plugin SDK and installer UI. The corrected installer dependency closure separately passed its ten tests, type checks and build. |
| Real Chromium acceptance | main: 61 passed, 7 skipped, zero failures or flakes; setup: 7 passed, 0 skipped, zero failures or flakes. The seven setup-only cases are skipped in the main phase and passed separately in setup, giving 68 distinct passing cases. Both processes and reports completed cleanly; the qualified source snapshot remained unchanged. Services, identities and external effects are local synthetic fixtures. |
| Native runtime container | Final image `sha256:84fe01ba67ab4c2c51d3393723cacf3cd1724674988721f55f11e37ef7da7b97` passed dependency and release-runtime checks. All 4,321 packaged files matched the checkout: 1,508 Python files and 2,813 declared assets. Two fresh unprivileged, network-isolated containers preserved the workspace artifact and native world-state goal across replacement. |
| Required static/contract gates | All 16 gates passed on the committed source, including Ruff, Vulture, reachability, dispatch, egress, asynchronous routes, failure policy and schema/migration governance. Both regenerated signed benchmark receipts verified; deterministic harness overhead invariants passed. |
| Linux Rust | 28 audit/scanner tests passed in the pinned local toolchain container; the wasm crate and documentation targets contained no tests. Native Windows/Tauri and PyO3 acceptance are separate. |
| Domain catalog | 2,031 packs passed lint. The migration preserved risk ceilings and deny lists; 255 packs still declare external adapter prerequisites. Catalog validity does not make those integrations provisioned. |
| Documentation | Public and full-site strict MkDocs builds passed with zero warnings or errors. The existing docs environment differs from current pins in three dependencies; no claim of exact docs-lock reinstallation. Pre-existing full-site INFO link diagnostics are recorded separately. |
| Dependency/security hygiene | Required Bandit threshold and offline secret-baseline delta passed. Public audits covered 124 installed external Python versions, 140 cross-platform pins, and 17 OSV dependency surfaces. Existing Rust advisory exceptions remain; this is not a completed exhaustive security scan. |

Counts in different rows overlap and must not be summed into a single independent-test total.

## Audited findings

The [machine-readable evidence index](customer-readiness-consolidation-2026-09-23.json) maps every finding to its retained qualification artifacts and SHA-256 hashes.

All 54 tracked findings have local implementations and focused passing regression evidence. The final qualification rows above describe the wider validation. P1/P2/P3 are the original audit priorities.

| ID | Priority | Repaired failure |
| --- | --- | --- |
| CORE-001 | P1 | Nested workflow retries can resume the wrong node and falsely complete |
| CORE-002 | P1 | Flow catch scopes consume a connector HALT and continue execution |
| CORE-003 | P2 | An agent accepts token-truncated FINAL text as a successful answer |
| CORE-004 | P1 | Knowledge re-ingestion does not rebuild vectors after embedding-model changes |
| CORE-005 | P1 | Governed long-document search silently omits text after the preview cutoff |
| CORE-006 | P1 | Flow budget-skipped work is marked done and its message becomes business data |
| CORE-007 | P2 | Cohere retrieval sends query text as search_document |
| CORE-008 | P2 | Process-scoped skill vector identity causes shared cache churn across workers |
| CORE-009 | P2 | Calendar free-slot result can omit its incomplete-busy-data warning |
| CORE-010 | P2 | Benchmark harness grades failed executions and turns NaN into a perfect pass |
| CORE-011 | P2 | DSAR fulfillment loses the verified request channel and delivers an empty bundle |
| CORE-012 | P2 | Historical regulatory search loses valid older versions after a later publication |
| CORE-013 | P2 | Governance reconciliation treats a new observation timestamp as authority drift |
| CORE-014 | P2 | Vendor document negations are converted into positive control answers |
| CORE-015 | P2 | Default ROI report adds mutually exclusive planning scenarios together |
| REL-01 | P2 | Channel retention compaction cannot progress beyond its first bounded window |
| REL-02 | P2 | Telegram recovery consumes the agent execution claim before retriable attachment preparation |
| REL-03 | P2 | MCP sidecar advertises SSE upstream responses but rejects them as invalid JSON |
| REL-04 | P2 | Malformed OAuth refresh success body poisons the sealed vault and prevents future refresh |
| REL-05 | P2 | Vendor license activation commits before its audit write and returns failure after activation |
| REL-CARRY-RESTORE | P2 | Restored channel authorities block bootstrap until a supported reconciliation authority exists |
| CLIENTS-001 | P2 | Delayed assessment decision acts on the next opened assessment |
| CLIENTS-002 | P2 | PIA guided answers advance after refusal and lock on transport failure |
| CLIENTS-003 | P2 | Desktop recovery mode readiness wait has no effective deadline |
| CLIENTS-004 | P2 | Status widget permanently stops polling after one hanging request |
| CLIENTS-005 | P3 | Status widget retains a removed result after a successful empty refresh |
| CLIENTS-006 | P3 | Chat widget allows an overlong goal but hides the server correction |
| ROOT-001 | P2 | VPS service uses a workspace that the default setup does not create |
| ROOT-002 | P2 | Advanced setup saves a new local workspace without creating it |
| ROOT-003 | P2 | Interrupted wizard writes leave the active configuration truncated |
| ROOT-004 | P2 | Container workspace defaults fall outside the declared persistent mounts |
| ROOT-005 | P3 | Several deployment recipes omit a required dashboard credential |
| ROOT-006 | P2 | Relay malformed requests escape validation and close without a JSON error |
| ROOT-007 | P3 | Demo launcher reports success even when every health check fails |
| BILL-001 | P2 | Subscription sync treats a canceled subscription as current |
| BILL-002 | P2 | Metered sync reuses an idempotency key for changed open-period amounts |
| BILL-003 | P2 | Metered collection ignores the configured minimum invoice charge |
| BILL-004 | P3 | Currency conversion violates its half-up rounding contract |
| CLI-001 | P2 | Cancelling multiline chat input submits the unfinished text as a goal |
| CLI-002 | P2 | Resume exits successfully when the actual kernel refuses the run as halted |
| SBX-001 | P2 | SSH sandbox ignores the explicit workdir override and runs in the configured directory |
| CLIENTS-007 | P2 | Bedrock forwarder leaves serialized object properties as strings |
| CLIENTS-008 | P2 | Documented live-schema import exposes operations the Bedrock forwarder rejects |
| CLIENTS-009 | P3 | Pitch demo form stays disabled when reopened after successful delivery |
| CONSENT-001 | P2 | Consent policy form cannot publish the next policy version |
| CONSENT-002 | P2 | Preference center keeps old checkboxes after accepting or rejecting all |
| PACK-001 | P1 | Shipped domain playbooks require built-in tools their own capability ceilings reject |
| PACK-002 | P2 | Four built-in skills instruct agents to call tool names absent from the shipped registries |
| INT-001 | P2 | Power Automate condition imports drop boolean clauses and invert NOT incorrectly |
| INT-002 | P2 | n8n IF expressions remain in source syntax and take the wrong imported branch |
| INT-003 | P2 | Imported agent fallback drops the step's operational inputs |
| PRIV-001 | P2 | Ready notice publication is hidden behind expired pending intents |
| PRIV-002 | P2 | Rights-source reconciliation retries a failing prefix and ignores saved retry deadlines |
| TRAIN-001 | P2 | Same-task training records collide within one second and replace each other |

## Additional integration findings

These were discovered while combining changes and exercising real runtime boundaries:

1. Overlapping billing windows or a currency change could collect the same usage twice. Frozen collection identity and overlap checks now retain the original receipt.
2. The merged `pydantic-core` pin conflicted with the pinned Pydantic release. The compatible core version was restored and qualified in a fresh resolver/install.
3. A knowledge write could race the final embedding-identity check. SQLite and pgvector store-level fencing now makes that transition atomic. Qdrant requires a new collection for identity changes; no in-place transactional writer fence is claimed.
4. Imported actions could truncate operational inputs, and some WDL references could select the wrong branch. Inputs remain complete within their bounds; unsupported expressions require repair.
5. Docker ignored the new runtime entrypoint. The build context now includes the exact deployment files and the real image builds.
6. A restored sealed channel record reached a plaintext-only reader. Reads authenticate existing envelopes and updates preserve their encryption, including retained key identity.
7. Installer `devalue` 5.8.2 had an upstream parsing advisory. A minimal generated lock update selects 5.9.2 without unrelated package changes.
8. Six standalone demos left AnyIO unconstrained; dependency expansion selected affected 4.9.0. They now use the central reviewed 4.15.1 pin.
9. Queue workers attempted `float(None)` after automatic caps were removed; two request schemas retained arbitrary $100 maxima. Optional limits now cross those boundaries consistently while explicit zero and deployment ceilings remain enforced.
10. Stripe collection JSON receipts lacked a restore authority policy. Restored receipts now block execution pending current external reconciliation.
11. Organization hold JSON and required-history markers lacked restore classifications. They now retain explicit startup blockers. The history SQLite database already failed closed; its diagnostic is now specific.
12. Four dashboard pages referenced an unserved shared access-state script. Its explicit route is restored; actual HTTP coverage verifies all 60 literal template/static asset references.

## Boundaries that remain

- The complete Python run uses Windows CPython 3.12.14. Hosted CI and its other interpreter/platform combinations are intentionally deferred to protect CI minutes. A local passing result is not a hosted required-check result.
- Browser providers, identities and external effects are synthetic. Live model providers, customer accounts, real IdPs, payments, microphone use and customer data are not qualified by these tests.
- Installed Windows/Tauri and macOS acceptance are incomplete. Windows Rust could not link without the native MSVC toolchain; the separate Linux toolchain test is labeled accordingly.
- Seven existing OSV advisory exceptions for the Tauri GTK/glib and unmaintained transitive/build chain remain configured through 2026-10-31. The scan retained 16 suppressed occurrences; it does not establish zero vulnerable dependencies.
- The dedicated Codex Security scan never started: its permission-profile preflight was blocked, so no scan ID or security-scan results exist. Required static/dependency gates and the limited changed-line review are not substitutes for an exhaustive security assessment.
- Hosted embedding revision labels remain operator declarations rather than attested model-weight identities. Qdrant identity changes require a new collection; SQLite/pgvector migration proof does not extend to in-place Qdrant migration.
- Ordinary agent checkpointing remains opt-in. Reconciliation of unknown provider/channel/collection effects requires an authorized operator and current evidence; this batch does not invent external readback.
- Organization admission coordinates processes sharing the configured tenant filesystem. Separate unshared filesystems do not form a distributed organization ledger, and actual provider charges can exceed admission estimates. This boundary is distinct from the PostgreSQL-backed provider-cap authority.
- Ambiguous Stripe receipts beyond the 23-hour safe retry window remain a manual stop. There is no implemented reconciliation/reset API for those receipts; the supported channel administrator-attestation workflow does not apply to them. Channel retention compaction retains its documented 10,000-identity bound.
- The 255 catalog entries with external adapter prerequisites still require those connections or approved exports before use. No risk ceiling was raised to hide an unavailable capability.
- Browser qualification uses short disposable Windows workspace paths. Earlier runs encountered a long-path startup failure and stalled cleanup in the managed user TEMP location. The final run completed browser shutdown and temporary-profile cleanup; arbitrary-length Windows installation/data paths and every TEMP configuration are not certified.

## Publication

The intended publication reuses PR #2519 and supersedes #2520–#2523 after their changes are integrated and locally qualified. The final commit uses `[skip ci]`; no main-branch merge or hosted workflow dispatch is part of this batch. Publication state is recorded separately from local completion.
