"""Closed, page-aware guidance for the shared dashboard Copilot.

The browser may propose a path, but it never supplies product instructions to
the model.  Only a context selected from this server-owned registry reaches the
system prompt.  The same registry also provides a deterministic guide when no
model provider is configured, so the helper remains useful on a fresh install.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

_READ_ONLY_BOUNDARY = (
    "Copilot guidance is read-only and never grants authority or executes an action."
)
_ROLE_AND_READINESS_BOUNDARY = (
    "Listed capabilities describe the workspace, not the caller's authority; every action "
    "still requires the caller's current role and verified server composition."
)
_MAX_BUILT_IN_REPLY_WORDS = 120
_MAX_BUILT_IN_REPLY_CHARS = 1_000


@dataclass(frozen=True, slots=True)
class CopilotPageContext:
    path: str
    title: str
    summary: str
    capabilities: tuple[str, ...]
    boundaries: tuple[str, ...]
    next_steps: tuple[str, ...]
    suggested_questions: tuple[str, ...]


def _ensure_read_only(context: CopilotPageContext) -> CopilotPageContext:
    """Make the no-action boundary explicit in every public page context."""

    missing = tuple(
        boundary
        for boundary in (_READ_ONLY_BOUNDARY, _ROLE_AND_READINESS_BOUNDARY)
        if boundary not in context.boundaries
    )
    if not missing:
        return context
    return CopilotPageContext(
        path=context.path,
        title=context.title,
        summary=context.summary,
        capabilities=context.capabilities,
        boundaries=(*missing, *context.boundaries),
        next_steps=context.next_steps,
        suggested_questions=context.suggested_questions,
    )


_GENERIC = CopilotPageContext(
    path="/",
    title="Lightwork",
    summary=(
        "Lightwork is a governed work and assurance platform. Use the sidebar to "
        "move from operating work to evidence, risk, privacy, security, and audit."
    ),
    capabilities=(
        "Create and supervise governed goals and workflows.",
        "Inspect signed operating evidence, controls, spend, and risk.",
    ),
    boundaries=(
        _READ_ONLY_BOUNDARY,
        "A green local status is not proof of live provider, customer, or regulator acceptance.",
    ),
    next_steps=(
        "Choose the workspace that matches the outcome you need.",
        "Read its readiness and evidence boundary before running an operation.",
    ),
    suggested_questions=(
        "What can I do on this page?",
        "What should I verify before I continue?",
    ),
)


_CONTEXTS = tuple(
    _ensure_read_only(context)
    for context in (
        CopilotPageContext(
            path="/assurance/cloud-agents",
            title="Cloud Agent Governance",
            summary=(
                "Inventory and govern registered agents in Bedrock, Azure AI Foundry, "
                "Vertex AI, and reviewed Databricks framework mappings."
            ),
            capabilities=(
                "Inspect bounded, server-configured cloud-agent inventory.",
                "Evaluate revision-pinned policy and prepare governed enforcement intents.",
                "Dispatch and reconcile an exact intent through enrolled provider authorities.",
            ),
            boundaries=(
                "The browser cannot choose credentials, provider URLs, native IDs, or tenants.",
                "Provider acceptance is not proof of applied state; reconciliation needs a fresh read.",
                "Inventory is bounded and may be incomplete.",
            ),
            next_steps=(
                "Load runtime status and confirm durable provider composition is ready.",
                "Review inventory before evaluating or preparing an enforcement intent.",
                "Reconcile ambiguous dispatches before accepting an outcome.",
            ),
            suggested_questions=(
                "Is this cloud-agent runtime ready?",
                "How do I safely enforce a policy?",
                "What does reconciliation prove?",
            ),
        ),
        CopilotPageContext(
            path="/ai-evidence-analysis",
            title="AI Evidence Analysis",
            summary=(
                "Extract bounded technical facts from an attested artifact and route the "
                "criteria-pinned result to a distinct human reviewer."
            ),
            capabilities=(
                "Analyze one bounded PDF, JPEG or PNG image, XLS or XLSX spreadsheet, or plain-text artifact against an enrolled criteria set.",
                "Inspect current, released, and historical analysis records.",
                "Review, revoke, verify integrity, and purge retention-due records.",
            ),
            boundaries=(
                "Results are evidence aids, not legal advice, certification, or compliance conclusions.",
                "Artifact and criteria authority are resolved independently by the server.",
                "Extracted text is not persisted, but process-memory zeroization is not guaranteed.",
            ),
            next_steps=(
                "Refresh status and confirm the tenant is enabled and configured for an operation attempt; status does not resolve artifact-specific authorities or prove them ready.",
                "Upload only an attested artifact with exact authority digests and revisions.",
                "Have a distinct authorized human review the pending result.",
            ),
            suggested_questions=(
                "What do I need before uploading evidence?",
                "What does a released analysis prove?",
                "When should I revoke an analysis?",
            ),
        ),
        CopilotPageContext(
            path="/ai-entity-tasks",
            title="AI Entity Tasks",
            summary=(
                "Manage review and remediation tasks tied to exact AI inventory entity revisions."
            ),
            capabilities=(
                "Filter and inspect revision-pinned tasks with complete lifecycle history.",
                "Create review or remediation work for an exact inventory entity revision.",
                "Update status, priority, assignee, collaborators, and due date under CAS.",
                "Inspect audit integrity and apply authorized retention maintenance.",
            ),
            boundaries=(
                "Tenant, actor, entity authority, entity digest, retention, and quotas are server-owned.",
                "Task creation independently re-resolves the current inventory authority.",
                "Local readiness does not prove an external workflow or customer has accepted the task outcome.",
            ),
            next_steps=(
                "Confirm the task and AI inventory compositions are ready.",
                "Copy the exact inventory entity ID and revision before creating a task.",
                "Inspect the returned authority digest and history after each update.",
            ),
            suggested_questions=(
                "What must I verify before creating a task?",
                "Why did my entity revision conflict?",
                "When can a task be purged?",
            ),
        ),
        CopilotPageContext(
            path="/governance-agents",
            title="Governance Agents",
            summary=(
                "Run closed, evidence-cited workflows for breach response, privacy risk, "
                "program analysis, knowledge governance, and third-party risk."
            ),
            capabilities=(
                "Publish independently approved agent policies.",
                "Analyze server-resolved evidence and inspect cited findings.",
                "Record human review and execute separately approved idempotent actions.",
            ),
            boundaries=(
                "Agents provide decision support and never become autonomous authority.",
                "Evidence, adapters, dispatchers, credentials, and tenant identity are server-owned.",
                "Execution is default-off and requires durable token custody plus independent approval.",
            ),
            next_steps=(
                "Load status and confirm the required callable composition is configured for an operation attempt; status does not open the store or verify live authorities.",
                "Publish an approved policy before starting an analysis run.",
                "Review cited evidence before granting any execution approval.",
            ),
            suggested_questions=(
                "Which governance agent should I use?",
                "What is required before execution?",
                "How do policy and evidence revisions stay pinned?",
            ),
        ),
        CopilotPageContext(
            path="/consent",
            title="Consent and Preferences",
            summary=(
                "Govern version-bound consent choices, pseudonymous profiles, tracker "
                "enforcement, and privacy-bounded consent analytics."
            ),
            capabilities=(
                "Inspect the active policy and notice, then collect an immutable preference receipt.",
                "Create and inspect pseudonymous profiles and run a fail-closed enforcement check.",
                "Review tracker governance and bounded aggregate consent analytics.",
                "Inspect current identity links and immutable unlink, relink, and account-transition evidence.",
            ),
            boundaries=(
                "Every choice is bound to an exact policy and notice version.",
                "Privacy signals can deny covered purposes; they never manufacture consent.",
                "An enforcement result does not prove every tracker or destination in an estate is known.",
                "The operator page cannot unlink, relink, choose a tenant, or submit identity subjects.",
                "Receipt rows are never moved or merged; the current link is an association projection.",
                "Production IdP/JWKS operation and live multi-device acceptance remain external evidence.",
            ),
            next_steps=(
                "Refresh the page and verify the consent authority is enabled for the active tenant.",
                "Confirm the exact policy, notice, purpose, and jurisdiction before recording a choice.",
                "Run the enforcement check before activating a tracker or destination.",
                "Use a fresh public pairing and exact revisions for unlink, relink, or account transition.",
            ),
            suggested_questions=(
                "What is required before I collect a preference?",
                "What does the enforcement check prove?",
                "What evidence is required to unlink or relink a profile?",
            ),
        ),
        CopilotPageContext(
            path="/consent/custom-indexes",
            title="Custom Consent Indexes",
            summary=(
                "Create governed indexes over approved volunteered profile-data elements "
                "and run exact, privacy-minimized subject searches."
            ),
            capabilities=(
                "Create an index definition pinned to one published data element.",
                "Build or rebuild a revision-pinned HMAC index without persisting raw values.",
                "Search one ready index and inspect bounded opaque profile matches.",
                "Revoke indexes and verify tenant-local audit and integrity records.",
            ),
            boundaries=(
                "The browser cannot choose tenant identity, actors, key material, key versions, retention, or quotas.",
                "Search is exact and intentionally excludes free-text, fuzzy, inferred, or cross-tenant discovery.",
                "A ready local index does not prove customer privacy review or production-scale acceptance.",
            ),
            next_steps=(
                "Load status and confirm profile-data authority, key custody, and source schema are ready.",
                "Create an index only for an approved published data element and review its source revision.",
                "Rebuild after a source revision changes before relying on search results.",
            ),
            suggested_questions=(
                "What values can be indexed safely?",
                "Why did this index become stale?",
                "What does an index search return?",
            ),
        ),
        CopilotPageContext(
            path="/consent/bulk-jobs",
            title="Consent Bulk Jobs",
            summary=(
                "Run tenant-scoped, credit-limited asynchronous receipt exports "
                "and governed consent-subject lifecycle actions."
            ),
            capabilities=(
                "Create, schedule, inspect, cancel, recover, and download bounded bulk exports.",
                "Run preference edits, subject deletion, identifier changes, receipt reactivation, and expiration clearing through their exact consent primitives.",
                "Inspect credits, progress, immutable history, retention, and integrity without exposing raw subject identifiers.",
            ),
            boundaries=(
                "Tenant, principal, actor, worker, stores, paths, and provider authority are server-derived and cannot be supplied by the browser.",
                "Each operation fails closed unless its real consent-store primitive is installed; a similarly shaped callback is not sufficient.",
                "Local completion does not prove production volume, customer acceptance, regulator delivery, or an external provider effect.",
            ),
            next_steps=(
                "Check readiness and the per-operation capability map before creating a job.",
                "Confirm the credit reservation, exact profile revisions, and receipt identifiers before scheduling mutations.",
                "Review terminal history and download integrity before relying on an export or action result.",
            ),
            suggested_questions=(
                "Which bulk operations are ready for this tenant?",
                "What should I verify before scheduling a destructive action?",
                "What does a successful export prove?",
            ),
        ),
        CopilotPageContext(
            path="/consent/publish-settings",
            title="Consent Publish Settings",
            summary=(
                "Govern immutable website runtime settings pinned to exact consent "
                "property and publication authority."
            ),
            capabilities=(
                "Version, activate, roll back, withdraw, and inspect SDK/runtime settings for an exact property publication.",
                "Control SDK version, per-geolocation reconsent, privacy-safe analytics categories, common trackers, cookie scope, SPA reload, CSP nonce requirements, and automatic refresh.",
                "Expose only the active integrity-checked SDK projection while retaining bounded history, recovery, and audit evidence.",
            ),
            boundaries=(
                "The browser cannot choose tenant, actor, authority digests, storage, retention, CSP nonce values, or signing material.",
                "A CSP nonce requirement is configuration metadata; Lightwork never stores or publishes a reusable nonce value.",
                "A locally active setting does not prove the SDK was installed on a customer property or accepted in live browsers.",
            ),
            next_steps=(
                "Verify readiness and load the exact property's current publishing authority.",
                "Create and independently review a new immutable version before activation.",
                "Validate the safe public projection and then separately test the deployed property and browser estate.",
            ),
            suggested_questions=(
                "Which publish settings are active for this property?",
                "What must be tested on the live website?",
                "How do rollback and withdrawal differ?",
            ),
        ),
        CopilotPageContext(
            path="/consent/global-opt-out",
            title="Global Opt-Out",
            summary=(
                "Govern a versioned mobile and email Global Opt-Out experience "
                "that submits a real profile-bound opt-out rights request."
            ),
            capabilities=(
                "Create, revise, activate, revoke, and audit an experience pinned to an exact public client, property, publication, policy, and channel authority.",
                "Serve a privacy-minimized public configuration and accept selector-free opt-out submissions through the existing public-consent trust boundary.",
                "Create a real privacy-rights opt-out case while retaining only bounded case and signed-result digests in this control plane.",
            ),
            boundaries=(
                "The browser cannot select tenant, profile, property, origin, service, publication, policy, storage, or retention authority.",
                "A successful local submission proves the governed workflow ran; it does not prove every device or service honored the request.",
                "Live mobile, email, connected-device, customer-estate, and legal acceptance require separate evidence.",
            ),
            next_steps=(
                "Confirm public consent and privacy-rights intake readiness for the active tenant.",
                "Bind the experience to the exact active public client and independently review its device and service coverage.",
                "Exercise revocation, retry, recovery, and representative live-device acceptance before relying on production coverage.",
            ),
            suggested_questions=(
                "What authority is this opt-out experience pinned to?",
                "What happens when the public client or publication changes?",
                "What still requires live-device validation?",
            ),
        ),
        CopilotPageContext(
            path="/consent/application-scans",
            title="Application Consent Scans",
            summary=(
                "Inspect bounded mobile and connected-app manifests and govern "
                "cited observations through independent review and retention."
            ),
            capabilities=(
                "Register exact Android, Fire OS, iOS, tvOS, Roku, Tizen, or webOS application authority.",
                "Analyze already-extracted manifests under per-file, aggregate, tree, and finding bounds.",
                "Review an exact application revision and result digest, verify integrity, and operate retention.",
            ),
            boundaries=(
                "Raw manifest bytes remain transient and SDK names remain unreviewed candidates rather than tracker or legal classifications.",
                "Binary APK AXML, executables, live devices, IDFA state, and runtime network behavior are not inspected.",
                "Local manifest analysis does not prove a native SDK, deployed-device behavior, completeness, scale, or customer acceptance.",
            ),
            next_steps=(
                "Confirm tenant composition and application authority are ready before uploading extracted manifests.",
                "Review cited completeness limits and have a different authorized person decide the exact result.",
                "Run separate device, runtime-traffic, accessibility, recovery, and scale acceptance before relying on production coverage.",
            ),
            suggested_questions=(
                "What does this manifest scan prove?",
                "Which application formats and evidence are supported?",
                "What still needs live-device testing?",
            ),
        ),
        CopilotPageContext(
            path="/consent/publishing",
            title="Consent Publishing",
            summary=(
                "Enroll exact web or mobile properties and promote immutable, structured consent "
                "resources through preview, publish, rollback, and withdrawal."
            ),
            capabilities=(
                "Enroll an exact property and append versioned consent resources.",
                "Preview a pinned bundle and publish it with compare-and-swap authority.",
                "Roll back as a new auditable publication or withdraw into an immutable tombstone.",
            ),
            boundaries=(
                "Resources accept closed structured data, not arbitrary HTML, script, URLs, or CSS.",
                "Publishing and rollback require exact current revisions; history is not rewritten.",
                "Withdrawal denies public resolution but does not delete prior audit records.",
            ),
            next_steps=(
                "Confirm publishing is enabled and the property enrollment is exact.",
                "Preview the pinned resource versions before promoting the bundle.",
                "Use rollback or withdrawal only after reviewing the immutable result it creates.",
            ),
            suggested_questions=(
                "What should I verify before publishing?",
                "How do rollback and withdrawal differ?",
            ),
        ),
        CopilotPageContext(
            path="/consent/experiments",
            title="Consent Experiments",
            summary=(
                "Govern immutable consent-presentation experiments, bounded assignments and outcomes, "
                "statistical review, and fail-safe rollback to control."
            ),
            capabilities=(
                "Create and independently approve a closed experiment definition.",
                "Assign variants without retaining raw identity and record bounded binary outcomes.",
                "Inspect, analyze, stop, kill, or roll an experiment back to control.",
            ),
            boundaries=(
                "The experiment creator and definition approver must be different authenticated people.",
                "Legal signals, required purposes, inaccessible rendering, or unequal choices fall back to control.",
                "A statistical winner is not legal, causal, accessibility, or customer-estate acceptance.",
            ),
            next_steps=(
                "Load current limits and confirm the feature, privacy review, and rollback authority are ready.",
                "Approve the exact immutable definition with a distinct authorized reviewer.",
                "Inspect guardrail and sample bounds before accepting or stopping a result.",
            ),
            suggested_questions=(
                "What makes an experiment eligible to run?",
                "What does a winning variant not prove?",
            ),
        ),
        CopilotPageContext(
            path="/consent/delivery",
            title="Consent Delivery",
            summary=(
                "Synchronize immutable preference receipts to exact HTTPS destinations through a "
                "signed, retryable queue with explicit dispatch and recovery."
            ),
            capabilities=(
                "Enroll or revoke an exact destination and rotate its signing-key identifier.",
                "Bridge authoritative preference changes into a durable signed queue.",
                "Dispatch bounded Salesforce, Marketo, Adobe Experience Platform, and Snowflake provider contracts.",
                "Inspect adapter readiness, minimized receipts, ambiguous outcomes, integrity, audit evidence, and dead letters.",
            ),
            boundaries=(
                "Sync queues only events reconstructed from immutable receipts.",
                "Provider origins, DNS authority, signing keys, OAuth credentials, tenant identity, and destination mappings are server-controlled.",
                "Named adapters refuse redirects and remain unavailable without deployment-held credential references and independent reconciliation authority.",
                "A local acknowledgement or ready state does not prove live provider interoperability, permissions, completeness, customer acceptance, or applied state.",
            ),
            next_steps=(
                "Confirm the destination, signing custody, retry, privacy, and recovery reviews are complete.",
                "Load queue and named-adapter readiness before dispatching a bounded batch.",
                "Reconcile ambiguous provider outcomes independently before replay or acceptance.",
            ),
            suggested_questions=(
                "What must be ready before dispatch?",
                "Which named provider bindings are ready?",
                "How should I reconcile an ambiguous delivery?",
            ),
        ),
        CopilotPageContext(
            path="/consent/public-clients",
            title="Public Consent Clients",
            summary=(
                "Mint and revoke exact-origin browser or service clients for the active consent "
                "publication and receipt policy."
            ),
            capabilities=(
                "Mint a browser client bound to an exact property, origin, jurisdiction, and policy revision.",
                "List minimized client bindings and revoke a client or profile capability.",
                "Use the inert public SDK for structured first-party consent integration.",
            ),
            boundaries=(
                "A raw client credential is returned once and is never stored by the authority.",
                "Origins, properties, publications, and receipt policies are exact rather than inferred.",
                "The SDK does not grant authority beyond the active server-side consent configuration.",
            ),
            next_steps=(
                "Verify consent publishing and the public API are enabled and the active publication is correct.",
                "Copy the one-time credential directly into approved custody.",
                "Revoke a compromised client or profile capability using its exact revision.",
            ),
            suggested_questions=(
                "What is the one-time credential boundary?",
                "What must match before I mint a client?",
            ),
        ),
        CopilotPageContext(
            path="/consent/native",
            title="Native Consent SDK",
            summary=(
                "Operate application and device authority for the attested mobile and "
                "OTT/CTV consent runtime and its offline-verifiable receipt keys."
            ),
            capabilities=(
                "Register a globally unambiguous application from an exact active service client and published consent bundle.",
                "Rotate configured receipt keys, rotate freshly attested device keys, revoke devices, and inspect exact CAS results.",
                "Check fail-closed readiness and integrity and discover current and historical public receipt verification keys.",
            ),
            boundaries=(
                "Tenant, principal, publication, service-client, signer and stored device authority are server-derived; the browser supplies only safe selectors, CAS revisions and attestation evidence.",
                "Platform attestation has no permissive fallback, and configured profile, rights and GPP/TCF callbacks are required before readiness.",
                "Local and synthetic success does not prove app-store, live-device, accessibility, conformance, customer-estate or regulator acceptance.",
            ),
            next_steps=(
                "Confirm signer custody, the external platform verifier and any configured callback requirements before registration.",
                "Register the create-only application authority from its exact public service client and save the returned authority digest.",
                "Exercise enrollment, refresh, offline receipt verification, recovery and representative live-device acceptance before production reliance.",
            ),
            suggested_questions=(
                "Why is native consent not ready?",
                "What authority is an application registration pinned to?",
                "What remains to be validated on live devices?",
            ),
        ),
        CopilotPageContext(
            path="/consent/profile-data",
            title="Consent Profile Data",
            summary=(
                "Govern closed profile-data catalogs and journeys with revision-pinned publication, "
                "independent approval, integrity, and retention controls."
            ),
            capabilities=(
                "Create and inspect closed catalog and journey versions.",
                "Record independent approval and publish exact revisions.",
                "Verify integrity and purge only retention-eligible operational records.",
            ),
            boundaries=(
                "Tenant and actor are server-derived; the page accepts no raw profile or capability selector.",
                "Authors cannot approve their own versions and every mutation uses an exact revision.",
                "Only closed published values and trusted purposes are supported; protected traits are never inferred.",
            ),
            next_steps=(
                "Load status and confirm the catalog, journey, and approval authorities are composed.",
                "Review the closed values and trusted purpose before submitting a version.",
                "Have a distinct authorized person approve before publication.",
            ),
            suggested_questions=(
                "What profile data is allowed here?",
                "Who may approve a catalog or journey?",
            ),
        ),
        CopilotPageContext(
            path="/consent/scan-inventory",
            title="Consent Scan Inventory",
            summary=(
                "Run bounded browser scans against enrolled properties, review minimized tracker "
                "discoveries, and bridge only fresh approved mappings."
            ),
            capabilities=(
                "Enroll an exact property, schedule scans, and explicitly run a leased job.",
                "Inspect bounded reports and record versioned classification review.",
                "Bridge fresh reviewed mappings and operate audit, recovery, and retention controls.",
            ),
            boundaries=(
                "A scan performs external browser requests only against an enrolled property.",
                "Every discovery remains unknown until reviewed; bridging is a separate governed action.",
                "A complete bounded report does not prove that no tracker exists.",
            ),
            next_steps=(
                "Confirm property authority, browser adapter, privacy review, and scan limits.",
                "Inspect completeness, truncation, and errors before classifying a discovery.",
                "Bridge only a fresh, revision-pinned reviewed mapping.",
            ),
            suggested_questions=(
                "What does scan completeness mean?",
                "When may a discovery be bridged?",
            ),
        ),
        CopilotPageContext(
            path="/consent/scan-notice-proposals",
            title="Scan Disclosure Proposals",
            summary=(
                "Turn independently reviewed scan evidence into content-free, revision-pinned "
                "disclosure proposals for separate approval and local acceptance."
            ),
            capabilities=(
                "Create a proposal from exact server-resolved scan and inventory evidence.",
                "Record independent approval and separate local acceptance.",
                "Inspect integrity and export a signed, content-free governance record.",
            ),
            boundaries=(
                "The browser cannot select tenant, principal, property, report, inventory revision, or revocation set.",
                "Approval and local acceptance require different authenticated people and exact source pins.",
                "Acceptance does not generate legal copy, mutate a site or app, call a provider, or publish a notice.",
            ),
            next_steps=(
                "Load status and verify the source report and reviewed inventory remain current.",
                "Use distinct authorized people for approval and local acceptance.",
                "Treat the signed export as governance evidence, not proof of publication.",
            ),
            suggested_questions=(
                "What evidence is pinned into a proposal?",
                "What does local acceptance not do?",
            ),
        ),
        CopilotPageContext(
            path="/consent/trust-center-authority",
            title="Consumer Trust Center Authority",
            summary=(
                "Govern digest-bound consumer capability grants and privacy-rights case authority "
                "through server-resolved identity and multi-party approval."
            ),
            capabilities=(
                "Create, inspect, approve, revoke, and verify capability grants.",
                "Authorize privacy-rights cases against exact package and consumer bindings.",
                "Inspect integrity and retention evidence without exposing raw identity material.",
                "Propose, independently review, activate, rotate, revoke, and retain bounded PNG/JPEG brand assets.",
                "Challenge and govern exact custom-domain claims through an injected DNS/TLS deployment authority.",
                "Bind an approved asset and optional verified domain to the exact enrolled client and consent publication.",
            ),
            boundaries=(
                "Tenant, operator, consumer binding, permission authority, and signing keys are server-resolved.",
                "Every grant and case authorization requires a requester plus two other distinct approvers.",
                "The surface performs no identity proof, network call, provider write, or completeness inference.",
                "Branding accepts no SVG, HTML, CSS, URL, raw storage path, caller-selected tenant, or encryption key.",
                "Live DNS/TLS/CDN issuance and accessibility, brand, and customer acceptance remain external.",
            ),
            next_steps=(
                "Confirm the identity, consumer-binding, permission, and key authorities are ready.",
                "Review the exact digest-bound grant or package before collecting approvals.",
                "Use two distinct authorized approvers other than the requester.",
                "For branding, review the raster digest and dimensions before activation, then pin it to the current publication.",
            ),
            suggested_questions=(
                "Why are three distinct people required?",
                "What identity material is excluded?",
                "What does the brand projection prove, and what remains external?",
            ),
        ),
        CopilotPageContext(
            path="/data-use",
            title="Data Use Governance",
            summary=(
                "Authorize data use from catalog bindings, independently sourced decision context, "
                "reviewed labels, policy, exceptions, and provider-neutral control intent."
            ),
            capabilities=(
                "Catalog assets and fields, lineage, context, labels, and policy versions.",
                "Create a project data-use request and record a revision-pinned authorization decision.",
                "Inspect provider-neutral control IR and bounded authority records.",
            ),
            boundaries=(
                "Four independent context records must be fresh before data use can be authorized.",
                "Policy authors cannot approve their own versions and requesters cannot authorize their own use.",
                "Provider writes remain off until an approved native adapter is explicitly configured.",
            ),
            next_steps=(
                "Verify catalog, lineage, purpose, jurisdiction, and classification context are fresh.",
                "Publish an independently approved policy before evaluating a request.",
                "Inspect the compiled control intent before any native-provider operation.",
            ),
            suggested_questions=(
                "Which context records must be fresh?",
                "When can a provider write occur?",
            ),
        ),
        CopilotPageContext(
            path="/data-use/providers",
            title="Native Data Controls",
            summary=(
                "Apply authorized data-use decisions to exact Snowflake and Databricks resources "
                "through server-controlled credentials and safe-deny reconciliation."
            ),
            capabilities=(
                "Enroll or permanently revoke an exact native-resource binding.",
                "Apply a freshly recompiled authorized masking, row-filter, access, or deny decision.",
                "Reconcile provider state and roll ambiguous or rejected operations toward safe deny.",
            ),
            boundaries=(
                "Binding enrollment accepts no credential or provider origin from the browser.",
                "Apply recompiles the exact authorized decision before performing an external write.",
                "Rollback converges to safe deny; it does not recreate arbitrary prior provider state.",
            ),
            next_steps=(
                "Confirm the native adapter, credential reference, target binding, and recovery plan.",
                "Re-read the data-use decision immediately before apply.",
                "Reconcile the provider after acceptance, timeout, or ambiguous acknowledgement.",
            ),
            suggested_questions=(
                "What is required before a native apply?",
                "What does rollback restore?",
            ),
        ),
        CopilotPageContext(
            path="/compliance",
            title="Technology Risk and Compliance",
            summary=(
                "Triage governed compliance assessments, evidence needs, control work, and exports "
                "without turning local workflow state into a compliance conclusion."
            ),
            capabilities=(
                "Review assessment work that needs attention and filter it by governed state.",
                "Inspect evidence and control records and prepare bounded exports.",
                "Open policy publication, scoping, and scope-action workbenches.",
            ),
            boundaries=(
                "Workflow state and local evidence do not establish legal applicability or compliance.",
                "Framework text, external evidence rights, and final disposition remain separately governed.",
            ),
            next_steps=(
                "Start with items that need attention and verify their evidence revision.",
                "Choose policy publication, scoping, or scope actions for the exact task.",
                "Have the configured accountable person accept any final disposition.",
            ),
            suggested_questions=(
                "Which compliance workbench should I use?",
                "What does an assessment state prove?",
            ),
        ),
        CopilotPageContext(
            path="/compliance/grc-studio",
            title="No-code GRC Studio",
            summary=(
                "Visually compose closed-schema GRC objects, fields, forms, rules, "
                "collector bindings, and report views under governed publication."
            ),
            capabilities=(
                "Build a draft through guided object, field, form, rule, collector-binding, and report-view controls.",
                "Submit, independently review, approve, publish, roll back, revoke, and recover an exact revision under CAS.",
                "Export and import authenticated portable packages and inspect integrity, retention, and runtime projections.",
            ),
            boundaries=(
                "Copilot is read-only and cannot create, review, approve, publish, roll back, revoke, import, or recover a studio.",
                "The studio cannot supply deployment-owned collector credentials, provider transports, or target write authority.",
                "Local studio state is not evidence of live provider interoperability, customer usability, regulator acceptance, or compliance.",
            ),
            next_steps=(
                "Confirm the active tenant, readiness state, and role before authoring a draft.",
                "Use different principals for authoring, review, and approval before publication.",
                "Validate runtime projections and recovery with the deployment-owned target before relying on them operationally.",
            ),
            suggested_questions=(
                "What can I configure without code?",
                "What must happen before publication?",
                "What does the runtime projection prove?",
            ),
        ),
        CopilotPageContext(
            path="/compliance/policy-links",
            title="Policy Publication Links",
            summary=(
                "Publish immutable, independently reviewed policy versions through stable links, "
                "scheduled promotion, fail-closed retrieval, and bounded evidence operations."
            ),
            capabilities=(
                "Create an immutable policy artifact and record its required independent review.",
                "Move a stable-link pointer with compare-and-swap or schedule a bounded promotion.",
                "Rotate or revoke private capabilities and inspect audit, access, retention, and integrity evidence.",
            ),
            boundaries=(
                "Only a policy version satisfying its exact review mode may be published.",
                "Private link capabilities are returned once and should replace shared administrator access.",
                "Scheduled changes require the bounded worker; expiry and revocation fail closed during retrieval.",
            ),
            next_steps=(
                "Verify storage, access, recovery, retention, and review-mode configuration.",
                "Record the required independent review before moving the stable pointer.",
                "Inspect the bounded worker and integrity evidence after a scheduled change.",
            ),
            suggested_questions=(
                "What must happen before publication?",
                "How are private links rotated safely?",
            ),
        ),
        CopilotPageContext(
            path="/compliance/scoping",
            title="Technology Risk and Compliance Scoping",
            summary=(
                "Publish closed, reference-only questionnaires and compute, independently approve, "
                "sign, export, and verify exact compliance-scope revisions."
            ),
            capabilities=(
                "Create and independently publish a closed scoping questionnaire.",
                "Compute a scope result from exact answers and record independent final approval.",
                "Sign, export, inspect, and verify a revision-pinned scope record.",
            ),
            boundaries=(
                "Questionnaires contain opaque references and closed values, never licensed framework prose.",
                "A questionnaire creator cannot publish it and a scope computer cannot approve their own result.",
                "Signing accepts only an environment, vault, or secret reference, never a key value.",
            ),
            next_steps=(
                "Confirm the framework references, question schema, signing reference, and retention authority.",
                "Use a distinct publisher and a distinct final scope approver.",
                "Verify the signed export against its exact revision and evidence pins.",
            ),
            suggested_questions=(
                "Why is framework prose excluded?",
                "Who must be independent in scoping?",
            ),
        ),
        CopilotPageContext(
            path="/compliance/scope-actions",
            title="Compliance Scope Actions",
            summary=(
                "Derive a governed action plan from an independently finalized scope and approved "
                "mapping catalog, then approve, apply, reconcile, roll back, and export it."
            ),
            capabilities=(
                "Publish an opaque mapping catalog and create an exact scope-derived action plan.",
                "Independently approve, apply, reconcile, or roll back an idempotent local operation.",
                "Inspect signed exports, history, recovery state, and integrity evidence.",
            ),
            boundaries=(
                "Plans require an exact finalized scope and an independently approved mapping catalog.",
                "A separate principal must approve the plan and the server must install the idempotent callback.",
                "Callback and plan stores are not one transaction; uncertain operations reuse their deterministic reference.",
            ),
            next_steps=(
                "Verify the finalized scope, approved catalog, callback, and recovery design.",
                "Have a distinct principal approve the exact plan revision before apply.",
                "Reconcile an ambiguous callback before retrying or rolling back.",
            ),
            suggested_questions=(
                "What must be approved before apply?",
                "How do I recover an uncertain callback?",
            ),
        ),
        CopilotPageContext(
            path="/compliance/continuous-evidence",
            title="Continuous Evidence Operations",
            summary=(
                "Operate exact continuous-evidence definitions, enrollments, bounded collection "
                "cycles, digest-only observations, change alerts, recovery, and integrity."
            ),
            capabilities=(
                "Run the local SOC 2 observer and configured read-only AWS Security Hub, AWS Config, Azure Defender, Azure Policy, Google Cloud Security Command Center, GitHub security-stream, and Okta posture collectors.",
                "Inspect exact definition and enrollment authority, jobs, runs, digest-only observations, alerts, retention, and integrity.",
                "Run one bounded server-authorized scheduler cycle or let the configured leased scheduler operate across active tenants.",
            ),
            boundaries=(
                "The browser cannot choose a tenant, provider origin, asset scope, source selector, credential reference, transport, or raw provider payload.",
                "Readiness resolves no credential value and performs no provider network request; collection rechecks the exact approved definition and active enrollment for every job.",
                "A configured source or successful observation does not prove estate completeness, control effectiveness, compliance, certification, licensed access, or live provider/customer acceptance.",
            ),
            next_steps=(
                "Refresh readiness and resolve every missing stock source manifest or credential reference.",
                "Register and independently approve the exact collector definition, then create the matching tenant enrollment using its current integrity digest.",
                "Run a bounded cycle, inspect incomplete/retry/dead-letter state, verify integrity, and obtain live provider and customer acceptance before relying on coverage.",
            ),
            suggested_questions=(
                "Which stock collector source is not ready?",
                "What authority is rechecked before provider I/O?",
                "What does a successful observation not prove?",
            ),
        ),
        CopilotPageContext(
            path="/privacy",
            title="Privacy Operations",
            summary=(
                "Coordinate privacy assessments, residual-risk review, vendor paper, program "
                "insights, lineage, and governed privacy records."
            ),
            capabilities=(
                "Triage privacy work and inspect assessment flow, residual risk, and framework views.",
                "Review vendor paper and supporting evidence beside the governed record.",
                "Inspect program insights, lineage, and bounded privacy records.",
            ),
            boundaries=(
                "Generated analysis and redlines are decision support, not legal advice or approval.",
                "Vendor submissions and local workflow state are not independently verified external facts.",
                "Final applicability, risk acceptance, and disposition remain human decisions.",
            ),
            next_steps=(
                "Open the work item that needs attention and verify its current evidence revision.",
                "Use lineage to understand why a prior decision was recorded.",
                "Have the accountable privacy reviewer accept or reject the result.",
            ),
            suggested_questions=(
                "Where should I start on this privacy page?",
                "What does lineage show?",
            ),
        ),
        CopilotPageContext(
            path="/privacy/data-discovery",
            title="Privacy Data Discovery",
            summary=(
                "Enroll exact data sources, run bounded provider scans, review minimized findings, "
                "and promote only fresh four-eyes-approved classifications."
            ),
            capabilities=(
                "Enroll, suspend, reactivate, or revoke an exact source and its schedule.",
                "Lease, execute, fail, replay, and inspect bounded jobs through stock AWS S3, Azure Blob, Google Cloud Storage, Snowflake metadata, ServiceNow CMDB, and Microsoft Entra adapters.",
                "Propose and independently approve a classification, then operate its durable remediation outbox and digest-only governed data-map projection.",
                "Inspect or enable the bounded managed remediation scheduler for tenant-derived retry and exact local ambiguity reconciliation.",
            ),
            boundaries=(
                "Raw secrets never enter the page; credentials and adapters are server-resolved.",
                "Every report is bounded and completeness-explicit; no findings never proves absence.",
                "Only a fresh four-eyes-approved classification can enter remediation; stock adapters do not prove live provider acceptance, estate completeness, or an authorized provider write.",
                "Managed retry is a separate explicit opt-in and operates only the built-in digest-only data map; running health is not provider or completeness evidence.",
            ),
            next_steps=(
                "Verify the exact source, credential reference, provider adapter, privacy review, and scan bounds.",
                "Inspect completeness, truncation, and errors before proposing a classification.",
                "Use a distinct authorized approver, then run and reconcile the durable outbox before treating the data map as current.",
                "Confirm the tenant scheduler opt-in, bounded cycle status, recovery counter, and concurrency exclusion before relying on automatic retry.",
            ),
            suggested_questions=(
                "What does a discovery report prove?",
                "When may a classification be bridged?",
            ),
        ),
        CopilotPageContext(
            path="/privacy/regulatory-intelligence",
            title="Privacy Regulatory Intelligence",
            summary=(
                "Govern licensed regulatory sources, immutable evidence, corpus-grounded research, "
                "jurisdiction comparison, saved queries, alerts, reports, and retention."
            ),
            capabilities=(
                "Enroll a source, record independent rights review, and ingest immutable evidence.",
                "Run cited research and jurisdiction comparison over the retained approved corpus.",
                "Operate saved-query alerts, durable delivery, reports, integrity, and retention.",
            ),
            boundaries=(
                "Source license and permitted uses require independent review before enrollment.",
                "Answers are corpus-grounded and abstain when the configured evidence floor is not met.",
                "The workspace does not bundle OneTrust DataGuidance content or replace legal advice.",
            ),
            next_steps=(
                "Confirm source rights, purpose, territory, retention, and evidence custody.",
                "Review every citation and abstention before relying on a research result.",
                "Inspect retry, dead-letter, and acknowledgement ambiguity after alert delivery.",
            ),
            suggested_questions=(
                "What source rights are required?",
                "What does an abstention mean?",
            ),
        ),
        CopilotPageContext(
            path="/privacy/rights-orchestration",
            title="Privacy Rights Orchestration",
            summary=(
                "Open and coordinate minimized privacy-rights cases, external identity evidence, "
                "tasks, callbacks, approvals, closure, integrity, and retention."
            ),
            capabilities=(
                "Open a case from HMAC-minimized subject and external-request references.",
                "Coordinate identity-evidence status, tasks, callback intent, and response-package authority.",
                "Record independent closure approval and inspect audit, integrity, and retention state.",
            ),
            boundaries=(
                "Raw subject and external request references are HMACed and never persisted or returned.",
                "Identity status records external evidence only; Lightwork does not perform identity verification.",
                "Callbacks are local worker intent, not network transport, provider write, response delivery, or completeness proof.",
            ),
            next_steps=(
                "Confirm case, HMAC, task, callback, and package authorities are composed.",
                "Review independently obtained identity evidence before advancing the case.",
                "Require a distinct authorized closure approver and verify callback outcomes separately.",
            ),
            suggested_questions=(
                "How is subject identity minimized?",
                "What does a callback record prove?",
            ),
        ),
        CopilotPageContext(
            path="/privacy/rights-consumer-delivery",
            title="Privacy Rights Consumer Delivery",
            summary=(
                "Mint digest-retained consumer delivery capabilities, govern exact response packages, "
                "revoke deliveries, export evidence, and purge retention-eligible records."
            ),
            capabilities=(
                "Create and inspect a delivery bound to server-resolved consumer and package authority.",
                "Revoke a delivery and produce a bounded signed evidence export.",
                "Inspect integrity and purge only retention-due delivery records.",
                "Deliver an exact pinned package from the encrypted governed-document vault after repeated orchestration and vault authority checks.",
            ),
            boundaries=(
                "A raw consumer capability appears only in the first mint response; only its digest is retained.",
                "Public consumer binding and reauthentication are independent trusted resolvers; case or package authority is never treated as identity proof.",
                "Digest, export-signing, and package-encryption authority are pinned key references; raw key values are not configuration or status data.",
                "The operator page exposes no redeem, object bytes, credentials, provider write, or live-customer acceptance.",
            ),
            next_steps=(
                "Load composition readiness and verify independent reauthentication, consumer binding, exact package authority, key-reference custody, and retention.",
                "Move the one-time raw capability directly into approved delivery custody.",
                "Revoke with an exact revision and verify the resulting evidence independently.",
            ),
            suggested_questions=(
                "How is the consumer capability retained?",
                "Why is operator minting still fail-closed?",
                "What is excluded from this operator page?",
            ),
        ),
        CopilotPageContext(
            path="/privacy/notice-governance",
            title="Privacy Notice Governance",
            summary=(
                "Govern digest-only privacy-notice releases and the optional exact-target "
                "local bridge into tenant-private Consent Publishing authority."
            ),
            capabilities=(
                "Create a notice release from opaque content and evidence references.",
                "Record independent approval, publication intent, rollback, withdrawal, revocation, and expiry.",
                "Run one bounded tenant-derived local publication cycle through an exact server-owned target binding.",
            ),
            boundaries=(
                "Releases carry only digests and opaque references; raw notice content and key values are rejected.",
                "Tenant, target property, consent reference, rule group, resource versions, and CAS revisions are server-derived.",
                "The runtime mutates only Lightwork's local consent publication store; it has no website, provider, tag-manager, or SDK mutation path.",
                "Local completion is not proof of customer browser behavior, provider acceptance, production deployment, or legal sufficiency.",
            ),
            next_steps=(
                "Load runtime status and confirm configuration preflight is ready for the active tenant.",
                "Have an authorized person other than the author approve the exact release.",
                "Verify the release digests and exact target binding before authorizing one local cycle.",
            ),
            suggested_questions=(
                "Is the local publication runtime configured for this tenant?",
                "What does a successful local publication prove?",
                "How does rollback choose the exact prior bundle?",
            ),
        ),
        CopilotPageContext(
            path="/third-party/contracts",
            title="Contracts & Engagements",
            summary=(
                "Govern first-class Contract and Engagement records tied to exact "
                "third-party vendor revisions."
            ),
            capabilities=(
                "Create, filter, inspect, update, archive, and tombstone Contracts and Engagements.",
                "Manage closed custom-field schemas, option codes, redaction, and deterministic formulas.",
                "Inspect immutable history, integrity, retention queues, and recovery readiness.",
            ),
            boundaries=(
                "Tenant, actor, vendor type, vendor authority, issuer, revocation, retention, and storage are server-owned.",
                "Raw contracts, licensed templates, executable formulas, provider adapters, and recovery paths are not accepted.",
                "Recovery preflight verifies readiness and integrity; actual backups remain server-configured administrator operations.",
            ),
            next_steps=(
                "Confirm the tenant composition is enabled, configured, and ready.",
                "Use the exact enrolled vendor ID and revision before creating a record.",
                "Review redaction, history, and integrity after material lifecycle changes.",
            ),
            suggested_questions=(
                "What vendor authority is pinned to this Contract?",
                "Which fields are redacted and why?",
                "When can an archived record be purged?",
            ),
        ),
        CopilotPageContext(
            path="/third-party/executive-reports",
            title="Third-Party Executive Reports",
            summary=(
                "Create role-scoped, brandable PDF reports from exact revision- and "
                "digest-pinned third-party records."
            ),
            capabilities=(
                "Resolve selected lifecycle, intelligence, contract, engagement, and collaboration records without copying raw source data.",
                "Render a bounded deterministic PDF with closed branding and role-aware metrics.",
                "Issue principal-bound one-time downloads, revoke reports, purge expired records, and verify audit integrity.",
            ),
            boundaries=(
                "Unavailable sources remain explicit and never become invented portfolio facts.",
                "Branding accepts closed text and color tokens, not HTML, scripts, URLs, fonts, or arbitrary assets.",
                "A local PDF is audit evidence, not proof of live portfolio completeness, customer acceptance, or an external WORM anchor.",
            ),
            next_steps=(
                "Confirm reporting composition and each selected source authority are ready.",
                "Choose the smallest exact source set and review unavailable-source reasons before creating a report.",
                "Use the one-time grant from approved custody and verify report integrity before distribution.",
            ),
            suggested_questions=(
                "Which source records can I include?",
                "What does this PDF prove?",
                "How are download grants protected?",
            ),
        ),
        CopilotPageContext(
            path="/administration/document-vault",
            title="Governed Document Vault",
            summary=(
                "Custody bounded approved document bytes in tenant-private encrypted "
                "storage and deliver them only through one-time principal-bound grants."
            ),
            capabilities=(
                "Ingest approved media without accepting paths, URLs, bucket keys, or object locators.",
                "List minimized authority metadata, revoke documents under CAS, and purge only after retention.",
                "Mint short-lived access tokens, atomically redeem one-time delivery grants, recover leases, and verify integrity.",
            ),
            boundaries=(
                "The UI never exposes ciphertext locations, encryption keys, raw token digests, or storage credentials.",
                "Local encrypted custody does not prove external object-store, HSM/KMS, backup, or disaster-recovery acceptance.",
                "A successful download proves this local authority consumed one grant; relying workflow and customer acceptance remain separate.",
            ),
            next_steps=(
                "Confirm the vault and Administration Document Access readiness probes are both ready.",
                "Ingest only approved bounded media and preserve the returned exact authority revision and digest.",
                "Exercise one-time delivery, replay rejection, revocation, recovery, retention, and integrity before production use.",
            ),
            suggested_questions=(
                "What document data is stored here?",
                "How does one-time delivery work?",
                "What still needs external acceptance?",
            ),
        ),
        CopilotPageContext(
            path="/third-party/intelligence",
            title="Third-Party Intelligence",
            summary=(
                "Govern licensed vendor intelligence, screening, explainable risk, enhanced due "
                "diligence, monitoring, delivery, and a consent-bound contribution exchange."
            ),
            capabilities=(
                "Enroll approved source rights and immutable snapshots for pseudonymous vendor screening.",
                "Review findings, risk policy, overrides, rollback, and enhanced due-diligence evidence.",
                "Operate DNS-pinned, GET-only normalized reference feeds for RiskRecon, SecurityScorecard, HackNotice, SupplyWisdom, ISS Corporate Solutions, and one approved generic provider.",
                "Operate scheduled monitoring, durable alerts, retention, integrity, and consent-bound aggregate exchange.",
            ),
            boundaries=(
                "Source license, purpose, territory, and retention require independent review.",
                "Sanctions, PEP, watchlist, and adverse-media candidates require explicit human disposition.",
                "A configured reference contract or verified credential reference does not prove licensed data, vendor-native interoperability, completeness, scale, or provider acceptance.",
                "External delivery occurs only through a configured server-side destination resolver and transport.",
            ),
            next_steps=(
                "Load provider runtime status, then have an administrator verify the exact approved monitoring source credential before the scheduler makes an attempt.",
                "Verify source rights, host authority, completeness, and the exact snapshot before screening a vendor.",
                "Disposition uncertain findings and review cited evidence before accepting risk.",
                "Confirm contribution consent and privacy review before using the exchange.",
            ),
            suggested_questions=(
                "Which named provider reference contracts are ready for an attempt?",
                "What evidence rights must be approved?",
                "Which findings require human review?",
            ),
        ),
        CopilotPageContext(
            path="/third-party/lifecycle",
            title="Third-Party Lifecycle",
            summary=(
                "Coordinate versioned vendor assessments, conditional workflow, evidence references, "
                "callbacks, offboarding, independent completion, audit, and retention."
            ),
            capabilities=(
                "Create and publish approved assessment templates and issue exact vendor assessments.",
                "Track answers, evidence references, conditions, callback intent, and lifecycle state.",
                "Plan offboarding, record independent completion, and inspect integrity and retention.",
            ),
            boundaries=(
                "Templates are versioned and records retain evidence references rather than raw vendor data.",
                "Offboarding, callbacks, state changes, and completion require explicit revision-bound decisions.",
                "Callbacks are local intent only; the surface proves no transport, provider write, or live acceptance.",
            ),
            next_steps=(
                "Verify the approved template, exact vendor binding, due dates, and evidence policy.",
                "Review conditions and callback results before changing lifecycle state.",
                "Use an independent authorized person to complete offboarding.",
            ),
            suggested_questions=(
                "What is retained from a vendor assessment?",
                "What does callback state prove?",
            ),
        ),
        CopilotPageContext(
            path="/third-party/collaboration",
            title="Third-Party Collaboration",
            summary=(
                "Invite consenting vendors into a scoped external portal and govern respondent "
                "attestations, document manifests, follow-up, rotation, revocation, and retention."
            ),
            capabilities=(
                "Create an invitation and disclose its one-time credential through approved custody.",
                "Inspect respondent answers and artifact manifests and record governed follow-up.",
                "Rotate, revoke, retain, purge, and verify exact invitation and response records.",
            ),
            boundaries=(
                "Invitation credentials are disclosed once and require an independently approved delivery channel.",
                "Answers and artifact manifests are respondent attestations, not verified evidence or uploaded files.",
                "The collaboration surface issues no risk score, vendor approval, or external acceptance claim.",
            ),
            next_steps=(
                "Confirm vendor consent, invitation scope, document contract, delivery custody, and retention policy.",
                "Send the one-time credential only through the approved external channel.",
                "Review attestations independently before any risk or lifecycle decision.",
            ),
            suggested_questions=(
                "What does a vendor response prove?",
                "How should an invitation credential be handled?",
            ),
        ),
        CopilotPageContext(
            path="/governance/inventory",
            title="Governance Inventory",
            summary=(
                "Inspect one revision-pinned, digest-only inventory across AI Governance, "
                "Consent, Data Use, Privacy, Tech Risk, and Third-Party Management."
            ),
            capabilities=(
                "Inspect the active immutable inventory snapshot and its six source authorities.",
                "Query bounded entities and relationships without loading raw source records.",
                "Reconcile exact source revisions, verify integrity, create bounded exports, and operate authenticated recovery checkpoints when authorized.",
            ),
            boundaries=(
                "The inventory proves only captured source revisions and never infers an unavailable source from another suite.",
                "Digest matches are deterministic references rather than fuzzy identity resolution or proof of real-world identity.",
                "Local reconciliation, exports, and recovery do not establish live provider completeness or external acceptance.",
            ),
            next_steps=(
                "Refresh readiness and confirm every required source authority is available.",
                "Inspect source completeness and the active revision before using a query or export.",
                "Use exact compare-and-swap values for reconciliation and verify integrity after recovery.",
            ),
            suggested_questions=(
                "What does this inventory prove?",
                "Which source is incomplete or unavailable?",
                "How should I safely reconcile or recover a snapshot?",
            ),
        ),
        CopilotPageContext(
            path="/security/assurance",
            title="Model Risk and AI Assurance",
            summary=(
                "Operate the AI evidence gateway, model-risk controls, and sixteen bounded governance "
                "modules from one assurance workspace."
            ),
            capabilities=(
                "Manage AI literacy evidence, the EU AI Act Article 73 incident clock, AI business intake, and AI cost and ROI.",
                "Inspect operational AI policy, explainable dynamic AI risk, transient runtime guardrails, and governed AI explanations.",
                "Prepare independent insurance-assurance profiles, EU AI Act conformity readiness, and regulated AI evidence packages.",
                "Operate the voluntary aggregate benchmark exchange and AI discovery sensor control plane.",
                "Inspect read-only identity and data-governance connectors and C2PA media-provenance evidence.",
                "Prepare and reconcile semantic GRC writeback through enrolled outbound authority.",
            ),
            boundaries=(
                "AI literacy, intake, cost, policy, risk, and guardrail records are local decision support, not legal conclusions.",
                "Article 73 records are not regulatory filings; explanations, insurance profiles, conformity readiness, and packages are not certification.",
                "Benchmark transport does not establish contribution consent, anonymity, differential privacy, or privacy-budget approval.",
                "Discovery inventory is bounded; identity connectors are read-only and do not prove estate completeness or enforced conditional access.",
                "C2PA verification records provenance evidence rather than truth, and GRC writeback requires external reconciliation.",
            ),
            next_steps=(
                "Open only the module needed and load its current readiness, authority, and evidence state.",
                "For literacy, Article 73, intake, cost, policy, risk, guardrails, explanations, insurance, conformity, or packages, verify the exact revision and required human decision.",
                "For benchmarks and discovery sensors, verify contribution consent, privacy review, completeness limits, key custody, and recovery before operation.",
                "For identity connectors, C2PA, and GRC writeback, reconcile live external state before accepting an outcome.",
            ),
            suggested_questions=(
                "Which assurance module should I use?",
                "Which modules require external reconciliation?",
                "What does this workspace not prove?",
            ),
        ),
        CopilotPageContext(
            path="/privacy/drop",
            title="California DROP Requests",
            summary=(
                "Bridge an independently enrolled California DROP record into the privacy-rights "
                "workflow and track a terminal, evidence-backed outcome."
            ),
            capabilities=(
                "List tenant-local broker, record, outcome, proposal, and key-revision authority without raw subject fields or key material.",
                "Propose broker enrollment/revocation, DROP record import/revocation, independent outcomes, and key lifecycle operations.",
                "Apply every authority mutation through a different authenticated administrator with revision and idempotency checks.",
                "Create a deletion or opt-out request from an enrolled DROP record.",
                "Load the current minimized request receipt.",
                "Record an exempted, deleted, opted-out, or not-found outcome.",
                "Inspect configured-for-attempt versus locally operational readiness, authority counts, scheduler health, revisions, and integrity.",
            ),
            boundaries=(
                "The page never accepts raw subject identity, credentials, key material, or attachments; authority imports use opaque references and SHA-256 evidence.",
                "A key revision cannot be retired while retained broker, record, outcome, request, or proposal evidence still depends on it.",
                "Terminal status requires independently resolved outcome evidence.",
                "Local records do not prove live DROP interoperability or legal acceptance.",
            ),
            next_steps=(
                "Refresh status and distinguish local operation from request executability; zero active records means no DROP request can execute.",
                "For a fresh tenant, enroll a broker and import a record through two-person authority proposals before creating a request.",
                "Use the exact external DROP ID, request type, approved template, and language.",
                "Propose and independently approve outcome evidence before recording a terminal status.",
            ),
            suggested_questions=(
                "What authority must exist for a DROP request?",
                "How do I complete a fresh-tenant two-person DROP lifecycle?",
                "Which records block retiring a principal-binding key revision?",
            ),
        ),
        CopilotPageContext(
            path="/assessments",
            title="Assessments",
            summary=(
                "Create, assign, answer, discuss, review, and decide governed assessments "
                "against exact questionnaire and evidence revisions."
            ),
            capabilities=(
                "Coordinate assessment owners and due dates.",
                "Keep threaded reviewer comments beside the governed assessment record.",
                "Record evidence, review, and final decisions with separation of duties.",
            ),
            boundaries=(
                "Comments are collaboration evidence, not approval or a compliance conclusion.",
                "A final decision still requires the configured human authority and exact revision.",
            ),
            next_steps=(
                "Open the assessment that needs attention.",
                "Resolve evidence questions in the review thread before final disposition.",
            ),
            suggested_questions=(
                "How do assessment comments affect approval?",
                "What should I review before deciding?",
            ),
        ),
        CopilotPageContext(
            path="/assess/evidence-autopopulation",
            title="Assessment Evidence Proposals",
            summary=(
                "Turn released evidence analyses into cited assessment-answer proposals "
                "without treating documents as attestations."
            ),
            capabilities=(
                "Generate questionnaire-bound proposals from exact released evidence analyses.",
                "Inspect confidence, eligibility, digest citations, and authority pins.",
                "Approve or reject an explicit answer subset, then invoke application separately.",
            ),
            boundaries=(
                "Proposal generation never changes an assessment answer or score.",
                "Application fills only unanswered questions under assessment CAS and never overwrites an answer.",
                "Evidence-assisted answers are not attestations, certifications, legal conclusions, or live-provider acceptance.",
            ),
            next_steps=(
                "Confirm both proposal and AI Evidence Analysis compositions are ready.",
                "Generate a proposal for an open pinned assessment and released analysis IDs.",
                "Have a different human review eligible answers before separately applying them.",
            ),
            suggested_questions=(
                "What does proposal eligibility mean?",
                "Why can this answer not be applied?",
                "Which authorities are rechecked during apply?",
            ),
        ),
    )
)
_CONTEXT_BY_PATH = {context.path: context for context in _CONTEXTS}
if len(_CONTEXT_BY_PATH) != len(_CONTEXTS):  # pragma: no cover - import invariant
    raise RuntimeError("duplicate Copilot page context path")


def context_for_page(page: object) -> CopilotPageContext:
    """Resolve a browser-proposed path to a closed server-owned context."""

    if not isinstance(page, str) or len(page) > 200:
        return _GENERIC
    path = page.strip()
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "?" in path
        or "#" in path
        or "\\" in path
    ):
        return _GENERIC
    return _CONTEXT_BY_PATH.get(path, _GENERIC)


def public_context(
    context: CopilotPageContext,
    permissions: frozenset[str] | None = None,
) -> dict[str, object]:
    """Return the bounded fields the browser can use for tips and quick prompts."""

    result: dict[str, object] = {
        "path": context.path,
        "title": context.title,
        "summary": context.summary,
        "capabilities": list(context.capabilities),
        "boundaries": list(context.boundaries),
        "next_steps": list(context.next_steps),
        "suggested_questions": list(context.suggested_questions),
    }
    if permissions is not None:
        order = ("view", "operate", "audit", "admin")
        result["caller_permissions"] = [item for item in order if item in permissions]
        result["authority_notice"] = _authority_notice(permissions)
    return result


def built_in_reply(
    context: CopilotPageContext,
    message: str,
    permissions: frozenset[str] | None = None,
) -> str:
    """Answer common page-help questions without an external model call."""

    normalized = message.casefold()
    if any(word in normalized for word in ("limit", "boundary", "prove", "safe", "authority")):
        lead = "Key boundary: "
        body = " ".join(context.boundaries[:2])
    elif any(word in normalized for word in ("start", "next", "how", "ready", "status")):
        lead = "Start here: "
        body = " ".join(context.next_steps)
    elif any(word in normalized for word in ("can", "capabil", "do", "feature")):
        lead = f"When role and server composition permit, {context.title} can: "
        body = " ".join(context.capabilities)
    else:
        lead = f"{context.title}: "
        body = f"{context.summary} Next: {context.next_steps[0]}"
    authority = f" {_authority_notice(permissions)}" if permissions is not None else ""
    return _bounded_reply(f"{lead}{body}{authority}")


def _authority_notice(permissions: frozenset[str]) -> str:
    """Describe caller authority without exposing identity or implying readiness."""

    if "admin" in permissions:
        access = "Your current role includes global administrator controls"
    elif "operate" in permissions:
        access = (
            "Your current role includes tenant operations but not global administrator controls"
        )
    elif "audit" in permissions:
        access = "Your current role includes read and global audit controls only"
    else:
        access = "Your current role is read-only"
    return f"{access}; server readiness and endpoint authorization are still enforced."


def sanitize_model_reply(reply: object) -> str:
    """Return display-safe, contract-bounded model guidance.

    Model text remains advisory rather than authoritative.  Strip control and
    bidi-formatting characters, collapse whitespace, and enforce the same
    response budget as the deterministic guide before the browser sees it.
    """

    if not isinstance(reply, str):
        return ""
    without_controls = "".join(
        " " if unicodedata.category(character).startswith("C") else character for character in reply
    )
    normalized = " ".join(without_controls.split())
    return _bounded_reply(normalized) if normalized else ""


def _bounded_reply(reply: str) -> str:
    """Keep deterministic no-provider guidance within the model reply budget."""

    words = reply.split()
    if len(words) > _MAX_BUILT_IN_REPLY_WORDS:
        reply = " ".join(words[:_MAX_BUILT_IN_REPLY_WORDS])
    if len(reply) > _MAX_BUILT_IN_REPLY_CHARS:
        reply = reply[: _MAX_BUILT_IN_REPLY_CHARS - 1].rsplit(" ", 1)[0]
    if not reply.endswith((".", "?", "!")):
        reply = f"{reply.rstrip(' ,;:')}."
    return reply


__all__ = [
    "CopilotPageContext",
    "built_in_reply",
    "context_for_page",
    "public_context",
    "sanitize_model_reply",
]
