"""Rendered-page OCR for a small, explicitly reviewed PDF subset.

Only call this module from the bounded vendor document worker. PDFium never
initializes JavaScript or XFA. The pinned local OCR runtime receives PNG pixels,
not the PDF, and neither component receives provider credentials or file paths.
Page images and complete text are transient; their identities survive review.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import math
import tempfile
from pathlib import Path

MAX_PAGES = 8
MAX_PIXELS = 4_000_000
MAX_EDGE = 3200
MAX_PNG_BYTES = 1024 * 1024
MAX_TOTAL_PNG_BYTES = 6 * 1024 * 1024
MAX_TEXT = 120_000
MAX_WORDS = 20_000
PDFIUM_VERSION = "5.13.0"
METHOD = "pdf_rendered_page_ocr"
_HEADERS = ["level", "page_num", "block_num", "par_num", "line_num", "word_num",
            "left", "top", "width", "height", "conf", "text"]
_UNREADABLE = "This PDF could not be reviewed safely. Use a smaller PDF or a readable copy."


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _tsv(content: bytes, width: int, height: int):
    """Validate optical words and preserve the engine's explicit reading order."""
    if len(content) > 2 * 1024 * 1024:
        raise ValueError(_UNREADABLE)
    reader = csv.DictReader(io.StringIO(content.decode("utf-8", errors="strict")), delimiter="\t")
    if reader.fieldnames != _HEADERS:
        raise ValueError(_UNREADABLE)
    lines, scores, last_line, length = [], [], None, 0
    for index, row in enumerate(reader):
        if index > 100_000 or set(row) != set(_HEADERS) or any(value is None for value in row.values()):
            raise ValueError(_UNREADABLE)
        level = int(row["level"])
        if level not in range(1, 6):
            raise ValueError(_UNREADABLE)
        if level != 5:
            continue
        value = row["text"].strip()
        if not value:
            continue
        score = float(row["conf"])
        left, top, box_width, box_height = (int(row[field]) for field in ("left", "top", "width", "height"))
        if (not math.isfinite(score) or not 0 <= score <= 100 or len(scores) >= MAX_WORDS
                or min(left, top, box_width, box_height) < 0
                or left + box_width > width or top + box_height > height
                or any(ord(char) < 32 for char in value)):
            raise ValueError(_UNREADABLE)
        key = tuple(int(row[field]) for field in ("page_num", "block_num", "par_num", "line_num"))
        if key[0] != 1 or min(key) < 1:
            raise ValueError(_UNREADABLE)
        length += len(value) + 1
        if length > MAX_TEXT:
            raise ValueError("PDF text exceeds the review limit. Split it into smaller evidence files.")
        if key == last_line:
            lines[-1] += " " + value
        else:
            lines.append(value)
        last_line = key
        scores.append(score)
    return "\n".join(lines), {
        "word_count": len(scores),
        "confidence_score": round(sum(scores) / len(scores), 2) if scores else None,
        "minimum_confidence": round(min(scores), 2) if scores else None,
    }


def _ocr(png, width, height, runtime):
    from .ai_evidence_local_parsers import _bounded_process

    # The parent worker owns the aggregate 30-second/512-MiB bound. The nested
    # process has a smaller deadline and only a private, already-rendered image.
    with tempfile.TemporaryDirectory(prefix="lightwork-page-ocr-") as directory:
        source = Path(directory) / "page.png"
        source.write_bytes(png)
        result = _bounded_process(
            [str(runtime["executable"]), str(source), "stdout", "--tessdata-dir", str(runtime["tessdata"]),
             "-l", "eng", "--oem", "1", "--psm", "3", "-c", "tessedit_create_tsv=1"],
            input_bytes=b"", timeout_seconds=12, max_stdout_bytes=2 * 1024 * 1024,
            max_stderr_bytes=4096, max_memory_bytes=384 * 1024 * 1024, cwd=directory)
    if result.returncode != 0:
        raise ValueError(_UNREADABLE)
    return _tsv(result.stdout, width, height)


def _render(page):
    size = page.get_size()
    if len(size) != 2 or any(not math.isfinite(value) or value <= 0 for value in size):
        raise ValueError(_UNREADABLE)
    dimensions = [math.ceil(value * 2) for value in size]
    if max(dimensions) > MAX_EDGE or dimensions[0] * dimensions[1] > MAX_PIXELS:
        raise ValueError("This PDF page is too large for local review. Provide a standard-size page copy.")
    bitmap = page.render(scale=2, may_draw_forms=False, draw_annots=True, rev_byteorder=True)
    try:
        with bitmap.to_pil() as image, image.convert("RGB") as pixels:
            width, height = pixels.size
            blank = all(low == high == 255 for low, high in pixels.getextrema())
            output = io.BytesIO()
            pixels.save(output, format="PNG", optimize=False)
    finally:
        bitmap.close()
    png = output.getvalue()
    if len(png) > MAX_PNG_BYTES:
        raise ValueError("This PDF page image exceeds the review limit. Provide a smaller page copy.")
    return png, width, height, blank


def extract(content: bytes):
    """Render every admitted page and OCR visible pixels, including digital PDFs."""
    import pypdfium2 as pdfium

    from .vendor_pdf_runtime import runtime as get_runtime

    if (not isinstance(content, bytes) or not content.startswith(b"%PDF-")
            or len(content) > 16 * 1024 * 1024 or str(pdfium.PYPDFIUM_INFO) != PDFIUM_VERSION
            or pdfium.PDFIUM_INFO.flags):
        raise ValueError(_UNREADABLE)
    runtime = get_runtime()
    manifest = runtime["manifest"]
    previews, identities, spans, chunks, total, offset, unreadable = [], [], [], [], 0, 0, False
    with pdfium.PdfDocument(content) as document:
        if not 1 <= len(document) <= MAX_PAGES:
            raise ValueError("Review PDFs of up to 8 pages. Split this file into smaller evidence files.")
        if document.get_formtype() != 0:
            raise ValueError("Interactive PDF forms need a saved flat PDF copy before review.")
        for index in range(len(document)):
            page = document[index]
            try:
                png, width, height, blank = _render(page)
            finally:
                page.close()
            total += len(png)
            if total > MAX_TOTAL_PNG_BYTES:
                raise ValueError("The combined PDF page images exceed the review limit. Split the file.")
            text, scores = ("", {"word_count": 0, "confidence_score": None, "minimum_confidence": None}) if blank else _ocr(png, width, height, runtime)
            unreadable |= not blank and not text.strip()
            if offset + len(text) > MAX_TEXT:
                raise ValueError("PDF text exceeds the review limit. Split it into smaller evidence files.")
            number = index + 1
            identities.append(dict(page=number, width=width, height=height, png_sha256=_sha(png),
                text_sha256=_sha(text.encode("utf-8")), method="blank_page" if blank else "optical_text", **scores))
            previews.append(dict(page=number, width=width, height=height, png_base64=base64.b64encode(png).decode("ascii"), text=text))
            if text:
                if chunks:
                    offset += 2
                spans.append({"start": offset, "end": offset + len(text), "pages": [number]})
                offset += len(text)
                chunks.append(text)
    limits = [
        "Local English OCR of rendered pages. Recognition confidence is not factual accuracy or independent verification.",
        "Check every page, names, numbers, reading order and omissions against the image before adding evidence.",
        "Handwriting, non-English text, complex tables and poor scans may be misread. Attach a readable copy when needed.",
        "Page images and full text are temporary; retained evidence contains fingerprints and short redacted quotations.",
    ]
    if unreadable:
        limits.append("At least one nonblank page produced no readable words. Transcribe the whole document or provide a readable copy; no partial text can be added.")
    return "" if unreadable else "\n\n".join(chunks), {
        "method": METHOD, "scope": "rendered_pages", "confidence": "untrusted", "review_required": True,
        "page_count": len(previews), "page_spans": [] if unreadable else spans,
        "pages": identities, "page_previews": previews,
        "renderer": {"pypdfium2": str(pdfium.PYPDFIUM_INFO), "pdfium": str(pdfium.PDFIUM_INFO)},
        "ocr": {"engine": "tesseract", "version": manifest["tesseract_version"],
                "language": "eng", "model_sha256": manifest["model_sha256"]},
        "limits": limits,
    }
