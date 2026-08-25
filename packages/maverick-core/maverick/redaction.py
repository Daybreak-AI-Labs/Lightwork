"""Deterministic text redaction over the content classifier's findings.

The DSAR path needs to remove OTHER people's data before a response
ships, and evidence surfaces need to quote documents without leaking the
identifiers inside them. This module replaces every validated content
finding with a typed placeholder (``[REDACTED:email]``), working from the
byte-exact spans :mod:`maverick.content_classifier` produced — so the
redaction is reproducible, reviewable, and reversible only by someone
holding the original.

Text only, by design: no OCR, no image or PDF rasterization (both would
need new dependencies, which is a decision, not a drive-by). A second
pass over redacted output is a no-op, so pipelines can redact defensively.
"""
from __future__ import annotations

from typing import Any

from .content_classifier import MAX_TEXT_CHARS, classify_text

REDACTION_TEMPLATE = "[REDACTED:{category}]"


def redact_text(
    text: object,
    *,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Replace validated findings with typed placeholders.

    ``categories`` limits redaction to those categories; ``None`` redacts
    every finding. Returns ``{"text", "redactions", "counts"}`` where
    ``counts`` maps category -> replacements made.
    """

    sample = str(text or "")
    if len(sample) > MAX_TEXT_CHARS:
        raise ValueError(f"text must be at most {MAX_TEXT_CHARS} characters")
    wanted = None if categories is None else {str(c) for c in categories}
    report = classify_text(sample)
    spans = [
        row
        for row in report["findings"]
        if wanted is None or row["category"] in wanted
    ]
    # Replace back-to-front so earlier spans keep their offsets; skip any
    # span that overlaps one already replaced (nested hits).
    spans.sort(key=lambda row: (-row["start"], row["end"]))
    out = sample
    counts: dict[str, int] = {}
    last_start = len(sample) + 1
    for row in spans:
        if row["end"] > last_start:
            continue
        out = (
            out[: row["start"]]
            + REDACTION_TEMPLATE.format(category=row["category"])
            + out[row["end"]:]
        )
        counts[row["category"]] = counts.get(row["category"], 0) + 1
        last_start = row["start"]
    return {
        "text": out,
        "redactions": sum(counts.values()),
        "counts": counts,
    }


__all__ = ["REDACTION_TEMPLATE", "redact_text"]
