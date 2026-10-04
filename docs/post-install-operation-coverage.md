# Post-install operation coverage

Source review: October 1, 2026; selected journeys updated October 2. This is a working gap register against the
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

## Everyday task controls (October 4, 2026)

The default-off product-operation catalog now exposes task discovery and a
reviewed new-task start on Chat, Tasks and Today. The native task API and
Copilot share task admission, current spending-limit resolution, rate limits,
provider readiness, owner authority and a keyed submission receipt. A lost
start response can be read back by its exact operation identity; a repeated
confirmation does not dispatch another task. Search returns bounded task links
after re-reading each full task and checking current department authority. The
native task-search API now applies the same full-row check.

| Customer job | Direct UI and Copilot operation | Observable result and recovery | Remaining boundary |
| --- | --- | --- | --- |
| Find a task | Tasks search; `tasks.find` on Chat, Tasks, Today and task pages | Read-only owner-scoped match list with current status and a link to task progress | Search examines the latest 1,000 tasks; older work needs the paginated task history. |
| Start a task | Chat composer or task API; `tasks.start` on Chat, Tasks and Today | Sealed preview of task text and effective limits, separate confirmation, native admission receipt and task-progress link; exact-key replay | Accepted admission is not completed work. Source attachments, specialist/template starts and connected-account choices remain in native controls. |
| Stop or restart | Native task progress; existing `tasks.stop` and `tasks.resume` | Current-owner checks, separate reviewed Stop/restart, exact receipts and progress readback | Stop is cooperative; prior external effects remain. A restart is a new attempt. |
| Review work | Project reviews; existing `reviews.respond` | Fixed-version response with current reviewer authority and receipt recovery | The separate high-risk Approvals queue and task deliverable sign-off still need typed Copilot decision adapters. |

Focused local product-operation and native-submission tests passed (27 checks).
No installed-app, real provider, customer-content or narrow-screen acceptance
was run for these additions. Whole-product Copilot parity remains open.

## High-impact action review (October 3, 2026)

Safety settings keep practice predictions in watch-only mode by default. An
administrator who selects Hold mode now gets a fresh Approvals request when a
prediction would stop a high-impact step. The review card commits the exact
task, tool and proposed arguments without putting their raw contents on the
approval page. An authorized person can approve or reject it in the ordinary
queue; approval lets that one step reach its remaining safety checks. Expired,
unanswered, stale, or rejected requests do not run and are removed from the
live queue. A synthetic signed-in browser journey covers the decision UI and
focused core checks cover release and later-gate enforcement. A real installed
task-to-review-to-effect journey, an inline task card, and avoiding duplicate
review when a specific tool requires its own consent remain open.

## Settings activity review (October 3, 2026)

Ordinary Settings saves for capabilities, features, provider rotation/removal,
model pins and allow-list, model discovery policy, task budget, and page
visibility now bind an administrator and a non-secret before/after value to the
serialized write. The same boundary covers Channels save/clear, plugin install
policy, model cost tier, feature/module switches, and the sandbox backend API.
Credential and channel fields appear only as set/not set or names of configured
fields. Activity shows a **prepared** intent before the write and **completed**
only after successful persistence (and, for provider rotation, effective-source
verification). Audit refusal before the write blocks it; storage failure leaves
no completed receipt. If the completion record fails after a non-transactional
save, the response tells the administrator to refresh Settings and Activity
before retrying. Settings links directly to the filtered Activity view and
explains that a prepared row alone is not proof of a save. Local route tests
cover the ordinary forms, redaction, refusal, failed persistence, and a role
change immediately after a writer acquires its lock.

This is scoped coverage, not a whole-platform settings audit claim. Provider
connection tests and model discovery refresh are probes/cache updates rather
than setting changes. Security-suite, learning, DGM, compliance, Trust and Store
catalog controls have separate activity contracts that were not normalized in
this pass. Other specialized writers still need a common review of old/new and
failure behavior, including immune monitoring switches, Savings assumptions,
research execution, flow autonomy, and workspace selection. Sandbox backend
selection alone does not remove installation-time tool denials. Permissions now
shows the additional **Allow file work** step for a selected running Docker or
Podman container. It changes only the four file/program tool blocks written by
consumer setup, preserves unrelated tool blocks, refuses higher-priority
deployment/workspace denials, records the administrator's reviewed change, and
reads the effective setting back. A generated consumer configuration is covered
by a local installer-to-dashboard route test. Other tool policies and runtime
readiness still appear separately; an installed Docker/Podman customer journey
and audit-store outage rehearsal remain open.

## Promise reviews pilot (October 2, 2026)

| Customer job | Current in-product path | Remaining boundary |
| --- | --- | --- |
| Start and revisit a review | Installer/Settings opt-in; account/workspace-bound page accepts two orders, reported capacity and stock, source basis, expiry, and a conditional choice. The guarded Copilot family offers the same typed create and status operations. A synthetic authenticated browser journey covers Settings, a lost create response, list/readback, and reload. | Figures are operator reports, not connected or independently verified customer orders, inventory, supplier capacity or reservations. Installed-app and customer acceptance remain open. The tenant's pilot namespace caps all its account contexts together at 1,000 active reviews. |
| Record contention and decide | Native form captures a completed interval; current evidence proposes a shared dependency. Only the review owner can confirm an exact current dependency or feasible option. Copilot uses the same store, authority, revision and receipt checks. | Confirmation updates the model only. It neither changes an accepted order nor books stock, labor or supplier capacity. Source revisions and the exclusive latest-decision time must be rechecked before any external action. |
| Recover and correct | GET reassesses current sources; exact operation references recover a committed mutation after a lost response. Stale or expired reports are labeled and require a new review. | No source refresh/edit in the same record, no automatic external reconciliation, and no external audit checkpoint. The local activity outbox can show pending until delivered. |
| Finish and retain | The owner can confirm completion in the page or Copilot, then archive the full review as read-only history. A governed close receipt and verified archive receipt precede active-slot release; an interrupted archive stays visible for owner resumption. Archived history is still listed and readable in the same account and workspace. | Archive storage grows until a separately governed retention policy is defined. Restored archives must be reconciled with current authority; an archived assessment is historical evidence, not a current delivery promise. |

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
| Admit a skill suggested from repeated completed work | Default local distillation writes a tenant-scoped suggestion, not an active skill. Administrators can read full instructions and linked currently shared task sources on Skills, choose a distinct saved name, and confirm one exact proposal version. The canonical skill validation, signing policy and shield checks run before admission; current source checks and a no-overwrite rule guard the write. Administrator permission is checked again at the file-change boundary. The activity record first marks the authorized intent and records success only after the file change; a post-change record failure tells the operator to refresh current state. If a source later becomes private or unavailable, runtime recall stops and the installed skill stays visible to administrators as paused so they can remove its exact version from Skills or guarded Copilot. Local core, API, JavaScript and typed Copilot tests cover this path. | Qualify the authenticated installed-app journey, keyboard and narrow-screen use, signed-only installations, and an interrupted activity record on a customer machine. The separate opt-in AI-provider distillation still writes directly to installed skills after validation and is not changed by this local-suggestion review path. |
| Run a saved learned workflow | Designer has ordinary typed input forms and checks the published version before execution. Unsupported forms refuse rather than silently converting data. | Full installed-app journey and authorized Copilot execution. |
| Turn off or delete a saved flow | Workflows and the designer now offer both actions. A current-state preview counts the live schedule, connected starts, pending form submissions, already active runs and published flows that contain a pinned copy. Turn off keeps the draft; delete requires the exact reviewed draft and publication, refuses while received forms await a decision, and clears the private editor recovery copy when available. Both reconcile the flow schedule. A synthetic browser journey covered create, publish, turn off and delete. | Connected starts need reconnection after a fresh publication. Already active runs and other published flows holding a pinned copy may continue. An unavailable recovery store leaves a separate cleanup warning. Qualify the installed-app journey, failure recovery, and Copilot control. |
| Edit a workflow with Copilot | Designer proposals protect newer edits, and Undo/Redo include settings. | Global operation coverage is separate from graph suggestions. |
| Stop future use of an accepted example | Run page and global Copilot now host the same reason/confirmation form. Copilot's closed catalog reads current selection and authority, prepares a sealed short-lived review, then calls the native withdrawal service only after human confirmation. It reports confirmed, pending and changed states separately. Native page maintenance remains available when Copilot actions, workflows or learning are stopped. | Authenticated installed-app browser acceptance, effective-source transfer for the optional setting, and the rest of the example lifecycle. No claim of erasing copies or undoing completed work. |
| Configure learning after installation | Configuration and installer controls exist. | Effective-source administration, dependencies, readiness and ordinary forms inside the platform. |
| Collect a suitable demonstration | A run page at the first approval now suggests editable, ordinary descriptions of retained inputs and action data, then enrolls a bounded example. Unknown tool results still require the person to describe their fields before capture. | Simpler supported data descriptions and installed-app acceptance. |
| Review examples and permit training use | Workflows and Teach now open a paged, owner-scoped Learning examples inbox. It shows current native status across flows and opens the exact run for inputs, actions, results, accept/reject review, separate permission or withdrawal. A read-only Copilot check on the inbox uses the same scoped status source and links to selected runs. The Teach page lists eligible runs for its selected flow. An accepted example without current permission, or with expired permission, cannot train through the customer route. Focused local route and an authenticated synthetic browser journey cover discovery, handoff, grant and readback. | The inbox scans bounded pages of indexed runs, so concurrent run updates can change page order; refresh to see the latest. Add fuller recovery after interrupted review or signing, qualify the installed-app journey and customer data. Other teaching commands still lack Copilot coverage. |
| Learn, admit, draft and refine a procedure | The ordinary Teach page now signs selected granted runs, learns a verified package, admits it on explicit draft confirmation, and hands a lineage-bound draft to the normal save and publish path. Refinement keeps the exact signed ancestry. Withdrawal blocks future native action eligibility for that admitted lineage. Signed calculations have the same bounded domain derivation. Customer capture or signed-lineage settings now refuse unsigned learned flows at save, publish and action execution, including previously saved flows. | Improve review of competing explanations and lineage consequences before admission; cover teaching through Copilot, installed-app use and customer data. Older unsigned CLI artifacts remain a compatibility path when all customer learning flags are off; an administrator can re-enable them by turning those flags off, so this is not an irreversible service-wide guarantee. |
| Explain why automated work can proceed | Native authority, lineage and signed receipts exist. | A joined, readable explanation of the evidence, approval, action and result; uncertainty must remain visible. |
| Compare an improvement candidate with a deployed baseline | A named owner can freeze a protected pilot against a governed candidate and a fresh deployment observation, then inspect the current promotion and deployed release from the pilot page or a read-only Copilot check. Opening the plan now checks whether release decisions and deployed-release observation are connected; a missing connection disables only the optional release choice and explains the setup gap. The report keeps case exposure and causal lift explicitly unverified. | Bind each actual case to an attested release, independently measure business outcomes, qualify the comparison, and test rollback and recovery in an installed deployment. The frozen plan and live status alone do not prove improvement. |

Implementation references:
[run page](../packages/maverick-dashboard/maverick_dashboard/templates/flow_run.html),
[ordinary inputs](../packages/maverick-dashboard/maverick_dashboard/templates/flow_designer_inputs.js),
[example API](../packages/maverick-dashboard/maverick_dashboard/api_flow_action_episodes.py),
[review service](../packages/maverick-core/maverick/flow/action_episode_review.py),
[lineage service](../packages/maverick-core/maverick/flow/recipe_lineage.py).
The [learning journey contract](./learned-capabilities-product-contract.md)
defines the wider acceptance requirements.

Settings and the installer expose example collection, signed histories, the
signing name and revoked key IDs. A standard local installation creates its
tenant audit-signing key on first signed write. Externally injected/KMS key
custody and deployment-owned configuration are not yet transferable or
recoverable entirely in the UI; the Teach page points administrators to
Settings when its signed prerequisites are missing, while the service still
refuses signing or admission if the effective authority is unavailable.

## Other platform operations

This bounded review does not classify providers, connections, access management,
backups or updates as wholly absent. Existing controls need qualification and
specific gaps closed.

| Area | Observed gap | Required next result |
| --- | --- | --- |
| Interrupted terminal setup | The optional terminal wizard and JSON sidecar retain a private exact-answer receipt before writing keys and configuration. A restart completes only those approved files; a changed file stops recovery without replacement. Local installer and sidecar-protocol tests cover crash points and key conflicts. | The shipped desktop installer uses headless bootstrap and separate dashboard onboarding, so this receipt does not qualify its first-run recovery. A preexisting orphan key file without the receipt needs administrator or support recovery. Qualify the signed desktop build, owner/provider onboarding, and power-loss behavior on a customer machine. |
| Archived Knowledge request lookup | An employee can enter the exact reference of their own locally archived request and see its limited status receipt; another employee receives no record. Local API, JavaScript and synthetic authenticated browser tests cover the owner path. | Add shared-storage transactional archiving, archive pagination and retention retirement, then qualify installed-app and customer acceptance. |
| Chat history completeness | Print loads every authorized saved history page and answered clarification before opening the print view; an incomplete load stops with a retry path. Print preparation stops after 20 history pages, 1,000 older turns, or a 30-second preparation budget (plus the current page request), offers the full-history download path, and never opens a knowingly partial print view. Copy provides a selectable text fallback when the clipboard is unavailable. Copilot checks that a requested control actually opened before claiming success. Local JavaScript, API and synthetic browser cases cover the path, including two earlier pages and a final access check. | Qualify installed-app behavior and real customer history. These fixes do not complete all Chat or whole-platform Copilot operations. |
| Pending work after service interruption | Startup recovery reclaims stale pending work, but the periodic orphan sweep handles active work only. A pending goal that misses the startup window can remain pending without a later owner check. Pending also covers legitimate queued and concurrency-waiting work, so age alone cannot safely prove abandonment. | Add a durable, tenant-scoped dispatch ownership receipt and an atomic recovery transition after ownership ends; then exercise local queue, Redis, gRPC and restart races before enabling periodic pending recovery. |
| Restored connected-channel work | Recovery now uses outcome choices, local date controls and evidence fields. A local Recovery Copilot hosts the same form and operation. Interrupted recording can be finished using the exact retained review. | Qualify the authenticated installed-app journey. Replace manual position numbers with in-product source lookup/selection, particularly for iMessage. Complete the other recovery operations in Copilot and the remaining technical wording on the wider page. |
| Settings controlled by deployment | Some settings correctly refuse local writes when an environment or deployment source controls them. Refusal alone leaves no post-install migration journey. | Explain the current owner and provide an authorized in-product transfer or administrator path with effective-value readback. |
| Connected-automation signing secret | The former false-success path was reproduced with environment and mounted-file secrets. The settings service now checks effective ownership, binds the change to the current administrator and setting, and verifies both incoming and outgoing signing results. The guided form clears another account's draft and distinguishes uncertain results. | Qualify installed-app use and shared Copilot operation. Deployment-source transfer, outbound-only rotation when a separate incoming secret is controlled elsewhere, and durable audit history remain separate gaps. |
| Chat accounts with single sign-on | My work issues a one-use connection code in an uncached POST response, observes it through durable Slack or Microsoft Teams intake, and asks the signed-in owner to confirm the exact chat account. Current workspace role, account and session revocation are checked again for each task. Administrators can see and disconnect links; a failed link store is shown as unavailable. Teams intake requires the Teams channel and endorsed Connector signing key. Legacy account links do not authorize OIDC chat work. | Qualify real provider installations, account changes, revocation and recovery in the installed app. Slack slash-command tasks and HTTP interaction decisions remain unavailable under single sign-on until they can carry the same durable identity proof. A pre-upgrade channel receipt without the provider tuple remains deferred if OIDC is enabled and needs an in-product review path. Email and other channels have no equivalent OIDC bridge. |
| Settings change history | Webhook-secret changes now require accepted deployment-global activity rows for the reviewed intent and confirmed effective result, linked by a random operation ID. A refused or false audit write cannot return success; the prior local overlay is restored when confirmation fails. No secret value or secret-derived fingerprint is put in those rows. | Finish the interrupted-recording journey: a crash between the local settings write and its confirmed activity row leaves a reviewed-only record, and the product cannot yet reconcile that attempt from the UI. An effective-value response or actor-bound review token alone is not a customer audit record. |
| Retention and encryption maintenance | Encryption-key backup already has an in-product download and confirmation. Retention enforcement and encryption migration, rotation and strict-mode changes still have operator commands for which this review did not find equivalent dashboard callers. | Permissioned preview, execution, verified outcome and recovery for the remaining commands. Do not add blind command execution behind a button. |
| Knowledge rights request capacity | Knowledge shows its live request count and, on the local record backend only, a finished-request archive path. An administrator can filter eligible requests, confirm one exact finished request in the page or guarded Copilot operation, and reopen its retained record by reference. Denied requests and approved requests with verified exact source revocation qualify only after holds are released and audit delivery is confirmed. The live list caps at 2,048 requests; local archiving frees one place while retaining the request and source restrictions. Submission and archiving share a strict local fence so a delayed request retry cannot restore a retired live row. Shared PostgreSQL installations refuse archiving and show that capacity cannot currently be recovered in-product, because the two-store move has no shared transactional retirement fence. Local core/API/JavaScript and synthetic authenticated browser checks cover the bounded path. | Add a transactional per-request retirement fence for shared storage, then qualify installed-app use, restored archive and independent audit custody. The archive retains sensitive request material under its own protected authority and has no in-product retention retirement. List views show a bounded recent page; full archive browsing/pagination is not yet qualified. |
| Advanced sandbox and single sign-on policy | Advanced sandbox guidance and identity configuration still include file/environment setup. | Dedicated administration with current-source ownership and safe validation; never weaken isolation or authentication to bypass missing UI. |
| Workspace lifecycle | The administrator's Workspaces roster now exposes plan change, daily limit, pause, resume and removal with confirmation, status reload and native API callers. Removal retains stored records and says so before confirmation. Local route and DOM tests cover these paths. | Qualify the authenticated installed-app journey, including stale state, permission changes, failed writes and recovery. Coordinate full retirement separately from removing a workspace from the roster. |
| Database tenancy and key maintenance | Tenant RLS preflight/backfill remain operator commands. The Workspaces page now previews and runs local workspace-key re-protection using selected key files, with administrator permission, recent MFA, a background result and local route/DOM tests. Switching the server's master key and verifying every workspace afterward remain outside that page. | Complete the in-product maintenance sequence: protected key custody, RLS planning, readiness checks, server-key transition, verified return to service and recovery. Qualify the installed-app journey before calling key rotation complete. |
| Local AI runtime | Provider connection testing exists. The separate native local-runtime planning operation has no dashboard caller found. | Present supported local-runtime preparation and health inside the product; configuring a URL alone does not prepare the service behind it. |
| Complete workspace retirement | Settings > Maintenance previews local client files and explains the offline boundary; the running dashboard refuses an erase before deleting anything. The offline kernel erase retains a signed receipt. Connected services, other storage roots, and exported copies are outside its scope. | Add a coordinated in-product offline retirement journey that stops writers, verifies all storage scopes, presents the final receipt, and prevents the retired client from reopening. Do not report a partial local delete as complete retirement. |
| Backup and installation boundaries | Local backup/recovery and packaged Windows update/repair/rollback have substantial product controls. Shared/remote storage recovery and other deployment types have explicit blockers or separate procedures. | Provide the remaining coordinated administration paths; do not claim all deployment types are qualified from Windows/local acceptance. |
| Stopped tasks on shared PostgreSQL | Startup and recurring recovery inspect stale active goals for the configured tenant, or rotate through a bounded batch of registered, non-retired tenants when the dashboard is unbound. Each write uses an exact tenant predicate and excludes legacy NULL-tenant rows. Zero or tiny reclaim windows and an unavailable registry cause no shared writes; a failed shared backend stops further attempts in that batch. Local tenant-scope, SQL-predicate and fairness tests cover these paths. | Pending goals remain queued because age alone cannot distinguish a lost dispatcher from legitimate Redis/gRPC or capacity waiting. Add durable, tenant-scoped dispatch ownership proof before reclaiming pending work; qualify recovery against a live shared PostgreSQL deployment and installed application. |
| Diagnostic parity | Cache controls, readiness and support-report preparation exist. Selected circuit/rate-limit/health/trace CLI diagnostics have no equivalent dashboard callers found. | Give customers readable health, affected work and actual repair actions. Support export alone does not replace operational diagnostics. |
| Aggregate boards for department-scoped employees | Overview, Spend, Workforce, Savings and Oversight aggregate owner work without filtering every metric by the caller's current department grants. Their pages and board APIs now refuse scoped access before reading those rollups, with plain recovery guidance; unscoped administrators retain the boards. Links to these pages may still be visible to scoped users. | Build domain-aware projections for every chart, server-rendered summary, download and linked detail, then restore useful scoped boards and navigation. A title-only filter would leave counts or costs exposed. |
| Global Copilot | The shared page guide still has a zero tool-call budget. Its recent visible questions and replies now survive navigation in the same browser tab for one hour of inactivity, keyed to the current account/workspace context, with a Clear control; only earlier user questions are returned as untrusted context to the guide. A separate default-off, closed operation catalog covers selected current-state and reviewed actions across registered families, including learning-example status and withdrawal, tasks, connections, and other bounded controls. Effective feature and family settings determine which of those actions are available. On a saved learned workflow, Copilot can open the same labelled run form as the designer after checking the current published release; the form and native run endpoint recheck that release and access before accepting work. Copilot can check the live status of an owner-selected improvement pilot without claiming measured lift, and can finish an exact signed form decision with owner review and receipt readback. Connected-service recovery has a separate restricted local form. These bounded operations do not constitute whole-platform coverage or installed-app acceptance. | Extend the typed catalog and current state across every supported job, including work in another panel or background job. Preserve each native service's authority, result and recovery contract. The retained tab transcript is not server-owned conversation memory or authority for model claims. |
| Chat and answer actions | Chat exposes the dashboard's role-filtered product destinations. The first composer accepts multiline briefs, derives only a short navigation title, and stores the complete text as the task and conversation message; Enter sends and Shift+Enter adds a line. Live completion refreshes answer controls without losing a drafted reply and offers recovery on failure. Copy uses saved content; historical downloads select the stored turn and recheck access and review requirements. Structured exports use reviewed table cells. Print retains stored clarifications and wraps wide content. A public link defaults to the saved answer; the owner can separately preview and confirm the full conversation through the selected message, including answered clarifications. The released transcript is bounded and version-bound; changing an included message or its release approval stops the link. Attachments and work logs remain excluded. Expiring public links route to the selected workspace; existing links remain revocable during a rerun or empty result. Mobile navigation supports a visible Close button and contained keyboard focus. Local route, DOM and Chromium journeys exercise selected paths with synthetic data. | Qualify the installed desktop and real customer content. Full ChatGPT/Claude interaction and whole-platform Copilot operation parity remain open; these answer actions do not establish either. |
| Public form intake | Public admission, binding changes and deletion share a cross-process lock. A submission is refused if its binding changed while the body was read. A form with accepted rows waiting beyond its durable cursor shows a count in Automations; changing any execution binding or deleting it returns a 409 with a Check now path. Polling retains accepted rows when their original flow or workflow is unavailable. Automations creates a high-entropy default link, shows Open/Copy actions on saved form rows, and serves a public page with fields from the bound workflow or published flow. GET and POST recheck the live owner and target. New submissions validate required saved-template parameters and published flow input types before append; flow dispatch coerces form answers against that same release. A fully checked form can replace its public link under the registry lock: the old URL stops, its submission file stays retained, and the new URL starts with an empty queue. A stale retry, waiting submission, or unreadable history refuses replacement. Local route and UI tests cover live, unknown, stale, unavailable, invalid and typed submissions. Automations lets the owner review waiting answers and make a signed Do not run decision for one exact row without changing the received file. If an interruption leaves the signed decision pending in its marker, partially written as an exact prefix of that signed decision, or appended ahead of its completion marker, the owner can inspect the exact row and reason and confirm completion under the original binding; the shared Copilot operation checks that same owner-bound receipt after a lost response. The rows themselves still lack an embedded target generation; a stale original target or previously accepted invalid row still has no authorized repair or rebind path. Storage remains capped at 5,000 rows or 32 MiB per link. | Complete safe repair or rebind for stale targets and invalid accepted rows, extend Copilot to the rest of the form lifecycle, and manage retained-file lifecycle. The signed decision recovery has local route and UI tests; installed-app and customer acceptance remain unverified. |

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
| Create a bulk consent export or change | `consent_bulk_jobs.html` provides operation-specific choices, people and receipt selection, and list upload. `lightwork-consent-bulk-jobs.js` builds the items and reads current profile revisions internally; `parseItems` validates the JSON built by that controller, not customer-entered item data. The change confirmation gives a record count and warns that cancellation will not undo completed changes. | Qualify an authenticated installed-app journey, including stale records, permission changes, per-item progress and recovery. Add a review of the exact selected people and consequences before starting a change; a count alone is not that preview. |
| Prepare an incident report | `_governance_operations.html` mounts labelled structured report fields, selects an incident and carries its current revision in a hidden field. The draft goes through independent approval; recording a filing requires the authority's reference and a receipt-file fingerprint, and the page says this is not proof of external receipt. | Qualify the authenticated installed-app journey and recovery after stale state or interrupted draft, approval and filing-record writes. Verify external submission with the authority separately. |
| Rotate a registered device's key | `consent_native_admin.html` and `lightwork-consent-native-admin.js` require public-key hex, encoded attestation material, verification fingerprints and epoch times. | A device enrollment/rotation handoff must obtain genuine signed evidence from the device, show understandable dates and verify the actual resulting access. Merely renaming these fields is insufficient. |
| Operate generic compliance consoles | The shared guided-action layer can create schema-based fields, but still exposes technical references, unknown-property authoring and some technical-editor fallbacks. | Trace each supported action; supply named record selection and business controls. Preserve exact authority and record binding. A generated form alone is not a complete nontechnical journey. |

The remaining boundaries are additional work items. None of this static review
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
