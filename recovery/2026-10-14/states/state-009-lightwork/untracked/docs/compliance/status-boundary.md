# Compliance evidence and status boundary

> **Compliance status — source-controlled boundary:** this taxonomy describes
> what repository artifacts can establish. Lightwork is not represented here as
> SOC 2 attested, ISO 27001 or ISO 42001 certified, compliant with a law, or
> operated by a staffed security/compliance organization. Source version `0.1.7`
> is unreleased and there is no supported production release.

Every Markdown document in `docs/compliance/` carries one of these exact status
classes near its title. A status describes the document itself, not a customer,
deployment, organization, product release, or framework conclusion.

| Status class | What it establishes | What remains required |
|---|---|---|
| **Source-controlled draft** | A policy, procedure, mapping, methodology, or roadmap is version controlled and can be reviewed. Repository regressions may verify narrow source behavior cited by the document. | Named organizational approval; ownership and staffing; deployment-specific adoption; operating records; counsel or assessor review where applicable. |
| **Repository mapping — source only** | A crosswalk or applicability table maps framework concepts to source locations as a diligence aid. A label such as `Source present` is only a source-inventory assertion. | Exact-release code review, configuration and deployment evidence, organizational operation, and independent assessment. It does not mean `Implemented` for a framework. |
| **Deployment reference — unqualified** | A checklist, configuration example, or verification script can help an operator collect evidence. | Qualification on the exact immutable release and target environment; retained outputs; owner approval. A passing command is not certification or legal compliance. |
| **Template — unexecuted** | A blank form or example is available for completion. | Named-owner completion and approval, signatures where required, and evidence that the resulting process operated. |
| **Sample register — not authoritative** | Seed entries illustrate fields or known source-level risks/vendors/assets. | A complete, current, deployment- and organization-specific inventory with review records. |
| **Historical local snapshot — non-independent** | A self-authored record describes a local observation on its stated date and revision, if identified. | Current exact-revision evidence, independent review, deployment provenance, retained raw outputs, and correction of any superseded conclusions. It cannot qualify a release. |

The following evidence layers must remain separate:

1. **Source control present** — a file, branch gate, or control implementation
   exists in the checkout.
2. **Repository regression** — a deterministic local or CI check exercises a
   narrow source contract.
3. **Deployment evidence required** — the exact candidate must be exercised in
   its real identity, storage, network, key-custody, backup, restore, and
   observability environment.
4. **Organization process required** — trained people must own, operate, review,
   escalate, notify, retain, and improve the process.
5. **Independent assessment required** — an authorized external assessor,
   auditor, counsel, certification body, or customer must make the applicable
   conclusion.
6. **Not claimed** — certification, legal compliance, control operating
   effectiveness, customer suitability, staffed response, and production
   readiness are not established by this directory.

Words such as *approved*, *operating*, *conforms*, *closed*, or *implemented*
inside a template, historical quotation, desired-state procedure, or framework
label do not override the status banner. Current assurance may be stated only
through a revision-bound claim/evidence ledger entry with the required
deployment, organizational, and independent evidence.
