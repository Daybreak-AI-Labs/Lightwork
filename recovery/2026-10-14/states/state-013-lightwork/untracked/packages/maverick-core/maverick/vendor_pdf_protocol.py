"""Validate rendered-page evidence before any worker output enters a case."""
from __future__ import annotations

import base64
import hashlib
import math
import re
import struct

ERROR = "The PDF page preview could not be verified. Review the file again or use a readable copy."


def _require(condition):
    if not condition:
        raise ValueError(ERROR)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _score(value):
    return value is None or type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def _page(page, identity, number):
    _require(isinstance(page, dict) and set(page) == {"page", "width", "height", "png_base64", "text"})
    _require(isinstance(identity, dict) and set(identity) == {"page", "width", "height", "png_sha256", "text_sha256", "method", "word_count", "confidence_score", "minimum_confidence"})
    _require(type(page["page"]) is int and page["page"] == number and type(identity["page"]) is int and identity["page"] == number)
    _require(_integer(page["width"], 1, 3200) and _integer(page["height"], 1, 3200) and page["width"] * page["height"] <= 4_000_000)
    _require(type(identity["width"]) is int and type(identity["height"]) is int and (page["width"], page["height"]) == (identity["width"], identity["height"]))
    _require(isinstance(page["text"], str) and len(page["text"]) <= 120_000 and _hash(identity["text_sha256"]))
    _require(hashlib.sha256(page["text"].encode("utf-8")).hexdigest() == identity["text_sha256"])
    _require(identity["method"] in {"optical_text", "blank_page"} and _integer(identity["word_count"], 0, 20_000))
    average, minimum = identity["confidence_score"], identity["minimum_confidence"]
    _require(_score(average) and _score(minimum) and (average is None) == (minimum is None) == (identity["word_count"] == 0))
    _require(average is None or minimum <= average)
    _require(identity["method"] != "blank_page" or (not page["text"] and identity["word_count"] == 0))
    value = page["png_base64"]
    _require(isinstance(value, str) and len(value) <= 1_398_104 and _hash(identity["png_sha256"]))
    try:
        png = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(ERROR) from exc
    _require(24 <= len(png) <= 1024 * 1024 and png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR")
    _require(struct.unpack(">II", png[16:24]) == (page["width"], page["height"]))
    _require(hashlib.sha256(png).hexdigest() == identity["png_sha256"])
    return len(png)


def validate(text, metadata):
    """Accept a closed, hash-bound representation, never partial OCR as complete."""
    required = {"method", "scope", "confidence", "review_required", "page_count", "page_spans", "pages", "page_previews", "renderer", "ocr", "limits"}
    _require(isinstance(text, str) and len(text) <= 120_000 and isinstance(metadata, dict) and set(metadata) == required)
    _require(metadata["method"] == "pdf_rendered_page_ocr" and metadata["scope"] == "rendered_pages" and metadata["confidence"] == "untrusted" and metadata["review_required"] is True)
    count = metadata["page_count"]
    _require(_integer(count, 1, 8) and isinstance(metadata["pages"], list) and isinstance(metadata["page_previews"], list))
    _require(len(metadata["pages"]) == len(metadata["page_previews"]) == count)
    renderer, ocr = metadata["renderer"], metadata["ocr"]
    _require(isinstance(renderer, dict) and set(renderer) == {"pypdfium2", "pdfium"})
    _require(all(isinstance(value, str) and 0 < len(value) <= 128 for value in renderer.values()))
    _require(isinstance(ocr, dict) and set(ocr) == {"engine", "version", "language", "model_sha256"})
    _require(ocr["engine"] == "tesseract" and ocr["language"] == "eng" and _hash(ocr["model_sha256"]) and isinstance(ocr["version"], str) and 0 < len(ocr["version"]) <= 128)
    _require(isinstance(metadata["limits"], list) and len(metadata["limits"]) <= 10 and all(isinstance(value, str) and len(value) <= 1024 for value in metadata["limits"]))
    total, offset, unreadable, chunks, spans = 0, 0, False, [], []
    for number, (page, identity) in enumerate(zip(metadata["page_previews"], metadata["pages"], strict=True), 1):
        total += _page(page, identity, number)
        _require(total <= 6 * 1024 * 1024)
        unreadable |= identity["method"] == "optical_text" and not page["text"].strip()
        if page["text"]:
            offset += 2 if chunks else 0
            spans.append({"start": offset, "end": offset + len(page["text"]), "pages": [number]})
            offset += len(page["text"])
            chunks.append(page["text"])
    _require(text == ("" if unreadable else "\n\n".join(chunks)))
    _require(metadata["page_spans"] == ([] if unreadable else spans))
