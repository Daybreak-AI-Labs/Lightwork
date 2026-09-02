"""Contract tests for closed framework-clause evidence bundles."""
from __future__ import annotations

import hashlib
import json

import pytest
from maverick import assurance_evidence as evidence
from maverick import regulatory_compiler


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _catalog() -> dict[str, dict]:
    return {
        item["id"]: item
        for item in regulatory_compiler.describe(["eu_ai_act", "iso_42001", "soc2"])[
            "obligations"
        ]
    }


def _row(obligation_id: str, *, disposition: str = "require_human") -> dict[str, str]:
    item = _catalog()[obligation_id]
    return {
        "audit_row_sha256": _digest(obligation_id),
        "obligation_id": obligation_id,
        "regulation": item["regulation"],
        "article": item["article"],
        "disposition": disposition,
    }


def _kwargs(*, rows=None) -> dict:
    return {
        "receipt_refs": [
            {"receipt_ref": "receipt:governance-001", "receipt_sha256": _digest("receipt")}
        ],
        "obligation_rows": rows
        if rows is not None
        else [
            _row("eu-aia-art14-human-oversight"),
            _row("iso-42001-a9-2-human-oversight"),
            _row("soc2-cc6-1-least-privilege-egress", disposition="blocked"),
        ],
        "verification_reports": [
            {
                "verification_report_ref": "verification:receipt-001",
                "verification_report_sha256": _digest("verification"),
            }
        ],
    }


def test_bundle_contains_each_requested_clause_and_preserves_row_provenance():
    bundle = evidence.build_framework_clause_evidence_bundle(
        ["soc2", "eu_ai_act", "iso_42001"], **_kwargs()
    )

    assert bundle["schema"] == evidence.EVIDENCE_BUNDLE_SCHEMA
    assert bundle["profiles"] == ["eu_ai_act", "iso_42001", "soc2"]
    assert bundle["notice"] == evidence.OPERATIONAL_MAPPING_NOTICE
    assert bundle["bundle_sha256"].startswith("sha256:")
    clauses = {item["clause_id"]: item for item in bundle["clauses"]}
    assert set(clauses) == {
        "eu_ai_act_art_12",
        "eu_ai_act_art_14",
        "iso_42001_a6_2_8",
        "soc2_cc7_2",
        "soc2_cc8_1",
    }
    assert clauses["eu_ai_act_art_14"]["direct_compiler_obligation_ids"] == [
        "eu-aia-art14-human-oversight"
    ]
    # Art. 12 does not get relabelled from the Art. 14 runtime control.
    assert clauses["eu_ai_act_art_12"]["direct_compiler_obligation_ids"] == []
    assert clauses["eu_ai_act_art_12"]["runtime_obligation_rows"][0]["article"] == "Art. 14"
    assert clauses["soc2_cc7_2"]["runtime_obligation_rows"][0]["article"] == "CC6.1"
    assert all(item["source"]["url"].startswith("https://") for item in clauses.values())
    assert "raw_prompt" not in json.dumps(bundle)


def test_bundle_is_deterministic_for_equivalent_input_ordering():
    first = evidence.build_framework_clause_evidence_bundle(
        ["soc2", "eu_ai_act", "iso_42001"], **_kwargs()
    )
    reversed_kwargs = _kwargs()
    reversed_kwargs["receipt_refs"].reverse()
    reversed_kwargs["obligation_rows"].reverse()
    reversed_kwargs["verification_reports"].reverse()
    second = evidence.build_framework_clause_evidence_bundle(
        ["iso_42001", "soc2", "eu_ai_act"], **reversed_kwargs
    )

    assert first == second
    rendered = evidence.render_framework_clause_evidence_bundle(
        ["eu_ai_act", "iso_42001", "soc2"], **_kwargs()
    )
    assert rendered.endswith("\n")
    assert json.loads(rendered) == first


def test_bundle_rejects_raw_content_and_mismatched_obligation_provenance():
    raw_row = {
        **_row("eu-aia-art14-human-oversight"),
        "raw_prompt": "never export this",
    }
    with pytest.raises(evidence.AssuranceEvidenceError, match="closed metadata"):
        evidence.build_framework_clause_evidence_bundle(
            ["eu_ai_act"], **_kwargs(rows=[raw_row])
        )

    mismatched = _row("eu-aia-art14-human-oversight")
    mismatched["article"] = "Art. 12"
    with pytest.raises(evidence.AssuranceEvidenceError, match="provenance"):
        evidence.build_framework_clause_evidence_bundle(
            ["eu_ai_act"], **_kwargs(rows=[mismatched])
        )


@pytest.mark.parametrize("profiles", [[], ["not-real"], "soc2"])
def test_bundle_rejects_empty_or_unrecognized_profile_selections(profiles):
    with pytest.raises(evidence.AssuranceEvidenceError):
        evidence.build_framework_clause_evidence_bundle(profiles, **_kwargs())
