# Post-install operation coverage

Source review: October 1, 2026; selected journeys updated October 6. This is a working gap register against the
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

## Selected partial journeys (October 4, 2026)

The current branch adds focused local checks for the paths below. These
are bounded source and test observations; authenticated installed-app use,
real provider effects and customer acceptance remain to be qualified.

| Customer job | Current in-product path | Remaining boundary |
| --- | --- | --- |
| Read a task's activity (UX-268) | Chat polling, both event streams and WebSocket use one readable projection. Internal role/brief prompts, prior-failure memory, model scratch text, raw tool envelopes and diagnostic errors are replaced with plain activity; useful findings remain visible. The exact durable event records are retained. | Action activity describes a request, not proof that an external effect completed. Actual action receipts and final results remain the evidence for completion. |
| Recover a stopped task (UX-294, UX-313) | Provider stops open the AI setup route for an installation administrator and offer a review of a retry without unrelated spending controls. Stopped-task event results and outbound failure callbacks use plain explanations without terminal commands. A prepared pilot retains its separate start review and allowance review. Live updates preserve a pending restart confirmation. | A retry still needs current admission and explicit confirmation; changing an AI key or service does not establish that the next run will succeed. |
| Edit workflow run controls through Copilot (UX-672) | Designer Copilot can change typed run questions, the time zone and simultaneous-run limit. Removing a step heals approval-expiry and switch-case routes. All changes use the existing draft patch validation and normal save/publish path. | Generated proposals still require customer review; this does not publish or start work automatically. |
| Collect and close a personal-data request ([UX-020](../USER_EXPERIENCE_AUDIT.md#ux-020--p1--trust--dsar-export-files-are-kept-forever-in-plaintext-after-the-request-is-closed)) | For access and portability, Privacy says **Data collected** after fulfillment. An officer can download the verified package for review and record a completed handoff statement against that exact package, with a secure-channel choice, date, evidence reference and separate review/recipient attestations. The same officer authority, exact package binding and recovery apply in Copilot. Statements stay readable after closure and original-package removal. Collection keeps the response deadline and work queue active until an actual answer. An authenticated disposable native-dashboard journey covers collection, review, statement recording, exact replay, operator refusal and closure. | A handoff statement is an operator attestation, not independent delivery or requester-receipt proof. Semantic redaction, controlled remote delivery, retention sweeps and installed customer acceptance remain open. |
| Manage API credentials ([UX-087](../USER_EXPERIENCE_AUDIT.md#ux-087--p1--feature--only-one-all-powerful-shared-bearer-token-for-the-whole-rest-api-no-per-userper-app-api-keys-scopes-expiry-or-overlap-rotation)) | Named API keys have owner, expiry, rotation and revocation. An API-key bearer can list permitted keys but cannot create, rotate or revoke keys, create service accounts or disable them. The shared approval queue, paused workflow decisions and selected security, consent, content-access, finance, governance and vendor review decisions require `approvals:decide`. An approval-only key cannot use unrelated operator routes. | `tasks:write` still reaches other operator actions; `settings:write` still reaches all administrator-gated controls, including many domain approvals. Some decisions also require task or admin scope. Remaining domain reviews and installed integration behavior need qualification. |
| Check provider spending ([UX-088](../USER_EXPERIENCE_AUDIT.md#ux-088--p1--perf--provider-spend-ledger-grows-by-one-never-pruned-row-per-model-call-and-is-rewritten-whole-twice-per-call-accounting-cost-grows-linearly-then-all-ai-calls-are-blocked-at-16-mb)) | An indexed local SQLite ledger replaces healthy JSON accounting after migration. The hot status read now seeks live holds and unknown receipts instead of scanning settled history; Provider Spending exposes health and recovery guidance. | Each active call still hashes the full database to check its seal, so cost grows with history. Larger-scale, damaged-ledger and installed-workspace recovery acceptance remain open. |
| Keep a local spending refusal separate from an AI outage (UX-249) | Both native AI-call paths exclude provider-cap and accounting-store refusals from health and routing observations. Native circuit admission and outcomes bind the exact tenant and admitted connection configuration, including its address and credential revision, through an opaque digest. A failed connection cannot pause a replacement connection or another tenant; an in-flight result stays attributed to its original connection. After cooldown, one actual probe owns admission. A local refusal releases that probe without inventing success or extending the outage. | Custom injected clients without native configuration metadata retain provider-wide circuit behavior. Live outage and installed-provider qualification remain separate. No spending limit is bypassed. |
| Check an outside agent's uncertain request (UX-240) | Outside-agent detail and selected-agent Copilot read the latest 20 retained execution records with current page authority. A failure after an effect started, a connector's uncertain result and an interrupted execution say the result is unknown. A service's asynchronous acceptance says completion is unconfirmed. Neither outcome automatically retries; the original private request fingerprint remains available to the authorized agent for reconciliation. Successful request readback distinguishes a service response from its business result. | This metadata-only view contacts no connected service and never repairs or repeats an approval. Execution retention is 24 hours and is not a complete activity archive. Confirm the business result in the connected service; automated service reconciliation and customer integration qualification remain separate. |
| Turn a flow suggestion into a fixed action ([UX-090](../USER_EXPERIENCE_AUDIT.md#ux-090--p1--docs--flows-advertised-differentiator-overclaims-automatic-hardening-from-tool-name-history)) | Designer and guarded Copilot can review the candidate action, bind supported scalar inputs to flow data or fixed values, then save a new draft under current version/revision checks. Publishing remains a separate step. Designer now supplies ordinary fields for supported nested details and lists. | Tool-name history is only a suggestion. The hardening review does not automatically supply nested values; complete those fields in Designer. Autonomous application, a real customer-connected action result and measured benefit remain unproved. |
| See the effective provider setting ([UX-217](../USER_EXPERIENCE_AUDIT.md#ux-217--p2--bug--all-settings-shows-compatible-ai-service-addresskey-not-set--default-while-openai_compatible_base_urlapi_key-are-set-and-in-use-catalog-rows-declare-no-environment-source)) | All settings resolves installer, dashboard, deployment and environment provider sources; it shows an environment address and set credential without revealing the secret. Reset can expose the effective server fallback. | Qualify a real installed provider switch, reset and failed-write recovery. A source label is not proof of a successful provider connection. |
| Manage current workspace members ([UX-226](../USER_EXPERIENCE_AUDIT.md#ux-226--p2--bug--workspace-level-administrators-see-workspace-members-controls-on-users-but-the-members-api-requires-installation-wide-admin-so-they-cannot-list-or-change-their-own-workspaces-members)) | A workspace administrator can list, change and clear existing direct members in an active workspace, and issue or cancel a one-time invite for a new identity in that workspace. The grant is checked against the issuing administrator's current role again at acceptance; self and last-admin protections remain. Installation administrators can invite a workspace's first member; pending invites from named installation administrators also require their current global authority at acceptance. Principal-less local and static-token invitations retain their existing link behavior. All SSO invite acceptance requires a matching email asserted in the current signed-in session. Local Python and JavaScript tests cover the path. | Qualify the complete invited-person journey, role changes and loss of authority in the authenticated installed UI. SSO without an asserted email cannot accept an invite until the provider supplies one. A role-store failure after token redemption requires a new link. |
| Read pages in an Arabic-language browser ([UX-198](../USER_EXPERIENCE_AUDIT.md#ux-198--p2--bug--a-browser-set-to-arabic-flips-the-entire-english-ui-right-to-left-dirrtl-mirroring-layout-and-misplacing-punctuation-continue-it-days-30-search-tasks)) | Overview, Chat and public sign-in shells declare the English body as left-to-right. Automatic browser-language selection skips the partial Arabic seed; a complete registered catalog can still be selected automatically. An explicit Arabic choice remains available for translated controls, while English regions retain their own language and direction. Focused local shell and catalog checks cover these paths. | Qualify an installed Arabic-browser journey, and translate and review full page bodies before declaring Arabic at document level or flipping the whole layout. Other locale journeys remain partial. |
| Start work with a full brief ([UX-270](../USER_EXPERIENCE_AUDIT.md#ux-270--p2--ux--the-main-composer-is-a-single-line-200-character-field-multi-line-pastes-are-flattened-and-longer-requests-are-rejected-after-sending)) | Chat and the Overview first-task empty state use a multi-line message composer. The full brief is retained up to 16,000 characters; the short title comes from its first nonempty line. | Qualify paste, keyboard submit/newline, accessibility and error recovery in an installed browser. The legacy separate-title submission path still has its 200-character title limit. |
| Understand a paused system ([UX-291](../USER_EXPERIENCE_AUDIT.md#ux-291--p2--ux--during-an-emergency-stop-the-chat-home-shows-no-stopped-banner-a-users-new-request-immediately-fails-as-lightwork-stopped--halted-with-a-try-again-button-that-cannot-work)) | Workspace readers can see the verified emergency-stop banner without incident details. On Chat, the new-task composer keeps its draft and stays disabled until a verified resume. A provider-unavailable stop names the AI service and asks a person to retry when it returns or ask an administrator to check the connection, without suggesting a spending-limit change. | Goal restart and queued-follow-up controls, live provider failure and installed stop/resume behavior still need acceptance. |
| Judge specialist results ([UX-094](../USER_EXPERIENCE_AUDIT.md#ux-094--p2--trust--a-specialists-job-card-scorecard-counts-every-finished-run-as-delivered-and-values-it-at-an-undisclosed-120-each-showing-100-success-and-26785-roi-after-three-unverified-test-runs)) | The job card distinguishes finished runs from unique deliverables with a current human approval and reports AI cost without rounding a positive amount to zero. It withholds savings and ROI when evidence is absent. Its cost band follows the configured runtime route, and the installation-wide change is labeled and confirmed. | Approval does not prove a financial outcome. Live routing can choose another model; customer-recorded value and installed evidence still need qualification. |
| Build and match specialists from a job description ([UX-260](../USER_EXPERIENCE_AUDIT.md#ux-260--p2--ux--saving-a-specialist-edit-that-is-rejected-shows-only-some-of-the-details-need-fixing-the-actual-reason-is-discarded-unless-the-user-separately-presses-check-settings), [UX-261](../USER_EXPERIENCE_AUDIT.md#ux-261--p2--ux--when-ai-drafting-from-a-job-description-returns-nothing-usable-the-draft-silently-becomes-a-generic-specialist-assistant-with-the-whole-jd-pasted-into-the-name-field-and-is-presented-as-drafted-from-the-job-description), [UX-262](../USER_EXPERIENCE_AUDIT.md#ux-262--p2--ux--jd-matching-shows-raw-pack-ids-as-result-titles-always-rates-the-top-hit-100-match-counts-filler-words-like-specialist-as-evidence-and-never-warns-that-the-match-cannot-run)) | The editor prevents a built-in pack's actions/risk from being widened and explains common rejected changes beside the control. An unusable AI draft falls back to JD bullet steps and states its source; the display name comes from the role title. Matches use readable names, distinctive shared-term bands and current action-readiness links instead of a 100% fit claim. Two local real-browser journeys and focused service checks cover matching, drafting and built-in ceilings. | Other lint failures still need field-specific wording. Relevance bands are heuristic, and live service/connection readiness is checked only when work starts. Provider quality, saved-draft use and installed end-to-end acceptance remain open. |
| Explore departments ([UX-289](../USER_EXPERIENCE_AUDIT.md#ux-289--p2--ux--21-of-54-departments-have-no-description-and-a-machine-cased-name-esg-sustainability-food-beverage-cpg-private-equity-vc-oil-gas-the-workforce-filter-uses-raw-keys-hr-it-grc-sales-gtm-manufacturing-vertical)) | All 54 shipped departments have a label and charter, and the Workforce specialist filter uses the same names as the department cards. A local catalog invariant checks coverage. | Charters still need customer review, and installed catalog accessibility/visual acceptance remains open. |
| Update a deployed team ([UX-238](../USER_EXPERIENCE_AUDIT.md#ux-238--p2--reliability--pressing-deploy-team-again-on-an-already-deployed-department-silently-rebuilds-the-team-deleting-specialists-the-admin-added-to-it)) | Workforce and Agent Factory review complete current and proposed named rosters before saving. Manual additions, removals, roles and descriptions are preserved while newly eligible ready specialists are offered. Save rechecks exact team, profiles, enabled department, plan and current actor access under the native authority/file fence. A bounded original receipt is saved atomically with the team; lost replies read that exact receipt without recreating the roster. Copilot uses the same complete review and original-outcome reader. Native service and authenticated 320px browser journeys cover save, intervening changes, repeated submission, denied access and later roster edits. | A pre-marker team's first update conservatively treats the present catalog as already offered and explains that historical limit. No task or provider call starts when a team is saved. Receipts retain the latest 32 saves; an absent original receipt never proves a save. Shared deployment and customer acceptance remain separate. |
| Recover when a specialist cannot start ([UX-338](../USER_EXPERIENCE_AUDIT.md#ux-338--p2--ux--a-specialist-that-cannot-deploy-stops-the-task-with-an-internal-error-string-a-paused-badge-and-a-try-again-button-that-can-only-fail-the-same-way)) | The task and chat list use a distinct "Can't start" state, show the missing required action and a setup route, and hide blind restart/budget controls. Setup recheck is read-only; restart admission requires a current action-readiness pass. A local browser failure-to-UI path passed. | Action readiness is narrower than a live provider, connection and permission check. The stopped task cannot be reassigned in place; choosing another specialist starts separate work. |
| Give a deployed team work ([UX-290](../USER_EXPERIENCE_AUDIT.md#ux-290--p2--ux--after-deploy-team-the-agent-factory-says-give-it-work-from-the-teams-page--there-is-no-teams-page-and-the-fleets-page-where-deployed-teams-live-is-hidden-from-the-sidebar)) | Agent Factory's two deploy paths link to the exact returned team card and task form, and offer a setup review when no specialist is ready. The concurrent Workforce deploy path links to that same team. Work navigation and the destination page now call the area Teams; its existing `/fleets` URL remains valid. A Teams Copilot guide names the actual task controls. Local UI, page, navigation and context checks cover the paths. | Check Workforce's zero-ready wording, then qualify role-scoped installed use, accessibility and real team task admission. |
| Ask Copilot about current work ([UX-284](../USER_EXPERIENCE_AUDIT.md#ux-284--p2--ux--copilot-is-absent-from-the-landing-chat-page-and-all-chat-conversations--the-pages-a-new-user-sees-first), [UX-285](../USER_EXPERIENCE_AUDIT.md#ux-285--p2--ux--copilot-treats-every-recorddetail-page-a-task-a-flow-run-all-settings-as-the-generic-home-page-so-it-doesnt-know-which-record-the-user-is-looking-at)) | Desktop and mobile Chat expose Copilot outside the hidden menu. The current task panel can show its authorized title and status; detail routes and All settings have distinct guidance. Record context stays on the server and does not enter model prompts or later guide history. | Copilot's operations cover only selected actions. Qualify installed accessibility, context freshness, and whole-platform operation coverage. |
| Get page-specific Copilot help ([UX-282](../USER_EXPERIENCE_AUDIT.md#ux-282--p2--ux--every-core-everyday-page-chat-home-overview-tasks-approvals-settings-flows-users-connections-spend-workforce-help-gets-only-auto-generated-boilerplate-copilot-help-hand-written-context-exists-only-for-47-governanceconsentprivacy-pages)) | Thirty everyday routes have control-specific guidance, including all visible Work, Insights and Administration navigation pages. Help, Account, setup, AI services and Billing also have direct guidance. Local tests compare 20 named first steps with their page controls; a browser fixture displays distinct Settings, Tasks and Help guidance. | Guidance explains the visible controls; it does not make every Copilot action available. Feature and role variants, installed wording review, and remaining Governance and advanced routes need qualification. |
| Reach only usable navigation ([UX-288](../USER_EXPERIENCE_AUDIT.md#ux-288--p2--ux--sidebar-shows-plugins-mcp-channels-workspaces-and-recovery-to-people-who-then-get-403-when-they-click)) | Sidebar and finder hide the five installation-wide consoles from workspace administrators and filter links against a verified API key's scopes. Local HTTP checks compare links with page gates. | Audit every registry entry against its route gate and qualify real signed-in navigation across roles. |

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
| Review work | Project reviews; existing `reviews.respond` | Fixed-version response with current reviewer authority and receipt recovery | Task deliverable sign-off still needs its separate typed decision and original-receipt adapter. |
| Decide an approval | Approvals full review and selected-request Copilot read, approve and reject | Exact actor, request identity, current claim, expiry, content owner and decision round are checked inside the native vote transaction. Copilot additionally binds its reviewed quorum state. A lost reply reads the original vote and audit-delivery status without another vote, acknowledgement, rejection note or audit delivery. A page hash preserves the selected request. | Send back, undo and deliverable decisions are separate actions. Local native browser checks cover both direct and Copilot late-claim refusal and original receipt recovery. PostgreSQL locking was source-checked, not exercised against a live server in this addition. |

Focused local product-operation and native-submission tests passed (27 checks).
No installed-app, real provider, customer-content or narrow-screen acceptance
was run for these additions. Whole-product Copilot parity remains open.

## High-impact action review (October 3, 2026)

Finished-result sign-off now retains the actual accepted reviewer, result
version and receipt atomically with the native decision. Chat and the selected
result's Copilot can recover an interrupted response without resubmitting the
decision or repeating learning or a configured handoff. A previous person's
matching decision remains attributed to that person. Changed results are
identified as historical receipts. Follow-through is reported as attempted or
uncertain rather than proof of delivery or training. The native SQLite browser
journey covers these controls; the PostgreSQL transaction path remains
source-reviewed rather than live-deployment-qualified.

Learned-skill prompt construction now rechecks the exact saved admitted bytes,
current tenant and source permission after ranking. A withdrawn or replaced
artifact stays unavailable even if a held object is relabeled as manual.
Ordinary manual skills retain their existing behavior. This check protects the
next prompt construction; it cannot remove instructions already sent to a model.

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

## Approval challenges pilot (October 5, 2026)

Learning links to ordinary controls for choosing a fictional approval change
and one or two sign-offs, building and checking a practice scenario, reviewing
an imported file before a separate local check, explicitly authorizing a
download, and removing an inactive saved record. Selected Copilot operations
use the same native authority, exact revision and original-operation receipts.
The feature defaults off and has both installer and Settings controls.

The fixed receiver checks supported local approval commit functions in a fresh
disposable process with no customer configuration, credentials or network.
Its result distinguishes the deliberately weakened fault from the actual guard
and becomes historical when supported installed source changes. Unknown checks
do not automatically run again. Both removal audits must be delivered before an
active record is deleted; the separate removal receipt remains, and a retired
operation cannot recreate it. Downloads already shared are not recalled.

The active namespace holds 256 records with 40-row browsing; imports are capped
at 8 KB. Supported modeled checks are limited to one request, two reviewers,
12 transitions and 50,000 states, with a 10-second isolated receiver deadline.
This is a model-only local pilot. It does not extract private incidents, qualify
another product or a shared backend, deploy a live correction, or establish
research novelty or customer business benefit.

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
| Enroll/restrict an employee | Employee companions; shared native policy update and selected-employee Copilot | Named workspace admin and explicit membership; current target membership, SCIM activity, account/workspace binding, atomic revision comparison | Exact saved policy; an ambiguous save reads current metadata without repeating the change or attributing another request's update | Native 320px direct/Copilot journey covers selection, stale review, lost reply, account change and readable mobile cards. Installed-app and live-provider acceptance remain open. |
| Ask about current work | My companion and selected-own-companion Copilot; closed supervision intent + owned world-model reads | Named current member; enabled employee policy; exact owner, department and project scope | Bounded current snapshot, task links and policy receipt; no AI charge for the current-work read | Native phone journey reads current permitted work and refuses another account. Installed-app acceptance remains open. |
| Converse and write drafts | My companion and selected-own-companion Copilot; same native bounded writer and reservation coordinator | Explicit AI grant; current identity, policy, generation, source ACL, pinned route and effective allowance before dispatch/publication; no tools | Copilot reviews the message, context and maximum spend, then reserves once and dispatches outside the global authority fence. Its exact private reply link survives navigation; a newer answer never substitutes for the original. Clearing or expiry withholds the answer, while a consumed request cannot dispatch again. | Native 320px direct/Copilot journey passes with synthetic model completion and real native stores and budget settlement. Live provider quality remains unqualified. Exact request metadata lasts 24 hours; answers obey current source access, retention and latest-50 history. Under Do not save, Copilot offers the native transient conversation instead of charging for an unrecoverable background answer. |
| Review/clear companion history | My companion; encrypted history read/clear service | Current exact owner and workspace; no admin content override; same-origin mutation and reviewed history revision | Cleared live history plus metadata receipt; refresh on uncertainty | Global Copilot now checks private history metadata and opens an owner-bound, confirmed clear with exact-retry receipt. Installed-app and retention/backup qualification pending |
| Review companion activity | Employee companions and selected-employee Copilot; shared native metadata projection | Current workspace admin, explicit membership | Policy/event metadata without conversation text; limited to matching entries from the latest 200 workspace activity records | Native phone journey passes; this is not a complete immutable activity archive or an administrator content-access grant. |

Connector access, learned memory, writes/approvals, active-run steering and
voice are unavailable. Deadline-driven purge, replicated policy
authority and existing task/chat administrator visibility remain separate work.

## Learning operations

Automatic provider skill generation now refuses both saved-name collisions and built-in names.
It cannot overwrite a reviewed company procedure. Learning settings distinguish locally
staged suggestions from the separately enabled automatic provider-writing path.
The Learning page also reports spending-limit learning as off unless it was explicitly
enabled, matching its runtime default instead of showing an unmet request to turn it on.

The October 5 dashboard scheduler now drives the configured nightly self-improvement
review, even when dreaming is off. Live setting changes reconcile the durable schedule;
the shared CLI/dashboard runner retains per-tenant learning-stop checks, a restore point,
one evaluation allowance across the fleet and eligible transfer sweep, and metadata-only
activity receipts. Learning displays completed, unfinished, stopped and unavailable
review states. Missing cases, additional-provider-call authority or exact deployed-prompt
checks cannot be represented as an adopted improvement. Ordinary percentage fields now
support paired-score comparison without creating a data file; that comparison is explicitly
separate from causal evidence and links to the managed Workflow pilots journey.

The current review groups seven learning families into 36 operations covering
readiness, configuration, source selection, retention, review, permission for
training use, learning, admission, drafting, execution, refinement and recovery.
These are grouped jobs, not a route count or completion percentage. Richer
inference families add separate data-range and decision controls.

Native learned-answer reuse now binds each stored answer to its actual source
task and original owner. Deleting, excluding or reassigning that source stops
later recall, including a withdrawal during output screening. Legacy answers
without source custody are withheld from task execution. Shared cache instances
are isolated by the current tenant and data path; expiry remains finite. The
existing My Data exclusion and its Copilot operation control this derivative
without a new configuration or maintenance command. Local native lifecycle
checks cover reuse, source withdrawal, deletion, reassignment and tenant changes.
This does not establish withdrawal consistency for every other learning store.

Saved taught workflows now offer **Compare real cases** in Designer. The owner
selects up to five retained finished runs of the exact published baseline,
reviews each original input and expected answer, and explicitly authorizes
their evaluation and exact release review. Both complete frozen rule versions
run through the native executor with the same original inputs. The retained
comparison binds the source runs, versioned definitions and actual replay
receipts. It can be selected in the existing independent release decision,
publication, observed-run and retained-restoration journey; Copilot opens the
protected case form, reads retained results and supports the existing release
controls. Lost replies reopen the original comparison instead of running again.
Source changes or missing replay receipts prevent a new release from relying
on that evidence. Native service and Chromium journeys covered the combined
path, including phone layout, independent approval and actual restoration.
The local adapter permits bounded scalar inputs and rule-only routing, without
transform expressions, waiting, cycles, AI fallback or connected actions. Cases
come from the latest 200 runs; original input is limited to 8 KB and a request
to 16 KB. A 10-second allowance is checked at native node boundaries. Matching
customer labels on these selected known cases is not held-out accuracy or
measured business lift, and local replay time includes native recording work.

| Customer job | Current boundary | Required follow-through |
| --- | --- | --- |
| Admit a skill suggested from repeated completed work | Default local distillation writes a tenant-scoped suggestion, not an active skill. Administrators can read full instructions and linked currently shared task sources on Skills, choose a distinct saved name, and confirm one exact proposal version. The canonical skill validation, signing policy and shield checks run before admission; current source checks and a no-overwrite rule guard the write. Administrator permission is checked again at the file-change boundary. The activity record first marks the authorized intent and records success only after the file change; a post-change record failure tells the operator to refresh current state. If a source later becomes private or unavailable, runtime recall stops and the installed skill stays visible to administrators as paused so they can remove its exact version from Skills or guarded Copilot. Local core, API, JavaScript and typed Copilot tests cover this path. | Qualify the authenticated installed-app journey, keyboard and narrow-screen use, signed-only installations, and an interrupted activity record on a customer machine. The separate opt-in AI-provider distillation still writes directly to installed skills after validation and is not changed by this local-suggestion review path. |
| Run a saved learned workflow | Designer and guarded Copilot open the same ordinary typed input form and recheck the published version before execution. Unsupported forms refuse rather than silently converting data. | Qualify the installed-app journey with real connected actions and customer inputs. |
| Configure a workflow's connected action | Designer loads the current account-authorized action description and presents ordinary nested detail, list, number, yes/no, date and named-choice fields. Omitted optional details remain distinct from explicit empty values. Invalid edits, unavailable descriptions and newer drafts are retained; save refreshes the current description. A native browser journey saves and reloads the typed order, publishes it, pauses for approval and executes the harmless local fixture action exactly once. Explicit write-only fields refuse credential authoring and link to Connections. | Supports bounded local references and declared alternatives, up to ten nesting levels, 128 details/items and 2,048 values/schema nodes. Conditional/composed constraints, external/cyclic references, unknown formats, unsafe integer precision and nested earlier-step substitutions refuse; there is no raw-data fallback. This does not qualify arbitrary connector descriptions or a live customer integration. |
| Check a specialist's answers | Workforce lets an administrator save and revise up to 20 business test questions per specialist, then select up to five at a time against its current answer instructions with no tools or company knowledge. The existing bounded-model-test installation setting gates paid runs. An exact request reference is saved before dispatch, so a lost response reads the original result without repeating a paid call. Completed answers, case versions, model, instruction fingerprint and recorded cost remain in workspace history. Copilot's enabled model-controls family can read and add checks, then select up to five named questions from a larger saved set with sealed review and exact-operation recovery. Only the selected versions are bound to that run. Customers can also review proposed instructions and compare paired actual answers, literal rule results and recorded costs without changing the specialist. Both instruction versions, current model and selected question versions are frozen before the one bounded admission. Later edits mark retained comparisons as historical; stale reviews refuse before spending and unknown paid outcomes read back without another call. Native phone and typed-Copilot browser journeys passed on Python 3.12. Specialist pickers now resolve their catalog once instead of rereading it for each specialist. | These are literal answer checks, not a full specialist or source-correctness evaluation. Add knowledge-grounded grading, safe workflow/tool simulation, flow cases, import, promotion gates, independent audit custody and installed-app acceptance. Paired literal checks do not establish source correctness or business lift. |
| Turn off or delete a saved flow | Workflows and the designer now offer both actions. A current-state preview counts the live schedule, connected starts, pending form submissions, already active runs and published flows that contain a pinned copy. Turn off keeps the draft; delete requires the exact reviewed draft and publication, refuses while received forms await a decision, and clears the private editor recovery copy when available. Both reconcile the flow schedule. A synthetic browser journey covered create, publish, turn off and delete. | Connected starts need reconnection after a fresh publication. Already active runs and other published flows holding a pinned copy may continue. An unavailable recovery store leaves a separate cleanup warning. Qualify the installed-app journey, failure recovery, and Copilot control. |
| Edit a workflow with Copilot | Designer proposals protect newer edits, and Undo/Redo include settings. | Global operation coverage is separate from graph suggestions. |
| Stop future use of an accepted example | Run page and global Copilot now host the same reason/confirmation form. Copilot's closed catalog reads current selection and authority, prepares a sealed short-lived review, then calls the native withdrawal service only after human confirmation. It reports confirmed, pending and changed states separately. Native page maintenance remains available when Copilot actions, workflows or learning are stopped. | Authenticated installed-app browser acceptance, effective-source transfer for the optional setting, and the rest of the example lifecycle. No claim of erasing copies or undoing completed work. |
| Configure learning after installation | Configuration and installer controls exist. | Effective-source administration, dependencies, readiness and ordinary forms inside the platform. |
| Collect a suitable demonstration | A run page at the first approval now suggests editable, ordinary descriptions of retained inputs and action data, then enrolls a bounded example. Unknown tool results still require the person to describe their fields before capture. | Simpler supported data descriptions and installed-app acceptance. |
| Review examples and permit training use | Workflows and Teach now open a paged, owner-scoped Learning examples inbox. It shows current native status across flows and opens the exact run for inputs, actions, results, accept/reject review, separate permission or withdrawal. A read-only Copilot check on the inbox uses the same scoped status source and links to selected runs. The Teach page lists eligible runs for its selected flow. An accepted example without current permission, or with expired permission, cannot train through the customer route. Focused local route and an authenticated synthetic browser journey cover discovery, handoff, grant and readback. | The inbox scans bounded pages of indexed runs, so concurrent run updates can change page order; refresh to see the latest. Add fuller recovery after interrupted review or signing, qualify the installed-app journey and customer data. The Teach page and guarded Copilot now cover selected-run learning, rule teaching, and recipe/rule refinement; signed-file import and admission stay in their native controls. |
| Learn, admit, draft and refine a procedure | The ordinary Teach page now signs selected granted runs, learns a verified package, admits it on explicit draft confirmation, and hands a lineage-bound draft to the normal save and publish path. Guarded Copilot offers selected-run learning and opens the native rule/recipe refinement forms with current flow, example and permission checks. The signed result stays in the human teaching interface; learning does not publish or start work. Refinement keeps the exact signed ancestry. Withdrawal blocks future native action eligibility for that admitted lineage. Signed calculations have the same bounded domain derivation. Customer capture or signed-lineage settings now refuse unsigned learned flows at save, publish and action execution, including previously saved flows. | Improve review of competing explanations and lineage consequences before admission; qualify installed-app use and customer data. Older unsigned CLI artifacts remain a compatibility path when all customer learning flags are off; an administrator can re-enable them by turning those flags off, so this is not an irreversible service-wide guarantee. |
| Restore a learning safety copy (UX-246, UX-247) | Learning now reviews the selected copy's actual named learning families and current saved-byte revision before confirmation. Native restoration checks that exact revision again before effects. Safety copies include safeguards, habits and accepted model instructions with their provenance, as well as the earlier six families. Database-backed instructions are restored through a selective tenant-bound transaction; customer work, activity, evaluation examples and transfer history stay current. Old six-family copies preserve families they never covered. Runtime safeguard, habit and model-instruction recall reload the restored guidance. Scheduled, manual and terminal cycles require one durable safety copy before learning; disabled, failed or oversized copies prevent admission. Two read-only Copilot controls use the native coverage and open the exact ordinary restoration form. | Copies are local records without independent custody. Restoration across several stores can partially fail and reports that separately from a refusal before effects. A saved point restores its named derived learning information, not completed work, outside actions, every cognitive subsystem or provider-internal training. Shared deployment, packaged installation and customer acceptance remain separate. |
| Explain and retain answer feedback (UX-668) | Chat offers an optional reason with the rating and reads the current saved feedback. A new encrypted native receipt binds the exact answer, actor, workspace and original request; any optional learning signal uses only the matching retained completed episode. A lost response reads the original receipt and never resubmits or trains again. A changed answer keeps the entered reason for review. Copilot opens the same private form without putting the reason in its model guidance. The native browser journey covers actual encrypted storage, one signal per original request, repeated requests, interrupted replies, stale answers and permission changes. | A rating is a human signal, not independently measured business performance or proof of completed training. Retained workspace-era feedback is visibly marked when its original answer version was not recorded; deployment-global legacy rows are not adopted. |
| Read an answer's recorded price and time (UX-669) | Chat and the typed task-status read display figures only from the retained completed native run whose summary matches the exact current answer. The helper rechecks owner, department, result, work status, source time and episode. Missing or damaged price evidence stays unavailable; tiny positive prices never appear as zero. Price-only updates refresh the separate footer while keeping unsaved notes and action-review versions. Native and 320px browser journeys cover matching figures, unknown prices, edited answers, damaged evidence, private notes and denied access. | Recorded runtime price is not a provider invoice and excludes separately billed outside services. Elapsed time includes waiting. SQLite was exercised; changed PostgreSQL behavior and live provider accounting still need qualification. |
| Keep answer controls reachable on a phone | Idle Chat recovery occupies one expandable status row. Its privacy explanation, comparison and storage controls remain available inside the disclosure; a real conflict, unconfirmed save or recovery error opens the controls. The authenticated native 320px journey keeps Save feedback above the composer, restores encrypted unsent text after restart, excludes attachments, preserves local edits through storage maintenance and reads an interrupted save. Other editors retain their full recovery presentation. | Files must be chosen again after reload. Unresolved recovery remains visible and can occupy more room until the person resolves it. |
| Confirm complete Copilot changes before applying them | Mutation review uses a larger bounded complete presentation than a status summary and withholds Apply if any detail is omitted. Team reviews use their full named roster renderer. Plugin checks retain the selected installed version, complete practice input, expected answer, cancellation target and migration consequences. Approval review reads the full authorized private card, including command line breaks, without widening the shared/channel or model projection. Deletion review shows exact per-store counts, preservation obligations and every retained exception while native receipts retain the complete source identifiers. Workflow release reviews group every input, teaching example and route instead of clipping a flat field list. | Large unsupported reviews require the protected full controls. A readable review does not establish approval, plugin correctness, external execution or a completed deletion. Existing native permission and source-version checks remain required when applying. |
| Apply or restore learned instructions | Learned and guarded Copilot review the complete instructions changed, removed or returned. Execution checks the exact reviewed revision and current approval; restoring a whole earlier instruction set updates every affected proposal state and verifies the resulting overlay. Recovery reads the actual result without applying twice. | Qualify installed use and interrupted activity recording; an applied instruction is not evidence of better results. |
| Obtain instructions used in actual work | Learning links to ordinary recording setup, an owner-named task picker, dated records, exact text review/download, withdrawal and interrupted-record resolution. Typed Copilot shares the native controls. Recording is default off and each task needs separate consent for one successful call started afterward within 24 hours. Actual native Agent/LLM arguments supply the record; no extra AI call is made. Current task, shared-source and contributing preference ownership are checked. A successful call consumes the request before optional persistence; failed privacy/source checks or unavailable receipts withhold text without recording a later call. Native permission/recovery checks and an authenticated 320px browser journey passed on Python 3.12. | Single-computer local storage only; up to 512 retained entries including task pointers and 100 recent eligible tasks in the picker. Withdrawal removes available text while retaining the decision; it does not free the history capacity or recall downloads. The record is the native connection's historical instruction argument, not the provider's internal prompt, a current baseline, factory generation evidence or improvement proof. File selection for a learning pass stays explicit. |
| Explain why automated work can proceed | Saved designer and run pages now join current authorized teaching sources, learning permission, publication, separate action approval and recorded results. Read-only Copilot uses the same explanation. Withdrawn or unavailable source evidence stays withheld; saved evidence is distinct from unsaved changes and business-outcome proof. | Qualify installed use, connected-service outcome evidence and current cross-panel changes. |
| Keep and check a recorded learning receipt | Learning history downloads the exact currently recorded adoption or restoration receipt. Its bounded download stays on the page if the selected record is unavailable. Receipt upload checks its contents against the current workspace record as well as its fingerprint; a valid receipt without a matching local record is not treated as a recorded workspace change. Human approval and a separately trusted history remain distinct. Audit-authorized Copilot summarizes the same current recorded history with readable dates and a link to its receipt controls. Legacy records no longer claim a sealed history. Mobile rows show ordinary labels and keep the download readable. A real local dashboard journey downloads and uploads the native receipt without leaking private record content. | The local history is not externally anchored. Receipt fingerprints alone cannot establish human approval, detection of a wholly replaced history or improved business results. |
| Retain and compare a learning-history checkpoint | Learning history and audit-authorized Copilot offer the same native complete-history download and protected file-selection comparison. The digest-only file covers a fully verified immutable native journal prefix, including adoption, restoration and related recording steps. Later native records remain compatible. The native browser journey retains the original file, restores a change and verifies the extended history; rebuilt earlier history and omitted earliest events fail comparison. Phone controls fit at 320px. | The original file must be retained independently and trusted by its owner; it is not an external witness, signature, approval proof or guarantee that every real action was recorded. Complete history is limited to 4,096 journal events and 16 MB; uploads to 4 KB. Missing, legacy, corrupt and larger histories refuse rather than produce a partial checkpoint. |
| Compare an improvement candidate with a deployed baseline | A named owner can freeze a protected pilot against a governed candidate and a fresh deployment observation, then inspect the current promotion and deployed release from the pilot page or a read-only Copilot check. Opening the plan now checks whether release decisions and deployed-release observation are connected; a missing connection disables only the optional release choice and explains the setup gap. The report keeps case exposure and causal lift explicitly unverified. | Bind each actual case to an attested release, independently measure business outcomes, qualify the comparison, and test rollback and recovery in an installed deployment. The frozen plan and live status alone do not prove improvement. |
| Release or restore a taught workflow | A stock single-computer installation connects exact-version practice, owner-authorized independent review, preparation, publication, an actual run under that release, recorded failure checks and exact retained-rule restoration. Native controls and guarded Copilot use current permission and saved-state checks; self-approval is refused. Restoration creates a new authorization, keeps the current draft and leaves waiting work from both old releases revoked. Lost replies read the saved decision. An authenticated native browser journey and 320px inspection passed; the signed native service journey also passed on Python 3.12. | This path supports bounded taught rules and simple routing with AI fallback off. The same path now pins the complete baseline and candidate of owned, acyclic nested workflows: up to 16 child workflows, 64 defined steps and eight nesting levels. Rule, routing and input-setting steps run with AI fallback off. Current child changes invalidate practice or a prepared release; restoration returns the retained child versions and grants a fresh release authorization without replacing current drafts. Connected actions and waiting steps need their own release connection. Practice and failure checks establish execution readiness, not accuracy, business improvement or whole-customer acceptance. Shared-storage releases are not qualified. |

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
| Archived Knowledge request lookup | Both employees and administrators browse protected forward batches beyond the old archive prefix. Employees see only their own ordinary request receipts in Active and Finished views; review-only material, other owners and Ready to archive are withheld. Administrators also receive current source and hold checks. Opaque positions bind account, workspace, view, authority and expiry. Next batch, Start over and typed Copilot use the same native read; Open these requests returns to the exact current batch. Native full-archive journeys covered 2,055 archive entries, duplicate hot/archive copies, changed roles and positions, current restrictions and holds, and 320px controls. Employee reads stayed bounded to 25 archive bodies per batch. | Archive retirement, shared-storage transactional moves, installed customer acceptance and independently retained audit custody remain separate. The exact owner-only reference receipt remains available. |
| Chat history completeness | Print loads every authorized saved history page and answered clarification before opening the print view; an incomplete load stops with a retry path. Print preparation stops after 20 history pages, 1,000 older turns, or a 30-second preparation budget (plus the current page request), offers the full-history download path, and never opens a knowingly partial print view. Copy provides a selectable text fallback when the clipboard is unavailable. Copilot checks that a requested control actually opened before claiming success. Local JavaScript, API and synthetic browser cases cover the path, including two earlier pages and a final access check. | Qualify installed-app behavior and real customer history. These fixes do not complete all Chat or whole-platform Copilot operations. |
| Pending work after service interruption | Startup recovery reclaims stale pending work, but the periodic orphan sweep handles active work only. A pending goal that misses the startup window can remain pending without a later owner check. Pending also covers legitimate queued and concurrency-waiting work, so age alone cannot safely prove abandonment. | Add a durable, tenant-scoped dispatch ownership receipt and an atomic recovery transition after ownership ends; then exercise local queue, Redis, gRPC and restart races before enabling periodic pending recovery. |
| Restored connected-channel work | Recovery now uses outcome choices, local date controls and evidence fields. A local Recovery Copilot hosts the same form and operation. Interrupted recording can be finished using the exact retained review. | Qualify the authenticated installed-app journey. Replace manual position numbers with in-product source lookup/selection, particularly for iMessage. Complete the other recovery operations in Copilot and the remaining technical wording on the wider page. |
| Settings controlled by deployment | Some settings correctly refuse local writes when an environment or deployment source controls them. Refusal alone leaves no post-install migration journey. | Explain the current owner and provide an authorized in-product transfer or administrator path with effective-value readback. |
| Connected-automation signing secret | The former false-success path was reproduced with environment and mounted-file secrets. The settings service now checks effective ownership, binds the change to the current administrator and setting, and verifies both incoming and outgoing signing results. The guided form clears another account's draft and distinguishes uncertain results. | Qualify installed-app use and shared Copilot operation. Deployment-source transfer, outbound-only rotation when a separate incoming secret is controlled elsewhere, and durable audit history remain separate gaps. |
| Chat accounts with single sign-on | My work issues a one-use connection code in an uncached POST response, observes it through durable Slack or Microsoft Teams intake, and asks the signed-in owner to confirm the exact chat account. Current workspace role, account and session revocation are checked again for each task. Administrators can see and disconnect links; a failed link store is shown as unavailable. Teams intake requires the Teams channel and endorsed Connector signing key. Legacy account links do not authorize OIDC chat work. | Qualify real provider installations, account changes, revocation and recovery in the installed app. Slack slash-command tasks and HTTP interaction decisions remain unavailable under single sign-on until they can carry the same durable identity proof. A pre-upgrade channel receipt without the provider tuple remains deferred if OIDC is enabled and needs an in-product review path. Email and other channels have no equivalent OIDC bridge. |
| Settings change history | Native signing-secret changes retain an authenticated encrypted record before publication. Settings and guarded Copilot let the original account verify exact prepared publication or exact prior state under unchanged source authority, then append a clearly labelled current-state observation to the original operation's activity history. Recovery never saves a secret again. Accepted activity contains no secret value or secret-derived fingerprint; completed local recovery erases its private prepared material. A native browser journey covers interrupted publication, real signature verification, audit refusal, rollback, lost replies, Copilot recovery and changed ownership. | A reconciliation observation does not reconstruct a missing original completion or prove that an intermediate change never occurred. Pre-upgrade attempts without the prepared publication cannot be confirmed retroactively. Pending recovery capacity is 128; the local view keeps 64 recent completed recovery records. Durable audit retention and deployment-source transfer remain separate. |
| Review original activity checkpoints | Activity log already provides native signed publication, retained receipts and separately kept receipt comparison. Three read-only Copilot controls now read current retained metadata, run the same native signed-chain check and select a saved checkpoint for its authenticated receipt download. Native signed publication, actual download, denied account and modified-log detection passed. The result distinguishes local matching records from independent custody, human approval and complete real-world recording. | Publishing, retirement, custody settings and separately kept-file comparison continue through their native forms. These read controls do not establish an all-action guarantee or outside retention. |
| Recover an uncertain future schedule | Maintenance opens the exact named schedule in Automations; the old attempts-only resume mutation refuses without changing work. The ordinary native form reviews current parked evidence, named successor choices, stopped-worker confirmation and a reason. A private original-request reference is saved before direct or Copilot repair; interrupted responses and reloads read the exact actor-bound native receipt without another repair or replay of historical work. | Historical business effects remain unknown. A missing original receipt does not prove that an outstanding request failed. Native future recurrence repair does not reconcile external systems. |
| Retention and encryption maintenance | Compliance controls preview the exact retention candidates, bind them inside the native writer transaction or file lock, and retain the original reviewed deletion result. Ordinary controls and three typed Copilot operations share this path. Settings > Maintenance reviews older-record protection and local key changes, with three equivalent Copilot operations, fresh authentication for rotation, private original-operation custody and read-only interrupted-result recovery. Native encryption migration now preserves a record edited concurrently instead of overwriting that customer's edit. Real native/browser journeys cover actual local deletion and encryption, stale content, blocked writers, rejected audit acknowledgement, lost replies, another account, original recovery and narrow screens. A disposable real PostgreSQL 16 journey also refuses same-count content changes, blocks a competing writer under the native reservation, deletes the exact two reviewed messages and preserves another tenant, legacy unbound rows and a later insert. | Retention reviews bound each store to 100,000 rows or 16 MiB and activity history to 256 files or 16 MiB; larger reviews refuse. A partial multi-store deletion remains partial. The PostgreSQL proof covers the native message purge, not the full shared installation or dashboard lifecycle. Local process-key rotation refuses shared, externally supplied and per-workspace keys. This is additive key change, not deletion of old keys or full-estate re-encryption. Strict protection controls remain in All settings; shared key custody and coordinated server/workspace-key transitions remain separate. |
| Knowledge rights request capacity | Knowledge shows its live request count and, on the local record backend only, a finished-request archive path. An administrator can filter eligible requests, confirm one exact finished request in the page or guarded Copilot operation, and reopen its retained record by reference. Denied requests and approved requests with verified exact source revocation qualify only after holds are released and audit delivery is confirmed. The live list caps at 2,048 requests; local archiving frees one place while retaining the request and source restrictions. Submission and archiving share a strict local fence so a delayed request retry cannot restore a retired live row. Shared PostgreSQL installations refuse archiving and show that capacity cannot currently be recovered in-product, because the two-store move has no shared transactional retirement fence. Local core/API/JavaScript and synthetic authenticated browser checks cover the bounded path. | Add a transactional per-request retirement fence for shared storage, then qualify installed-app use, restored archive and independent audit custody. The archive retains sensitive request material under its own protected authority and has no in-product retention retirement. Administrator views now use protected forward batches and current native source/hold projections, including records beyond the former archive prefix; employees now have owner-only forward history, while the archive still has no retention retirement. |
| Advanced sandbox and single sign-on policy | Settings links to Sign-in and work protection: ordinary current-source controls, exact review, canonical settings approval, original-attempt recovery and eight typed Copilot controls. Mounted sign-in secrets remain controlled by their actual secret service and invalidate an older review when changed. A native browser journey covers a real memory limit, Copilot processor limit, protected sign-in secret, interrupted response, stale source, independent account and narrow screen. | This page reuses current isolation and authentication policy; it does not install a missing isolation system, transfer deployment-owned policy or qualify a real identity provider. The existing access guard refuses an unverified sign-in transition. Long lists and specialist certificate controls remain in All settings. |
| Workspace lifecycle | The administrator's Workspaces roster now exposes plan change, daily limit, pause, resume and removal with confirmation, status reload and native API callers. Removal retains stored records and says so before confirmation. Local route and DOM tests cover these paths. | Qualify the authenticated installed-app journey, including stale state, permission changes, failed writes and recovery. Coordinate full retirement separately from removing a workspace from the roster. |
| Database tenancy and key maintenance | Tenant RLS preflight/backfill remain operator commands. The Workspaces page now previews and runs local workspace-key re-protection using selected key files, with administrator permission, recent MFA, a background result and local route/DOM tests. Switching the server's master key and verifying every workspace afterward remain outside that page. | Complete the in-product maintenance sequence: protected key custody, RLS planning, readiness checks, server-key transition, verified return to service and recovery. Qualify the installed-app journey before calling key rotation complete. |
| Local AI runtime | Settings links to AI on this computer, with reviewed existing-program/model setup, actual native owned-process start and stop, loopback health, retained original-request readback and five equivalent typed Copilot controls. The existing model path and port have ordinary settings controls and effective source metadata. | Only supported programs already installed on this computer and existing models qualify. This does not download a model, install an engine or prove answer quality. An uncertain live process from an earlier dashboard instance blocks a duplicate start. Shared deployment operation, real inference quality and packaged desktop qualification remain separate. |
| Complete workspace retirement | Settings > Maintenance previews local client files and explains the offline boundary; the running dashboard refuses an erase before deleting anything. The offline kernel erase retains a signed receipt. Connected services, other storage roots, and exported copies are outside its scope. | Add a coordinated in-product offline retirement journey that stops writers, verifies all storage scopes, presents the final receipt, and prevents the retired client from reopening. Do not report a partial local delete as complete retirement. |
| Backup and installation boundaries | Local backup/recovery and packaged Windows update/repair/rollback have substantial product controls. Shared/remote storage recovery and other deployment types have explicit blockers or separate procedures. | Provide the remaining coordinated administration paths; do not claim all deployment types are qualified from Windows/local acceptance. |
| Stopped tasks on shared PostgreSQL | Startup and recurring recovery inspect stale active goals for the configured tenant, or rotate through a bounded batch of registered, non-retired tenants when the dashboard is unbound. Each write uses an exact tenant predicate and excludes legacy NULL-tenant rows. Zero or tiny reclaim windows and an unavailable registry cause no shared writes; a failed shared backend stops further attempts in that batch. Local tenant-scope, SQL-predicate and fairness tests cover these paths. | Pending goals remain queued because age alone cannot distinguish a lost dispatcher from legitimate Redis/gRPC or capacity waiting. Add durable, tenant-scoped dispatch ownership proof before reclaiming pending work; qualify recovery against a live shared PostgreSQL deployment and installed application. |
| Diagnostic parity | Settings > Maintenance already exposes Check services. Its bounded recent connection pauses and request counts now use readable service names, with private no-cache readback. The existing AI-service Copilot check reads the same metadata under installation-administrator authority. Neither interface claims all services are healthy, starts a probe or automatically retries work. A pause can describe an older connection configuration. | Complete current-connection attribution, readable traces and the remaining health/repair operations. Local recorded status is not an external availability check, and support export alone does not replace operational diagnostics. |
| Aggregate boards for department-scoped employees | Overview, Spend, Workforce, Savings and Oversight now use native projections of the caller's own tasks within current department permissions and general work. Pages, report APIs, links and five typed Copilot reads share those checks. Workforce counts actual owned specialist work. Savings joins actual authorized pilot cases and labels operator-entered figures and missing inputs; it does not manufacture savings from time estimates. Oversight uses current approval permission and excludes company-wide stops and other owners. Account, workspace, grants and department changes invalidate displayed reports. Native page, API-key, Copilot and 320px journeys passed. | These are permitted personal operating pictures, not company or team totals. Runtime prices are not reconciled provider invoices, and reported pilot figures are not independently verified savings. Shared deployment and installed customer acceptance remain separate. |
| Interrupted Copilot actions | Selected task stop/resume, clarification, result sign-off, approval, schedule repair, local AI control and installation protection actions now offer Check original outcome after a lost reply. The dedicated endpoint only calls explicitly registered read-only native receipt readers; it never invokes the action or a general recovery writer. The sealed original review, current native permission and account/workspace context are rechecked. Missing attribution stays unconfirmed, pending work stays queued and terminal failure remains visible. The native lost-sign-off-response journey verified no repeated learning or handoff. | The current panel retains its review for ten minutes; closing or reloading uses the action's native retained-history controls. Actions without a pure original-attempt receipt stay outside this read-only endpoint. A matching current state does not prove the original action succeeded. |
| Global Copilot | The shared page guide still has a zero tool-call budget. Its visible questions and replies survive navigation in the same browser tab for one hour, with a Clear control. Earlier ordinary guide exchanges can now be carried in a short-lived encrypted token bound to the current account, workspace and coarse permissions; forged browser assistant text is ignored, and live private-record replies are excluded from model history. The panel shows a page-specific plain-language tip rather than repeated implementation caveats. A separate default-off, closed operation catalog covers selected current-state and reviewed actions across registered families. On a saved learned workflow, Copilot can open the same labelled run form as the designer after checking the current published release; the form and native run endpoint recheck that release and access before accepting work. Copilot can check live provider readiness and the status of an owner-selected improvement pilot without claiming measured lift, and can finish an exact signed form decision with owner review and receipt readback. These bounded operations do not constitute whole-platform coverage or installed-app acceptance. | Extend the typed catalog and current state across every supported job, including work in another panel or background job. Preserve each native service's authority, result and recovery contract. A guide token resolves conversational references but cannot authorize an operation, prove a live result, or replace server-owned customer records. |
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
| Create a bulk consent export or change | Operation-specific controls provide paged people search, selection retained across pages, bounded list import, and older-receipt paging for every selected person. Review job refreshes the selected profiles and shows each exact change; removing a person clears their selected receipts and invalidates review. Start reviewed job separately admits the reviewed changes. Interrupted admission reads the original account/workspace-bound submission without running it again. Run now advances all bounded parts while the page stays open; Pause after this part stops between parts. An uncertain response stops further dispatch and reads saved progress. New jobs isolate effect keys by job; same-person receipt changes advance revisions only from verified successful predecessor results, and outside edits still refuse. Activity and job lists have Older/Latest controls with bounded responses. A native 390px journey pages 205 saved jobs, cancels an older job and confirms its stored state. Native phone-browser journeys also cover person 604, more than 50 selected people, old receipts and multiple changes to one person. Selecting a saved job now gives Copilot the same native read, one-part execution, future-work cancellation and verified download controls. The one-part review binds the current revision atomically before work starts; a changed or uncertain result reads progress without another part. Native phone and desktop journeys cover selection, cancellation, lost replies, denied cross-account replay and actual download. Native creation can also host the same ordinary six-operation form inside Copilot without relaying private people or receipt selections through a model. Admission reserves the current native preference store while checking exact reviewed records. Allowance changes retain an actor-bound original grant; both the direct form and typed control read a lost response without granting twice. The native phone journey covers interrupted creation, one accepted job, retained people/receipt selections across pages, actual two-receipt changes, stale profile refusal, lost allowance responses, exact replay and account-change clearing. | Qualify installed customer use and all six operation families; this new journey exercised export and expiration changes. A queued job is admission, not completion. Pausing or cancelling preserves completed changes; closing the page stops automatic advancement rather than creating an unowned worker. |
| Prepare an incident report | `_governance_operations.html` mounts labelled structured report fields, selects an incident and carries its current revision in a hidden field. The draft goes through independent approval; recording a filing requires the authority's reference and a receipt-file fingerprint, and the page says this is not proof of external receipt. | Qualify the authenticated installed-app journey and recovery after stale state or interrupted draft, approval and filing-record writes. Verify external submission with the authority separately. |
| Rotate a registered device's key | Mobile app consent now registers an app through its named app key, lists genuinely enrolled devices and explains automatic native-device key renewal. The ordinary page has no manual key-hex, attestation or epoch editor. | Genuine device enrollment and renewal still need the installed app, its platform attestation provider and signed device evidence. Administrator page rendering does not qualify that external device journey. |
| Operate generic compliance consoles | Turn scope into tasks now includes a native mapping builder: choose a named final scope, its current controls, responsible role, work status, evidence action and cadence. The service creates an exact immutable mapping; another authorized person approves it before a plan can use it. Three typed Copilot controls share creation, current read and independent approval. The actual native browser journey builds two mappings, recovers a lost save without another mapping, refuses self-approval and another account, publishes independently and uses the selected mapping to create a 12-control/12-evidence plan. The ordinary selector and 320px layout passed. | This covers one formerly JSON-only customer workflow and 1–2,000 final-scope controls. Initial uniform choices remain supported; the October 5 follow-through adds individual choices and immutable revision editing as recorded below. It does not supply licensed control text or external task-system execution. Other generic-console actions still need named record selection and business controls; unknown-property and technical-editor fallbacks remain elsewhere. |

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

## October 5 delivered controls and recovery

- Compliance questionnaire creation now uses named topics, ordinary questions,
  business choices, conditional questions and named control groups. The native
  page and Copilot share exact source-version review and original-save recovery;
  independent publication remains a separate decision. The 320px native journey
  exercised saving, a lost reply, a conflicting newer publication and an actual
  assessment using the published questions. Imported unsupported structures
  remain identified; licensed control text is not supplied by this builder.
- Inspected plugin inputs now have ordinary nested, list, number and yes/no
  controls with a complete expected-answer review. The actual restricted Python
  worker checks the exact reviewed schema before dispatch. The native/Copilot
  phone journey recovered the original single result after a lost response;
  changed schemas and accounts were refused. Node/gRPC and arbitrary third-party
  plugins still require their own installed environment qualification.
- Settings offers a named, explicitly loaded list of already installed local
  Linux practice environments. Review and save inspect the exact selected
  immutable identity again. Unrelated settings never read Docker; loading and
  choosing never start containers. Named customer history preserves raw exact
  identities and configuration revisions in the original accepted audit record.
- Outcome recording distinguishes reported business results, human ratings and
  local execution verdicts. A later local rating cannot replace a reported
  business result. Independent workers reread new complete records; failed or
  uncertain persistence cannot report a successful save. Recording an unknown
  outcome never becomes an instruction to repeat the original work.
- Initial and live Chat withhold an answer overtaken by an edit or access change.
  Assurance attaches only to the matching completed native run. Events equal to
  its start/end timestamps are ambiguous and cannot support a “Checked” claim.
  Copilot's read-only catalog remains available with mutation controls disabled.
- Learning and Learned list the signed-in person's retained background learning
  tasks after navigation or reload. Native controls and Copilot read recorded
  observations and review a cooperative stop against the exact current task.
  Interrupted stop replies recover the original request without another stop.
  The phone journey distinguishes stopping from stopped, refuses another account
  and stale access, and clears old-account results. The October 5 follow-through
  retains these bounded 40 observations and original stop acknowledgements in
  protected storage across restarts, as recorded below. Missing records never
  prove failure or authorize an automatic retry. Stopping does not undo completed
  work.

These are bounded native product journeys and installed-package checks, not
customer-outcome evidence or completion of the 28 research hypotheses.

## October 5 individual task mappings and immutable revisions

The native mapping form now supports a different initial status, responsible
role and evidence work for each selected control. A control can have several
evidence jobs with separately chosen work, responsibility and frequency. Named
saved mappings open their exact latest immutable version for revision. Unchanged
entries and saved evidence identities remain exact; genuinely new evidence work
receives a new identity. Every save creates a separate version rather than
changing earlier mappings or plans. The editor pages eight controls at a time
while the complete reviewed save includes every control.

Copilot's protected create and revision handoffs use the same native form and
authority. The existing typed uniform creation operation keeps its contract.
The actual 320px native journey saved mixed choices, recovered interrupted
replies from the original operation without another save, refused an identical
competing version without attributing it to the original request, preserved the
editable draft, and explicitly reloaded before saving a new revision. A separate
reviewer published that version; the ordinary Task plans page consumed its
exact differing control states, owners, evidence work and frequencies. A further
real save preserved an earlier evidence job while creating a distinct new job.
Earlier immutable versions and the original plan remained unchanged.

Revision requires a selected final scope whose complete framework/control set
matches the saved mapping. An unmatched imported mapping is refused explicitly;
no saved entries are dropped to fit the editor. Publication remains a separate
independent decision. This does not supply licensed control text, external task
execution or customer acceptance of those integrations.

## October 5 self-coaching questions and retained learning work

Learning now provides an ordinary model, question and expected-answer form.
An installation with no question location can first use Set up learning questions
on the same page or through Copilot. The reviewed action changes only the existing
question-location setting through the native Settings writer, chooses Lightwork's
managed location and keeps existing questions. It changes no learning switch,
model, schedule or storage option, and makes no AI call. Deployment or workspace
ownership, stale configuration and current account authority are checked again
before writing. A retained original setup claim prevents a second setting write
after an interrupted reply. Current readiness is shown independently; equal
settings cannot establish the original request's outcome.
Saved questions can be browsed in named batches, selected, corrected or retired
through full before/after reviews. Its direct controls and three typed Copilot
actions review the actual configured model and current shared question collection
before adding or changing one exact question.
The current collection, model, account and storage authority are rechecked under
the native writer lock. Private encrypted recovery copies preserve unsaved text
for explicit restore; restoring does not add a question or run a check. Questions
are shared with administrators of this learning storage, and future checks can
send them to the configured AI providers. The page states both boundaries.

The authenticated 320px journeys performed native and Copilot saves, corrections
and retirements, recovered lost replies without duplicate effects, refused
competing changes or withdrawn sources, and restored a private draft after reload.
Unrelated questions, extra operator fields and source provenance were preserved;
unsupported imported rows were identified and retained. The native self-coaching
reader and checker consumed the corrected answers and omitted retired or withdrawn
questions, with deliberately offline answer and judge functions. A full-length
Chinese question and expected answer passed the actual save. This proves the
saved-question connection, not live model
quality or an improvement in business results. Legacy imports keep unrelated
fields while stripping imported authoring markers; a matching question from
another operation cannot confirm the original save. Corrections and retirement
commit the original private effect marker atomically with the collection change.
An interrupted final receipt can read that marker even after the selected row
has been removed; equal replacement text or absence alone cannot prove success.
Background task refreshes also preserve another panel's selected question and
Copilot's visible original result instead of replacing the shared context.

The supported single-host SQLite learning store also passed native add, correction,
retirement, process restart and original-effect recovery. A concrete data-loss
defect was repaired: an unreadable database or malformed retained row previously
looked like an empty or partial collection to a writer. Strict writer reads now
refuse unreadable rows, duplicate metadata and unavailable storage before any
collection is replaced. Staging and resolution preflight their live, pending and
rejected histories. Native SQL interruption rolled back both the collection and
its effect ledger. The existing checker retains its tolerant read behavior.
This qualifies local SQLite, not PostgreSQL, multiple hosts or live model quality.

Learning's native worker now saves admission before dispatch and retains at most
40 encrypted observations, bounded result material and original stop receipts.
Unavailable or corrupt retention refuses admission instead of starting untracked
work. Saved completion, failure and cooperative stop remain distinguishable from
an older observation whose worker is no longer available. Restart recovery never
replays that work. An original stop intent, an acknowledged cancellation request
and actual worker completion are separate facts; failed acknowledgement storage
cannot become a confirmed receipt on an identical retry.

Native UI and Copilot journeys exercised actual learning cycles, lost replies,
stop acknowledgement storage failure, process termination and restart, denied
accounts and workspaces, and explicitly admitted new work after the old process
released its ownership. The narrow layout had no overflow. Reading 40 large
retained observations used two native storage reads and returned a 26,947-byte
public response without the private result text. Oversized or unreadable results
show that their result was not retained. Same-host operating-system ownership
locks preserve the existing three-worker limit and prevent overlapping processes
from repeating a learning kind. This qualifies one host with shared local data;
it does not qualify multiple hosts or automatically resume interrupted work.

## October 5 notice update proposals

Privacy notices now provide an ordinary update form through the native page and
Copilot. The person selects the named latest notice, its real current consent
publication and retained regulatory documents from independently approved
research sources. Three file pickers calculate fingerprints privately; the
review shows the original file names and sizes, reasons and named references.
The native proposal retains fingerprints and references, not the files' contents.
The page explicitly separates those records from examination of the files, a
legal applicability decision and publication.

Saving reserves the actual research and consent stores before the notice writer,
then rechecks current authority, the latest base and source eligibility inside
that writer. Waiting for a lock cannot bypass retention or review expiry. The
existing direct proposal route uses the same ordered reservation. Another
administrator accepts or rejects the proposal. An accepted proposal can then
be selected in the existing notice-version form; original files must match and
the current approved references, base and account are rechecked before creating
a draft version. Creating that version does not publish it.

The authenticated default-store phone journey opened Copilot's protected form,
kept file selections through a same-account selection change, saved a proposal,
and recovered the exact original lost response after reload. Self-acceptance
was refused; another administrator accepted it. Matching original files created
the separate draft version, and an interrupted version reply read its exact
native receipt without creating another version. A changed document refused
the reviewed save while retaining selected files. Separate original-operation
attribution and final read permissions prevent matching unrelated records or
revoked access from producing a confirmed result. External publication and
legal review remain their own required decisions.

## October 5 questionnaire work across panels

A same-account Copilot selection now returns the exact live questionnaire form
to its native page, with entered questions, choices, conditions and the existing
private recovery copy intact. The form checks current source and account before
keeping an unsubmitted review. A competing publication invalidates that review
while preserving editable work for an explicit comparison and fresh review.
Interrupted original saves keep their own sealed request and recover the original
version without another save. Account changes or loss of administrator access
clear private page details. The native 320px journey covered those transitions,
reload and explicit draft restoration, actual lost replies, independent
publication and real administrator demotion; it made no provider calls.

## October 5 private mapping drafts

Individual task-mapping choices now have encrypted private recovery copies,
ordinary comparison and explicit restore. Restoration reads the named current
control pages and refuses changed scopes or saved mapping revisions before
replacing any entered work. Choosing a different mapping or reloading current
choices asks before replacing an edited draft. A failed reload keeps the prior
individual choices. A same-account Copilot selection keeps editable work;
genuine account or workspace changes clear private page details.

The original business-save reminder is separate from the private draft copy.
It remains bound to its original account/workspace and can be reopened after
switching away and back. Confirming a business save cannot falsely discard an
older unresolved recovery save, or mark newer text as protected while a delayed
clear is still completing. Those newer changes are saved as a new recovery copy.

Native 320px journeys covered multiple evidence jobs, names and defaults,
reload/restore, independent-tab conflicts, scope and mapping changes, failed
reload, interrupted business replies, account switching and denied access.
The real encrypted draft store also exercised delayed clearing with new edits
both during initial loading and after a write, and an interrupted earlier recovery
save. No provider calls were made. Recovery
refuses a complete copy above 1 MiB, 20,000 objects or 25 nesting levels instead
of dropping choices. Comparison explicitly limits its display to 20 changed
controls and 10 evidence jobs per side; the complete copy remains retained.

## October 5 exact provider-spending replays

An unchanged original spending receipt now returns after full database-integrity
verification without rewriting rows, committing or resealing the database. The
same native writer reservation and authority lock remain required. New attempts,
uncertain charges, changed receipts and fresh holds still persist normally; zero
cost alone cannot trigger this shortcut. The receipt comparison captures its
original nested values before the operation runs.

A disposable native 40,000-receipt history verified fresh reserve/settlement,
exact replay, cap and duplicate-attempt refusals, conflicting charges, uncertain
admission and tampering with restored file metadata. Exact unchanged settlement
replays used one full digest and no SQL writes, commits or seal writes, keeping
the original database and seal bytes. This removes redundant replay work. Fresh
active AI calls still verify the complete retained history; the addition is not
a claim that arbitrary history size has constant cost or that a live financial
integration has been qualified.

## October 5 reviewing suggested learning questions

The default review-first search for new learning questions now leads to a named
Suggestions form on Learning. An administrator selects a model and the complete
question and expected answer, then reviews Use this question or Reject this
suggestion. The selected-question Copilot action uses the same native writer.
Accepting makes the question available to the existing self-coaching checker;
rejecting remembers the question so later searches avoid suggesting it again.
The review states when the bounded rejection history will release an older entry.
No AI runs as part of either decision.

Both decisions recheck the complete saved, suggested and rejected collections,
the selected model, storage source, learning permission and current account.
Unknown imported information is retained instead of being silently discarded.
SQLite commits those three collections and the exact original effect receipt
together. File storage reports a partially interrupted decision as unconfirmed;
it never automatically repeats that decision. Matching text or a missing
suggestion alone cannot confirm an original operation. Withdrawn learning
permission also withholds private question text from original receipts.

The native 320px and Copilot journeys saved and rejected real retained suggestions,
fed the accepted answer to the offline checker, excluded the rejected question
from subsequent searches, and recovered interrupted original replies after a
real process restart. Stale collections, a changed storage source, unavailable
storage and foreign account/workspace requests were refused. This qualifies the
existing single-host file and SQLite paths, not live model improvement.

## October 5 managed unseen-question record setup

Learning now offers Choose the managed location, followed by a separate reviewed
Initialize record action. Neither step needs a file path or terminal command.
Location setup changes only the existing setting through the canonical Settings
writer. Initialization uses the existing exclusive native record writer; it
never replaces an existing record or runs a check, changes a learning switch,
selects a model, schedules work or spends money. Copilot offers those same two
reviews and a read-only current record check. The former direct initialization
endpoint now guides the administrator to this reviewed journey.

An installation-wide attempted-once anchor follows the canonical physical
record location. Deleting the record, choosing an equivalent path or moving the
dashboard settings file cannot silently reset that anchor. A retained original
completion, an interrupted unconfirmed request and a separately verified current
record remain distinct. The ordinary checker reads the same created record.
Both native and Copilot reviews recheck current authority and source under the
writer reservation; an expired review cannot proceed after waiting for a lock.
Verification follows the actual runtime path rules rather than treating a
resolved alias as proof that the checker can use the configured path.

Actual native and Copilot setup, lost replies, independent current readback,
original recovery without a second initialization, deployment-owned settings,
stale reviews and missing previously initialized records passed locally. The
record stays on this server; this setup does not create independent audit custody
or qualify multi-host storage.

## October 5 workspace invitations and member controls

Accepting an invitation now shows its original outcome and current membership.
Opening the invited workspace is an explicit separate action through the native
workspace selection and audit service. Reopening a completed link does not grant
the role again. A signed-in person must match the invited verified email; an
opaque sign-in subject is no longer presented as the person's email. Local
passphrase invitations retain their own ordinary acceptance journey.

A completed role grant receives a retained confirmation only after the native
write returns. A burned invitation with interrupted confirmation stays
unconfirmed, even when today's membership happens to match. Its page explains
current access and the administrator's replacement path. Historical invitations
remain readable after a custom role is deleted; redeeming a pending invitation
still requires that the role and issuer's authority are currently valid.

The People form now binds invitations and member changes to its actual account
and workspace. Switching accounts within the same workspace cannot submit an
earlier private form as the new person. Access loss clears private form details;
temporary connection failure keeps entered work. Copilot's protected People
handoff verifies current context, preserves current work and focuses the ordinary
destination. Closing Copilot manually retains its ordinary focus behavior.

Native 320px journeys covered signed matching-email acceptance, local passphrase
acceptance, lost replies, reopening original outcomes, explicit workspace
selection, changed and removed memberships, expired/revoked links, deleted custom
roles, stale accounts and protected handoff focus. No external sign-in or email
delivery service was contacted.

## October 5 offline and configured token estimates

The default server estimate now includes its tokenizer and reviewed vocabulary
in the normal core installation. It counts Unicode and literal reserved-looking
text without downloading a vocabulary or reading a populated cache. The native
authenticated measurement job completed with outbound connections and download
helpers blocked and an initially empty cache that remained empty. An isolated
installed wheel also loaded the Windows native tokenizer and its verified
vocabulary, license and provenance. Frozen-release collection and the offline
release-runtime check were qualified; a complete frozen installer was not built.

Optional provider counting now uses the native configured Anthropic connection,
including the credential and residency headers saved through Settings, rather
than constructing a client from ambient environment variables. It normalizes
the configured model and offers this choice only for the qualified direct
connection. The interface calls this a provider estimate and explicitly states
that the selected messages are sent to that service. Each count, including a
cached result, rechecks current access, learning permission, model and connection.
A changed credential or workspace refuses the remaining measurement. The
bounded per-measurement cache removes repeated identical count requests.

Actual native configuration, SDK serialization and authenticated measurement
jobs were exercised with only the HTTP transport replaced by a local response.
Wrong providers, gateways, credential rotation and withdrawn permission were
refused without further transport calls. This is not a live-provider quality,
pricing or customer acceptance claim.

## October 5 ordinary supplied-answer checks

The task-set answer checker now offers ordinary questions and choices for the
three packaged public example sets: personal-data request routing, processor
privacy commitments, and privacy risks and safeguards. Supporting passages come
from each question's information. A complete review shows the selected questions,
entered answers, supporting passages and optional reported model before starting
the existing local scorer. JSON imports remain an optional advanced disclosure.
This does not contact AI, verify the reported answer producer, qualify a model,
change routing, or treat published examples as unseen evaluation evidence.

Copilot can open the protected answer form and read a selected original result.
The handoff checks current account, question source and model choices before
replacing or focusing work. A changed question source or superseded Copilot
selection keeps entered answers. Scoring uses the existing retained worker and
a distinct original request; lost replies cannot start another copy. Original
admission, recorded completion, unavailable workers and a no-longer-retained
detailed result remain separate outcomes. Another local check requires a fresh
review and explicit confirmation.

The actual 320px native journey checked all three packaged sets with the real
local scorer, complete answer review, an incomplete private draft saved and
restored after reload, lost replies, exact original readback and duplicate
submission without another worker. Foreign requests and real authority loss
were refused; authority loss cleared private work, while temporary unavailability
kept it. Stale sources, unavailable model labels and delayed Copilot selections
could not replace the draft or move focus. An expired review waiting on the
native authority fence created no claim, worker or evaluation. Native phone
layout and protected Copilot handoff had no JavaScript errors.

## October 5 learned instruction decisions and recovery

Applying a suggested instruction and undoing a prior change now retain exact
original decision records in the native proposal store. Ordinary Learning
controls and typed Copilot decisions use the same full review, current source
permissions and independent approval checks. Undo also verifies every source
whose text would be restored. Source-filtered instruction lists and reviews do
not reveal private text merely because it remains in a shared history.

An original confirmation records the native completed decision, rather than
inferring it from matching current text. An interruption between the instruction
write and confirmation remains unconfirmed and is never replayed. Confirmed
history can be read when today's instruction overlay is unavailable. Original
readback remains available through the ordinary page after a short-lived Copilot
execution token expires. Expired full reviews are refused after a writer wait;
refused admissions cannot create or prune instruction restore points.

An unresolved change holds subsequent shared instruction edits. The administrator
can review and accept the current instructions through a separate reconciliation
decision, which records that human acceptance without repeating the old effect
or relabeling an unknown original as confirmed. A currently authorized successor
can resolve that shared hold without reading a former administrator's private
original receipt. Both paths require a complete current-source review and record
their own actor and exact current state. Reconciliation does not rewrite the
instruction text. This remains a single-host recovery protocol, not independent
audit custody.

The actual 320px native journey applied four changes, performed two whole
restores and retained six exact original records. Two deliberately interrupted
effect-to-confirmation gaps remained unconfirmed after separate current-state
acceptance, including a departed administrator's hold accepted by another current
administrator after a full review and a lost acceptance reply. Expired queued
reviews, withdrawn sources, stale versions and foreign context were refused.
Fresh-process original and successor reads, including same-request acceptance
readback, left the native ledger bytes unchanged. Stacked instruction cards kept
Undo visible on the phone without document overflow or JavaScript errors. No AI
service was contacted in either journey.

## October 5 delayed Copilot handoff buttons

The shared Copilot handoffs to saved learning points, compliance mappings,
privacy-notice updates, installed practice environments, workspace people and
plugin practice now retain the clicked control before awaiting the destination.
A delayed refusal re-enables the correct control without reading an expired
browser event. Detached controls and superseded selections cannot change the
current panel. A bounded real Chromium event check covered all six callbacks,
including delayed refusals, detached controls and obsolete selections, with no
JavaScript errors. Only metadata and deferred destination controllers were
substituted in that presentation check; it does not newly qualify the six native
services or claim a customer effect.

## October 5 reviewed lessons, local ranking and suggested skills

The meaning check now reviews the current shared lessons, configured models and
spending controls before starting the native retained worker. It records the
original request and worker admission before contacting an AI service. Missing
answers, provider failures and lexical fallback remain distinct from an AI
finding. Reading the original check does not start another check, and withdrawn
or inaccessible source text is withheld from history. Native SDK requests through
a local HTTP transport covered nine synthetic calls, delayed identity writes,
interrupted admissions, lost replies and fresh-process recovery. This checks the
operating contract; it does not establish a model's conflict-detection accuracy.

Learning now offers an ordinary reviewed form for training the existing local
ranking model from eligible shared task outlines. Its choices, exact sources and
original publication belong to one retained request. Training, scoring and
download recheck current source permission. Withdrawal removes source details
from an old result and prevents later use, while preserving truthful publication
history. Actual local training exercised three runs and two published models,
including lost replies and an interrupted publication. No paid AI service was
used and no improvement in customer outcomes is claimed.

Adding and removing suggested skills now require a full current review through
Skills or Copilot. The final native file writer checks the source, authority and
review deadline. Original confirmation is retained separately from today's file
state. A write whose confirmation was interrupted remains unconfirmed; matching
file contents or a later removal cannot establish that original outcome.
The ordinary page retains original changes and recovers a lost reply without
repeating the effect. Actual 320px journeys completed three additions and two
removals, refused stale sources and expired queued reviews, and kept current
skills separate from five original records without document overflow or
JavaScript errors.

## October 5 reviewed audit maintenance and original custody

Activity log now reviews every selected closed day, exact source bytes,
destination and recorded retention before encryption or copying. Ordinary
controls and Copilot call the same native writers. Native signed copy intent
precedes each upload, including command-line uploads. An uncertain upload holds
automatic repetition. A current administrator can separately review and
authorize a new copy while explicitly accepting that an earlier copy may
already exist; the earlier original remains uncertain.

Original result reads recover exact signed request completions rather than
inferring completion from a matching object. A departing administrator's
private review remains private. A successor can resolve the shared copy hold
without inheriting the earlier person's authority or rewriting that outcome.
Narrow screens show each day's complete labeled review in cards. Recorded
source-chain consistency, service-reported retention, local read-only files
and independent custody remain distinct.

Actual local page and Copilot journeys encrypted three synthetic closed days
and produced six native local objects, including lost completion replies and
current-administrator succession. Three simulated remote admissions remained
uncertain with no blind repetition. A separate native 320px review displayed
all eight fields for two closed days without horizontal scrolling, script
errors, encryption or copying. No real remote service was contacted or its
retention enforcement independently qualified.

## October 5 reviewed saved-skill removal and captured examples

Manually saved skills now have full instruction review and exact-version
removal through Skills and Copilot. Removal uses the native file lock and
checks current authority, the reviewed bytes and deadline immediately before
deletion. Exact original receipts remain separate from today's file state.
An interrupted deletion-to-receipt gap stays uncertain and is never replayed.
Actual native 320px journeys covered four deletions, a competing replacement,
lost replies, foreign context and an uncertain original; no AI call occurred.

Captured learning examples now expose complete retained inputs, actions,
results, teaching questions, choices and guards before Accept, Reject or
permission to learn. Ordinary controls and typed Copilot share this exact
review; a missing full-review renderer prevents confirmation. Permission
expiry and full current workflow definition are checked inside the native
write. A changed saved workflow cannot receive an old reviewed decision or
grant, including through the older API. Historical originals remain readable
after a later source change or learning is switched off, subject to current
read authority.

The actual native phone journey reviewed 259 retained values, completed the
three decisions, recovered lost responses without repeating them, and refused
changed sources, foreign context and withdrawn access. Native queued-run
repair and approval refresh now acquire their locks in the same order as
execution. A deterministic native concurrent repair preserved the exact owner,
input and run while restoring its missing original queue job once.

## October 5 current authority in retained work

Self-coaching now uses a complete current review of shareable recent mistakes,
questions, accepted instructions, configured models and spending settings
before its retained native worker starts. Current sources and authority are
checked before every physical AI request and every native learning write.
Successful writes advance only their own expected instruction generation;
another person's edits cannot silently become part of the reviewed pass.
The worker is recorded before AI dispatch, requires a safety copy and does not
repeat an uncertain admission. A confirmed cooperative stop keeps earlier
completed learning visible and reports the unfinished work as stopped.

Actual file-backed and single-host SQLite-world passes each adopted one
synthetic lesson for each of two models. The real SDK used a local synthetic
HTTP response only. Changed instructions, settings, authority and source
permission between requests stopped further calls and writes. Native local
PostgreSQL advisory locking also exercised nested same-thread writes,
cross-thread exclusion and exception release against a disposable server.
These checks establish the bounded operating contract, not live AI accuracy,
customer lift or complete shared-host deployment acceptance.

Retained learning workers discard the initial HTTP configuration memo while
keeping their admitted owner and workspace. Principal-less local and static
token access is checked against current authentication settings rather than
a cached admission flag. Explicit API keys are reverified at protected
permission gates without changing the admitted owner or workspace. Key
revocation, expiry, service-account lifecycle and scope reductions therefore
apply before delayed protected effects. Key mutation and effect admission now
share the native authority fence in a consistent lock order.

Actual retained workers refused authentication activation and static-token
rotation. Native reviewed audit writes and retained workers also refused
revoked, expired, disabled and scope-reduced keys before changing source bytes.
Existing browser-owner admission and throttled key-use recording remained
intact. The local ranking trainer rechecks its deadline after native job
identity and source loading; a delayed admission created no model or trainer
run, and repeat submission read the original request.

## October 6 shared decision information

Shared decision reviews has ordinary authoring for up to eight facts, sixteen
cases, four choices per case and 128 explicit possible joint situations.
Oversized enumeration refuses the model rather than dropping situations. An
installation administrator enables it in Settings or the advanced installer;
only a literal explicit `true` enables the default-off feature. Copilot shares
the same seven native writes and two readers. Every change displays its complete
protected review, binds the current owner/workspace and retains its exact original.

The actual disposable native browser journey authored two correlated facts and
two cases. One reported capacity observation resolved both; automatic minimum
observed subsets contained only capacity. Each check additionally bound its
own required current source meaning, permission and expiry. Withdrawing the
first case's permission invalidated that case while retaining the second case's
exact check. Expired sources became unknown. Ordinary and Copilot lost replies
recovered original create/acquire/answer effects without another question charge.
An actual governed-record writer wait crossed the review deadline: the reserved
admission remained unknown, its exact retry made no second effect, and a fresh
separately reviewed creation remained available. Generated certificates passed;
a recomputed-hash forgery with omitted dependencies was refused semantically.
Foreign originals were refused, while own history remained available after
removal and feature disable. The 320px page fit without JavaScript errors.

These are reported premises within entered situations. The page neither
independently verifies business facts nor carries out a choice. Separate cases
do not establish joint resource feasibility; Promise reviews supplies that
separate bounded model. The independent exhaustive acquisition comparator
matched the selected synthetic observation, not a novelty or benefit claim.

## October 6 rechecking changed rules

Recheck affected rules is available from Learning and real-case comparisons.
Its ordinary form binds the complete original inputs, published/current root
and included sources, policy and explicit case reuse permission. Typed Copilot
opens the same protected form without replacing newer user choices. Only finite
linear local pure taught-rule/set-value workflows and owned included rules are
supported; connected actions, AI, retries, waits and cycles remain outside it.

The actual native phone journey retained a complete component check and
recovered a discarded accepted response from its original record. A changed
subtotal reran itself and the dependent invoice while two independent results
were reused. Changing an included child invalidated a stale review; a fresh
review reran three components and reused one. Both matched separately executed
complete native workflows. Withdrawing reuse permission blocked further reuse,
source changes withheld old result bodies, and foreign actor/permission loss
refused or cleared private controls. A fault after both calculations but before
the final receipt retained an unconfirmed original; reading and exactly retrying
that original never repeated either calculation. The 320px layout fit.

Both selective and complete calculations count toward displayed work and time;
saving receipts and browser overhead are excluded from the calculation figure.
No workflow publishes automatically and no future accuracy or latency saving is
established by agreement on an original case.

## October 6 safe restored learning authority

Workspace restore now classifies 24 recent audit, learning, score and release
record namespaces as authority-bearing. Restoring their bytes cannot silently
revive the earlier grants, training admissions, retained decisions or release
attempts. The existing native bootstrap recovery holds start pending current
reconciliation and external readback, while preserving original record bytes.
A disposable native restore exercised all 24 namespaces and case-insensitive
classification with no customer-file changes. The source inventory contract
also covered every literal local governed-record store. This narrows a concrete
restore gap; it is not acceptance of every installed recovery or shared-host path.

## October 6 privacy search uncertainty

Both Privacy's request checklist and an individual request's Where to search
now distinguish unreadable records, unmatched people and confirmed zero counts.
Failures in the data map, vendor register or connected document search remain
unconfirmed rather than becoming a switched-off module or an empty result.
Only nonempty platform areas contribute to the area count. The bounded 100-
location/vendor and eight-document views disclose their limits. Refreshing a
request clears the earlier checklist and retains an explicit unknown state on
failure. Actual page-handler checks exercised seven response conditions in both
surfaces, preserving genuine zero counts and refusing failed/malformed counts;
these focused rendering checks do not qualify an entire customer data search.

## October 6 missing distinctions and observed comparison cells

Find missing distinctions uses four to thirty-two currently accepted, permitted
examples and at most six available observations. It enumerates every smallest
observation addition that separates demonstrated action disagreements. Conflicts
that survive all observations remain unresolved. A supported addition passes
the existing signed two-action learner; only the choice observations narrow,
while full argument and source contracts remain bound. Ordinary controls and
typed Copilot prepare an unpublished package and retain its exact original.

The disposable native journey completed accepted signed capture, package
creation, ordinary Teach admission, Designer publication, labelled Run inputs,
two actual human approvals and the selected fictional connector action. The
Designer now recognizes these runtime-bound learned actions without inserting
ordinary fixed parameters that invalidate their reviewed argument binding.
Legacy unprojected artifacts remained byte-identical, and the projected package
passed signed round-trip and complete argument checks. Lost replies and exact
retries created no second package. An actual final-writer wait crossed expiry
and refused saving after the retained claim. Withdrawal preserved historical
saved custody while withholding source/package contents; stale preparation and
later published execution stopped before any additional action.

Observed cells consumes actual immutable native paired comparisons with current
source and exact arm receipts. It keeps complete input, expected answer and both
results separately for each immutable version pair. Conflicting recorded
expectations produced an ambiguous cell, and unavailable included sources denied
its read. Copilot opens that complete protected evidence without replacing newer
selections. The native 320px views fit without JavaScript errors.

These results cover the supported known examples and exact comparison cells.
They establish neither unobserved applicability regions nor future accuracy,
causal customer lift, automatic publication or research superiority.

## October 6 specialist editing and literal file display

Specialist validation, saving and resetting now run as synchronous native
FastAPI handlers in its worker pool. Cold action-registry construction,
complete pack validation and local file writes therefore leave the dashboard
event loop available. Existing permissions, validation, version conflicts,
pre-change activity recording and saved-result shapes are preserved. Six
focused existing HTTP checks passed, including stale save and reset refusal;
this establishes operating behavior, not a production latency benchmark.

Chat and shared task results now show code, JSON and CSV artifacts as escaped
literal preformatted content. Markdown interpretation cannot remove code
indentation, turn data into headings or reinterpret embedded markup in those
file types. Ordinary documents keep their formatted reading view.

## October 6 additional-work capability qualification

Promise reviews now has ordinary additional-offer authoring and local
qualification behind its explicit default-off setting. Four offers due
together, twenty-seven competing entered material/capacity/shared-setup
explanations, six possible additions and thirty-two observations are supported.
Every joint offer/addition check uses the native accepted-work feasibility
engine for every surviving explanation. Conditional quotes are excluded from
available stock. Complete current/proposed sources and business choices appear
before the native write; recorded identities remain available in collapsed
source details. Copilot uses the same eight writes and two readers, and opens
the complete native authoring form for the wider four-offer/six-addition scope
without resetting entered choices. A superseded handoff cannot adopt or focus
the form; exact current accepted-work binding is rechecked before opening.

Actual native qualification narrowed eight explanations to one with three
separately reviewed Yes/No local-rule executions. The joint inclusion-minimal
addition frontier changed while two accepted orders remained protected. Exact
native rules, their included sources, published revisions, model meaning and
permission are retained with each observation. Completed local trial reports
are separately attributed operator assertions. Changed model, source, parent
review or permission withholds old evidence from current calculations.

Native and Copilot interrupted replies recovered exact originals without
another test. An interruption after admission stayed unknown, and exact retry
did not execute again. Separate acknowledged release ends waiting without
confirming the unknown outcome or refunding the used test/effort allowance.
Final writer expiry refused the effect. Full-review overflow refused unseen
choices. Own ordinary history remained readable after disable and archive;
foreign ownership refused access. The native page fit 320px without JavaScript
errors. Installer emission requires literal `true`; no setting is inferred from
a truthy string or number. Workspace restore classifies the new qualification
namespace as authority-bearing, so copied bytes cannot silently revive grants.

The bounded computation is finite version-space learning and exhaustive
abduction. A same-information exhaustive acquisition reference matches the
chosen synthetic qualification. Hypothetical additions and entered premises
do not establish physical stock, capacity, customer willingness, profit,
fulfilment or research superiority. Qualification runs only saved local rules;
AI, connectors, purchases, reservations and business commitments are excluded.

## October 6 complete compliance draft saves

Compliance studio now shows every supported current/proposed draft choice
before Save through the existing native create/replace writer. A full review
supports 512 rows and 128 KiB of rendered material; larger material is refused
with the entered draft preserved. Literal condition/action strings and evidence
value locations keep their exact punctuation rather than being humanized into
indistinguishable choices. Ordinary labels describe fixed rule types.

An exact sealed request is retained before Save, and the existing actor-bound
native GRC receipt records one-time metadata admission. Actual browser journeys
discarded accepted create and replace replies, reloaded and recovered both
originals without another write. An interruption between admission and write
remained unknown; exact retry did not dispatch. An actual SQLite writer wait
crossed review expiry and refused the effect. Accepted expired originals stayed
readable, foreign originals were refused, and switching away from and back to a
workspace invalidated the earlier review. Current operating permission clears
private controls on loss. Native current-source checks run after the writer wait.

Copilot opens the protected native review or original reader with exact current
context and selection checks; a superseded handoff cannot replace or focus newer
choices. The complete native review fit 320px, and existing draft formats stayed
editable. Save creates an editable draft only. Independent review, approval,
publication and applying to connected destinations remain separate native
actions; these checks do not qualify customer evidence acquisition or destination
delivery.
