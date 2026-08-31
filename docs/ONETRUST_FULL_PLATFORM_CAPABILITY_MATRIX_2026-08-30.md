# OneTrust Full-Platform Public Capability Matrix

Date checked: 2026-08-30

Lightwork repository: `Daybreak-AI-Labs/Lightwork`

Lightwork branch inspected: `claude/aigov-gap-buildout`

## Executive result

Lightwork does **not** yet have every capability publicly marketed for the six
OneTrust offerings in scope. The strongest overlap is AI Governance and the
local privacy/security control planes. This working tree now also has coherent,
focused-test-covered local paths for public web consent capture, versioned
consent publishing, bounded analytics, durable downstream delivery, scheduled
tracker scanning, governed consent experiments, identity-bound OTT/CTV rights
intake, Data Use Governance policy compilation and native Snowflake and
Databricks enforcement/audit adapters, privacy-preserving personal-data
discovery, licensed-source regulatory research, third-party intelligence/due-
diligence controls, evergreen public/private policy links, five packaged
governance agents, multimodal AI Evidence Analysis, named cloud-agent
governance contracts, assessment-question comments, and a California DROP
request/status control plane. None of those local paths is proof of live provider
interoperability, customer-scale operation, regulatory acceptance, or literal
OneTrust parity. The largest remaining product gaps are full mobile and OTT/CTV
consent SDKs, accepted branded/native cross-channel trust centers, named
MarTech/AdTech integrations, live privacy-discovery and intelligence feeds,
end-to-end DSR automation, a hosted full-product customer sandbox/tenant-copy
service, and curated compliance-content and continuous-evidence breadth.
The refreshed public catalog also names packaged privacy/third-party agents,
multimodal AI evidence analysis, provider-specific Bedrock/Azure/Vertex agent
detection and a Databricks AI Security Framework outcome. Lightwork now has
coherent local product paths for those outcomes, but provider writes, durable
production cloud authority, licensed content, off-host token custody and live
customer/provider acceptance remain materially incomplete.

This is a public-claims inventory and an implementation map, not a source-code
comparison. OneTrust is not open source, its marketing claims were not tested
here, and contract-specific or customer-only features may not be visible on its
public site. The 138 rows normalize public product outcomes and sometimes group
multiple UI-level features; they are not proof that every toggle, workflow
variant, API, integration contract, or customer-only module was enumerated.
Conversely, a Lightwork module is not counted as parity merely because it has a
similar name: it must implement the same product outcome.

One especially important market-state distinction: OneTrust's public Data Use
Governance page currently presents a **Request Early Access** action. Every
Data Use Governance capability below is therefore a marketed early-access
claim, not a claim that this review independently established general
availability.

## Status definitions

- **Implemented**: a coherent Lightwork product path and focused automated
  coverage exist in this checkout. This means local repository implementation,
  not customer-scale or third-party acceptance.
- **Partial**: useful code exists, but a material part of the advertised product
  outcome, integration, breadth, deployment, or validation is absent.
- **Missing**: no coherent implementation of the advertised outcome was found.
  Adjacent utilities, prompts, domain packs, or proposal documents do not count.
- **External / unverifiable**: the outcome depends on live providers, licensed
  content, commercial data, customer estates, independent acceptance, or
  operational scale that this repository cannot prove.

### Snapshot counts

These counts classify each distinct public capability row below; they are not a
weighted product score.

| Scope | Total | Implemented | Partial | Missing | External / unverifiable |
|---|---:|---:|---:|---:|---:|
| Shared platform | 15 | 5 | 9 | 0 | 1 |
| AI Governance | 26 | 11 | 14 | 0 | 1 |
| Consent & Preferences | 20 | 4 | 14 | 0 | 2 |
| Data Use Governance (early access) | 10 | 8 | 2 | 0 | 0 |
| Privacy Automation | 21 | 7 | 13 | 0 | 1 |
| Tech Risk & Compliance | 25 | 16 | 8 | 0 | 1 |
| Third-Party Management | 21 | 8 | 12 | 0 | 1 |
| **Total** | **138** | **59** | **72** | **0** | **7** |

Zero rows remain classified **Missing**, but that is not parity: 72 rows are
still **Partial** and seven depend on external/licensed/commercial evidence.
“No missing code path” must not be reported as “every marketed product outcome
is complete.”

## Official OneTrust source set

The product list comes from the official [OneTrust Products catalog](https://www.onetrust.com/products/)
and the shared claims come from the official [OneTrust Platform overview](https://www.onetrust.com/platform/).
The six solution families and their linked official product/capability pages
used in the tables are:

- AI Governance: [solution](https://www.onetrust.com/solutions/ai-governance/),
  [AI Discovery and Registry](https://www.onetrust.com/solutions/ai-governance/ai-discovery-and-registry/),
  [AI Policy Management](https://www.onetrust.com/solutions/ai-governance/ai-policy-management/),
  [Dynamic Risk Scoring](https://www.onetrust.com/solutions/ai-governance/dymanic-risk-scoring/),
  [AI Governance in Production](https://www.onetrust.com/ai-governance-in-production/),
  and [EU AI Act Compliance](https://www.onetrust.com/solutions/eu-ai-act-compliance/).
- Consent & Preferences: [solution](https://www.onetrust.com/solutions/consent-and-preferences/),
  [Universal Consent and Preference Management](https://www.onetrust.com/products/universal-consent-and-preference-management/),
  [Consent Management Platform](https://www.onetrust.com/products/consent-management/),
  [Cookie Consent](https://www.onetrust.com/products/cookie-consent/),
  [Mobile App Consent](https://www.onetrust.com/products/mobile-app-consent/),
  and [OTT and CTV Consent](https://www.onetrust.com/products/ott-and-ctv-consent/).
- Data Use Governance: [early-access solution](https://www.onetrust.com/solutions/data-use-governance/).
- Privacy Automation: [solution](https://www.onetrust.com/solutions/privacy-automation/),
  [Privacy Operations](https://www.onetrust.com/products/privacy-operations/),
  [DSR Automation](https://www.onetrust.com/products/data-subject-request-dsr-automation/),
  and [DataGuidance](https://www.onetrust.com/products/data-guidance/).
- Tech Risk & Compliance: [solution](https://www.onetrust.com/solutions/tech-risk-and-compliance/),
  [Compliance Automation](https://www.onetrust.com/products/compliance-automation/),
  and [IT Risk Management](https://www.onetrust.com/products/it-risk-management/).
- Third-Party Management: [solution](https://www.onetrust.com/solutions/third-party-management/),
  [Third-Party Risk Management](https://www.onetrust.com/products/third-party-risk-management/),
  [Third-Party Risk Exchange](https://www.onetrust.com/products/third-party-risk-exchange/),
  and [Third-Party Due Diligence](https://www.onetrust.com/products/third-party-due-diligence/).

The 2026-08-30 refresh also checked the official
[Privacy AI](https://www.onetrust.com/scale-privacy-with-ai/) page, the
[Winter '26 release](https://www.onetrust.com/blog/evolving-privacy-programs-with-ai-whats-new-in-the-onetrust-winter-26-release/),
the official [OneTrust demos](https://www.onetrust.com/resources/onetrust-demos/),
and the [Third-Party Risk Agent early-access material](https://www.onetrust.com/resources/highlights-of-the-fall-onetrust-release-for-risk-solutions-webinar/).
Those sources add six explicitly named rows below. The official
[2026-07-10 OneTrust release changelog](https://developer.onetrust.com/onetrust/changelog/onetrust-20260710-released)
adds three more tracked API outcomes: assessment-question comments via the
[create-comment](https://developer.onetrust.com/onetrust/reference/createcommentusingpost.md)
and [paginated get-comments](https://developer.onetrust.com/onetrust/reference/getcommentsforassessmentusingget.md)
contracts; California DROP request creation and status updates via the
[create-DROP-request](https://developer.onetrust.com/onetrust/reference/createdroprequestusingpost.md)
and [update-DROP-status](https://developer.onetrust.com/onetrust/reference/updatedropusingput.md)
contracts; and the `aiEvidenceAnalysisResult` evidence-task result object.
Several pages mix
private preview, public preview, early access and generally available claims
without mapping maturity per feature, so the matrix does not infer entitlement
or general availability from a marketing mention.

## Shared OneTrust platform capabilities

These are cross-cutting claims from the Platform page rather than a seventh
solution family.

| ID | Public OneTrust capability | Lightwork evidence | Status and exact boundary |
|---|---|---|---|
| PX-01 | One shared data model and inventory across governance domains | `entity_graph.py`, `privacy_ops.py`, `security_ops.py`, `ai_inventory.py`, and model-risk records link governed entities with cited provenance. | **Partial** — the entity graph is a rebuildable index across several stores, not one authoritative shared schema covering all six product families. |
| PX-02 | AI-driven discovery/classification across 200+ connectors | Generic connector infrastructure, bounded AI inventory and identity adapters, a browser tracker scanner, and provider-neutral structured/unstructured privacy discovery exist. | **Partial** — this is not a live 200+ connector classification estate; most named enterprise source contracts, maintained taxonomies, and provider acceptance remain absent. |
| PX-03 | Dynamic graphs across projects, models, datasets, vendors, and systems | `entity_graph.py`, `agent_topology.py`, evidence lineage, and model-risk dependency records. | **Implemented** — local graph/lineage paths exist, while completeness still depends on connected source data. |
| PX-04 | Regulatory and contract intelligence across jurisdictions | `privacy_regulatory_intelligence.py`, `regulatory_feeds.py`, `ai_regulatory_feed.py`, DPA/vendor-paper review, licensed-source rights, grounded research/comparison, trackers and evidence reports. | **Partial** — the local product path exists, but Lightwork lacks OneTrust/DataGuidance-equivalent maintained expert content, licensed global coverage and live legal-content acceptance. |
| PX-05 | AI-powered workflow automation and reuse of assessment answers | `assessment_memory.py`, `assessment.py`, `ai_business_intake.py`, document extraction, and approval workflows. | **Implemented** — local, advisory reuse is evidence-bound and human reviewed; customer-scale operating acceptance is unverified. |
| PX-06 | Integrated external breach, compliance, and adverse-media intelligence | `third_party_intelligence.py` provides governed source rights/snapshots, sanctions/PEP/watchlist/adverse-media/cyber/breach/compliance/performance findings, monitoring, evidence-bound scoring and an explicit bounded alert-delivery worker. | **Partial** — the provider-neutral control plane is coherent, but named commercial feeds, maintained external content, operated delivery and live interoperability are absent. |
| PX-07 | Programmatic guardrails embedded in systems and workflows | `ai_runtime_guardrails.py`, the external-gateway pilot, privacy egress controls, the web consent SDK, and Snowflake/Databricks data-use adapters provide local enforcement points. | **Partial** — the local control paths are substantial, but broad third-party, MarTech/AdTech, and enterprise-workflow enforcement plus live provider acceptance remain incomplete. |
| PX-08 | Always-on monitoring across privacy, data, third-party, tech risk, and AI | Schedulers and monitoring exist for AI inventory, risk, identity, cost, security, consent scans, privacy discovery jobs, and selected evidence flows. | **Partial** — there is no continuously operated six-domain production monitoring plane in a customer estate. |
| PX-09 | Role- and attribute-based access controls | Dashboard RBAC, exact-principal binding, tenant authority, tool ACLs, capabilities, and identity-governance controls. | **Implemented** — local enforcement exists; live enterprise identity-provider acceptance remains external. |
| PX-10 | Unified audit logs | `unified_audit.py` and the dashboard source/composition/API/scheduler path now provide a default-off, signed-only projection over a fixed six-suite source set. Separately injected authoritative readers and independent verifiers feed closed normalized records with stable identifiers and mandatory signed page receipts, including empty terminal pages, through a canonical tenant-bound strict sink. Exact authority/schema/verifier/trust-root/key rotation pins, generation sweeps, CAS leases, bounded retry/replay/revocation, immutable history, local integrity linkage and active-tenant scheduling are focused-test-covered. | **Partial** — no authoritative six-domain store readers or independently operated signed/anchored verifier deployments ship with this path. Local SQLite linkage is not external WORM or a cross-day/off-host anchor, off-host signing-key custody is unaccepted, and live scheduler crash/recovery, concurrency, scale, provider completeness, customer operation and SIEM acceptance remain unproven. |
| PX-11 | Customer-managed encryption keys | `crypto_at_rest.py`, secret providers, signing-key controls, and C2PA external signer boundaries. | **Partial** — cryptographic/key seams exist, but a production enterprise CMK lifecycle across every store and service is not accepted. |
| PX-12 | Sandbox environments | `customer_sandbox_lifecycle.py` plus its global-admin API/composition now provide a default-off production-source-to-isolated-workspace lifecycle across safe projections of all six suites. Immutable revision/digest/module/expiry/quota/plan snapshots, independent creation/destruction or failure-abandon approval, suspend/resume/resync/expiry, exact retry and crash-window recovery, target-collision checks, a path-confined local provisioner, retention tombstones and SQLite-authority integrity are focused-test-covered. | **Partial** — this is a bounded local managed sandbox-workspace path, not a registered Lightwork tenant or execution sandbox, and it intentionally copies neither arbitrary configuration nor tenant data. Workspace integrity is not attested end to end, the audit chain is unkeyed, two Windows reparse tests were skipped where junction creation was unavailable, and no hosted full-product tenant copy, live customer operation, production recovery, concurrency, scale, usability or SLA acceptance has been proven. |
| PX-13 | Executive reporting on risk posture, maturity, and control effectiveness | Privacy board, security program insights, AI portfolio risk, audit binder, assurance cockpit, and reporting APIs. | **Implemented** — local reporting is evidence-backed; executive/customer acceptance and complete cross-domain data quality remain external. |
| PX-14 | Broad pre-built integration catalog and partner ecosystem | Lightwork has a large generic connector catalog and selected governed adapters. | **External / unverifiable** — equivalent product-contract breadth, maintained vendor certification, support, and live interoperability cannot be established from connector names. |
| PX-15 | Packaged OneTrust AI Agents operating out of the box over the shared data model with common context and authentication | `governance_agents.py` plus its strict API/workbench package breach-response, privacy-risk-assessment, program-Copilot, knowledgebase-governance and third-party-risk agents over independently resolved tenant/principal/context, adapter and dispatcher authority. Immutable policy versions, bounded analysis reservations, closed findings/actions, human review, execution receipts, replay/recovery/revocation, quotas, retention and integrity are focused-test-covered. | **Implemented** — the coherent local packaged-agent path exists. Deployment still requires a durable encrypted execution-token vault with accepted HSM/KMS custody, independently operated adapters/dispatchers, a licensed knowledge corpus, one authoritative live six-suite data plane and customer usability/operations acceptance. |

## AI Governance

Official sources for this table: [AI Governance](https://www.onetrust.com/solutions/ai-governance/),
[AI Discovery and Registry](https://www.onetrust.com/solutions/ai-governance/ai-discovery-and-registry/),
[AI Policy Management](https://www.onetrust.com/solutions/ai-governance/ai-policy-management/),
[Dynamic Risk Scoring](https://www.onetrust.com/solutions/ai-governance/dymanic-risk-scoring/),
[AI Governance in Production](https://www.onetrust.com/ai-governance-in-production/),
and [EU AI Act Compliance](https://www.onetrust.com/solutions/eu-ai-act-compliance/).

| ID | Public OneTrust module/capability | Lightwork evidence | Status and exact boundary |
|---|---|---|---|
| AG-01 | Central inventory of models, datasets, agents, vendors, ownership, lifecycle, and component dependencies | `ai_inventory.py`, `model_risk_assurance.py`, `agent_topology.py`, inventory API/UI and scheduler. | **Implemented** — the governed local system of record exists; enterprise completeness depends on discovery sources. |
| AG-02 | Declared AI intake through business intake, procurement, architecture review, and model onboarding | `ai_business_intake.py`, assessments, model-risk inventory creation/linking, and review work. | **Implemented** — local intake is coherent; procurement/customer workflow adoption is not accepted. |
| AG-03 | Native discovery from AI/cloud/model platforms | Ten bounded adapters in `ai_inventory_connectors.py` plus scheduler and contract-shaped transports. | **Partial** — adapters are locally tested but were not accepted against live provider tenants. |
| AG-04 | Undeclared, shadow, and embedded-SaaS AI discovery | Six-class sensor plane with deployable network-egress, browser-event and OAuth-event agents. | **Partial** — three deployable agent types passed a local consenting estate; SaaS posture, directory, catalog, external-estate completeness, privacy, recovery, and scale remain unproven. |
| AG-05 | Structured metadata, purpose, data use, deployment status, owner, version and record-level change history | Versioned inventory, evidence graph, declarations, lifecycle/review records, and immutable audit. | **Implemented** — local record semantics and traceability exist. |
| AG-06 | EU AI Act, NIST AI RMF, and ISO 42001 templates/mappings | Signed/versioned NIST AI RMF and EU AI Act starter packs, AIMS/ISO 42001 support artifacts, semantic diffs and attestations. | **Partial** — bounded starter mappings are not OneTrust-equivalent maintained content, licensed standards text, legal advice, or certification. |
| AG-07 | Risk tiering by use case/system/component and risk-to-control framework mapping | `ai_act.py`, risk-tier classifiers, dynamic risk, policy packs, model-risk reviews, vertical packages, and control mappings. | **Implemented** — local deterministic classification/control routing exists; production calibration and legal acceptance do not. |
| AG-08 | Configurable intake, approvals, attestations, signoff, evidence, and audit-ready outputs | Assessment lifecycle, `ai_business_intake.py`, approval/quorum controls, evidence ledger/gateway, audit binder, and assurance exports. | **Implemented** — customer-specific configuration and usability acceptance remain deployment work. |
| AG-09 | Operational/versioned AI policy objects with statements, applicability, required controls, and exact scope | `ai_policy_management.py` implements immutable versions, exact applicability, strictest-wins evaluation and human activation. | **Implemented** — local policy object semantics match the advertised layer. |
| AG-10 | Policy exceptions, compensating controls, approving authority, expiry, remediation, change and revalidation | Four-eyes expiring exceptions, evidence-bound evaluation, revalidation and change-aware review in `ai_policy_management.py`. | **Implemented** — operating adoption and migration from customer policy sets are not proven. |
| AG-11 | Continuous telemetry ingestion and monitoring for model/agent drift, quality, safety, performance, prompts, responses, actions, tools and retrieval sources | Drift/quality engines, runtime telemetry, red-team assurance, dynamic risk and alert/finding paths. | **Partial** — coherent engines exist, but no broad live observability estate or always-on production acceptance exists. |
| AG-12 | Dynamic risk scoring enriched by purpose, user, sensitivity, regulation and policy, with attributable change history | `ai_dynamic_risk.py` implements normalized signals, freshness, context floors, attributable score/tier changes, exclusions and history. | **Implemented** — production calibration and live telemetry breadth remain unverified. |
| AG-13 | Real-time AI-policy violation detection, PII/sensitive-attribute detection, and early warning | Shield/PII redaction, runtime guardrails, dynamic risk, privacy egress, findings and alert routing. | **Partial** — local detectors and control hooks exist; comprehensive production detection quality and alert operations are unaccepted. |
| AG-14 | Explanation/diagnosis plus model cards, system summaries, issue histories and audience-specific dashboards | `ai_explainability.py`, model cards, evidence graph, assurance cockpit, portfolio dashboards and issue/finding records. | **Partial** — strong local reporting exists, but live provider explainers and legal/adverse-action acceptance are absent. |
| AG-15 | Runtime prompt/output filtering and policy-based allow, block, redact, route or restrict actions | `ai_runtime_guardrails.py`, external gateway, and restart-safe local gateway pilot covering retry, outage ambiguity, rollback and compensation. | **Partial** — executable local enforcement exists; no consenting real external/customer gateway acceptance was performed. |
| AG-16 | Native data/pipeline policies: masking/redaction, pre-production evaluation gates and unsafe-response blocking | Redaction, privacy egress, promotion gates, behavioral evaluation and runtime guardrails are joined by closed native Snowflake and Databricks masking/row-filter operations with exact binding, no-change, rollback and audit correlation. | **Implemented** — the coherent local path now spans Lightwork runtime and native Snowflake/Databricks controls; live provider interoperability, arbitrary pipeline breadth and customer acceptance remain unverified. |
| AG-17 | Targeted re-review when models, agents, datasets, grounding, actions, tools or usage materially change | Policy revalidation, lifecycle review triggers, inventory deltas, evidence staleness and behavioral-diff/promotion gates. | **Implemented** — local change-to-review mechanics exist. |
| AG-18 | Agent registration with purpose, enforced permissions/actions, and MCP policy/audit | Signed delegation, exact agent identity, capability attenuation/revocation, topology/blast radius, MCP inventory/policy, and signed audit. | **Implemented** — this is a Lightwork strength; cross-enterprise identity adoption remains partial. |
| AG-19 | EU AI Act classification, impact assessments, documentation, post-market monitoring, conformity readiness, and serious-incident workflow | `ai_act.py`, `ai_conformity_workflow.py`, `eu_ai_incidents.py`, FRIA/Annex IV/Article 43/post-market/declaration evidence workflows. | **Partial** — readiness code exists, but no legal determination, conformity assessment, filing transmitter, authority directory, signature, CE mark, or regulator acceptance exists. |
| AG-20 | Machine identity and governed agent-principal access | Entra/Okta/Purview connectors, identity scheduler, enrolled federation binding, capability mapping, conditional-access decisions and CAS writeback. | **Partial** — local control plane exists; live provider enrollment, policy enforcement and writeback acceptance are unavailable. |
| AG-21 | Third-party AI/vendor governance connected to the AI registry and assessments | `ai_vendor_risk.py`, vendor portal, bounded cited document extraction, deterministic scoring and local synthetic pilot. | **Partial** — no live consenting vendors, real documents, ecosystem acceptance or crash/restart pilot. |
| AG-22 | Automated model documentation, bills of materials, lineage and reusable audit evidence | Model cards, deployment lineage, evidence graph, exact artifact/evaluator bindings, BOM/SBOM and assurance packets. | **Partial** — primitives are strong but are not yet one fully accepted end-to-end customer workspace. |
| AG-23 | Enterprise-scale integrations across models, applications, agents, third-party AI and jurisdictions | Governed adapters, tenant stores, Postgres option, CAS, schedulers and APIs. | **External / unverifiable** — live integration breadth, deployment scale, support, regulatory operations and customer history cannot be proven from local code. |
| AG-24 | Closed-loop production governance: visibility, risk, control, monitoring, escalation and evidence | Local modules cover each stage and the platform acceptance runner exercises selected suites. | **Partial** — the loop is not continuously operated across a real production estate. |
| AG-25 | Named agent detection and governance for AWS Bedrock, Azure AI Foundry and Google Vertex AI, plus demonstrated live-agent observability/policy enforcement for Bedrock | `ai_cloud_agent_governance.py` normalizes version-pinned Bedrock, Azure AI Foundry and Vertex inventory into opaque tenant/scope-bound records, accepts closed observability events, evaluates policies, creates exact enforcement intents and requires signed provider receipts plus later inventory reconciliation. Azure has an exact hosted-agent disable mapping; unsafe/unresolved Bedrock operations and generic Vertex mutations fail closed. | **Partial** — canonical product state is still process-local, credentials and production transports are not bundled, Bedrock write breadth is incomplete, Vertex writes deliberately reject, and no provider tenant has accepted discovery completeness, observability or policy effects. |
| AG-26 | Databricks AI Security Framework support in AI governance | `ai_cloud_agent_governance.py` adds an immutable, versioned, content-free Databricks framework mapping with independent review, evidence linkage, attestation and activation, alongside existing Databricks Data Use control/audit adapters. | **Partial** — the local control plane is coherent, but no licensed/maintained framework prose, vendor-certified mapping, credentialed end-to-end provider operation, certification or customer acceptance is present. |

## Consent & Preferences

Official sources for this table: [Consent & Preferences](https://www.onetrust.com/solutions/consent-and-preferences/),
[UCPM](https://www.onetrust.com/products/universal-consent-and-preference-management/),
[CMP](https://www.onetrust.com/products/consent-management/),
[Cookie Consent](https://www.onetrust.com/products/cookie-consent/),
[Mobile App Consent](https://www.onetrust.com/products/mobile-app-consent/),
and [OTT and CTV Consent](https://www.onetrust.com/products/ott-and-ctv-consent/).

The existing `maverick.safety.consent` ledger governs permission for agent/tool
actions. It is not a consumer CMP, cookie consent system, marketing preference
center, or first-party data product and is not counted as one below.

This working tree now also contains a separate consumer-consent product path:
`consent_preferences.py` provides the tenant authority and immutable receipts;
`consent_publishing.py` provides versioned/localized purpose, topic, custom
preference, notice, template, rule and property publications; the narrow
`consent_public_api.py` and `lightwork-consent-sdk.js` provide exact-origin,
capability-bound public web capture and rendering; and the admin API/operator
surfaces provide governed setup and export. `consent_analytics.py` supplies
cohort-suppressed aggregate reporting. `consent_delivery.py` and its bridge/
runtime add an ordered, signed, retryable outbound delivery path. The tracker
scanner, reviewed inventory and attached scheduler add browser-backed,
bounded, fail-closed discovery with explicit completeness limits.

These paths have focused local automated coverage, but the classifications
below deliberately stop at the product outcome actually implemented. No live
web property, mobile app, OTT device, MarTech/AdTech provider, consenting
customer estate, privacy office, or regulator accepted them. The default
browser scheduler does not own authenticated-session credentials; downstream
delivery has no named Salesforce/Marketo/Adobe contract; and local test doubles
do not establish external interoperability, accessibility, recovery, scale, or
regulatory acceptance.

| ID | Public OneTrust module/capability | Lightwork evidence | Status and exact boundary |
|---|---|---|---|
| CP-01 | Universal Consent and Preference Management: one consumer portal for consent, communication preferences and first-party data | Capability-bound public profiles/preferences, known/anonymous linking, purpose/channel/vendor choices, versioned topics/custom-preference definitions, property-bound publications, volunteered closed-schema profile values and progressive prompts now sit behind an exact-origin web Trust Center. It combines active publication, consent history, profile data, explicitly authorized rights-case summaries, signed consumer export and publication-pinned localized color/copy branding. A default-off server-injected OIDC verifier and one-time digest-only PKCE pairing flow now bind exact source/target client, property, profile/CAS revision, publication/configuration/policy and browser-origin or service-client authority before linking through the existing consent store. | **Partial** — the web surface does not complete the communications workflow, native mobile/OTT/CTV experience, governed identity unlink/relink/account-transition lifecycle, production IdP/JWKS and subject-digest-key custody, continuous synchronization at deployed scale, governed logo/asset/custom-domain branding or live customer acceptance required for a complete cross-channel UCPM outcome. |
| CP-02 | Dynamic first-party data collection, progressive profiling and personalized journey prompts | `consent_profile_data.py`, its mounted strict admin API/operator workbench and the public consent API/SDK provide versioned independently approved boolean/single/multi-select definitions; publication/policy-bound values, history, export, unset/revoke and retention; versioned approved purpose-signal-targeted journeys with closed conditions, required-first prompts, cooldown/frequency caps, kill/rollback and aggregate interactions; and a safe public prompt renderer. Exact tenant, profile, property, catalog, journey, capability and targeting authority is server-derived. | **Implemented** — the core, admin and coherent local public web paths have dedicated behavioral coverage. Targeting is intentionally purpose-signal-only, and no live traffic, accessibility/privacy/legal review, native runtime, customer acceptance or protected-trait inference is claimed. |
| CP-03 | Branded trust centers with dynamic publishing and customer visibility into collected data | `consumer_trust_center.py`, its persisted digest-only context/grant/case authority, the mounted authenticated authority workbench, public exact-origin routes and safe SDK renderer provide a capability-gated web Trust Center with the current active publication, consent history, volunteered profile data, explicitly authorized rights summaries and signed export. The production composition and operator prepare route derive an immutable revision-pinned context from safe client/property/profile selectors, require a current redeemed OIDC pairing, and leave `rights_cases` proposal plus two distinct approvals separate; the public SDK can then create and check access, portability, erasure, rectification, restriction, objection or opt-out requests through the subject-bound orchestration bridge. `consent_publishing.py` versions localized closed theme tokens, pins an exact theme into publication/rollback, validates 4.5:1 text contrast, and lets the SDK apply only six-digit colors and plain text across the CMP and Trust Center. | **Partial** — branding remains limited to governed colors and localized plain copy; logo/media assets, custom domains, standalone/native channels, communications delivery, production IdP/signing/assurance custody, live provider fulfillment and usability/accessibility/customer acceptance remain absent. |
| CP-04 | Synchronize preferences into SaaS/legacy MarTech such as Salesforce, Marketo, Snowflake and Adobe | `consent_delivery.py`, its receipt bridge and deployable runtime provide enrolled destinations, per-profile ordering, signed envelopes, bounded retries, dead letters/replay, key rotation/revocation and DNS-pinned HTTPS delivery. | **Partial** — no named Salesforce, Marketo, Adobe or equivalent destination contract has passed live interoperability and operational acceptance. |
| CP-05 | Real-time consent/preference dashboards tied to marketing and privacy KPIs | `consent_analytics.py`, the admin API and consent workspace provide bounded aggregate trends and dimensions with unique-profile cohort suppression and explicit truncation. | **Partial** — the privacy-bounded local analytics path is coherent, but it is not joined to marketing performance data and has no accepted live KPI correlation, customer calibration or operating-scale proof. |
| CP-06 | Web/mobile CMP with accessible explanations, intuitive controls, branded banners, geolocation and multilingual templates | Versioned localized templates/rules and contrast-checked closed theme tokens, exact web origins and mobile bundle/platform bindings, plus the structured-DOM web SDK's branded banner and preference-center renderers, form a local web CMP path. | **Partial** — no native mobile SDK, IP-geolocation service, governed logo/media asset path, live accessibility/localization review, or production property acceptance exists. |
| CP-07 | Website scanning for cookies, tags, pixels, beacons and trackers, including hidden pages and authenticated paths | `consent_scanner.py`, `consent_scan_inventory.py` and the attached scheduler crawl enrolled HTTPS origins with bounded Playwright collection, inventory cookies/storage/network findings, recover leases, retry/dead-letter jobs, and require reviewed classification before registry admission. | **Partial** — no consenting live estate or completeness/scale acceptance exists, and authenticated scans require a separately injected credential-owning storage-state resolver that the default scheduler does not provide. |
| CP-08 | Automatic tracker categorization using OneTrust's marketed 45-million-cookie database | No comparable classification corpus or service exists in Lightwork. | **External / unverifiable** — this depends on OneTrust's proprietary maintained content; Lightwork has neither the corpus nor a substitute product. |
| CP-09 | Pre-consent auto-blocking through no-code blocking, tag-manager integration and script rewriting | The public SDK evaluates current purpose decisions before activating declaratively tagged scripts/images/iframes and emits a bounded tag-manager event; the core remains fail closed on stale authority. | **Partial** — there is no full no-code tag-manager product, arbitrary script-rewrite engine, edge enforcement point, or live browser/AdTech acceptance. |
| CP-10 | Consumer consent receipts, change history, transaction database and exportable regulatory logs | The public API authenticates exact property/origin client capabilities plus anonymous profile capabilities, records hash-linked policy/context-bound grant/deny/revoke/withdraw or GPC/DNT receipts, and supports bounded admin history/export/access logs. | **Implemented** — the coherent local public transaction path is focused-test-covered; production signing custody, regulator delivery and customer/regulator acceptance remain external validation. |
| CP-11 | Scheduled scans that update cookie inventories and auto-populate cookie/privacy policies | The browser scanner, durable inventory and attached scheduler update reviewed tracker inventory and bridge only fresh approved classifications into the consent registry. The inventory now exposes an exact forward discovery cursor, and `consent_scan_notice_proposals.py` follows public pages up to an explicit 10,000-discovery ceiling while double-reading the enrolled property, immutable report, discoveries and current independent reviews; rejecting unknown/stale/retired/revoked/drifting authority; rejecting incomplete/truncated reports by default unless an exact property/report-bound current independent exception remains explicitly labeled; and creating digest-only structured disclosure proposals with distinct creator/approver/local-acceptor, desired/no-change, reconcile, rollback and signed export. | **Partial** — the proposal path generates neither legal copy nor an automatic notice/site/app/provider publication. The 10,000-item bound is explicit rather than a completeness claim, and live authenticated-property completeness, recovery, scale, legal review and customer acceptance remain absent. |
| CP-12 | A/B testing, targeted template variants and opt-in optimization | `consent_experiments.py`, its strict API and console provide immutable certified variants, independently approved definitions, audience targeting, keyed sticky assignment, control/holdout and safety precedence, aggregate exposures/outcomes, bounded sequential analysis, kill and rollback. The public consent API now derives the experiment and pseudonymous subject from existing property/profile authority, and the SDK assigns before render, records exposure only after DOM insertion, and records outcomes only after a successful consent save. | **Implemented** — the default application path uses the tenant-local experiment authority with a published-control fallback and focused runtime tests; this is still local product implementation, not live traffic, causal-validity, accessibility, recovery or scale acceptance. |
| CP-13 | Cross-domain, cross-app and cross-device consent synchronization; unify unknown and known profiles and reduce re-prompts | Pseudonymous known/anonymous profiles can be linked; the web SDK persists a capability-bound anonymous profile; and ordered receipt changes enter a replayable downstream delivery plane. The new OIDC/PKCE reference protocol verifies exact issuer/single-audience/time/nonce/`kid` authority through an injected key resolver, retains no raw token or subject, binds both endpoints and their publication/policy/capability revisions, supports browser and mobile/TV service clients, recovers exact partial links, and uses the existing CAS link so linked receipts retain latest-negative semantics. | **Partial** — this is a focused locally tested reference flow, not an operated synchronization service: production IdP/JWKS and subject-digest custody, native cookieless SDK integration, identity unlink/relink/household-account lifecycle, continuous multi-device transport, recovery/scale exercises and live customer acceptance remain absent. |
| CP-14 | Granular preference center, opt-down rather than opt-out, and collection of additional volunteered first-party data | The public SDK renders granular purpose choices and now supports capability-bound view/history/export plus set/unset/revoke for independently approved closed-schema volunteered values. Progressive journeys present required-first boolean/single/multi-select prompts under purpose-signal targeting, cooldown/cap and kill/rollback rules, with assignment retained before DOM insertion and completion only after successful saves. | **Implemented** — the coherent local web collection and opt-down path is focused-test-covered; free text and protected-trait inference are excluded, and live usability, accessibility, native-channel and customer acceptance remain unverified. |
| CP-15 | Personalized-advertising consent, industry frameworks, vendor-specific opt-outs, GPC/Do-Not-Sell-or-Share signaling | `consent_standards.py` detects exact `Sec-GPC`/DNT headers, projects US opt-out fields, maps Google Consent Mode v2, and encodes/decodes the GPP v1 header; vendor-scoped choices and signal receipts are policy-bound. | **Partial** — TCF 2.3 and GPP discrete sections intentionally fail closed, and there is no IAB certification, maintained vendor-list service, AdTech adapter or live signal acceptance. |
| CP-16 | Mobile App Consent: scan permissions, IDFA, frameworks and SDKs; localized banners, cookieless sync, receipts and analytics | Publishing and public-client contracts bind Android/iOS/FireOS app bundles and mobile-sheet configuration, and the general receipt/profile/analytics authorities are channel-aware. The OIDC/PKCE pairing API admits an exact service client with bundle/platform and profile/publication/policy pins, allowing a deployment-owned native client to join its anonymous profile to a verified known profile without consumer-supplied selectors. | **Partial** — there is no bundled native iOS/Android SDK/scanner, permission/IDFA collector, deployed localized sheet, native integration of the pairing flow, production key custody, recovery/scale exercise or live mobile acceptance. |
| CP-17 | OTT/CTV Consent: scan apps, capture/store/signal consent, downstream AdTech signaling, household profiles and device sync | The server-bound publication/public-client contracts recognize Roku, tvOS, Tizen, webOS and FireOS bundles, and the generic public API can store policy-bound receipts. The pairing protocol also supports exact service-client and TV bundle/platform bindings and joins the device profile through the existing consent ledger after verified OIDC authorization. | **Partial** — there is no TV app scanner/native SDK, household/device ownership and unlink/relink lifecycle, downstream AdTech signaling, native pairing integration, production key custody or live device/recovery/scale acceptance. |
| CP-18 | IAB TCF support plus native/API support for Apple TV, Roku, Amazon Fire and similar platforms | Provider-neutral API/publication bindings cover tvOS, Roku and FireOS-style platforms, and the selector-free service-client pairing path can bind those platform profiles to a verified known identity through an injected OIDC authority. | **Partial** — no native TV SDK or live platform interoperability exists, and TCF wire encoding/decoding deliberately fails closed because it is neither implemented nor certified. |
| CP-19 | Consumer rights access/deletion from OTT/CTV preference experiences | `privacy_rights_ott_ctv.py` and its public API bind a fresh redeemed OIDC/device pairing, exact service client/profile/current publication/policy and Trust Center `rights_cases` authority to idempotent access/deletion case creation through the existing rights orchestrator. Proof expiry, account transition, grant/client/pairing revocation, recovery, signed digest-only status and controlled retention are explicit; deletion still requires the orchestrator's separate destructive approval and respects legal hold. | **Partial** — a focused-test-covered local control-plane bridge now exists, but there is no native TV/mobile SDK, built-in estate retrieval/deletion adapter, retrieved-data delivery, household/device lifecycle, live device/provider interoperability or customer acceptance. |
| CP-20 | Production-grade consent integrations and regulatory change deployment across regions | No consenting consumer estate or MarTech/AdTech acceptance exists. | **External / unverifiable** — this requires deployed digital properties, vendor contracts and jurisdictional operating review. |

## Data Use Governance — marketed early access

All rows in this section come from the OneTrust page that says **Request Early
Access** as of 2026-08-30: [Data Use Governance](https://www.onetrust.com/solutions/data-use-governance/).

Lightwork now has a local, provider-neutral Data Use Governance authority and
operator API/UI for assets, fields, lineage, four independently sourced
contexts, reviewed machine labels, versioned policies/exceptions, requests and
authorization decisions. The companion compiler revalidates those exact
authorities and emits immutable desired/rollback control IR. Default-off
Snowflake and Databricks adapters, a credential-owning/DNS-pinned runtime, and
admin APIs translate only that server-compiled IR into bounded native
mask/row-filter/allow/deny operations with idempotency, retry, ambiguity,
no-change, reconciliation, rollback and binding-revocation evidence. Focused
tests use controlled transports; no live Snowflake/Databricks tenant accepted
the SQL/API contracts, performance, recovery, credential lifecycle, or
last-mile provider evidence.

| ID | Public OneTrust early-access capability | Lightwork evidence | Status and exact boundary |
|---|---|---|---|
| DUG-01 | Capture intended purpose at project initiation and use purpose as a policy input | `data_use_governance.py` stores project/request purpose codes plus an independently revisioned business-purpose authority and pins both into authorization decisions. | **Implemented** — a coherent local project-to-data-use authorization path is focused-test-covered; customer workflow adoption remains unverified. |
| DUG-02 | AI-driven classification of structured and unstructured data using business, regulatory, consent and data context | Privacy discovery produces reviewed structured/unstructured classifications, and `privacy_discovery_data_use_bridge.py` now double-reads exact source/report/finding/review and target-asset/context authority before a durable CAS/idempotent desired, no-change, reconcile or rollback write to the observed-classification context. Business-purpose, regulatory and consent contexts remain independent and are never inferred from discovery. | **Partial** — the safe local bridge is not composed into a continuously operated live enterprise estate, and it intentionally cannot manufacture the three independently governed non-observational contexts. |
| DUG-03 | Machine-readable labels that drive automated data-use decisions | Versioned four-eyes-reviewed machine labels and exact field assignments are bound into decisions, revalidated by the compiler, and consumed by provider adapters. | **Implemented** — the governed local label-to-decision/control contract is focused-test-covered. |
| DUG-04 | System of record for privacy, consent and regulatory data policies across ingestion, use and sharing | The data-use store versions policy rules, actions, effects, purposes, context/label predicates, lifecycle, exceptions and exact decision history. | **Implemented** — this is a coherent local data-policy authority; enterprise source completeness and operating acceptance remain unverified. |
| DUG-05 | Convert documented data policies into enforceable control code | `data_use_policy_compiler.py` re-resolves every bound authority and deterministically compiles a closed, immutable mask/row-filter/allow/deny/review IR with a safe rollback state. | **Implemented** — the local compiler is focused-test-covered and rejects arbitrary SQL/provider operations. |
| DUG-06 | Connect policies to real-time controls native to modern data and AI systems | Default-off provider APIs and runtimes compile exact decisions server-side, own ephemeral credential leases, pin HTTPS egress and invoke controlled Snowflake/Databricks adapters. | **Implemented** — the coherent local deployment path exists; live provider interoperability and production operations are not established. |
| DUG-07 | Apply column masking and row-filtering controls | Snowflake and Databricks translators emit bounded native masking and row-filter operations only from validated control IR, with safe identifiers and closed expression/function vocabularies. | **Implemented** — local translations and mutation semantics are focused-test-covered; no live table policy was changed in this review. |
| DUG-08 | Audit where masking/row filters are applied in real time and consume infrastructure audit logs for last-mile proof | Provider operations retain desired/observed digests, receipts, no-change, ambiguity and rollback evidence. The audit plane adds a durable digest-only pre-write authority outbox, exact post-write registration or `recovery_pending`, independently issued read grants, credential bridge, fair persistent recovery, scheduler/app wiring and operator API. Built-in DNS-pinned, redirect-disabled, byte/time-bounded transports query Snowflake `QUERY_HISTORY` plus `ACCESS_HISTORY` and Databricks `system.query.history` plus supplemental `system.access.audit`; they validate exact tags/statement bindings, result metadata, chunks/partitions, keyset pagination, retention and watermarks without persisting raw SQL, principals, origins, statement IDs or provider payloads. | **Implemented** — the native local read/correlation/completeness path is focused-test-covered and fails closed when accepted delivery authority is absent. No live Snowflake/Databricks tenant, provider latency/permission/retention behavior, continuously operated estate, scale or customer acceptance has been proven; Databricks query tags remain a preview dependency. |
| DUG-09 | Shorten data-enablement approval cycles through project-initiated, programmatic governance | The API/UI supports project requests through four-context/label/policy authorization, deterministic compilation and controlled provider apply/rollback. | **Implemented** — the end-to-end local path is coherent; tenant usability and cycle-time improvement have not been measured in a live program. |
| DUG-10 | Native Snowflake/Databricks interoperability for policy pushdown | Dedicated Snowflake and Databricks adapters/runtime contracts now cover enrollment, credential revision, apply, no-change, retry, ambiguity, reconciliation, rollback and permanent revocation. | **Partial** — contract-shaped local tests are not live provider interoperability; real tenant SQL/API compatibility, permissions, rate limits, recovery and scale remain unverified. |

## Privacy Automation

Official sources for this table: [Privacy Automation](https://www.onetrust.com/solutions/privacy-automation/),
[Privacy Operations](https://www.onetrust.com/products/privacy-operations/),
[DSR Automation](https://www.onetrust.com/products/data-subject-request-dsr-automation/),
and [DataGuidance](https://www.onetrust.com/products/data-guidance/).

The new `privacy_data_discovery.py` path is a default-off, provider-neutral
control plane rather than a claim of live enterprise discovery. It enrolls
exact HTTPS structured/unstructured sources by secret reference, runs bounded
scheduled jobs with retry/recovery/dead letters, detects a closed set of
personal-data categories without persisting source values, records explicit
errors/truncation, and requires fresh four-eyes review. Its approved-review
seam can now enqueue digest-only work in
`privacy_data_discovery_remediation.py`, whose injected worker supplies leases,
retry/dead-letter/replay, revocation/cancellation and ambiguous-result
reconciliation; a separate exact-pinned bridge can update only Data Use
Governance's observed-classification context. The API and discovery operator
console are locally covered; named live adapters and callback targets, broad
document parsing, provider scale and legal/completeness acceptance are not.

| ID | Public OneTrust module/capability | Lightwork evidence | Status and exact boundary |
|---|---|---|---|
| PA-01 | DataGuidance regulatory intelligence: expert content, comparison charts, trackers and jurisdictional coverage | The licensed-source privacy-intelligence authority now supports immutable cited documents, jurisdiction comparison and trackers. | **External / unverifiable** — Lightwork does not bundle the marketed expert network/content corpus; OneTrust's product page says 1,700 contributors and 25,000+ articles while its solution page also advertises a larger trusted-expert figure. |
| PA-02 | OneTrust Copilot research, personalized/shareable collections, email alerts and exportable reports | `privacy_regulatory_intelligence.py`, its API/console and delivery worker provide evidence-floor research/abstention, ACL collections, scoped expiring shares, saved queries, deterministic alerts, at-least-once SMTP delivery and cited reports. | **Partial** — local grounded lexical research is not an accepted AI legal-research copilot; licensed content, semantic retrieval, destination enrollment, live email operation, multi-node recovery and customer/legal acceptance remain absent. |
| PA-03 | Central privacy compliance program planning, evidence collection, reporting and workflow | Privacy workspace/board, assessments, audit binder, record types and evidence-backed reporting. | **Implemented** — local program operation exists; maintained legal intelligence and customer acceptance remain external. |
| PA-04 | Continuous privacy asset detection from IAM, cloud and CMDB sources that triggers downstream workflows | Privacy discovery has scheduled/recoverable structured and unstructured source scans plus an approved-only durable remediation outbox; identity and AI inventory connectors provide adjacent source paths. | **Partial** — no named IAM/cloud/CMDB discovery adapters or continuously operated live-tenant workflow has been accepted. |
| PA-05 | Connect to data assets to find/classify personal data, monitor risk and trigger remediation | `privacy_data_discovery.py`, its API and console enroll provider-neutral sources, scan in memory, classify bounded personal-data categories and retain digest-only findings/posture. `privacy_data_discovery_remediation.py` double-reads freshly approved authority and adds leases, retry/dead-letter/replay, revocation/cancellation, ambiguity reconciliation, retention and an injected idempotent worker without persisting raw provider data. | **Partial** — the production-shaped local control plane still lacks built-in live provider/target adapters, PDF/OCR/archive/proprietary-format coverage, deployed worker composition and enterprise completeness/scale acceptance. |
| PA-06 | Evergreen data/activity map across assets, processing activities and vendors; auto-generate RoPA and identify transfers | `privacy_ops.py` provides Art. 30 RoPA/transfer maps, while the discovery remediation outbox can deliver only fresh approved digest-bound findings to an injected data-map/RoPA target with retry/recovery evidence. The separate Data Use Governance bridge updates only exact reviewed observed-classification context. | **Partial** — no built-in data-map/RoPA target adapter or live source estate proves continuous completeness, transfer identification, or operating acceptance. |
| PA-07 | PIA, DPIA, TIA, vendor and AI risk assessments with mitigation workflows | `assessment.py` and the privacy workspace include PIA, DPIA, LIA, CCPA/CPRA, TIA, AIRA, vendor and security questionnaires, review, acceptance and remediation. | **Implemented** — bounded templates are decision support, not legal conclusions. |
| PA-08 | AI document scanning and inventory-record analysis to auto-populate assessments | DPA/vendor-paper extraction, cited AI-vendor document answers, assessment memory and source discovery. | **Partial** — useful bounded paths exist, not the general document/inventory automation claimed across the suite. |
| PA-09 | Vendor privacy-risk assessment and DPA management | DPA review, clause playbook, tracked-changes redline, vendor records, assessments and entity graph. | **Implemented** — local deterministic/human-reviewed workflows exist; live counterparty acceptance is external. |
| PA-10 | Privacy incident intake, impact/root-cause context and automated notification guidance by jurisdiction | `privacy_ops.py`, `security_ops.py` and `eu_ai_incidents.py` implement incident registers, clocks, context, response and human decisions. | **Partial** — no maintained legal notification engine, live authority directory or filing transmitter exists. |
| PA-11 | Privacy notice templates, approval/version workflows, SDK publishing and automatic updates across sites/apps | `consent_publishing.py` versions localized notices/templates/rules, previews and publishes exact property bundles with rollback and immutable withdrawal tombstones; the public SDK resolves only an active configuration. `privacy_notice_governance.py`, its strict API and operator workbench add role/quorum/TTL approval policies, independently reviewed digest-bound releases, approval revocation, scheduling, leased retry/dead-letter publication intents, rollback/withdrawal, cited update proposals, effective-version resolution, signed exports, retention and integrity. `privacy_notice_publication_bridge.py` now consumes exact claimed intents through a constructor-bound tenant/target seam, pins notice/release/rule/bundle digests, performs compare-and-swap publication, no-change/recovery and rollback, and maps withdrawal to a fail-closed consent-publication tombstone. | **Partial** — this is a content-free tenant-local bridge between two non-atomically committed local authorities, not an operated transport worker, consent-provider integration, SDK deployment or site/app/native mutation. Ambiguous cross-store recovery requires exact re-read, and broad rollout plus live legal/customer/provider acceptance remain absent. |
| PA-12 | Regulatory-context DSR intake and dynamic fulfillment workflow | DSAR tracker, message detection and deadlines now sit alongside an exact-origin SDK/API intake bridge. Its production composition re-resolves client/profile/publication/policy authority, requires a fresh independently assured redeemed OIDC identity and an approved `rights_cases` Trust Center grant, applies tenant and per-subject active-request quotas, and creates signed status for the existing `privacy_rights_orchestration.py` case path. That orchestrator plans right-specific phases, provider-neutral callback intents, evidence and completeness manifests. | **Partial** — the authenticated local consumer-to-orchestration path is coherent, but no built-in provider retrieves/deletes data, no production identity/assurance/signing custody is provisioned, and no live enterprise source estate or consumer journey has been accepted. |
| PA-13 | DSR identity verification | DSAR Concierge includes an emailed verification loop. | **Partial** — no production identity-verification provider, fraud controls or cross-channel acceptance is proven. |
| PA-14 | Automated data retrieval/deletion, legal-hold checks and fulfillment without rescanning the whole estate | `privacy_rights_orchestration.py`, its strict API and operator workbench enroll opaque source/credential references, HMAC raw subject/request references before persistence, plan right-specific retrieve/delete/verify phases, gate destructive work on distinct approval plus legal-hold/retention state, and operate a leased retry/dead-letter callback outbox with source evidence and bounded completeness manifests. | **Partial** — a coherent provider-neutral orchestration plane now exists, but callbacks are worker intent only: no built-in source adapter performs retrieval/deletion, and no live provider or estate-completeness acceptance exists. |
| PA-15 | DSR redaction and secure response/customer portal | Redaction primitives, DSAR Concierge and response-package metadata now sit behind a default-off one-time delivery core, authenticated operator API/page, selector-free public redeem route and SDK. The local path binds current case/package evidence, reauthentication and consumer context, immediately claims an opaque server grant, and returns only bounded hash-verified package bytes through an injected adapter. | **Partial** — no built-in object-store/decryption or source-system adapter, production identity/redaction/provider acceptance, or robust recovery after a post-claim delivery failure has been operated. |
| PA-16 | Unified consumer experience connecting DSR intake with CMP and Trust Center | The exact-origin web Trust Center joins active consent publication, receipt history, volunteered profile data and explicitly authorized rights-case summaries; the same capability-bound SDK can create and check privacy-rights requests after the server-side context preparation and independent grant approvals, and it exposes the separately governed one-time package-download path. | **Partial** — this is a connected local web journey, but production identity/assurance/signing custody, cross-estate fulfillment, native/cross-channel UX, accessibility/recovery/scale operation and customer acceptance remain outstanding. |
| PA-17 | Privacy dashboards, posture, SLA aging, transfers, risk, incidents and audit-ready exports | Privacy command center, board, report, audit binder, SLA heatmap and transfer map. | **Implemented** — local values derive from operating records rather than fabricated scores. |
| PA-18 | Cross-product privacy foundation feeding AI Governance, Third-Party Risk and Data Use Governance | Entity graph/shared evidence, consent authorities, privacy records/discovery, vendor assessments and the Data Use Governance authority now provide local foundations across all named domains. | **Partial** — they are not one continuously synchronized, live-source-complete customer data model with accepted cross-suite operations. |
| PA-19 | Privacy AI suite: Breach Response Agent, Risk Assessment Agent, Copilot/Copilot Analytics over program data and a OneTrust Knowledgebase, with constrained APIs, default-off workflows, human-review thresholds and annotated decisions | The packaged governance-agent authority exposes breach-response, privacy-risk-assessment, program-Copilot and knowledgebase-governance agent kinds with immutable default-off policies, server-resolved bounded context, closed annotated findings/actions, independent human review, signed execution receipts, recovery/revocation, quotas, retention and strict APIs. | **Implemented** — the coherent local privacy-agent suite is focused-test-covered. It does not include OneTrust's licensed knowledge corpus, a production token vault/HSM, independently operated adapters or live privacy-program/customer acceptance; OneTrust's source also mixes preview and GA maturity. |
| PA-20 | Assessment Comments APIs to create a comment on an assessment question and retrieve paginated comments sorted by section and question | `assessment.py` and the strict assessment-session API create audited, bounded comments only on questions pinned to the saved questionnaire, preserve follow-up workflow state, and return deterministic bounded pages ordered by section, question and time. | **Implemented** — the coherent local question-comment API is focused-test-covered; OneTrust wire compatibility, migration of customer assessment histories and live operator usability have not been accepted. |
| PA-21 | California Delete Request and Opt-out Platform (DROP) APIs to create deletion/permanent-opt-out requests for registered data brokers and update DROP status | `privacy_drop_management.py` plus its strict API provide tenant/principal-bound `DataDeletion` and `OptOut` request creation through an existing DSAR-intake callback and evidence-bound `PENDING`, `EXEMPTED`, `DELETED`, `OPTED_OUT` or `NOT_FOUND` status updates. The store retains only opaque references/digests and enforces independent broker/subject/request authority, replay controls, retention and integrity. | **Implemented** — the coherent local control plane is focused-test-covered. No live California DROP authority or registered-broker interoperability, subject matching, production delivery, legal determination, regulator acceptance or customer operation has occurred. |

## Tech Risk & Compliance

Official sources for this table: [Tech Risk & Compliance](https://www.onetrust.com/solutions/tech-risk-and-compliance/),
[Compliance Automation](https://www.onetrust.com/products/compliance-automation/),
and [IT Risk Management](https://www.onetrust.com/products/it-risk-management/).

`policy_publication_links.py` and its admin/public APIs now add a focused-test-
covered local evergreen-link path: immutable plain-text policy versions,
approval/four-eyes modes, CAS current-version pointers, stable opaque public or
one-time-capability private links, capability rotation/revocation, scheduled
publication/expiry/revocation, fail-closed expiry, escaped HTML/text rendering,
hash-chained access/audit evidence and retention checkpoints. This is not live
edge/CDN publication, customer usability, recovery or scale acceptance.

`compliance_scope_actions.py`, its strict API and mounted operator workbench
now join the finalized-scoping authority to independently approved opaque
mapping catalogs and deterministic control/evidence-task plans. The path pins
the exact scope and assessment revisions/digests, carries first-line and
second-line ownership plus reason codes, deduplicates shared evidence, and
supports approval, apply/no-change, reconciliation, rollback, immutable
history and signed exports. Apply remains unavailable without a server-injected
idempotent local callback; callback and plan stores are not one transaction,
and no licensed framework content, built-in provider write or live acceptance
is implied.

| ID | Public OneTrust module/capability | Lightwork evidence | Status and exact boundary |
|---|---|---|---|
| TRC-01 | Compliance scoping survey that identifies responsibility and overlap | `compliance_scoping.py`, its strict API and operator workbench provide immutable independently published questionnaires across organization, industry, jurisdiction, data, system, vendor, AI and operational domains; deterministic conditional traversal; evidence-digest responses; reason-coded framework/control applicability; explicit overlaps/crosswalks; first-line, second-line, control-owner and evidence-owner assignments; independent scope approval/finalization; signed exports and integrity/retention controls. | **Implemented** — the dedicated local scope/overlap path is coherent and focused-test-covered; it includes no licensed framework text, legal advice, automatic external discovery, certification or live operating acceptance. |
| TRC-02 | 55+ ready-to-action frameworks, prescriptive content and insights across 300 jurisdictions | Security workspace covers nine named security frameworks with deep selected catalogs plus other bounded compliance/AI packs. | **Partial** — materially narrower than the marketed breadth; several standards require licensed copies and content is not a maintained 300-jurisdiction service. |
| TRC-03 | Control library with measurable evidence tasks | `security_ops.py` provides control register/SoA, owners, evidence requests, verdicts, tests, findings and audit workpapers. | **Implemented** — local core exists with explicit review gates. |
| TRC-04 | Automatically generate required controls/evidence tasks based on operations | `compliance_scope_actions.py` and its strict mounted API/workbench translate an exact independently finalized operational scope through a versioned, independently approved opaque mapping catalog into deterministic reason-coded control and measurable evidence-task intents. Plans pin scope/assessment/catalog digests, deduplicate shared evidence and provide approval, apply/no-change, reconcile, rollback, history and signed export. | **Implemented** — the coherent local generation path is focused-test-covered. Its catalog contains no licensed framework prose, apply needs a server-installed idempotent callback, cross-store mutation is non-atomic, and maintained OneTrust-equivalent content/provider/live acceptance is not claimed. |
| TRC-05 | Collect once, comply across 50+ frameworks through shared evidence mapping | Control crosswalks, evidence references and reuse/coverage metrics exist. | **Partial** — the reuse mechanism exists, but Lightwork does not ship or maintain the advertised 50-plus-framework content breadth and has not accepted reuse across that catalog. |
| TRC-06 | Continuous evidence collection from pre-architected external collectors | Independently approved collector definitions, exact enrollment pins, leased/recoverable jobs, terminal cursor/completeness state, digest-only observations, change alerts, retention/integrity, a strict operator API and bounded active-tenant scheduler now exist. A local SOC 2 observation collector is built in; external collector and credential injection seams exist but no external implementation is installed by default. | **Partial** — this is not a broad maintained external collector catalog, and no continuously operated customer estate, provider interoperability, completeness, recovery or scale acceptance has been proven. |
| TRC-07 | Policy, risk and control review workflows with automation rules | `security_ops.py`, `policy_packs.py`, workflow/assessment engines, CAS and human approval. | **Implemented**. |
| TRC-08 | Dynamic real-time compliance/risk dashboards and trend reporting | Security report/workspace, program insights, risk dashboards, privacy/AI boards and audit reports. | **Implemented** — local data only. |
| TRC-09 | Expanded risk and incident self-reporting/intake | Security/privacy incident APIs, workspaces, assessment intake and response workflows. | **Implemented** — external organizational adoption is not proven. |
| TRC-10 | 200+ integrations, 500+ system connectors and a visual integration builder | Lightwork has generic connectors, flow automation and governed adapters. | **Partial** — the official page itself uses multiple marketed counts; Lightwork has not proven equivalent product integration depth, evidence semantics or visual GRC configuration. |
| TRC-11 | Evergreen inventory of assets, processes, vendors, data and risk relationships | Work discovery, security/vendor/privacy registers and entity graph. | **Partial** — local records exist; enterprise discovery completeness is unverified. |
| TRC-12 | Flexible qualitative/quantitative risk aggregation and scoring | Risk matrices, inherent/residual scoring, portfolio risk, KRI/risk registers and explicit evidence/rationales. | **Implemented** — customer methodology calibration remains external. |
| TRC-13 | Translate first-line business information into second-line risk/control identification | Finalized scoping results carry explicit first-line, second-line, control-owner and evidence-owner assignments plus reason-coded applicability. The scope-actions authority deterministically preserves those bindings in second-line control and evidence-task plans and rejects scope/catalog drift. | **Implemented** — the exact local first-line-to-second-line operating path and mounted workbench are focused-test-covered; organization-wide adoption, methodology calibration and live GRC-provider acceptance remain unverified. |
| TRC-14 | Issue and incident workflow from intake through remediation and reporting | Security incidents, risks, exceptions, POA&M, findings, remediation and closure gates. | **Implemented**. |
| TRC-15 | Measurable evidence tasks for non-compliance stakeholders | Evidence requests, assignments, action items and review flows exist. | **Implemented** — usability for broad business users remains unverified. |
| TRC-16 | UI-driven configuration without IT resources | Editable questionnaires, settings and some no-code workflow surfaces exist. | **Partial** — there is no comprehensive no-code GRC configuration studio across objects, rules, collectors and reports. |
| TRC-17 | Employee policy portal with version control, development/review/approval and attestations | Security policy lifecycle, versions, approval, attestations, exceptions and follow-up records. | **Implemented** — local operator surface exists. |
| TRC-18 | Evergreen public/private policy links | `policy_publication_links.py` plus tenant-bound admin and opaque public APIs provide stable public/private paths, immutable approved versions, CAS pointer updates, scheduled changes, one-time private capabilities, rotation/revocation, access evidence and retention. | **Implemented** — the coherent local link lifecycle is focused-test-covered; live edge/CDN delivery, independent customer usability, recovery and scale acceptance remain unverified. |
| TRC-19 | Continuous controls monitoring | The continuous-evidence authority and dashboard-lifespan scheduler preserve exact control/scope/collector authority, normalized observation equality, incomplete/truncated states, retries, dead letters/replay, cancellation, change alerts and immutable audit without writing compliance conclusions or control implementation. | **Partial** — only the local digest-only collector and injected fixtures are accepted; broad external control coverage, continuously operated production monitoring and customer/auditor acceptance remain absent. |
| TRC-20 | IT-risk discovery of vulnerabilities and cyber risk across assets/processes/vendors, monitored over time | Agent-EDR, discovery, asset/vendor registers, security findings and risk lifecycle. | **Partial** — not a general enterprise vulnerability-management/data-ingestion product. |
| TRC-21 | KRI and exposure dashboards across critical assets, providers and processes | Risk/KRI domain packs and evidence-backed security/program reporting. | **Implemented** — production feed breadth and executive acceptance remain external. |
| TRC-22 | Audit engagements, evidence requests, findings and complete audit history | `security_ops.py`, audit binder and signed audit implement governed engagements/workpapers. | **Implemented**. |
| TRC-23 | Certification/readiness outcomes supported by maintained content | Readiness packs and evidence artifacts exist. | **External / unverifiable** — actual certifications, external auditor acceptance and equivalent licensed/current content cannot be created or proven by repository code alone. |
| TRC-24 | AI Evidence Analysis across PDF, JPEG, Excel and text with dynamic validation criteria, summaries, justification score, identified gaps and remediation guidance | `ai_evidence_analysis.py` plus its strict API/workbench provide bounded PDF, JPEG, Excel and text intake, independently attested extraction/analysis adapters, immutable criteria authority, closed cited facts, deterministic summaries and transparent justification components, identified gaps/remediation, provenance, distinct human release, revocation, retention and integrity. Raw bytes/text remain transient. | **Implemented** — the coherent local multimodal analysis product is focused-test-covered. Parser/detector quality, score calibration, production adapter operation, adversarial/customer workflow acceptance and OneTrust preview/GA entitlement remain unverified. |
| TRC-25 | Evidence-task implementations expose an `aiEvidenceAnalysisResult` object | The evidence-analysis API projects a durable, schema-bound analysis record/result with artifact and criteria pins, adapter-output digests, summary, justification score, criterion assessments, gaps, remediation, provenance and review state while excluding raw artifact content. | **Implemented** — a coherent local result object and lifecycle exist; exact OneTrust wire-schema compatibility, cross-product migration and live relying-workflow acceptance have not been established. |

## Third-Party Management

Official sources for this table: [Third-Party Management](https://www.onetrust.com/solutions/third-party-management/),
[Third-Party Risk Management](https://www.onetrust.com/products/third-party-risk-management/),
[Third-Party Risk Exchange](https://www.onetrust.com/products/third-party-risk-exchange/),
and [Third-Party Due Diligence](https://www.onetrust.com/products/third-party-due-diligence/).

| ID | Public OneTrust module/capability | Lightwork evidence | Status and exact boundary |
|---|---|---|---|
| TPM-01 | End-to-end third-party lifecycle: onboarding, assessment, mitigation, monitoring, reporting and offboarding | `third_party_lifecycle.py`, its strict API and operator workbench add tenant-isolated onboarding/active/suspended/offboarding/offboarded state, dependency inventories, independently attested completion, legal holds, access/task blockers, durable callback intent with retry/dead-letter recovery, evidence exports and retention controls alongside the existing assessment, monitoring and reporting paths. | **Implemented** — a coherent local provider-neutral lifecycle path and focused tests now exist; callbacks remain governed intent only, so external deprovisioning, live vendor/provider operation and scale acceptance are not claimed. |
| TPM-02 | Central editable third-party inventory/profile and dashboard | Security/privacy vendor records, assessment workspaces, entity graph and vendor console. | **Implemented** — record completeness still depends on source intake. |
| TPM-03 | Custom and out-of-box assessments with conditional questions | `third_party_lifecycle.py` provides immutable versioned custom assessment templates, independently approved publication, six risk domains, closed conditional operators, required-question derivation, evidence-bound responses, risk rules and strict API/workbench operation. | **Partial** — the coherent custom conditional-assessment engine is focused-test-covered, but Lightwork does not ship a maintained out-of-box commercial assessment catalog and has no live ecosystem acceptance. |
| TPM-04 | Intake screening against risk-rating and compliance databases | The third-party intelligence authority enrolls independently approved, purpose/territory-bound sources and revision-pinned snapshots, then runs evidence-cited screening across eight closed intelligence kinds. | **Partial** — no named risk-rating/compliance database adapter, commercial corpus, credentialed feed or provider acceptance is bundled. |
| TPM-05 | Contextual tiering/triage to determine assessment depth and workflow priority | Vendor assessment scoring, inherent/residual risk, risk trend, routing and domain-specific questionnaires. | **Implemented** — external data enrichment is missing. |
| TPM-06 | AI ingestion of external risk evidence and generated questionnaire responses | `ai_vendor_risk.py` extracts bounded cited answers and reuses approved prior evidence. | **Partial** — implemented for AI-vendor documents only, not a general TPRM evidence ingestion product. |
| TPM-07 | Auto-approve low-risk third parties | Versioned independently approved policies may autoapprove only when all six mandatory sources are fresh, complete and in scope, every hard-stop finding is cleared, and vendor criticality/data access/territory/score remain within explicit limits; decisions support human override and rollback. | **Implemented** — the fail-closed local decision path is focused-test-covered; live feed quality, risk-owner calibration and customer acceptance remain operational validation. |
| TPM-08 | Multi-domain assessment across security, privacy, ethics, compliance and other risks | Security/privacy/AI/vendor frameworks and domain packs exist. | **Partial** — ethics/compliance content breadth and integrated third-party experience are incomplete. |
| TPM-09 | Assign owners and track issues, risks and tasks across internal/external teams | Assessments, ownership, risks, remediation, action items and portal follow-up exist. | **Implemented** — the external collaboration surface is limited. |
| TPM-10 | Integrated vendor communication, questionnaire exchange and follow-up | Tenant-scoped AI vendor portal has invitation, attestation, response, revocation and human-review import. | **Partial** — AI-specific and locally synthetic; no general/live vendor ecosystem acceptance. |
| TPM-11 | Collaborative risk acceptance and accountability | Named-owner rationale/expiry, review, four-eyes controls and signed audit exist. | **Implemented**. |
| TPM-12 | Cyber ratings and breach monitoring through RiskRecon, SecurityScorecard, HackNotice, SupplyWisdom, ISS Corporate Solutions and similar sources | `third_party_intelligence_providers.py` now supplies closed fixed-host, GET-only normalized contract adapters for the RiskRecon, SecurityScorecard and HackNotice concepts plus one deployment-approved generic provider. `third_party_intelligence_runtime.py` binds their explicit complete observations into exact approved cyber-posture/breach source snapshots and the existing monitor/alert/outbox authority, retaining only digests, citations and minimized receipts. | **Partial** — these are injected-transport reference contracts, not accepted vendor-native integrations. No SupplyWisdom or ISS Corporate Solutions contract, bundled HTTP transport, credential, licensed/live ratings or breach feed, operated notification delivery, sandbox interoperability, or provider/customer acceptance is present. |
| TPM-13 | Use external data to inform inherent/residual scoring and trigger notifications/reassessments | The durable feed worker double-reads exact tenant/source/monitor revision and integrity authority around provider I/O, records only complete and currently valid source snapshots, and submits deterministic change observations through the existing cited alert/outbox flow. Existing approved snapshots/findings remain available to the separate explainable scoring authority; the worker does not infer a score, regulatory conclusion or vendor approval. | **Partial** — the local evidence-to-monitor path, retry/recovery and no-change behavior are focused-test-covered, but no external feed was operated and no automated live reassessment, notification outcome, risk-owner calibration or customer acceptance was established. |
| TPM-14 | Continuous third-party performance/risk monitoring and critical-event workflows | Tenant monitors now have a default-off cross-tenant-safe scheduler and durable feed-job ledger with deterministic deduplication, leases/reclaim, bounded retry/backoff, dead-letter/replay, source/monitor drift revocation, minimized receipts, immutable history/audit, retention/integrity checks and tested lost-acknowledgement recovery. The existing monitor remains the sole source of cited alerts and notification outbox work. | **Partial** — scheduler/runtime code and local concurrency/recovery tests exist, but application deployment still requires injected tenant stores, trusted transport and credential resolution; continuous licensed cyber/breach/performance operation, multi-node recovery, portfolio scale and provider/customer acceptance remain unverified. |
| TPM-15 | Dow Jones integration; PEP, sanctions, watchlists, adverse media, anti-slavery, bribery/corruption and EDD reports | The intelligence authority supports PEP/sanctions/watchlist/adverse-media screening, mandatory independent dispositions, and EDD cases/tasks/cited evidence/decisions/escalation. | **Partial** — there is no Dow Jones adapter/content, maintained anti-slavery/bribery legal package, external EDD report feed or live compliance acceptance. |
| TPM-16 | Third-Party Risk Exchange with risk analytics/control gaps on thousands of vendors and multiple external data feeds | Explicit-consent exchange contributions are restricted by reviewed audience/purpose/metric, evidence and lineage; queries are scoped and withdrawal/source revocation remove availability without inventing anonymity. | **Partial** — this is a tenant-local controlled exchange, not a multi-customer vendor corpus, mathematically proven anonymity/privacy system, external ecosystem or scaled intelligence network. |
| TPM-17 | Rules-based mitigation recommendations, workflow triggers and auto-assignment | Versioned lifecycle assessment templates now bind closed risk rules to mitigation recommendations and remediation task codes; evaluation derives reason-coded severity, recommendations, assignees and evidence-bound tasks without executable expressions. | **Implemented** — the configurable local rule/recommendation/assignment path is coherent and focused-test-covered; provider-side task delivery and live operating acceptance remain unverified. |
| TPM-18 | Role-aware dashboards, metrics, audit-ready records and brandable PDF reporting | Vendor/privacy/security dashboards, reports, audit binder and CSV/print exports exist. | **Partial** — no equivalent branded executive PDF product and live portfolio data acceptance. |
| TPM-19 | Due-diligence programs for FCPA, UK Bribery Act, Sapin II and LkSG | General EDD cases now provide governed tasks, assignees, due dates, cited evidence, four-eyes decisions and escalation alongside compliance-domain primitives. | **Partial** — maintained jurisdiction-specific content/program templates for all four laws, legal validation and operating acceptance are absent. |
| TPM-20 | Live vendor intelligence, ecosystem and scale | The local AI-vendor pilot used synthetic consent/documents and explicitly did not enroll live vendors. | **External / unverifiable** — commercial feeds, vendor participation, live documents, customer acceptance and scaled operations require external systems and parties. |
| TPM-21 | Third-Party Risk Agent for intake/inherent-risk acceleration, document summaries and key-finding extraction, plus real-time critical-risk-event monitoring | The packaged `third_party_risk` governance agent joins server-resolved context, closed cited findings/actions, human review and signed execution receipts to the existing lifecycle intake, deterministic risk rules, document extraction, intelligence snapshots and durable monitor/alert scheduler. | **Implemented** — the coherent local packaged-agent path is focused-test-covered. Commercial feeds are not live, the knowledge corpus and production token vault/HSM are not bundled, independently operated adapters and vendor/customer acceptance are absent, and OneTrust describes the source outcome as early access. |

## What must be true before claiming complete parity

A defensible statement that Lightwork has every capability above requires more
than adding matching class names. At minimum, the following evidence is still
required:

1. Complete and accept the consumer UCPM/CMP outcome across real web, mobile,
   and OTT/CTV estates: extend the new capability-gated web Trust Center into
   an accepted branded/native cross-channel experience; add native/device SDKs and scanners,
   authenticated browser-session custody, maintained categorization, full
   TCF/GPP sections, named
   MarTech/AdTech adapters, identity/synchronization and production
   accessibility/recovery/scale operations.
2. Operate the local Data Use Governance compiler, Snowflake/Databricks writes
   and new native audit readers against consenting test tenants; broaden the
   provider contracts and independently accept permissions, source latency and
   retention, delivery-watermark authority, retry/ambiguity, rollback,
   recovery, concurrency, scale and last-mile completeness evidence.
3. Add and accept named structured/unstructured privacy-discovery adapters,
   durable approved-finding delivery, broad PDF/OCR/archive/proprietary parsing,
   secure end-to-end DSR retrieval/deletion and legal-hold integration; operate
   the governed local notice-to-consent publication bridge through accepted
   site/app/native deployment and cross-store recovery;
   populate and continuously operate the
   new regulatory-intelligence authority with a maintained licensed corpus,
   accepted semantic/AI research and live delivery.
4. Expand Tech Risk & Compliance to maintained/licensed framework breadth,
   operate the new scope-action callback against accepted GRC/control systems,
   add pre-architected continuous evidence collectors and a
   complete no-code configuration experience; separately operate the new
   policy-link lifecycle at a production edge and accept usability/recovery/
   scale without treating local API tests as publication acceptance.
5. Operate third-party lifecycle callbacks, conditional assessments and
   offboarding against consenting vendors/providers; connect the new
   intelligence authority to accepted live cyber, breach,
   performance, PEP, sanctions, watchlist and adverse-media feeds; operate EDD,
   delivery and exchange workflows at representative ecosystem scale; and
   obtain vendor/customer/privacy/legal acceptance.
6. Operate the strongest existing AI Governance paths against consenting live
   provider and customer estates, with production recovery, scale, privacy,
   calibration, alerting and relying-party acceptance.
7. Operate the managed sandbox-workspace lifecycle in a hosted control plane
   with consenting source tenants, representative safe configuration from all
   six suites, independently reviewed tenant/data exclusion contracts, and
   recovery, concurrency, scale, usability, audit-export and SLA acceptance.
   Extend it to a true product tenant copy only after defining the additional
   data, secret, identity, provider and rollback authorities; do not infer that
   outcome from the local isolated-workspace tests.
8. Re-run a requirement-by-requirement audit after implementation and require
   authoritative evidence for every row; absence of a detected gap is not proof
   of parity.
9. Operate the local packaged Privacy AI and Third-Party Risk agents and AI
   Evidence Analysis with separately controlled adapters/dispatchers, a durable
   encrypted token vault with accepted HSM/KMS custody, a governed licensed
   knowledge corpus, adversarial evaluation and representative customer users.
   Run Bedrock/Azure/Vertex inventory, observability and enforcement against
   credentialed test tenants from durable canonical production storage; extend
   safe provider writes and accept completeness, recovery and policy effects.
   License/maintain and externally review the Databricks framework content.
10. Operate California DROP with a registered broker and the external
    authority using consenting test subjects, independently validate subject
    matching, deletion/permanent-opt-out status semantics, retry/revocation,
    privacy, legal and regulator boundaries, and customer workflows. Validate
    the local assessment-comment and evidence-result contracts in their real
    relying workflows; local API tests do not prove OneTrust wire compatibility.

Until those conditions are satisfied, the accurate statement is: Lightwork has
substantial locally implemented AI-governance, consent, data-use, privacy,
security and audit mechanics, but it does not yet match the full publicly
marketed OneTrust platform, and it has not proven production superiority across
all six suites.
