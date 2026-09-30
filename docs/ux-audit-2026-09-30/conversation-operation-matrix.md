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

Root-integrated adapters additionally cover schedule lifecycle/recovery, connection
diagnostics, notification delivery, budget/accounting, current preferences,
project workspaces, device actions, Stop, plugin qualification, approved knowledge,
activation and reviewed identity administration. Their exact native services are
recorded by the owning slices. Durable queue/guidance adapters are being completed
with LW-001; they are not claimed by this checkpoint. Review inventory above 200
packets directs the user to the native review page instead of silently truncating.

Authored native regression coverage: `test_product_operations_tasks.py` covers
clarification receipt replay without duplicate dispatch, result CAS conflict,
recovery after a later human edit, content-grant mutation refusal, and independent
review choices. Existing native clarification and project-review suites qualify
the underlying service independently when the local suite runs.
