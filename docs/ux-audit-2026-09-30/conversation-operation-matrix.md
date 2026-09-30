# Conversation operation evidence

Implementation checkpoint: 2026-09-30. Local runtime qualification remains pending
the consolidated suite; this matrix does not certify customer acceptance.

All catalog operations require the shared `copilot_operations` feature, the
administrator's enabled family, the current account/workspace, and an explicit
review and confirmation for mutations. Models cannot invent execution arguments
or consent. Native screens remain usable without a model.

| Customer job | Direct interface | Shared native behavior / Copilot operation | Authority and current-state check | Result and recovery |
| --- | --- | --- | --- | --- |
| Read current task and result | Conversation answer panel | `answer_projection.projection` / `tasks.current_status` | Exact task content authority, source read twice, current saved artifact and sign-off version | Saved content and execution status are separate from external delivery. Retry a changing or unavailable source. |
| Answer a task question | Conversation answer composer | `clarification_submission.submit` / `clarifications.answer` | Current owner and operate permission, question fingerprint, account/workspace, unchanged source, native restart admission | Atomic answer receipt; only first admitted send dispatches. Receipt recovery never repeats dispatch. Copilot text limit 12,000 characters; native composer also admits selected evidence files. |
| Revise saved result | Conversation Edit control | `goal_mutations.edit_result` through `api.edit_deliverable` / `artifacts.edit_saved_result` | Current owner and operate permission, finished task, reviewed `updated_at`, actor-bound operation receipt | New saved version, previous sign-off cleared, no claim of completed training. Stale draft is refused; exact receipt replay preserves a later edit. Larger results use native Edit. |
| Respond to fixed project review | Project reviews | `ProjectReviews.respond` / `reviews.respond` | Current membership/suite permission, assigned reviewer for decisions, owner for withdrawal, packet revision and source fingerprint | Immutable response history; current source changes refuse stale acceptance. Exact operation replay returns its recorded decision. Review does not transfer ownership or publish externally. |

Task stop/restart, project workspace planning, accepted execution handoff, and
selected deliverable bundles require additional native adapters as their service
work is completed. They are not advertised by this checkpoint. Review inventory
above 200 packets explicitly directs the user to the native review page instead
of silently truncating selectable records.

Authored native regression coverage: `test_product_operations_tasks.py` covers
clarification receipt replay without duplicate dispatch, result CAS conflict,
recovery after a later human edit, content-grant mutation refusal, and independent
review choices. Existing native clarification and project-review suites qualify
the underlying service independently when the local suite runs.
