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
| Shadow/embedded AI discovery | `ai_discovery_sensors.py` governs browser-extension, network-egress, OAuth-grant, SaaS-posture, identity-directory, and data-catalog sensor identities and normalized observations | **Implemented-local control plane; deployed sensors missing.** It neither ships those six sensor agents nor claims discovery completeness. |
| Identity/data governance discovery | `ai_identity_governance_connectors.py` plus pinned runtime transport: bounded Entra identities/grants, Okta apps/assignments, and Purview classification posture | **Implemented-local read-only adapters; partial live capability.** This is not Entra Agent ID/Okta agent-principal federation, conditional-access enforcement, or Purview writeback; live tenant acceptance and continuous collection are unverified. |
| Business intake | `ai_business_intake.py`, API, and operator UI create/link inventory, proposed declarations, and review work | **Implemented-local.** Fresh-customer usability and timing acceptance remain unverified. |
| Framework and control packs | Signed/versioned NIST AI RMF and EU AI Act starter mappings, semantic diffs, subscriptions, exact attestations, re-attestation UI | **Implemented-local bounded starters; partial library.** Not licensed standards text, legal advice, certification, or OneTrust-equivalent content breadth. |
| Operational policy management | `ai_policy_management.py`: immutable versions, exact applicability, strictest-wins decisions, human activation, four-eyes expiring exceptions, evaluation and revalidation | **Implemented-local core/API/UI.** Live customer policy migration and operating acceptance are external. |
| Dynamic risk | `ai_dynamic_risk.py`: exact-asset normalized signals, freshness, CAS lifecycle, context floors, attributable score/tier changes, exclusions, and history | **Implemented-local core/API/UI.** Production calibration and broad live telemetry remain unverified. |
| Production drift/quality monitoring | Aggregate-only PSI/KL, prediction, embedding, and performance drift with evidence/finding emission | **Implemented-local engines; partial deployment.** No live observability estate or alert-delivery acceptance. |
| Runtime controls | `ai_runtime_guardrails.py`, authenticated external evaluate/execute routes, policy/Shield/PII/secret composition, digest-only receipts, exact approved asset binding, dynamic-risk coupling | **Implemented-local enforcement path; partial live coverage.** Broad third-party gateways, Copilot, agent, and MCP traffic acceptance remain unverified. |
| Agent/MCP governance | Signed delegation, agent trust/topology, capability attenuation/revocation, blast radius, containment, signed audit, MCP inventory | **Implemented-local and comparatively strong.** Live enterprise identity federation and cross-product adoption remain partial. |
| Explainability | `ai_explainability.py` admits closed native/SHAP/LIME/Captum/Alibi/counterfactual receipts with exact model binding and independent review | **Implemented-local core/API/UI.** Provider adapters and adverse-action/legal acceptance are not proven. |
| Model documentation and BOM/lineage | Model cards, deployment lineage, evidence graph, exact artifact/evaluator bindings, assurance packets, SBOM/provenance machinery | **Partial.** Strong primitives, but no single mature workspace proven against OneTrust's complete product experience. |
| Vendor AI risk | `ai_vendor_risk.py`: AI diligence domains, bounded cited extraction, prior-answer precedence, independent review, risk projection, API/UI | **Implemented-local core/product surface; partial ecosystem.** No external vendor portal or live document/vendor acceptance. |
| Regulatory change | Versioned feed engine plus `ai_regulatory_feed.py` impact bridge, review, stale-evidence resolution, and notices | **Implemented-local bridge; partial live service.** Licensed/current AI-regulation operations, counsel validation, and delivered notices are external. |
| EU incident workflow | `eu_ai_incidents.py`: Article 73 triage, 2/10/15-day clocks, human authority routing, four-eyes approval, export, and external-receipt recording | **Implemented-local workflow.** No filing transmitter, live authority directory, legal calendar, or legal determination. |
| EU conformity readiness | `ai_conformity_workflow.py`: FRIA, Annex IV, Article 43 route, post-market, declaration and CE-readiness checklist/evidence workflow | **Implemented-local readiness workflow.** It does not make legal determinations, certify, sign declarations, or affix a CE mark. |
| GRC writeback | Semantic, idempotent ServiceNow IRM and Archer preview/execute/receipt/conflict/event paths | **Implemented-local adapters; partial live capability.** Vendor sandbox acceptance and broader targets remain external. |
| Portfolio/executive reporting | Transparent portfolio formula, quarterly immutable snapshots, trend/delta, contributor drill-through, API/UI | **Implemented-local engineering.** Production data quality and CRO/customer acceptance are unverified. |
| AI literacy | Role requirements, pseudonymous assignments, completion receipts, waivers, gaps, evidence export, API/UI | **Implemented-local.** HRIS/LMS contracts and customer acceptance are external. |
| AI cost and ROI | Governed AWS, Azure, GCP, and OpenAI organization-cost collectors; scheduler; exact citations; allocation/corrections; assumption-labelled ROI; API/UI | **Implemented-local adapters.** Credentials and live billing tenants were not used; model-level OpenAI cost attribution is explicitly unsupported by the Costs contract. |
| Red-team assurance | Governed schedules/leases/dispatcher, bounded suites, commitment-only response storage, evidence and promotion gate | **Implemented-local core/dispatch.** No live enrolled-agent end-to-end or comprehensive legal bias audit. |
| Media provenance | `ai_media_provenance.py`: typed C2PA verifier/signer adapter interfaces, digest-only receipts, exact asset/deployment/context bindings, transient media, four-eyes release, API/UI | **Implemented-local adapter boundary; partial live capability.** The stock runtime reports unsupported because no C2PA implementation/key custody ships by default, and no live adapter was accepted. |
| Benchmark exchange | `ai_benchmark_exchange.py`: voluntary aggregate contribution packages, four-eyes export, pseudonymous identifiers, fail-closed cohort-report admission through an injected signature verifier, k>=5 and explicit DP parameters | **Implemented-local control plane; external network missing.** The stock service has no verifier/coordinator/transport, cross-tenant pooling, anonymity proof, or privacy-budget accounting. |
| Certification and market discovery (G21) | Internal AIMS, statement-of-applicability, audit and evidence primitives can support an application | **External.** ISO 42001 certification, analyst review, and directory/profile listings require independent organizations and were not obtained. |
| Insurance evidence | `ai_insurability.py`: exact-evidence profile versions, measurements, limitations, verifier guide, four-eyes export, API/UI | **Implemented-local packet.** Coverage, quote, pricing impact, and insurer acceptance are external. |
| Certification-body channel (G23) | Evidence shapes can be exported for review | **External.** No accredited certification-body relationship or jointly accepted evidence format was established. |
| Systems-integrator channel (G24) | Deployment and governance artifacts exist | **External.** No named-partner commercial/support/certification program or active SI relationship was established. |
| Regulated vertical packages | `ai_regulated_verticals.py`: credit reasons, aggregate fair-lending intervals, NYC LL144 readiness, NAIC mapping, governed package lifecycle | **Implemented-local decision support.** No individual decision, legal conclusion, audit, filing, certification, or regulator acceptance. |
| Bank model risk | `ai_bank_mrm.py`: nine closed review sections, explicit missing/unverified states, exact evidence, package-integrity four-eyes approval, API/UI | **Implemented-local package.** It does not approve a model or establish bank/regulator acceptance. |
| Enterprise scale/integration maturity | Tenant-scoped governed stores, Postgres option, CAS, bounded reads, signed audit, schedulers, APIs, adapters | **Partial.** OneTrust retains the observable live-integration, deployment, support, and customer-history advantage. |

## What OneTrust still has that Lightwork does not

1. **Deployed discovery collectors.** Lightwork now has a governed six-sensor
   contract, but not the actual browser extension, proxy/network sensor,
   OAuth/SaaS crawler, identity-directory daemon, or data-catalog daemon. Its
   current observation route is human-principal authenticated rather than a
   dedicated machine-sensor ingress.
2. **Live-provider acceptance.** The registry, identity, cost, GRC, runtime,
   and C2PA paths are tested locally or with injected transports, not against
   production customer/vendor tenants.
3. **Continuous operations.** Several schedulers exist, but cross-product alert
   operations, calibrated thresholds, on-call workflow, recovery, and scale
   have not been proven in a customer deployment.
4. **Curated content breadth.** Lightwork intentionally ships bounded starter
   mappings rather than a deep maintained library of regulatory and standards
   content.
5. **External collaboration portals.** Vendor, insurer, regulator,
   certification-body, and implementation-partner collaboration is incomplete
   or external.
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

## Recommended next build/acceptance sequence

1. Implement and deploy one network/egress sensor and one browser/OAuth sensor
   against a consenting test estate, with privacy review and completeness
   limits documented.
2. Validate one full runtime policy path against an external model/agent
   gateway, including retry, outage, rollback, and no-change acceptance.
3. Run live sandbox acceptance for one inventory provider, Entra or Okta,
   ServiceNow or Archer, OpenAI Costs, and a real C2PA adapter.
4. Add retained snapshot/evidence scheduling for identity/discovery collectors
   and connect normalized changes to dynamic risk and alerts.
5. Run fresh-tenant usability, cross-tenant adversarial, recovery, scale, and
   audit-export acceptance before making product-readiness claims.
