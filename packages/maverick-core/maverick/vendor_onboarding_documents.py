"""Bounded vendor evidence extraction; source bytes and full text stay transient."""
from __future__ import annotations

import hashlib
import math
import re

from . import ai_vendor_risk as risk
from .privacy_ops import _document_text
from .vendor_onboarding_adapters import GOOGLE_DOC_MIME, MAX_PDF_BYTES, MAX_TEXT_BYTES

MAX_DOCUMENTS = 8
MAX_TOTAL_BYTES = 32 * 1024 * 1024


def extract(document, *, source: str, name: str, now: float, freshness_days: int) -> dict:
    """Retain only provenance, parser limits and bounded redacted quotations."""
    if (not document.version or not math.isfinite(document.modified_at)
            or document.modified_at <= 0 or document.modified_at > now):
        raise ValueError("document date or version is invalid")
    mime = document.export_mime or document.mime
    if document.mime == GOOGLE_DOC_MIME and mime != "text/plain":
        raise ValueError("Google Docs requires the version-bracketed text export")
    if mime not in {"text/plain", "application/pdf"}:
        raise ValueError("unsupported document format")
    limit = MAX_PDF_BYTES if mime == "application/pdf" else MAX_TEXT_BYTES
    if len(document.content) > limit:
        raise ValueError("document exceeds the supported byte limit")
    if mime == "text/plain":
        document.content.decode("utf-8", errors="strict")
    metadata = {}
    text = _document_text(document.content, mime, _metadata=metadata)
    result = None
    status = "empty" if not text.strip() and mime == "text/plain" else "extraction_failed"
    if text.strip():
        result = risk.extract_answers_from_document(
            document.content, mime, source=source, document_id=document.document_id,
            ref={"version": document.version, "source_mime": document.mime},
        )
        if result["truncated"]:
            # A complete-looking assessment must not omit the uninspected tail.
            status = "extraction_limit"
        else:
            status = "available"
        for answer in result["answers"].values():
            for citation in answer["citations"]:
                citation.update(source=source, document_id=document.document_id,
                                source_version=document.version, document_name=name)
                if mime == "application/pdf":
                    pages = sorted({page for span in metadata.get("page_spans", [])
                                    if span["end"] > citation["start"] and span["start"] < citation["end"]
                                    for page in span["pages"]})
                    if not pages:
                        raise ValueError("PDF quotation has no verified page location")
                    citation["pages"] = pages
                else:
                    citation["paragraph"] = 1 + len(re.findall(r"\n\s*\n", text[:citation["start"]]))
    return dict(document.identity(), source=source, name=name, export_mime=document.export_mime,
                status=status, byte_length=len(document.content), collected_at=now,
                content_sha256=hashlib.sha256(document.content).hexdigest(), extracted=result,
                fresh_until=document.modified_at + freshness_days * 86400,
                date_basis="uploader_asserted_document_date" if source == "upload" else "provider_modified_time",
                extraction={key: value for key, value in metadata.items() if key != "page_spans"})


def document_diff(previous: dict | None, current: dict) -> dict:
    """Compare retained evidence versions and cited suggestions, never invent text."""
    def docs(evidence):
        if not evidence:
            return []
        return evidence.get("documents") or [evidence]

    old = {(item.get("source", "gdrive"), item.get("document_id")): item for item in docs(previous)}
    new = {(item.get("source", "gdrive"), item.get("document_id")): item for item in docs(current)}
    changes = []
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key), new.get(key)
        state = "added" if before is None else "removed" if after is None else "unchanged"
        if before and after and any(before.get(field) != after.get(field) for field in ("version", "content_sha256", "status")):
            state = "changed"
        answers = []
        before_answers = ((before or {}).get("extracted") or {}).get("answers", {})
        after_answers = ((after or {}).get("extracted") or {}).get("answers", {})
        for question in sorted(before_answers.keys() | after_answers.keys()):
            left, right = before_answers.get(question, {}), after_answers.get(question, {})
            if left.get("answer") != right.get("answer") or [c["quote"] for c in left.get("citations", [])] != [c["quote"] for c in right.get("citations", [])]:
                answers.append({"question_id": question, "before": left.get("answer", "unknown"),
                                "after": right.get("answer", "unknown"),
                                "before_citations": left.get("citations", []), "after_citations": right.get("citations", [])})
        changes.append({"source": key[0], "document_id": key[1], "change": state,
                        "before_version": (before or {}).get("version"), "after_version": (after or {}).get("version"),
                        "answer_changes": answers})
    return {"comparison": "previous_review" if previous else "initial_evidence", "documents": changes,
            "review_required": True}
