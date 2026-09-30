# Conversation operation evidence

Implementation checkpoint: 2026-09-30. Focused native task, clarification, handoff and result tests now pass; consolidated qualification remains pending; this matrix does not certify customer acceptance.

All catalog operations require the shared `copilot_operations` feature, the
administrator's enabled family, the current account/workspace, and an explicit
review and confirmation for mutations. Models cannot invent execution arguments
or consent. Native screens remain usable without a model.

| Customer job | Direct interface | Shared native behavior / Copilot operation | Authority and current-state check | Result and recovery |
| --- | --- | --- | --- | --- |
| Read current task and result | Conversation answer panel | `answer_projection.projection` / `tasks.current_status` | Exact task content authority, source read twice, current saved artifact and sign-off version | Saved content and execution status are separate from external delivery. Retry a changing or unavailable source. |
| Answer a task question | Conversation answer composer | `clarification_submission.submit` and same-thread `submit_text` / `clarifications.answer` | Current owner and operate permission, question fingerprint, account/workspace, unchanged source, native restart admission | Atomic answer receipt; only first admitted send dispatches. Receipt recovery never repeats dispatch. Copilot text limit 12,000 characters; native composer also admits selected evidence files. |
| Revise saved result | Conversation Edit control | `goal_mutations.edit_result` through `api.edit_deliverable` / `artifacts.edit_saved_result` | Current owner and operate permission, finished task, reviewed `updated_at`, actor-bound operation receipt | New saved version, previous sign-off cleared, no claim of completed training. Stale draft is refused; exact receipt replay preserves a later edit. Larger results use native Edit. |
| Respond to fixed project review | Project reviews | `ProjectReviews.respond` / `reviews.respond` | Current membership/suite permission, assigned reviewer for decisions, owner for withdrawal, packet revision and source fingerprint | Immutable response history; current source changes refuse stale acceptance. Exact operation replay returns its recorded decision. Review does not transfer ownership or publish externally. |

| Share only selected deliverables | Selected deliverable bundles | `DeliverableBundles.prepare/create` / `artifacts.share_bundle` | Exact current owner, selected artifact/file identities, named recipient, source fingerprint, preview receipt | Immutable selected bytes, expiring ACL, exact create replay; failed export preserves original bundle. |
| Accept or revoke selected copy access | Bundle review panel | `DeliverableBundles.respond` / `artifacts.respond_bundle` | Named recipient for acceptance, owner for revocation, current membership and source freshness, revision | Acceptance is human review, separate from technical integrity or task authority. Old copies cannot be recalled. |
| Download a portable selected bundle | Bundle download | `deliverable_bundle_export` with native current access / `artifacts.bundle_download` | Current named access and source freshness checked before release | Native originals, editable representations, safe rendered view and offline manifest. Local href points to authorized export. |
| Offer a task continuation | Bundle handoff link and Task handoffs | `TaskHandoffs.prepare/create` / `tasks.offer_handoff` | Current source owner and named operate recipient, unchanged selected bundle, idle source tree, explicit scope/commitments/cost/ceiling review | Offer is durable and idempotent; original responsibility remains until acceptance. |
| Accept, decline or withdraw a handoff | Task handoff review | `TaskHandoffs.respond` / `tasks.respond_handoff` | Recipient acceptance, sender withdrawal, current roles/source/expiry, shared execution leases, revision | Atomic blocked successor + source closure + native receipt. Recovery never creates a second successor; no automatic execution or credential transfer. |

The registered families below extend the same contract. Each operation ID is a closed entry in the named adapter module; grouping related IDs in one row does not grant additional authority.

| Operation IDs | Direct interface | Shared native service | Reviewed prerequisite and recovery |
| --- | --- | --- | --- |
| `tasks.resume` | Task restart review | `reviewed_resume.apply/recover` and `goal_resume.reserve` through `product_operations_resume` | Exact owner/context/revision and current workspace ceiling, old runner execution lease, native chat serialization/admission, atomic restart receipt. Separate confirmation, no automatic retry; receipt lookup after reload never dispatches. |
| `tasks.stop` | Current task Stop | `reviewed_stop.apply/recover` through `product_operations_stop` | Exact owner, current goal revision and actor-bound receipt; requesting Stop is separate from confirmed termination. |
| `connections.review` | Connections dependency review | `connection_dependencies.impact/require_review` | Owned connection revision and current dependency digest; no provider test or credential mutation is implied. |
| `schedules.change` | Automations lifecycle controls | `schedule_definitions.change_definition` | Owned durable definition revision, explicit active/paused/retired choice, native version history records exact operation. Existing occurrences are not replayed. |
| `schedules.recovery` | Automations parked-occurrence review | `schedule_ops_api.recurrence_recovery_review` | Administrator authority and exact evidence digest. This operation reads recovery evidence; `schedules.repair` separately reviews and confirms a mutation. |
| `schedules.repair` | Automations recovery confirmation | `schedule_ops_api.recurrence_recovery_apply` and `JobQueue.reconcile_recurrence` | Explicit stopped-worker confirmation, exact evidence/attempt/context, choice of compatible successor or one future occurrence; atomic actor-bound receipt survives history retention and prevents replay. Historical external outcome remains unknown. |
| `conversation.inbox`, `conversation.send`, `conversation.decide`, `conversation.edit` | Conversation background-message composer and receipt panel | `conversation_inbox` and `conversation_inbox_api` via `product_operations_conversation` | Exact current task/employee, revision, erasure generation and independent snapshot digest; explicit guidance conflict decision. Native composer admits selected files, Copilot edits preserve chosen evidence. Durable receipt/claim recovery never manufactures consent or starts a guidance task. |
| `preferences.status`, `preferences.update` | Preferences | `preferences_api._state/update_preferences` | Current employee/thread scope, inheritance and admin ceilings, reviewed preference revision, exact operation receipt. |
| `today.status`, `today.layout` | Today | `today_projection` and `preferences_api.update_preferences` | Current owned work and preference revision; layout edits cannot change work authority. |
| `notifications.delivery_status`, `notifications.send_test`, `notifications.retry_test` | Notification delivery settings | `notification_health._state/_test` and `DeliveryReceipts` | Current administrator/destination config, explicit test consent, exact receipt and refused-test-only retry; uncertainty never implies delivery. |
| `notifications.personal_status`, `notifications.personal_update` | Notifications personal preferences | `personal_notifications_api.save_preferences` | Current employee context and policy, native preference CAS/receipt; mandatory alerts remain enforced. |
| `budget.preview` | Budget preview | `budget_guidance` and `budget_guidance_api._projection` | Current limits/accounting scope; unavailable accounting remains unknown. Preview does not change spending authority. |
| `budget.accounting.request`, `budget.accounting.claim`, `budget.accounting.request_evidence`, `budget.accounting.verify` | Budget accounting-help cases | `budget_guidance_api.request_help/progress` | Requester or authorized accounting reviewer, original-period evidence and case revision; case receipt recovery does not rebook cost. |
| `models.guidance`, `models.test`, `models.test.history`, `models.test.cancel` | Settings model guidance/tests | `model_guidance`, `model_tests_api.start`, `model_tests` | Current allowed model/config evidence, explicit bounded paid-test review, owner receipt/cancellation. No live model tests were run during this implementation qualification. |
| `tasks.diagnostics.prepare` | Private task diagnostic report | `task_diagnostics.prepare_report/report_result` | Exact current task content authority, current source revision and actor receipt. Preparing a private report never sends it to support. |
| `knowledge.assessment_precedents` | Assessment precedent panel | `assessment_memory.suggest_answers` | Current allowed assessment types and source authority; suggestion is not a submitted answer. |
| `knowledge.source_analysis` | Knowledge access source analysis | `knowledge_task_context.validate_selection`, native `api.create_goal` admission | Explicit approved sources/purpose, current source ACL, fixed limits and idempotent task admission. Copied/handoff tasks cannot inherit source bindings. |
| `knowledge.rebuild`, `knowledge.rebuild_job` | Knowledge access search rebuild | `knowledge_reindex_jobs` and `knowledge_library_api.reindex_action` | Native administrator/source revision guards, durable bounded job state and explicit stop/resume actions. |
| `activation.create`, `activation.status`, `activation.edit`, `activation.launch`, `activation.accept`, `activation.workflow` | Employee Get started | `employee_activation.save/launch/accept/workflow` | Private draft revision, current readiness and result version, reviewed launch/native submission receipt; workflow remains a reusable draft. |
| `devices.status`, `devices.remove` | Devices | `device_pairing_api.state/remove` | Current own device and revision; recorded removal receipt, no inferred access to another employee's device. |
| `plugins.status`, `plugins.check`, `plugins.publish`, `plugins.migrate`, `plugins.cancel` | Plugins workbench | `plugin_workbench`, `plugin_workbench_api` | Sandbox qualification evidence, exact scoped version and dependencies, stopped-work pin review; prior attempt outcome remains separate from publication. |
| `projects.status`, `projects.details`, `projects.milestone`, `projects.reorder`, `projects.dependency`, `projects.member`, `projects.context`, `projects.remove_milestone`, `projects.remove_context`, `projects.defaults` | Project workspaces | `project_workspaces_api.state/prepare/execute` | Exact metadata ACL, current source/milestone revision, independently authorized context selection; planning changes do not transfer task ownership or execution authority. |
| `departures.status`, `departures.begin`, `departures.handoff`, `departures.refresh`, `departures.close`, `departures.retry_audit` | Employee departures | `employee_departures` and `employee_departures_api` | Current administrator, reviewed inventory/retained obligations, case revision and durable receipt; no blanket employee-content grant. |
| `content_access.status`, `content_access.request`, `content_access.decide` | Reviewed content access | `content_access_api` | Exact resource/purpose/fingerprint, independent authorized reviewer and expiring audited grant; read grants never confer mutation authority. |
| `permissions.status`, `permissions.request`, `permissions.decide` | Access requests | `permission_requests` and `permission_requests_api` | Effective role plus suite ceiling, current request revision and independently authorized reviewer; native grant receipt. |

The existing learning-example status/withdrawal interface remains registered in
`copilot_operations` and uses its sealed native preparation and receipt contract.
The durable queue/guidance operations are supplied by LW-001's
`product_operations_conversation` adapter and its owning ledger. Integration must
preserve the companion native service and UI checkpoints; a model cannot bypass
the separate conflict decision or choose an unreviewed snapshot.

The shared `product_operations` registry checks native snapshots before and after
preparation, seals operation/arguments/context/selection/source revision and
expiry, and requires an explicit human confirmation before execution under the
authority effect fence. A current native receipt is consulted before another
mutation, so a valid replay can recover even after a later edit. Typed arguments
reject extra fields; the optional model can return only one advertised operation
ID, never targets, arguments or consent.

`lightwork-product-operations.js` refreshes native controls without calling a
model, renders bounded typed inputs and native status links, invalidates prepared
forms when selected context changes, aborts obsolete requests and suppresses
late results. Closing/reopening re-reads authority. Failed or unknown execution
shows native recovery controls and never automatically submits a second action.
Deterministic controls remain usable when AI suggestions are unavailable. Limits
such as the 12,000-character Copilot artifact editor or more than 200 review
packets direct the employee to the full native editor; they are not hidden loss.

Local coverage includes `test_product_operations_tasks.py` (clarification receipt
replay without duplicate dispatch, result CAS conflict, recovery after a later
human edit, content-grant mutation refusal and independent review choices),
`test_product_operations_handoffs.py`, and native clarification, project review,
bundle and handoff tests. The root's registry/family tests qualify shared context,
sealed consent and individual native adapters. PostgreSQL and installed-browser
qualification remain explicitly separate from these local passes.

Lifecycle qualification: 39 focused local Python tests passed across restart/readiness, native schedule definitions and recovery adapters; four browser-controller restart tests exercise explicit review, exact uncertain retry, changed-limit invalidation and reload receipt-only recovery. No live providers were called. The native JobQueue SQLite schema additively persists recurrence recovery receipts; world restart receipts reuse the existing v42 table.
