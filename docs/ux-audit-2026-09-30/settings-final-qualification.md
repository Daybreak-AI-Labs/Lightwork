# Final settings acceptance reconciliation — LW019–042

This review reread all 24 complete source-backlog rows, including recommendations
and acceptance criteria, against the integrated implementation based on
87c5df110b1884e33a619ad09f2e234ece004b7b. It also incorporated the consolidated
conversation, identity/memory, Today and browser evidence already present in that
source. The canonical `slices/settings.json` now records current evidence for all
24 IDs; its obsolete tests-deferred wording and resolved integration TODOs have
been removed. No aggregate or source-backlog rows were changed.

The review found two actual missing product journeys and implemented them:

- LW029: d0de3d1b4ed827ce783dba11c1ab3969f2939adc adds typed task-map rename/move,
  native current-state guards, actual selected-task context, exact durable
  structure receipts, bounded native request/recovery handling and inherited
  handoff-ceiling protection. See `task-map-final-qualification.md`.
- LW041: f6ac623503021fe0ad8135d9c95ef0f729ff91aa completes saved task-filter views
  around the pre-existing owned UX store and endpoints. It adds native Tasks
  controls, shared typed Copilot services, optional atomic revision checks,
  explicit replacement/removal review and truthful uncertain readback. See
  `saved-task-views-qualification.md`.

The integrating branch owns the explicit lazy catalog registration for
`product_operations_task_map` and `product_operations_task_views`. These are real
native adapters, not simulated UI clicks. Their registration must accompany the
implementation commits before the consolidated PR claims their Copilot coverage.

## Evidence provenance

Local tests are authorized and were run. Test counts below describe separate
checkpoints and overlap; they must not be added into a fabricated unique total.

| Checkpoint/run | Observed result | Practical limit |
| --- | --- | --- |
| Frozen b546225 seven-module catalog matrix | 2,600 passed, 3 failed, 6 skipped | Completed run, not an all-pass claim. Six skips concern POSIX permissions/links or Windows symlink privilege. |
| 3abdd3e repaired catalog failures and owner/read fixtures | 32 passed, 4 optional DuckDB skips; separate catalog cross-checks 19 passed | All three catalog failures passed after their fixture/runtime corrections. The full 2,609-case catalog run was not repeated at this checkpoint. |
| b546225 focused settings foundation | 1,604 passed; safety/voice 35 passed; privacy 13 passed; forms controller 17 passed | These are overlapping focused qualification groups, not a final repository-wide result. |
| b3fa1e2 reviewed administration fixtures | 76 passed; exact settings-file install 1 passed; billing/retention safety 11 passed | Provider effects were fakes. Reviewed authority, versions, receipts and refusal assertions remain intact. |
| 308aedd shared shell/localization | 120 Python and 61 JS passed | Product-copy and DOM/static behavior; no multilingual human-acceptance claim. |
| This final settings/map review | 63 Python passed | Integrity, runtime acknowledgement, resets, preferences, memory projection, Today and map/core cases. |
| Map qualification across two focused runs | 30 distinct native/API/core cases; 5 jsdom cases passed | Includes seven new adapter cases; SQLite was real and local. PostgreSQL and installed multi-process parity are not inferred. |
| Settings/editor JS batch before final fixture repair | 61 passed, 1 failed | The failure was the extracted workflow fixture missing the new localized `productText` helper. |
| Saved-view/editor/forms/settings follow-up | 29 native/core/legacy tests passed; 41 JS passed | The failing workflow case passed with unchanged draft assertions. All eight saved-view tests passed again after protecting incompatible legacy-name collisions. |
| Consolidated browser evidence already in source | 70 distinct cases passing across complete and targeted runs; 174 advertised asset HTTP/MIME contracts | Original complete run was 64 passed/5 failed; repaired targeted cases plus enterprise coverage are not one final-commit 70-case run. |

Earlier exact test commands, failures and corrections remain in
`settings-fixture-validation.md`, `owner-read-runtime-validation.md`,
`shared-shell-localization.md`, `browser-qualification.md` and the per-slice
records. Root coordinates final immutable ordinary/secure/heavy/browser gates;
this document does not preempt their results. No live model/provider, production
write, dependency mutation, deployment or merge occurred in this review.

## Acceptance mapping

| IDs | Implemented acceptance evidence |
| --- | --- |
| 019–020 | Result-only CAS, prior human revisions, actor/operation receipts, atomic approval invalidation; effective-settings source/environment revision, ordinary writer fence and audited committed values. |
| 021–023 | Dirty navigation protection, safe draft recovery excluding credentials/protected documents, submitted-control locks, section-local save/error state and preservation of unrelated edits. |
| 024–025 | Live appearance preview/rollback/undo and account/device persistence; encrypted account/workspace/thread drafts with retention/discard CAS, acknowledged clear and honest attachment reselection. |
| 026–027 | Exact-byte reviewed installation with actor/scope/current-source/environment binding; pending intent, durable outcome/readback, replay/no-op distinction and uncertain reconciliation. |
| 028–029 | Child operation identity and deduplication, bounded pending UI, preserved drafts and named child; native/Copilot task-map CAS, receipt recovery, cycles/owner/handoff ceilings and current-versus-draft comparison. |
| 030–034 | Allowlisted inheritance and administrator ceilings; runtime-complete persona choices; independent personal/conversation style; named-account preferred names and explicit legacy migration; account-safe display/device override and separate interface/output language. |
| 035–037 | Permission-aware catalog/dedicated search and exact focus targets; mixed change impact review and immutable observed runtime configuration; distinct product-default reset versus inherited installer/configuration. |
| 038–040 | Selective versioned non-secret profiles through ordinary review; bounded audited history with safe selective restore; fully expanded research/high-assurance presets with drift, dependencies, qualitative/cap tradeoffs and explicit confirmation. |
| 041–042 | Permission-filtered personal pins/templates/start page, keyboard reorder and current owned recents; complete saved task filters; Today source projections, mandatory decision reminder and personal card layout using existing authoritative state. |

## Remaining qualification and declared boundaries

All 24 original IDs are marked **implemented-test-needed**, because local
regressions and source inspection do not establish final installed, PostgreSQL,
cross-process, device, accessibility or customer acceptance. Exact remaining
checks are attached to each row. No design decision is being deferred merely for
time or scope, and the two discovered implementation gaps were built.

Runtime acknowledgement is particularly limited: immutable first-read snapshots
and heartbeats identify the revision captured by observed processes sharing the
configuration data folder. Tenant-specific/manual gRPC overrides and absent or
stale processes remain unconfirmed. A restart acknowledgement never proves
provider health, successful work or adoption by an unobserved process.

Portable profiles contain an explicit allowlist, not a deployment backup. Audit
history is bounded to its recorded last-100/30-day window; missing legacy
revisions remain labelled, and secret rollback is prohibited. Presets do not
invent local-provider availability or dollar-price estimates. Saved task views
state their result-limit coverage and use the pre-existing deployment-local UX
store; their uncertain readback does not manufacture historical operation
receipts. Root was notified that this existing store is separate from encrypted
experience-preference export/erasure and must be accounted for in any claim of a
complete My Data inventory.
