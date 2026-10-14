"""Evaluate the SHA-pinned authored corpus through current vendor extraction.

The profile describes supported extraction and explicit omissions/refusals. A
passing baseline is not an OCR accuracy or representative-document claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).parent


def materialize(case):
    path = ROOT / case["file"]
    if path.parent != ROOT or path.suffix not in {".pdf", ".docx", ".xlsx"}:
        raise ValueError("Unexpected authored fixture path")
    raw = path.read_bytes()
    if len(raw) != case["bytes"] or hashlib.sha256(raw).hexdigest() != case["sha256"]:
        raise ValueError("Authored corpus bytes differ from their manifest")
    if "append_spaces_to_bytes" in case:
        size = case["append_spaces_to_bytes"]
        if type(size) is not int or not len(raw) <= size <= 16 * 1024 * 1024 + 1:
            raise ValueError("Unexpected virtual oversized fixture")
        raw += b" " * (size - len(raw))
        if hashlib.sha256(raw).hexdigest() != case["derived_sha256"]:
            raise ValueError("Derived fixture bytes differ from their manifest")
    return raw


def evaluate_case(case):
    from maverick.paths import tenant_scope
    from maverick.vendor_document_worker import parse
    from maverick.vendor_onboarding_adapters import EvidenceDocument
    from maverick.vendor_onboarding_documents import extract
    content = materialize(case)
    expected = case["baseline"]
    preview, result, error = {}, None, None
    started = time.monotonic()
    try:
        with tenant_scope(tenant="authored-document-corpus"):
            result = extract(EvidenceDocument(case["id"], "authored-v1", 100, content, case["mime"]),
                source="upload", name=case["file"], now=200, freshness_days=30, preview=preview)
    except ValueError as exc:
        error = str(exc)
    text = preview.get("text", "")
    status = "refused" if error else result["status"]
    mismatches = []
    if status != expected["status"]:
        mismatches.append("status")
    if error and expected.get("error", "NOT EXPECTED") not in error:
        mismatches.append("refusal_reason")
    if not error:
        metadata = result["extraction"]
        if (metadata.get("method") != expected.get("method") or metadata.get("review_required") is not True
                or result["content_sha256"] != hashlib.sha256(content).hexdigest()):
            mismatches.append("source_or_extraction_metadata")
        for name in ("cached_formula_values", "page_count"):
            if name in expected and metadata.get(name) != expected[name]:
                mismatches.append(name)
    found = [item["text"] in text for item in case["ground_truth"]]
    if found != expected["found"]:
        mismatches.append("ground_truth_coverage")
    if any(value in text for value in case["excluded"]):
        mismatches.append("excluded_content")
    spans = preview.get("locations", [])
    if status == "available" and case["mime"] == "application/pdf":
        parsed, metadata = parse(content, case["mime"])
        if parsed != text:
            mismatches.append("worker_preview_disagreement")
        spans = metadata.get("page_spans", [])
    locations_checked = 0
    for item, present in zip(case["ground_truth"], found, strict=True):
        if not present or not ("location" in item or "pages" in item):
            continue
        start = text.index(item["text"])
        selected = [span for span in spans if span["end"] > start and span["start"] < start + len(item["text"])]
        locations = [{key: value for key, value in span.items() if key not in {"start", "end"}} for span in selected]
        if "location" in item and item["location"] not in locations:
            mismatches.append("location")
        if "pages" in item and sorted({page for span in selected for page in span.get("pages", [])}) != item["pages"]:
            mismatches.append("page_location")
        locations_checked += 1
    for excluded in case.get("excluded_locations", []):
        if any(all(span.get(key) == value for key, value in excluded.items()) for span in spans):
            mismatches.append("excluded_location")
    citations, invalid_citation = check_citations(result, text, content, case["id"])
    if invalid_citation:
        mismatches.append("citation_provenance")
    return {"id": case["id"], "file": case["file"], "categories": case["categories"],
            "source_sha256": hashlib.sha256(content).hexdigest(), "status": status,
            "expected_status": expected["status"], "method": (result or {}).get("extraction", {}).get("method"),
            "ground_truth_items": len(found), "ground_truth_items_found": sum(found),
            "locations_checked": locations_checked, "citations_checked": citations,
            "excluded_items": len(case["excluded"]), "mismatches": sorted(set(mismatches)),
            "elapsed_seconds": round(time.monotonic() - started, 6), "error": error}


def check_citations(result, text, content, identifier):
    citations, invalid = 0, False
    for answer in ((result or {}).get("extracted") or {}).get("answers", {}).values():
        for citation in answer["citations"]:
            citations += 1
            if (citation["document_sha256"] != hashlib.sha256(content).hexdigest()
                    or citation["source_version"] != "authored-v1" or citation["document_id"] != identifier
                    or citation["quote"] not in text):
                invalid = True
    return citations, invalid


def evaluate():
    manifest = json.loads((ROOT / "document-corpus.json").read_text(encoding="utf-8"))
    rows = [evaluate_case(case) for case in manifest["cases"]]
    return {"schema": "lightwork.authored-document-evaluation.v1", "profile": manifest["profile"],
            "authored_noncustomer": True, "ocr_accuracy_tested": False, "rendered_layout_tested": False,
            "representative_accuracy_claim": False, "manifest_sha256": hashlib.sha256((ROOT / "document-corpus.json").read_bytes()).hexdigest(),
            "all_declared_expectations_met": all(not row["mismatches"] for row in rows),
            "cases": rows, "limits": manifest["limits"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate()
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
    return 0 if result["all_declared_expectations_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
