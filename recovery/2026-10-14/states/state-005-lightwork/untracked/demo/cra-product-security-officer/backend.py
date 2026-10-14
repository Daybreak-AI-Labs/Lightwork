"""Authority-preserving backend seam for the CRA Officer standalone SKU.

Auto mode may reuse Lightwork's immutable CRA clock declarations.  It never
calls platform stores, audit, evidence, connectors, or filing code: all writes
remain in the unsigned local CAS store.
"""
from __future__ import annotations

import cra_engine as local
from capabilities import (
    CAPABILITY_LABELS,
    CAPS,
    FORCED_STANDALONE,
    PLATFORM_ENGINE,
    STANDALONE,
)
from capabilities import caps_summary as _caps_summary

if PLATFORM_ENGINE:
    from maverick import cra_ops as _cra_ops
else:
    _cra_ops = None


STORE = local.LocalStore()
CLOCK_RULES = (
    tuple(dict(rule) for rule in _cra_ops.CRA_CLOCKS)
    if _cra_ops is not None
    else local.VENDORED_CLOCK_RULES
)


def _binding(implementation: str, authority: str, active: bool = True) -> dict:
    return {"implementation": implementation, "authority": authority, "active": active}


def _integrated_only(path: str) -> dict:
    return _binding(
        f"Not bound in this reduced SKU; use integrated Lightwork {path}",
        "integrated-lightwork-only",
        False,
    )


_CLOCK_ENGINE = (
    "maverick.cra_ops.CRA_CLOCKS declarations (analysis only)"
    if PLATFORM_ENGINE
    else "cra_engine vendored Article 14 declarations"
)

CAPABILITY_BINDINGS = {
    "product_release_inventory": _binding("cra_engine.LocalStore", "unsigned-local"),
    "cyclonedx_spdx_normalization": _binding("cra_engine.normalize_sbom", "analysis-only"),
    "human_applicability_gate": _binding("cra_engine.decide_product_applicability", "unsigned-local-human"),
    "human_reportability_gate": _binding("cra_engine.decide_case_reportability", "unsigned-local-human"),
    "article14_clock_projection": _binding(_CLOCK_ENGINE, "calculation-only"),
    "local_vex_drafts": _binding("cra_engine.create_vex_draft", "unsigned-local"),
    "local_report_drafts": _binding("cra_engine.create_report_draft", "unsigned-local"),
    "packet_digest_approval": _binding("cra_engine.decide_report", "unsigned-local-human"),
    "receipt_digest_binding": _binding("cra_engine.record_submission_receipt", "unsigned-local-user-attestation"),
    "local_cas_store": _binding("cra_engine.LocalStore.save/replace", "unsigned-local"),
    "platform_clock_rule_adapter": _binding(
        _CLOCK_ENGINE if PLATFORM_ENGINE else "Not bound: Lightwork core is unavailable or forced off",
        "analysis-only-no-platform-authority",
        PLATFORM_ENGINE,
    ),
    "automatic_legal_decisions": _binding(
        "Deliberately unsupported; a qualified human decision is required",
        "prohibited",
        False,
    ),
    "regulator_network_filing": _integrated_only("/security/cra"),
    "signed_audit": _integrated_only("/security"),
    "continuous_evidence_graph": _integrated_only("/security/evidence"),
    "evidence_connectors": _integrated_only("/security/connectors"),
    "governed_platform_authority": _integrated_only("/security/cra"),
    "raw_secret_retention": _binding(
        "No dedicated secret fields; allowed text values are not secret-scanned",
        "prohibited",
        False,
    ),
}


def _validate_bindings() -> None:
    if set(CAPABILITY_BINDINGS) != set(CAPS):
        raise RuntimeError("CRA Officer capability binding drift")
    for capability, enabled in CAPS.items():
        binding = CAPABILITY_BINDINGS[capability]
        if binding.get("active") is not enabled:
            raise RuntimeError(f"CRA Officer capability state drift: {capability}")


_validate_bindings()


def caps_summary() -> dict:
    summary = _caps_summary()
    summary["capabilities"] = [
        {
            "id": capability,
            "label": CAPABILITY_LABELS[capability],
            **CAPABILITY_BINDINGS[capability],
        }
        for capability in CAPS
    ]
    summary["platform_authority_used"] = False
    return summary


def current_revision() -> int:
    return STORE.current_revision()


def list_records(kind: str | None = None) -> list[dict]:
    rows = STORE.list_records()
    reports = {
        str(row.get("id")): row
        for row in rows
        if row.get("type") == "report_draft"
    }
    receipts: dict[str, dict] = {}
    for receipt in (
        row for row in rows if row.get("type") == "submission_receipt"
    ):
        report_id = str(receipt.get("report_id") or "")
        if report_id in receipts:
            raise ValueError("duplicate local submission receipt authority")
        report = reports.get(report_id)
        if report is None:
            raise ValueError("local submission receipt report is missing")
        local.validate_submission_receipt(report, receipt)
        receipts[report_id] = receipt

    for case in (row for row in rows if row.get("type") == "cra_case"):
        anchored = case.get("full_notification_submission_receipt")
        if anchored is None:
            continue
        if not isinstance(anchored, dict):
            raise ValueError("stored severe-incident receipt anchor is malformed")
        receipt = receipts.get(str(anchored.get("report_id") or ""))
        if receipt is None:
            raise ValueError("stored severe-incident receipt authority is missing")
        expected = {
            key: receipt[key]
            for key in (
                "type",
                "report_id",
                "case_id",
                "stage",
                "packet_sha256",
                "approved_packet_sha256",
                "approval_decided_at",
                "submitted_at",
            )
        }
        if anchored != expected:
            raise ValueError("stored severe-incident receipt anchor has drifted")
        if case.get("clocks") != local.project_article14_clocks(case, CLOCK_RULES):
            raise ValueError("stored severe-incident clock projection has drifted")
    return [row for row in rows if kind is None or row.get("type") == kind]


def get_record(record_id: str) -> dict | None:
    return STORE.get_record(record_id)


def _record(record_id: str, kind: str, label: str) -> dict:
    if not isinstance(record_id, str) or not record_id or len(record_id) > 160:
        raise ValueError(f"{label} id must contain 1 to 160 characters")
    value = STORE.get_record(record_id)
    if value is None:
        raise LookupError(f"stored {label} not found")
    if value.get("type") != kind:
        raise ValueError(f"record is not a {label}")
    return value


def create_product(name, manufacturer, support_end_date, expected_revision: int) -> dict:
    return STORE.save(
        local.create_product(name, manufacturer, support_end_date), expected_revision
    )


def decide_product(product_id, decision, reviewer, rationale, expected_revision: int) -> dict:
    product = _record(product_id, "product", "product")
    return STORE.replace(
        product_id,
        local.decide_product_applicability(product, decision, reviewer, rationale),
        expected_revision,
    )


def create_release(
    product_id, version, released_at, sbom_document, expected_revision: int
) -> dict:
    product = _record(product_id, "product", "product")
    return STORE.save(
        local.create_release(product, version, released_at, sbom_document),
        expected_revision,
    )


def open_case(
    product_id, release_id, case_type, awareness_at, title, expected_revision: int
) -> dict:
    product = _record(product_id, "product", "product")
    release = _record(release_id, "release", "release")
    return STORE.save(
        local.open_case(
            product, release, case_type, awareness_at, title, CLOCK_RULES
        ),
        expected_revision,
    )


def decide_case(case_id, decision, reviewer, rationale, expected_revision: int) -> dict:
    case = _record(case_id, "cra_case", "CRA case")
    return STORE.replace(
        case_id,
        local.decide_case_reportability(
            case, decision, reviewer, rationale, CLOCK_RULES
        ),
        expected_revision,
    )


def record_case_anchor(
    case_id, anchor, occurred_at, actor, expected_revision: int
) -> dict:
    case = _record(case_id, "cra_case", "CRA case")
    return STORE.replace(
        case_id,
        local.record_case_anchor(case, anchor, occurred_at, actor, CLOCK_RULES),
        expected_revision,
    )


def create_vex(
    product_id,
    release_id,
    vulnerability_id,
    status,
    justification,
    author,
    expected_revision: int,
) -> dict:
    product = _record(product_id, "product", "product")
    release = _record(release_id, "release", "release")
    return STORE.save(
        local.create_vex_draft(
            product, release, vulnerability_id, status, justification, author
        ),
        expected_revision,
    )


def create_report(
    case_id, stage, summary, facts, author, expected_revision: int
) -> dict:
    case = _record(case_id, "cra_case", "CRA case")
    return STORE.save(
        local.create_report_draft(case, stage, summary, facts, author),
        expected_revision,
    )


def decide_report(
    report_id,
    decision,
    reviewer,
    rationale,
    packet_sha256,
    expected_revision: int,
) -> dict:
    report = _record(report_id, "report_draft", "report draft")
    return STORE.replace(
        report_id,
        local.decide_report(
            report, decision, reviewer, rationale, packet_sha256
        ),
        expected_revision,
    )


def record_receipt(
    report_id,
    packet_sha256,
    receipt_reference,
    submitted_at,
    recorded_by,
    expected_revision: int,
) -> dict:
    report = _record(report_id, "report_draft", "report draft")
    if any(
        row.get("report_id") == report_id
        for row in STORE.list_records("submission_receipt")
    ):
        raise ValueError("a submission receipt is already recorded for this report")
    receipt = local.record_submission_receipt(
        report,
        packet_sha256,
        receipt_reference,
        submitted_at,
        recorded_by,
    )
    case = _record(report["case_id"], "cra_case", "CRA case")
    if report.get("stage") == "full_notification" and case.get("case_type") == "severe_incident":
        anchored = local.anchor_case_from_full_notification_receipt(
            case, receipt, CLOCK_RULES
        )
        return STORE.save_with_replacement(
            receipt, case["id"], anchored, expected_revision
        )
    return STORE.save(receipt, expected_revision)


__all__ = [
    "CAPABILITY_BINDINGS",
    "CAPS",
    "FORCED_STANDALONE",
    "PLATFORM_ENGINE",
    "STANDALONE",
    "caps_summary",
    "create_product",
    "create_release",
    "create_report",
    "create_vex",
    "current_revision",
    "decide_case",
    "decide_product",
    "decide_report",
    "get_record",
    "list_records",
    "open_case",
    "record_case_anchor",
    "record_receipt",
]
