# Post-install operation coverage

Source review: October 1, 2026. This is a working gap register against the
[customer experience contract](./customer-experience-contract.md), not a claim
that the entire platform has been inventoried or accepted. A working backend,
command or settings file does not complete a customer operation.

## Delivery boundary

An operation needs an ordinary in-product path, current effective settings,
appropriate permissions, useful failure/recovery states, and equivalent
authorized Copilot operation. A raw-data editor does not satisfy the ordinary
path. Configuration saved to a file is not an effective change when a higher
priority source still controls it. Keep backend checks shared across interfaces.

Local service tests and synthetic browser fixtures provide distinct evidence;
neither alone is an authenticated installed-application acceptance test. Track
those gaps explicitly instead of calling a screen complete because it renders.

## Employee companion preview (October 1, 2026)

The 90 focused Python cases pass serially and with eight local workers. One
companion case failed during a broader parallel repository run and has not been
reproduced in isolation; that suite result remains unresolved. These tests do
not qualify the authenticated installed-app, provider or visual journey. See
[the bounded feature contract](employee-companions.md).

| Customer job | Direct entry and shared operation | Current-state and authority checks | Observable result / recovery | Copilot and evidence |
| --- | --- | --- | --- | --- |
| Enable the preview | Settings or installer; existing feature toggle service | Installation admin; effective-switch readback | Effective setting or explicit conflict | Page guidance only; installed-app acceptance open |
| Enroll/restrict an employee | Employee companions; policy update service | Named workspace admin and explicit membership; current target membership, SCIM activity, account/workspace binding, revision comparison | Metadata receipt; refresh stale policy and review again | Operational Copilot missing; no browser acceptance |
| Ask about current work | My companion; closed supervision intent + owned world-model reads | Named current member; enabled employee policy; exact owner, department and project scope | Bounded current snapshot, source links and policy receipt; retry unavailable stores | Companion status implemented; global Copilot integration and installed-app acceptance open |
| Converse and write drafts | My companion; configured writer LLM facade and existing Budget | Explicit AI grant; current identity, policy, generation, source ACL and pinned route before dispatch/publication; no tools | Draft label, context provenance and receipt; metadata recovery with no automatic paid retry; task refresh remains available | Provider and browser acceptance unrun; global Copilot operations missing |
| Review/clear companion history | My companion; encrypted history read/clear service | Current exact owner and workspace; no admin content override; same-origin mutation and reviewed history revision | Cleared live history plus metadata receipt; refresh on uncertainty | Global Copilot now checks private history metadata and opens an owner-bound, confirmed clear with exact-retry receipt. Installed-app and retention/backup qualification pending |
| Review companion activity | Employee companions; metadata projection | Current workspace admin, explicit membership | Policy/event metadata without conversation text | Copilot action missing; no immutable archive qualification |

Connector access, learned memory, writes/approvals, active-run steering and
voice are unavailable. Deadline-driven purge, replicated policy
authority and existing task/chat administrator visibility remain separate work.

## Learning operations

The current review groups seven learning families into 36 operations covering
readiness, configuration, source selection, retention, review, permission for
training use, learning, admission, drafting, execution, refinement and recovery.
These are grouped jobs, not a route count or completion percentage. Richer
inference families add separate data-range and decision controls.

| Customer job | Current boundary | Required follow-through |
| --- | --- | --- |
| Run a saved learned workflow | Designer has ordinary typed input forms and checks the published version before execution. Unsupported forms refuse rather than silently converting data. | Full installed-app journey and authorized Copilot execution. |
| Edit a workflow with Copilot | Designer proposals protect newer edits, and Undo/Redo include settings. | Global operation coverage is separate from graph suggestions. |
| Stop future use of an accepted example | Run page and global Copilot now host the same reason/confirmation form. Copilot's closed catalog reads current selection and authority, prepares a sealed short-lived review, then calls the native withdrawal service only after human confirmation. It reports confirmed, pending and changed states separately. Native page maintenance remains available when Copilot actions, workflows or learning are stopped. | Authenticated installed-app browser acceptance, effective-source transfer for the optional setting, and the rest of the example lifecycle. No claim of erasing copies or undoing completed work. |
| Configure learning after installation | Configuration and installer controls exist. | Effective-source administration, dependencies, readiness and ordinary forms inside the platform. |
| Collect a suitable demonstration | Native enrollment and bounded retention contracts exist. | Find eligible owned runs and define retained fields without writing schemas or JSON. |
| Review examples and permit training use | Authenticated review, separate bounded training grants and signed export services exist. | Owned inbox, readable evidence, separate consent forms, status, expiry and recovery. |
| Learn, admit, draft and refine a procedure | Bounded learners, lineage checks and draft services exist; HTTP coverage varies by family. | Source selection by retained examples, missing service adapters, ordinary controls, comparison and designer handoff. |
| Explain why automated work can proceed | Native authority, lineage and signed receipts exist. | A joined, readable explanation of the evidence, approval, action and result; uncertainty must remain visible. |

Implementation references:
[run page](../packages/maverick-dashboard/maverick_dashboard/templates/flow_run.html),
[ordinary inputs](../packages/maverick-dashboard/maverick_dashboard/templates/flow_designer_inputs.js),
[example API](../packages/maverick-dashboard/maverick_dashboard/api_flow_action_episodes.py),
[review service](../packages/maverick-core/maverick/flow/action_episode_review.py),
[lineage service](../packages/maverick-core/maverick/flow/recipe_lineage.py).
The [learning journey contract](./learned-capabilities-product-contract.md)
defines the wider acceptance requirements.

## Other platform operations

This bounded review does not classify providers, connections, access management,
backups or updates as wholly absent. Existing controls need qualification and
specific gaps closed.

| Area | Observed gap | Required next result |
| --- | --- | --- |
| Restored connected-channel work | Recovery now uses outcome choices, local date controls and evidence fields. A local Recovery Copilot hosts the same form and operation. Interrupted recording can be finished using the exact retained review. | Qualify the authenticated installed-app journey. Replace manual position numbers with in-product source lookup/selection, particularly for iMessage. Complete the other recovery operations in Copilot and the remaining technical wording on the wider page. |
| Settings controlled by deployment | Some settings correctly refuse local writes when an environment or deployment source controls them. Refusal alone leaves no post-install migration journey. | Explain the current owner and provide an authorized in-product transfer or administrator path with effective-value readback. |
| Connected-automation signing secret | The former false-success path was reproduced with environment and mounted-file secrets. The settings service now checks effective ownership, binds the change to the current administrator and setting, and verifies both incoming and outgoing signing results. The guided form clears another account's draft and distinguishes uncertain results. | Qualify installed-app use and shared Copilot operation. Deployment-source transfer, outbound-only rotation when a separate incoming secret is controlled elsewhere, and durable audit history remain separate gaps. |
| Settings change history | The existing webhook-secret setter has no authoritative durable audit-event boundary. An effective-value response or actor-bound review token is not a customer audit record. | Join reviewed intent, authority, exact local setting change and confirmed/uncertain outcome in a durable audit history with interrupted-recording recovery. Do not include secret values or guessable secret fingerprints. |
| Retention and encryption maintenance | Encryption-key backup already has an in-product download and confirmation. Retention enforcement and encryption migration, rotation and strict-mode changes still have operator commands for which this review did not find equivalent dashboard callers. | Permissioned preview, execution, verified outcome and recovery for the remaining commands. Do not add blind command execution behind a button. |
| Advanced sandbox and single sign-on policy | Advanced sandbox guidance and identity configuration still include file/environment setup. | Dedicated administration with current-source ownership and safe validation; never weaken isolation or authentication to bypass missing UI. |
| Workspace lifecycle | The administrator's Workspaces roster now exposes plan change, daily limit, pause, resume and removal with confirmation, status reload and native API callers. Removal retains stored records and says so before confirmation. Local route and DOM tests cover these paths. | Qualify the authenticated installed-app journey, including stale state, permission changes, failed writes and recovery. Coordinate full retirement separately from removing a workspace from the roster. |
| Database tenancy and key maintenance | Tenant RLS preflight/backfill remain operator commands. The Workspaces page now previews and runs local workspace-key re-protection using selected key files, with administrator permission, recent MFA, a background result and local route/DOM tests. Switching the server's master key and verifying every workspace afterward remain outside that page. | Complete the in-product maintenance sequence: protected key custody, RLS planning, readiness checks, server-key transition, verified return to service and recovery. Qualify the installed-app journey before calling key rotation complete. |
| Local AI runtime | Provider connection testing exists. The separate native local-runtime planning operation has no dashboard caller found. | Present supported local-runtime preparation and health inside the product; configuring a URL alone does not prepare the service behind it. |
| Complete workspace retirement | Settings > Maintenance previews local client files and explains the offline boundary; the running dashboard refuses an erase before deleting anything. The offline kernel erase retains a signed receipt. Connected services, other storage roots, and exported copies are outside its scope. | Add a coordinated in-product offline retirement journey that stops writers, verifies all storage scopes, presents the final receipt, and prevents the retired client from reopening. Do not report a partial local delete as complete retirement. |
| Backup and installation boundaries | Local backup/recovery and packaged Windows update/repair/rollback have substantial product controls. Shared/remote storage recovery and other deployment types have explicit blockers or separate procedures. | Provide the remaining coordinated administration paths; do not claim all deployment types are qualified from Windows/local acceptance. |
| Diagnostic parity | Cache controls, readiness and support-report preparation exist. Selected circuit/rate-limit/health/trace CLI diagnostics have no equivalent dashboard callers found. | Give customers readable health, affected work and actual repair actions. Support export alone does not replace operational diagnostics. |
| Aggregate boards for department-scoped employees | Overview, Spend, Workforce, Savings and Oversight aggregate owner work without filtering every metric by the caller's current department grants. Their pages and board APIs now refuse scoped access before reading those rollups, with plain recovery guidance; unscoped administrators retain the boards. Links to these pages may still be visible to scoped users. | Build domain-aware projections for every chart, server-rendered summary, download and linked detail, then restore useful scoped boards and navigation. A title-only filter would leave counts or costs exposed. |
| Global Copilot | The shared page guide still has a zero tool-call budget. A separate default-off, closed operation catalog covers learning-example status and withdrawal. On a saved learned workflow, Copilot can open the same labelled run form as the designer after checking the current published release; the form and native run endpoint recheck that release and access before accepting work. Connected-service recovery has a separate restricted local form. These bounded operations do not constitute whole-platform coverage or installed-app acceptance. | Extend the typed catalog and current state across every supported job, including work in another panel or background job. Preserve each native service's authority, result and recovery contract. |
| Chat and answer actions | Chat exposes the dashboard's role-filtered product destinations. Live completion refreshes answer controls without losing a drafted reply and offers recovery on failure. Copy uses saved content; historical downloads select the stored turn and recheck access and review requirements. Structured exports use reviewed table cells. Print retains stored clarifications and wraps wide content. Expiring public links route to the selected workspace; existing links remain revocable during a rerun or empty result. Mobile navigation supports a visible Close button and contained keyboard focus. Local route, DOM and Chromium journeys exercise these paths with synthetic data. | Qualify the installed desktop and real customer content. Full ChatGPT/Claude interaction and whole-platform Copilot operation parity remain open; these answer actions do not establish either. |
| Public form intake | Public admission, binding changes and deletion share a cross-process lock. A submission is refused if its binding changed while the body was read. A form with accepted rows waiting beyond its durable cursor shows a count in Automations; changing any execution binding or deleting it returns a 409 with a Check now path. Polling retains accepted rows when their original flow or workflow is unavailable. Automations creates a high-entropy default link, shows Open/Copy actions on saved form rows, and serves a public page with fields from the bound workflow or published flow. GET and POST recheck the live owner and target. New submissions validate required saved-template parameters and published flow input types before append; flow dispatch coerces form answers against that same release. A fully checked form can replace its public link under the registry lock: the old URL stops, its submission file stays retained, and the new URL starts with an empty queue. A stale retry, waiting submission, or unreadable history refuses replacement. Local route and UI tests cover live, unknown, stale, unavailable, invalid and typed submissions. The rows themselves still lack an embedded target generation; a stale original target or previously accepted invalid row can leave the form blocked until a recovery path exists. Storage remains capped at 5,000 rows or 32 MiB per link. | Build an in-product review and durable audited abandonment or repair path for stuck submissions, plus retained-file lifecycle management. Qualify installed-app and customer journeys before calling this customer-ready. |

Implementation references:
[recovery controls](../packages/maverick-dashboard/maverick_dashboard/static/lightwork-workspace-recovery.js),
[recovery page](../packages/maverick-dashboard/maverick_dashboard/templates/workspace_recovery.html),
[settings authority](../packages/maverick-dashboard/maverick_dashboard/settings_store.py),
[dashboard API and Copilot](../packages/maverick-dashboard/maverick_dashboard/api.py).

The administration review traced selected commands in `cli/__init__.py`,
`cli/_ops_groups.py`, `cli/_trust_groups.py` and `cli/_connector_groups.py` to
their native services and searched the dashboard for those callers. It did not
complete parity review for signing/WORM/SIEM administration, licensing/billing,
plugins/MCP/skills, channels, worker topology, schema migrations, knowledge
maintenance, every business module or every operating system. Those remain
unreviewed work; they are not implicitly covered by the rows above.

## Technical work still exposed by existing screens

The September 27 review also scanned all 165 dashboard HTML templates for
labelled serialized-data, record-version, verification-material and machine-time
inputs. It found 295 candidate controls across 52 templates. These are inspection
leads, not 295 confirmed defects: optional import editors, hidden controls and
schema-driven replacements are included. JavaScript-generated controls are not
covered by that static count. In particular,
[`lightwork-guided-actions.js`](../packages/maverick-dashboard/maverick_dashboard/static/lightwork-guided-actions.js)
already replaces some underlying data editors with guided fields. Each candidate
must be traced through its actual controller and ordinary alternatives.

The following paths were inspected beyond their labels:

| Customer job | Confirmed current path | Required next result |
| --- | --- | --- |
| Create a bulk consent export or change | `consent_bulk_jobs.html` exposes required item-data editors; `lightwork-consent-bulk-jobs.js` parses their contents through `parseItems` and constructs the job. These editors do not have the shared guided-field bindings. | Select eligible records and business choices, preview the affected people and consequences, obtain current versions internally, and show per-item progress and recovery. Preserve separate permissions for each operation. |
| Prepare an incident report | `_governance_operations.html` loads the report template into a data editor and submits `parseObject(details)` alongside a manually entered version. | Present the actual report fields, select the incident, bind its current version internally, preserve draft/independent approval, and distinguish recording a receipt from external submission. |
| Rotate a registered device's key | `consent_native_admin.html` and `lightwork-consent-native-admin.js` require public-key hex, encoded attestation material, verification fingerprints and epoch times. | A device enrollment/rotation handoff must obtain genuine signed evidence from the device, show understandable dates and verify the actual resulting access. Merely renaming these fields is insufficient. |
| Operate generic compliance consoles | The shared guided-action layer can create schema-based fields, but still exposes technical references, unknown-property authoring and some technical-editor fallbacks. | Trace each supported action; supply named record selection and business controls. Preserve exact authority and record binding. A generated form alone is not a complete nontechnical journey. |

These are additional work items, not delivered fixes. None of this static review
qualifies the whole application, live integrations or full Copilot operation.

## Next complete journeys

1. Finish the guided restored-channel journey with source-position selection,
   authenticated installed-app acceptance and the remaining recovery operations.
   Unknown external outcomes stay unresolved. See the
   [connected-service review boundary](recovery-connected-services.md).
2. Complete a learning-example lifecycle: readiness, eligible source selection,
   retention, actual approved execution, review, separate permission for training
   use, signed evidence and withdrawal.
3. Extend that journey through learning a repeated procedure, reviewed admission,
   an unpublished draft, ordinary input execution and refinement from additional
   examples. Cover direct controls and Copilot against the same service contract.

Each delivered journey needs evidence for cancellation, stale state, repeated
clicks, permission changes, pending audit recording, interrupted requests and
recovery, plus keyboard and narrow-screen inspection. Do not retire this register
on the strength of component test counts alone.
