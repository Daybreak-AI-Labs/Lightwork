"""Manifest-driven authored corpus, through real bounded vendor extraction."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3] / "scripts/acceptance/fixtures/vendor-documents"
spec = importlib.util.spec_from_file_location("authored_corpus_evaluation", ROOT / "evaluate_document_corpus.py")
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)
MANIFEST = json.loads((ROOT / "document-corpus.json").read_text(encoding="utf-8"))


def test_corpus_names_scope_and_every_promised_selected_format_explicitly():
    assert MANIFEST["authored_noncustomer"] is True
    assert len({case["id"] for case in MANIFEST["cases"]}) == len(MANIFEST["cases"])
    covered = {category for case in MANIFEST["cases"] for category in case["categories"]}
    assert {"word_body", "word_table", "word_header_omission", "word_hidden_omission",
            "excel_hidden_row_omission", "excel_hidden_column_omission", "excel_hidden_sheet_omission",
            "excel_cached_value", "excel_uncached_omission", "pdf_digital_reference", "pdf_digital_reportlab",
            "pdf_multicolumn", "pdf_font_mapping", "pdf_image_only", "pdf_blank", "pdf_rotated_digital",
            "pdf_lowquality_scan", "pdf_malformed", "pdf_oversize", "office_oversize"} <= covered
    assert all(len(case["ground_truth"]) == len(case["baseline"]["found"]) for case in MANIFEST["cases"])


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["id"])
def test_authored_documents_match_declared_text_locations_omissions_and_refusals(case):
    row = corpus.evaluate_case(case)
    assert row["mismatches"] == [], row
    if row["status"] == "available":
        assert row["ground_truth_items_found"] > 0
        assert row["locations_checked"] > 0
        assert row["citations_checked"] > 0


@pytest.mark.parametrize("case", [case for case in MANIFEST["cases"] if "append_spaces_to_bytes" in case], ids=lambda case: case["id"])
def test_corpus_oversize_inputs_are_refused_before_any_untrusted_parser(case, monkeypatch):
    from maverick import vendor_document_worker

    def forbidden(*args, **kwargs):
        pytest.fail("Oversized authored input reached a document parser")

    monkeypatch.setattr(vendor_document_worker, "parse", forbidden)
    row = corpus.evaluate_case(case)
    assert row["status"] == "refused" and not row["mismatches"]


def test_corpus_identity_drift_is_not_silently_accepted():
    changed = copy.deepcopy(MANIFEST["cases"][0])
    changed["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="bytes differ"):
        corpus.materialize(changed)


def test_evaluator_refuses_wrong_location_or_false_coverage_claim():
    word = copy.deepcopy(next(case for case in MANIFEST["cases"] if case["id"] == "word-layout"))
    word["ground_truth"][0]["location"]["paragraph"] = 99
    assert "location" in corpus.evaluate_case(word)["mismatches"]
    scan = copy.deepcopy(next(case for case in MANIFEST["cases"] if case["id"] == "image-only"))
    scan["baseline"]["found"] = [True]
    assert "ground_truth_coverage" in corpus.evaluate_case(scan)["mismatches"]
