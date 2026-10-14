"""Fixed, bounded child protocol for vendor PDF and Office parsing.

This separates parser failures from the serving process; it is not an OS
filesystem/network sandbox. Only the existing non-executing local parsers run
here. No provider credentials, caller configuration, source paths or document
names cross the boundary. Sources and extracted text are never written to disk.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading

from .vendor_office_text import DOCX_MIME, MAX_OFFICE_BYTES, XLSX_MIME

PROTOCOL = "lightwork.vendor-document.v1"
WORKER_FLAG = "--vendor-document-worker"
MAX_PDF_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 10 * 1024 * 1024
MAX_MEMORY_BYTES = 512 * 1024 * 1024
TIMEOUT_SECONDS = 30
_SLOTS = threading.BoundedSemaphore(2)
_MIMES = frozenset({DOCX_MIME, XLSX_MIME, "application/pdf"})
_UNAVAILABLE = "Evidence extraction could not finish safely. Retry with a smaller supported file."
_PUBLIC_ERRORS = frozenset({
    "Office XML declarations are unsupported",
    "Office XML must be valid UTF-8 without external entities",
    "Office document exceeds the 8 MiB upload limit",
    "Office archive exceeds the extraction limits",
    "Office archive contains ambiguous or unsafe paths",
    "Encrypted, active, embedded or oversized Office content is unsupported",
    "Office archive member size changed",
    "Office documents with external links are unsupported; provide a copy without links",
    "Macro-enabled Office documents are unsupported",
    "Office package has no content-type manifest",
    "Office document is damaged or uses unsupported compression",
    "Office text exceeds the review limit; split the document into smaller evidence files",
    "Word document body is missing",
    "Word fields, embedded content and tracked changes require a reviewed plain-text copy",
    "Worksheet cells exceed limits or have ambiguous locations",
    "Worksheet shared-string reference is invalid",
    "Workbook structure is missing",
    "Workbook relationships are ambiguous",
    "Workbook must contain between 1 and 32 sheets",
    "Workbook sheet relationship is unsupported",
})


def _limit(mime):
    if mime not in _MIMES:
        raise ValueError("Unsupported evidence extraction format")
    return MAX_PDF_BYTES if mime == "application/pdf" else MAX_OFFICE_BYTES


def _command(mime, page_review=False):
    _limit(mime)
    extra = ["--page-review"] if page_review else []
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_FLAG, mime, *extra]
    # -I excludes the caller's directory, PYTHONPATH and user site-packages.
    # The module is installed in the same runtime as the serving application.
    return [sys.executable, "-I", "-m", "maverick.vendor_document_worker", mime, *extra]


def _integer(value, lower, upper):
    return type(value) is int and lower <= value <= upper


def _validate_spans(spans, text, mime, page_count):
    if not isinstance(spans, list) or len(spans) > 100_000:
        raise ValueError(_UNAVAILABLE)
    end = 0
    for span in spans:
        if (not isinstance(span, dict) or not _integer(span.get("start"), end, len(text))
                or not _integer(span.get("end"), span["start"], len(text))):
            raise ValueError(_UNAVAILABLE)
        end = span["end"]
        if mime == "application/pdf":
            pages = span.get("pages")
            valid = (set(span) == {"start", "end", "pages"} and isinstance(pages, list)
                     and 1 <= len(pages) <= page_count
                     and all(_integer(page, 1, page_count) for page in pages))
        elif mime == DOCX_MIME:
            valid = set(span) == {"start", "end", "paragraph"} and _integer(span.get("paragraph"), 1, 100_000)
        else:
            import re
            valid = (set(span) == {"start", "end", "sheet", "cell"}
                     and isinstance(span.get("sheet"), str) and len(span["sheet"]) <= 255
                     and isinstance(span.get("cell"), str) and re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", span["cell"]))
        if not valid:
            raise ValueError(_UNAVAILABLE)


def _validate_result(payload, content, mime, page_review=False):
    if (not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL
            or payload.get("mime") != mime or payload.get("source_sha256") != hashlib.sha256(content).hexdigest()):
        raise ValueError(_UNAVAILABLE)
    if set(payload) == {"protocol", "mime", "source_sha256", "error"}:
        message = payload["error"]
        raise ValueError(message if isinstance(message, str) and message in _PUBLIC_ERRORS else _UNAVAILABLE)
    if set(payload) != {"protocol", "mime", "source_sha256", "text", "metadata"}:
        raise ValueError(_UNAVAILABLE)
    text, meta = payload["text"], payload["metadata"]
    if not isinstance(text, str) or len(text) > 2 * 1024 * 1024 or not isinstance(meta, dict) or meta.get("review_required") is not True:
        raise ValueError(_UNAVAILABLE)
    if page_review:
        from .vendor_pdf_protocol import validate
        validate(text, meta)
    elif mime == "application/pdf":
        base = {"method", "scope", "confidence", "review_required"}
        complete = base | {"referenced_streams", "page_spans", "page_count"}
        if (set(meta) not in (base, complete) or meta.get("method") != "pdf_page_content_streams"
                or meta.get("scope") != "page_referenced" or meta.get("confidence") != "untrusted"
                or (text and set(meta) != complete)):
            raise ValueError(_UNAVAILABLE)
        if set(meta) == complete:
            if not _integer(meta["page_count"], 1, 4096) or not _integer(meta["referenced_streams"], 0, 4096):
                raise ValueError(_UNAVAILABLE)
            _validate_spans(meta["page_spans"], text, mime, meta["page_count"])
    else:
        expected = ("word_body_text", "body_paragraphs_and_tables") if mime == DOCX_MIME else ("excel_visible_cell_text", "visible_worksheet_cells")
        if (set(meta) != {"method", "scope", "confidence", "review_required", "location_spans", "cached_formula_values", "limits"}
                or (meta.get("method"), meta.get("scope")) != expected or meta.get("confidence") != "best_effort"
                or type(meta.get("cached_formula_values")) is not bool
                or not isinstance(meta.get("limits"), list) or len(meta["limits"]) > 10
                or any(not isinstance(item, str) or len(item) > 1024 for item in meta["limits"])):
            raise ValueError(_UNAVAILABLE)
        _validate_spans(meta["location_spans"], text, mime, 0)
    return text, meta


def parse(content: bytes, mime: str, *, page_review=False):
    """Run the fixed parser once; never fall back to in-process parsing."""
    if not isinstance(content, bytes) or len(content) > _limit(mime):
        raise ValueError("Document exceeds the supported extraction byte limit")
    if type(page_review) is not bool or page_review and mime != "application/pdf":
        raise ValueError("PDF page review requires an explicit PDF-only mode")
    if not _SLOTS.acquire(blocking=False):
        raise ValueError("Evidence extraction is busy. Wait for the current previews, then retry.")
    try:
        from .ai_evidence_local_parsers import _bounded_process

        with tempfile.TemporaryDirectory(prefix="lightwork-vendor-parser-") as directory:
            result = _bounded_process(_command(mime, True) if page_review else _command(mime), input_bytes=content,
                timeout_seconds=TIMEOUT_SECONDS, max_stdout_bytes=MAX_OUTPUT_BYTES,
                max_stderr_bytes=4096, max_memory_bytes=MAX_MEMORY_BYTES, cwd=directory)
        if result.returncode != 0 or len(result.stdout) > MAX_OUTPUT_BYTES:
            raise ValueError(_UNAVAILABLE)
        payload = json.loads(result.stdout.decode("utf-8"))
    except Exception as exc:
        # No parser traceback, source bytes, stderr or deployment paths reach UI.
        raise ValueError(_UNAVAILABLE) from exc
    finally:
        _SLOTS.release()
    return _validate_result(payload, content, mime, page_review)


def main(argv=None):
    """Worker input is raw bytes on stdin; output is one bounded JSON envelope."""
    args = sys.argv[1:] if argv is None else argv
    page_review = len(args) == 2 and args == ["application/pdf", "--page-review"]
    if (len(args) != 1 and not page_review) or args[0] not in _MIMES:
        return 2
    mime = args[0]
    try:
        content = sys.stdin.buffer.read(_limit(mime) + 1)
        if len(content) > _limit(mime):
            return 2
        payload = {"protocol": PROTOCOL, "mime": mime, "source_sha256": hashlib.sha256(content).hexdigest()}
        try:
            if page_review:
                from .vendor_pdf_review import extract
                text, metadata = extract(content)
            elif mime == "application/pdf":
                from .privacy_ops import _pdf_extraction
                text, metadata = _pdf_extraction(content)
            else:
                from .vendor_office_text import extract_office
                text, metadata = extract_office(content, mime)
            payload.update(text=text, metadata=metadata)
        except ValueError as exc:
            payload["error"] = str(exc) if str(exc) in _PUBLIC_ERRORS else _UNAVAILABLE
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_OUTPUT_BYTES:
            return 2
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
