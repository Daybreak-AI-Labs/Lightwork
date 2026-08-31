# OneTrust vs. Lightwork: Code and Capability Gap Analysis

Date checked: 2026-08-28

Repository: `Daybreak-AI-Labs/Lightwork`

Build branch: `claude/aigov-gap-buildout`

## Bottom line

OneTrust's source code is not public, so a source-to-source comparison is not
possible. This report compares OneTrust's current public product claims with
code and automated evidence in this Lightwork checkout. Those OneTrust claims
were not independently tested here.

OneTrust still has the stronger proven enterprise product envelope: deployed
discovery, broad live integrations, implementation history, customer-scale
operations, curated regulatory content, and an established privacy/GRC/data
governance ecosystem. Lightwork now implements most of the corresponding
control-plane mechanics locally, often with stricter evidence and execution
bindings, but it has not proven parity in live customer environments.

## Public OneTrust baseline used

- [AI Discovery and Registry](https://www.onetrust.com/solutions/ai-governance/ai-discovery-and-registry/)
  describes centralized inventory and discovery of systems, models, datasets,
  agents, vendors, owners, dependencies, and shadow or embedded AI.
- [AI Policy Management](https://www.onetrust.com/solutions/ai-governance/ai-policy-management/)
  describes operational policies, applicability, control requirements,
  exceptions, approvals, and revalidation.
- [Dynamic Risk Scoring](https://www.onetrust.com/solutions/ai-governance/dymanic-risk-scoring/)
  describes risk that changes with telemetry and business/regulatory context,
  with explanations for score changes.
- [AI Governance in Production](https://www.onetrust.com/ai-governance-in-production/)
  and the [runtime governance demo](https://www.onetrust.com/resources/runtime-ai-governance-demo-video/)
  describe monitoring and allow, block, redact, or restrict enforcement.
- [EU AI Act Compliance](https://www.onetrust.com/solutions/eu-ai-act-compliance/)
  describes assessments, classification, evidence workflow, and compliance
  documentation.
- [AI Governance at Enterprise Scale](https://www.onetrust.com/why-onetrust/ai-governance-at-enterprise-scale/)
  describes enterprise workflow, reporting, and integration with privacy,
  risk, and data-governance operations.
- OneTrust publishes a broad [integration catalog](https://www.onetrust.com/integrations/)
  and a specific [ServiceNow integration](https://www.onetrust.com/integrations/servicenow/).

## Status definitions

- **Implemented-local**: working code with focused automated validation in this
  checkout. This is not live-production or customer acceptance.
- **Partial**: meaningful code exists, but the deployed or end-to-end product
  path is incomplete or unverified.
- **Missing**: no coherent Lightwork implementation was found.
- **External**: code alone cannot satisfy the result.

## Capability comparison

| Capability | Lightwork evidence on this branch | Status and remaining boundary |
|---|---|---|
| Central inventory and lineage | `model_risk_assurance.py`, `ai_inventory.py`, `agent_topology.py`, inventory scheduler, assurance APIs/UI | **Implemented-local control plane; partial product.** Completeness across a real enterprise is unverified. |
| Native registry discovery | Ten bounded adapters: Bedrock, Azure AI Foundry, Vertex AI, Databricks, Copilot Studio, MLflow, SageMaker, Snowflake Cortex, Agentforce, and Hugging Face | **Implemented-local adapters; partial live capability.** No provider tenant was used for acceptance. |
| Shadow/embedded AI discovery | Six governed sensor classes, tenant-scoped Ed25519 ingress, reference normalizers/client, and deployable consent-gated JSONL network-egress, browser-extension and OAuth agents with private normalized-only spools | **Implemented-local three-agent path; partial deployed fleet.** A consenting local estate passed key lifecycle, privacy, recovery, concurrency and 1,000-event scale while declaring incomplete coverage. Production source adapters, the other three sensor classes and enterprise acceptance remain unproven. |
| Identity/data governance discovery | Read-only Entra/Okta/Purview connectors and scheduler plus `ai_identity_governance_controls.py`: replay-safe change fanout, pseudonymous agent-principal federation through an independently injected enrolled subject-binding resolver, capability mapping, fresh conditional-access decisions and Purview CAS writeback/compensation | **Implemented-local, separately default-off controls; partial live capability.** No provider test tenant was available, so scheduler operation, subject enrollment, federation policy, conditional access and writeback remain live-unverified. |
| Business intake | `ai_business_intake.py`, API, and operator UI create/link inventory, proposed declarations, and review work | **Implemented-local.** Fresh-customer usability and timing acceptance remain unverified. |
| Framework and control packs | Signed/versioned NIST AI RMF and EU AI Act starter mappings, semantic diffs, subscriptions, exact attestations, re-attestation UI | **Implemented-local bounded starters; partial library.** Not licensed standards text, legal advice, certification, or OneTrust-equivalent content breadth. |
| Operational policy management | `ai_policy_management.py`: immutable versions, exact applicability, strictest-wins decisions, human activation, four-eyes expiring exceptions, evaluation and revalidation | **Implemented-local core/API/UI.** Live customer policy migration and operating acceptance are external. |
| Dynamic risk | `ai_dynamic_risk.py`: exact-asset normalized signals, freshness, CAS lifecycle, context floors, attributable score/tier changes, exclusions, and history | **Implemented-local core/API/UI.** Production calibration and broad live telemetry remain unverified. |
| Production drift/quality monitoring | Aggregate-only PSI/KL, prediction, embedding, and performance drift with evidence/finding emission | **Implemented-local engines; partial deployment.** No live observability estate or alert-delivery acceptance. |
| Runtime controls | Runtime guardrails plus an executable authenticated gateway pilot covering allow/deny, preview/enforced no-change, approval, exact retry, response-loss recovery, indeterminate after-effect outage and approved compensation | **Implemented and operated against an isolated local gateway estate; partial live coverage.** No external/customer gateway credential was available, so third-party, Copilot, agent and MCP acceptance remains unverified. |
| Agent/MCP governance | Signed delegation, agent trust/topology, capability attenuation/revocation, blast radius, containment, signed audit, MCP inventory | **Implemented-local and comparatively strong.** Live enterprise identity federation and cross-product adoption remain partial. |
| Explainability | `ai_explainability.py` admits closed native/SHAP/LIME/Captum/Alibi/counterfactual receipts with exact model binding and independent review | **Implemented-local core/API/UI.** Provider adapters and adverse-action/legal acceptance are not proven. |
| Model documentation and BOM/lineage | Model cards, deployment lineage, evidence graph, exact artifact/evaluator bindings, assurance packets, SBOM/provenance machinery | **Partial.** Strong primitives, but no single mature workspace proven against OneTrust's complete product experience. |
| Vendor AI risk | Portal/diligence product plus a consent-gated local pilot using two representative synthetic documents, exact questionnaire/document/citation contracts, drift probes, exact retry/same-process replay, revocation, tenant isolation and pending-human-review import | **Implemented and operated locally with synthetic vendors/documents; partial ecosystem.** Crash/restart recovery, live consenting vendors, real documents and ecosystem acceptance remain unverified. |
| Regulatory change | Versioned feed engine plus `ai_regulatory_feed.py` impact bridge, review, stale-evidence resolution, and notices | **Implemented-local bridge; partial live service.** Licensed/current AI-regulation operations, counsel validation, and delivered notices are external. |
| EU incident workflow | `eu_ai_incidents.py`: Article 73 triage, 2/10/15-day clocks, human authority routing, four-eyes approval, export, and external-receipt recording | **Implemented-local workflow.** No filing transmitter, live authority directory, legal calendar, or legal determination. |
| EU conformity readiness | `ai_conformity_workflow.py`: FRIA, Annex IV, Article 43 route, post-market, declaration and CE-readiness checklist/evidence workflow | **Implemented-local readiness workflow.** It does not make legal determinations, certify, sign declarations, or affix a CE mark. |
| GRC writeback | Semantic, idempotent ServiceNow IRM and Archer preview/execute/receipt/conflict/event paths; provider-pilot mutations require an exact reviewed-origin allowlist rather than hostname/environment inference | **Implemented-local adapters; partial live capability.** Vendor sandbox acceptance and broader targets remain external. |
| Portfolio/executive reporting | Transparent portfolio formula, quarterly immutable snapshots, trend/delta, contributor drill-through, API/UI | **Implemented-local engineering.** Production data quality and CRO/customer acceptance are unverified. |
| AI literacy | Role requirements, pseudonymous assignments, completion receipts, waivers, gaps, evidence export, API/UI | **Implemented-local.** HRIS/LMS contracts and customer acceptance are external. |
| AI cost and ROI | Governed AWS, Azure, GCP, and OpenAI organization-cost collectors; scheduler; exact citations; allocation/corrections; assumption-labelled ROI; API/UI | **Implemented-local adapters.** Credentials and live billing tenants were not used; model-level OpenAI cost attribution is explicitly unsupported by the Costs contract. |
| Red-team assurance | Governed schedules/leases/dispatcher, bounded suites, commitment-only response storage, evidence and promotion gate | **Implemented-local core/dispatch.** No live enrolled-agent end-to-end or comprehensive legal bias audit. |
| Media provenance | Concrete `c2patool` adapter, real-binary acceptance runner, digest-only receipts, transient media and `C2PAToolExternalSigner` KMS/HSM subprocess boundary | **Development interoperability passed on separately installed `c2patool` 0.27.16.** The public fixture is not production custody; production key ceremonies, certificate/trust ecosystem and relying-party acceptance remain deployment work. |
| Benchmark exchange | Client/trust plane plus a restartable aggregate-only coordinator, exact packet admission after two-human evidence review and a digest-bound trust-root rotation ceremony | **Implemented and operated locally; external network missing.** The current protocol explicitly does not claim anonymity or differential privacy and treats a privacy budget as not applicable; no live participant or cross-tenant acceptance exists. |
| Certification and market discovery (G21) | Internal AIMS, statement-of-applicability, audit and evidence primitives can support an application | **External.** ISO 42001 certification, analyst review, and directory/profile listings require independent organizations and were not obtained. |
| Insurance evidence | `ai_insurability.py`: exact-evidence profile versions, measurements, limitations, verifier guide, four-eyes export, API/UI | **Implemented-local packet.** Coverage, quote, pricing impact, and insurer acceptance are external. |
| Certification-body channel (G23) | Evidence shapes can be exported for review | **External.** No accredited certification-body relationship or jointly accepted evidence format was established. |
| Systems-integrator channel (G24) | Deployment and governance artifacts exist | **External.** No named-partner commercial/support/certification program or active SI relationship was established. |
| Regulated vertical packages | `ai_regulated_verticals.py`: credit reasons, aggregate fair-lending intervals, NYC LL144 readiness, NAIC mapping, governed package lifecycle | **Implemented-local decision support.** No individual decision, legal conclusion, audit, filing, certification, or regulator acceptance. |
| Bank model risk | `ai_bank_mrm.py`: nine closed review sections, explicit missing/unverified states, exact evidence, package-integrity four-eyes approval, API/UI | **Implemented-local package.** It does not approve a model or establish bank/regulator acceptance. |
| Enterprise scale/integration maturity | Tenant-scoped governed stores, Postgres option, CAS, bounded reads, signed audit, schedulers, APIs, adapters | **Partial.** OneTrust retains the observable live-integration, deployment, support, and customer-history advantage. |

## What OneTrust still has that Lightwork does not

1. **Broad deployed discovery collectors.** Lightwork now has deployable
   network-egress, browser-event and OAuth-event consumers and a local consented
   test estate, but production source adapters plus SaaS-posture,
   identity-directory and data-catalog daemons are still absent. Enterprise
   completeness, privacy and scale remain unproven.
2. **Live-provider acceptance.** The registry, identity, cost, GRC, runtime,
   paths are tested locally or with injected transports, not against production
   customer/vendor tenants. C2PA real-binary development-fixture
   interoperability is the exception; it is still not production custody.
3. **Continuous operations.** Several schedulers exist, but cross-product alert
   operations, calibrated thresholds, on-call workflow, recovery, and scale
   have not been proven in a customer deployment.
4. **Curated content breadth.** Lightwork intentionally ships bounded starter
   mappings rather than a deep maintained library of regulatory and standards
   content.
5. **Accepted external collaboration ecosystems.** A bounded vendor-response
   portal now exists in code, but has no live-vendor or ecosystem acceptance;
   insurer, regulator, certification-body and implementation-partner
   collaboration remains incomplete or external.
6. **Integration breadth and product maturity.** OneTrust markets a much larger
   integration ecosystem and has customer/implementation history that this
   repository cannot reproduce with code alone.

## Where Lightwork is differentiated

- Governance can execute inside the agent/runtime action path rather than only
  around inventory and questionnaires.
- Signed delegation, capability attenuation, containment, and agent/MCP blast
  radius are first-class.
- Exact asset/artifact/evaluator/evidence bindings and promotion interlocks are
  unusually explicit.
- Self-hosted/local execution and digest-only/raw-transient paths are designed
  into the platform.
- Cost, red-team, runtime, evidence, and self-improvement controls share one
  governed execution substrate.

These are engineering differentiators, not proof of OneTrust parity or market
readiness.

## Recommended next external-acceptance sequence

The corresponding repository code and local/synthetic pilots now exist. The
next sequence is deployment and independent acceptance, not another claim that
local tests are live proof:

1. Deploy the new network-egress, browser-extension and OAuth agents in a
   consenting external estate; validate DPIA/privacy decisions, measured source
   coverage, key lifecycle, recovery and production-volume limits. Build the
   remaining SaaS, directory and catalog agents.
2. Repeat the completed local runtime pilot against a consenting real external
   gateway, including retry, outage, rollback, no-change and operator sign-off.
3. Provide test credentials and run the fail-closed provider harness for Hugging
   Face, Entra, ServiceNow and OpenAI Costs after independently reviewing the
   exact ServiceNow write-origin allowlist. The current readiness artifact is
   blocked for all four and performed no provider I/O.
4. Operate the completed identity scheduler/control plane against approved
   Entra or Okta and Purview tenants; review provider-specific federation,
   conditional-access and mutation semantics before enabling writeback.
5. Move the successful `c2patool` development interop behind production
   certificate/HSM/KMS custody, rotation/revocation, timestamping, recovery and
   relying-party acceptance.
6. Pilot with consenting external vendors and representative documents, and
   enroll live benchmark contributors only after independent inclusion,
   anonymity, differential-privacy and privacy-budget review.
7. Repeat the five-suite automated platform runner in deployment-like
   infrastructure and add moderated human usability/customer acceptance.
