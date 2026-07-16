"""Seed the Privacy + Finance workspaces with ~14 months of realistic history.

Run it against the demo home (same MAVERICK_HOME the demo servers use):

    # PowerShell
    $env:MAVERICK_HOME = "$PWD\\.demo-home"; python seed_workspace.py
    # bash
    MAVERICK_HOME=.demo-home python3 seed_workspace.py

Everything goes through the real engines (assessment scoring, DPA clause
review, AI Act classifier, RoPA drafting, DSAR machinery); only timestamps
are backdated afterwards so the workspaces look lived-in: approved records
with review cadences (some overdue), items awaiting review or answers,
stale work, a spread of risk levels, and the four privacy record types.

Idempotent-ish: a marker file stops accidental double-seeding; pass --force
to add another batch anyway.
"""
from __future__ import annotations

import os
import sys
import time

DAY = 86400
NOW = time.time()


def _backdate_assessment(aid: str, *, created_days_ago: float,
                         decided_days_ago: float | None = None,
                         cadence_days: int | None = None) -> None:
    from maverick.assessment import _rewrite_saved

    def _mut(rec: dict) -> None:
        rec["created_at"] = NOW - created_days_ago * DAY
        for f in rec.get("followups") or []:
            f["asked_at"] = rec["created_at"] + 2 * DAY
        if decided_days_ago is not None:
            rec["decided_at"] = NOW - decided_days_ago * DAY
            if cadence_days:
                rec["next_review_at"] = rec["decided_at"] + cadence_days * DAY

    _rewrite_saved(aid, _mut)


def _assessment(type_, subject, answers, *, days_ago, decide=None,
                cadence=365, decided_days_ago=None, followups=None,
                answer_followups=False):
    from maverick import assessment as A
    s = A.AssessmentSession(type=type_, subject=subject)
    qs = {q.id: q for q in A.get_template(type_).questions}
    for qid, (ans, note) in answers.items():
        if qid in qs:
            s.record(qid, ans, note)
    A.save_session(s)
    if followups:
        A.add_followups(s.id, followups, asked_by="privacy-lead")
        if answer_followups:
            rec = A.load_saved(s.id)
            for f in rec.get("followups", []):
                A.answer_followup(s.id, f["id"], "Confirmed — see ticket.",
                                  answered_by="requester")
    if decide:
        A.decide_assessment(s.id, decide, decided_by="privacy-lead",
                            cadence_days=cadence)
    _backdate_assessment(s.id, created_days_ago=days_ago,
                         decided_days_ago=decided_days_ago,
                         cadence_days=cadence if decide == "approved" else None)
    return s.id


GOOD_DPA = """Data Processing Agreement. The processor shall process personal
data only on documented instructions from the controller. Personnel are bound
by confidentiality. The processor implements the technical and organisational
measures of Article 32 including encryption at rest. Sub-processors require
prior written authorization. The processor shall assist the controller with
data subject requests and shall notify the controller of any personal data
breach without undue delay. Upon termination the processor shall delete or
return all personal data. The controller has audit and inspection rights.
Transfers rely on Standard Contractual Clauses. Data is retained for 12
months per the retention schedule."""

THIN_MSA = """Master services agreement. Vendor provides marketing analytics
over customer personal data, including transfer to processing centers outside
the EU. Payment terms are net 30. Either party may terminate with notice."""

ONETRUST_CSV = (
    '"Processing Activity Name","Purpose of Processing",'
    '"Personal Data Categories","Categories of Data Subjects",'
    '"Cross-Border Transfers","Retention Period"\n'
    '"CRM marketing","Campaign targeting and analytics",'
    '"Contact details, usage history","Customers, prospects",'
    '"SCCs to US vendors","24 months"\n'
    '"Payroll","Salary processing and statutory filings",'
    '"Bank details, salary, tax ids","Employees","None","7 years"\n'
    '"Recruiting pipeline","Candidate evaluation",'
    '"CVs, interview notes","Job applicants","None","12 months"\n')


def seed() -> None:
    from maverick import privacy_ops

    # ---- Privacy assessments: a spread of ages, statuses, risk levels ----
    _assessment("pia", "Acme CRM rollout",
                {"pia_transfers": ("yes", "us-east-1; SCCs pending legal"),
                 "pia_retention": ("no", ""),
                 "pia_security": ("yes", "AES-256 at rest, TLS 1.3")},
                days_ago=2)
    _assessment("pia", "Marketing email platform",
                {"pia_necessity": ("yes", ""), "pia_lawful_basis": ("yes", ""),
                 "pia_transparency": ("no", "privacy notice not yet updated"),
                 "pia_retention": ("yes", ""), "pia_security": ("yes", "")},
                days_ago=12)  # stale: sat >7d unreviewed
    _assessment("pia", "HR onboarding portal",
                {"pia_special_category": ("yes", "health data for benefits"),
                 "pia_security": ("unknown", "")},
                days_ago=6,
                followups=["Which benefits provider receives health data?",
                           "Is the health data segregated from the HRIS?"])
    _assessment("pia", "Customer support ticketing",
                {"pia_necessity": ("yes", ""), "pia_lawful_basis": ("yes", ""),
                 "pia_transparency": ("yes", ""), "pia_retention": ("yes", ""),
                 "pia_security": ("yes", "")},
                days_ago=400, decide="approved", cadence=365,
                decided_days_ago=395)  # ~1 month overdue for re-review
    _assessment("pia", "Data warehouse consolidation",
                {"pia_transfers": ("yes", "EU-hosted, adequacy"),
                 "pia_retention": ("yes", ""), "pia_security": ("yes", "")},
                days_ago=200, decide="approved", cadence=365,
                decided_days_ago=190)  # due in ~5.5 months
    _assessment("aira", "Support triage copilot",
                {"aira_purpose": ("yes", "conversational assistant that "
                                         "drafts replies to tickets"),
                 "aira_prohibited": ("no", ""), "aira_high_risk": ("no", ""),
                 "aira_transparency": ("yes", ""),
                 "aira_oversight": ("yes", "agent approves every reply")},
                days_ago=90, decide="approved", cadence=180,
                decided_days_ago=85)
    _assessment("aira", "CV screening pilot",
                {"aira_purpose": ("yes", "ranks job candidates for hiring"),
                 "aira_high_risk": ("yes", "employment - Annex III"),
                 "aira_bias": ("unknown", ""),
                 "aira_oversight": ("no", "")},
                days_ago=30,
                followups=["Has the vendor shared bias-evaluation results "
                           "across protected groups?"])
    _assessment("vendor_risk", "Globex support tooling",
                {"vr_soc2": ("yes", "SOC 2 Type II current"),
                 "vr_dpa": ("yes", ""), "vr_encryption": ("yes", ""),
                 "vr_breach_history": ("no", "")},
                days_ago=320, decide="approved", cadence=365,
                decided_days_ago=310)  # due in ~2 months
    _assessment("vendor_risk", "Initech analytics",
                {"vr_dpa": ("no", "MSA only, no DPA countersigned"),
                 "vr_soc2": ("no", ""),
                 "vr_breach_history": ("yes", "2024 credential-stuffing "
                                              "incident disclosed")},
                days_ago=45, decide="rejected", cadence=0,
                decided_days_ago=40)
    _assessment("hipaa", "Benefits portal PHI flows",
                {"hipaa_risk_analysis": ("yes", ""),
                 "hipaa_access_control": ("yes", ""),
                 "hipaa_encryption": ("yes", ""),
                 "hipaa_baa": ("yes", "BAA on file with carrier"),
                 "hipaa_training": ("yes", "")},
                days_ago=150, decide="approved", cadence=365,
                decided_days_ago=140)
    _assessment("soc2", "Platform trust readiness",
                {"soc2_access": ("yes", ""),
                 "soc2_encryption": ("yes", ""),
                 "soc2_risk": ("unknown", "risk assessment refresh "
                                          "scheduled next quarter")},
                days_ago=4)
    _assessment("pci_dss", "Checkout tokenization review",
                {"pci_segmentation": ("yes", ""),
                 "pci_stored_pan": ("yes", "PANs tokenized, none stored"),
                 "pci_transit": ("yes", ""), "pci_malware": ("yes", "")},
                days_ago=75, decide="approved", cadence=90,
                decided_days_ago=70)  # due in ~20 days

    # ---- Finance assessments: same chassis, its own department ----
    _assessment("sox_control", "Q3 revenue recognition controls",
                {"sox_evidence": ("no", "walkthrough evidence incomplete")},
                days_ago=9)  # stale
    _assessment("itgc", "ERP access management",
                {"itgc_access_least_priv": ("yes", ""),
                 "itgc_access_review": ("yes", ""),
                 "itgc_change_mgmt": ("yes", "")},
                days_ago=270, decide="approved", cadence=365,
                decided_days_ago=260)
    _assessment("fraud_risk", "AP vendor master process",
                {"fraud_vendor_create_approve": ("no", "same analyst creates "
                                                       "and approves")},
                days_ago=20,
                followups=["When will creation/approval be segregated?"])
    _assessment("close_readiness", "June close",
                {"close_bs_recon": ("yes", ""),
                 "close_bank_rec": ("yes", ""),
                 "close_accruals": ("no", "estimate process manual"),
                 "close_flux": ("yes", "")},
                days_ago=16)

    # ---- Privacy records: DPA reviews, AI registry, RoPA, DSARs ----
    privacy_ops.review_dpa("Acme Corp", GOOD_DPA,
                           document_name="acme-dpa-2025.pdf")
    privacy_ops.review_dpa("Initech LLC", THIN_MSA,
                           document_name="initech-msa.docx")
    privacy_ops.review_dpa(
        "Globex GmbH",
        GOOD_DPA.replace("Standard Contractual Clauses", "a mechanism to be "
                                                          "agreed"),
        document_name="globex-dpa-draft.docx")

    privacy_ops.register_ai_system(
        "Support triage copilot", "conversational assistant that drafts "
        "replies to customer tickets", provider="Anthropic", owner="support")
    privacy_ops.register_ai_system(
        "CV screener", "ranks job candidates for hiring",
        provider="internal", owner="talent-ops")
    privacy_ops.register_ai_system(
        "Invoice-coding copilot", "suggests GL codes for incoming invoices; "
        "human approves every posting", provider="internal", owner="finance")
    privacy_ops.register_ai_system(
        "Churn model", "generates renewal-risk scores for account teams",
        provider="internal", owner="revops")

    privacy_ops.import_onetrust_ropa(ONETRUST_CSV)
    privacy_ops.upsert_ropa({
        "activity": "Support ticket processing",
        "purpose": "Customer support and quality assurance",
        "controller": "Company Ltd",
        "data_categories": "Contact details, ticket content",
        "data_subjects": "Customers",
        "recipients": "Ticketing processor under Art. 28 DPA",
        "transfers": "None",
        "retention": "36 months",
        "security_measures": "Encryption in transit and at rest",
    })

    r1 = privacy_ops.open_dsar("jordan.ellis@example.com", "access",
                               channel="email")
    privacy_ops.fulfill_dsar(r1["id"])
    privacy_ops.close_dsar(r1["id"], closed_by="privacy-lead")
    r2 = privacy_ops.open_dsar("sam.rivera@example.com", "erasure",
                               channel="slack")
    privacy_ops.fulfill_dsar(r2["id"])  # awaiting the operator erase step
    privacy_ops.open_dsar("alex.chen@example.com", "access", channel="email")
    r4 = privacy_ops.open_dsar("pat.kumar@example.com", "portability",
                               channel="email")
    # Backdate one open request past its statutory clock (overdue).
    privacy_ops._DSAR.update(r4["id"], lambda rec: rec.update(
        created_at=NOW - 40 * DAY, due_at=NOW - 10 * DAY))

    # A decided assessment feeding both registers (provenance on show).
    from maverick.assessment import list_saved
    aira_ids = [s["id"] for s in list_saved() if s["type"] == "aira"]
    if aira_ids:
        privacy_ops.register_ai_system_from_assessment(aira_ids[-1])
    pia_ids = [s["id"] for s in list_saved() if s["type"] == "pia"]
    if pia_ids:
        privacy_ops.draft_ropa_from_assessment(pia_ids[0])

    print("Seeded: 16 assessments (privacy + finance, mixed statuses and "
          "risk), 3 DPA reviews, 5 AI systems, 6 RoPA entries, 4 DSARs "
          "(one overdue, one awaiting erasure).")


def main() -> None:
    home = os.environ.get("MAVERICK_HOME", "")
    if not home:
        print("Set MAVERICK_HOME to the demo home first "
              "(e.g. demo/pia-concierge/.demo-home).")
        raise SystemExit(2)
    from pathlib import Path
    marker = Path(home) / ".workspace-seeded"
    if marker.exists() and "--force" not in sys.argv:
        print(f"Already seeded ({marker}). Pass --force to add another "
              "batch, or delete the demo home for a clean slate.")
        raise SystemExit(0)
    seed()
    marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


if __name__ == "__main__":
    main()
