# Procurement material scope

> **Draft template only:** source `0.1.7` is unreleased, there is no supported
> production release, and this page creates no offer, warranty, SLA,
> certification, or legal commitment.

This register prevents repository templates from being mistaken for completed
customer paperwork.

| Topic | Repository material | Current status before customer use |
|---|---|---|
| License / EULA | Root `LICENSE`; `commercial-agreement-checklist.md` | The source license requires a separate written agreement. No form customer EULA or commercial terms are approved by this repository. |
| Privacy / DPA | `privacy-notice-template.md`; `dpa-template.md` | Templates only. Complete controller/processor roles, purposes, data categories, legal bases, rights, contacts, jurisdictions, and transfer terms with counsel. |
| Subprocessors | `subprocessors.md` | Deployment-specific blank register. Inventory the exact model, identity, hosting, telemetry, messaging, storage, and support recipients before approval. |
| Acceptable use | `../../compliance/templates/acceptable-use-policy.md` | Organization template, not accepted customer terms or evidence that personnel acknowledged it. |
| Retention, deletion, and export | DPA template; data-protection policy; deployment playbook | Policy/source controls only. Set approved periods and legal holds, map every store, and prove export/erasure/backup expiry on the exact deployment. The current production materials explicitly retain uncovered/external-store limitations. |
| Third-party notices | Root `THIRD-PARTY-NOTICES.md`; release SBOM workflow | The checked-in notice is a declared direct-dependency aid, not an authoritative transitive notice set. Regenerate from the exact artifact and reproduce required license/notice text. |
| Accessibility | `accessibility-statement-template.md`; accessibility audit recipe | No conformance claim. Run an exact-browser/manual/assistive-technology assessment against the exact release and record exceptions and remediation. |
| Security and compliance | `SECURITY.md`; compliance mappings and templates | Control-design and source evidence only. No SOC 2, ISO, HIPAA, regulatory, penetration-test, or legal-compliance attestation is established. |
| Support / SLA | `sla-template.md`; production support matrix | Blank targets only. Staffing, hours, contacts, escalation, service levels, exclusions, credits, and dependency responsibility require separate approval and live evidence. |

## Approval record required

Before providing a procurement pack externally, retain:

- the exact release tag, source revision, image and artifact digests, and signed
  production governance packet;
- a deployment-specific data/subprocessor/retention/export/deletion map;
- counsel approval of the commercial agreement, DPA, privacy notice, AUP,
  transfers, notices, and applicable-law statements;
- security approval of current external assessments and open exceptions;
- accessibility approval tied to exact browsers and assistive technologies;
- support approval for contacts, staffing, hours, targets, escalation, and
  customer responsibilities; and
- a claim-ledger review proving that every included statement is current,
  scoped, owned, and refresh-dated.

Missing approval leaves the corresponding item a template; it must not be
represented as complete.
