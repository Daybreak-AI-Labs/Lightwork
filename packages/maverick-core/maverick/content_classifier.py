"""Content-level personal-data detection: deterministic, checksum-validated.

Where the name-based classifier in :mod:`maverick.data_map` reads column
NAMES, this module reads the DATA. Every detector is deterministic and,
where the identifier carries its own integrity math, validated against it:
credit cards must pass Luhn, IBANs must pass mod-97, SSNs must satisfy the
SSA's structural rules, IPv4 octets must parse. A finding therefore comes
with a reason an auditor can recompute — never a model score.

Pure functions over bounded text; no I/O, no storage, no network. The
governed surfaces that feed text through this module (the data map's
content verification, redaction, DSAR response scrubbing) carry their own
config knobs and caps. Detection is evidence for a human mapping
exercise, never a guarantee that unflagged text is clean.
"""
from __future__ import annotations

import re
from typing import Any

#: Hard cap on scanned text; surfaces may impose smaller ones.
MAX_TEXT_CHARS = 2_000_000
#: Findings cap so a pathological input cannot balloon a report.
MAX_FINDINGS = 5_000

_EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_PHONE = re.compile(
    r"(?<![\d.])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?)?"
    r"\d{2,4}[ .-]\d{3,4}[ .-]\d{3,4}(?!\d)(?!\.\d)"
)
_SSN = re.compile(r"(?<!\d)(\d{3})-(\d{2})-(\d{4})(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_IBAN = re.compile(r"(?<![A-Z0-9])([A-Z]{2})(\d{2})([A-Z0-9]{11,30})(?![A-Z0-9])")
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
_DOB = re.compile(
    r"(?<!\d)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\d)"
)
_DOB_CONTEXT = re.compile(r"\b(?:birth|born|dob|geburtsdatum|nascimento)\b", re.IGNORECASE)
_ICD10 = re.compile(r"(?<![A-Z0-9])([A-TV-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?)(?![A-Z0-9])")
_ICD10_CONTEXT = re.compile(
    r"\b(?:icd|diagnos\w*|condition|clinical|patient|medical)\b", re.IGNORECASE
)
_GPS = re.compile(
    r"(?<![\d.-])(-?(?:90|[0-8]?\d)\.\d{3,8}),\s*"
    r"(-?(?:180|1[0-7]\d|\d{1,2})\.\d{3,8})(?!\d)(?!\.\d)"
)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = ord(char) - 48
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _iban_ok(country: str, check: str, body: str) -> bool:
    rearranged = body + country + check
    total = 0
    for char in rearranged:
        total = (
            total * 10 + (ord(char) - 48)
            if char.isdigit()
            else total * 100 + (ord(char) - 55)
        )
    return total % 97 == 1


def _ssn_ok(area: str, group: str, serial: str) -> bool:
    # SSA structural rules: area never 000/666/900-999, group never 00,
    # serial never 0000.
    if area in {"000", "666"} or area >= "900":
        return False
    return group != "00" and serial != "0000"


def _mask(value: str) -> str:
    """A finding sample that proves the hit without repeating the datum."""

    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _scan_validated(sample: str, add: Any) -> None:
    """The detectors whose identifiers carry recomputable integrity math."""

    for match in _EMAIL.finditer(sample):
        add("email", "email_address", match.start(), match.group())
    # Card-SHAPED digit runs are remembered even when Luhn fails: a long
    # grouped digit run is card-shaped, not a phone number, and must not
    # fall through to the phone detector.
    card_spans: list[tuple[int, int]] = []
    for match in _CARD.finditer(sample):
        digits = re.sub(r"[ -]", "", match.group())
        if 13 <= len(digits) <= 19:
            card_spans.append((match.start(), match.end()))
            if _luhn_ok(digits):
                add("financial", "payment_card_luhn",
                    match.start(), match.group())
    for match in _IBAN.finditer(sample):
        if _iban_ok(match.group(1), match.group(2), match.group(3)):
            add("financial", "iban_mod97", match.start(), match.group())
    for match in _SSN.finditer(sample):
        if _ssn_ok(match.group(1), match.group(2), match.group(3)):
            add("national_id", "us_ssn_structural", match.start(), match.group())
    for match in _IPV4.finditer(sample):
        if all(int(octet) <= 255 for octet in match.groups()):
            add("ip_address", "ipv4", match.start(), match.group())
    for match in _PHONE.finditer(sample):
        digits = re.sub(r"\D", "", match.group())
        overlaps_card = any(
            match.start() < end and match.end() > start
            for start, end in card_spans
        )
        if 7 <= len(digits) <= 15 and not overlaps_card:
            add("phone", "phone_number", match.start(), match.group())


def _scan_contextual(sample: str, add: Any) -> None:
    """The detectors that fire only in corroborating context."""

    if _DOB_CONTEXT.search(sample):
        for match in _DOB.finditer(sample):
            add("date_of_birth", "iso_date_with_birth_context",
                match.start(), match.group())
    if _ICD10_CONTEXT.search(sample):
        for match in _ICD10.finditer(sample):
            if "." in match.group(1) or match.group(1)[1:3].isdigit():
                add("health", "icd10_code_in_clinical_context",
                    match.start(), match.group(1))
    for match in _GPS.finditer(sample):
        add("location", "gps_coordinate_pair", match.start(), match.group())


def classify_text(text: object) -> dict[str, Any]:
    """Deterministic content findings for one bounded text sample.

    Returns ``{"categories": [...], "counts": {category: n},
    "findings": [{category, kind, start, end, sample} ...]}``. Findings
    carry masked samples only — the raw datum never appears in a report.
    """

    sample = str(text or "")
    if len(sample) > MAX_TEXT_CHARS:
        raise ValueError(f"text must be at most {MAX_TEXT_CHARS} characters")
    findings: list[dict[str, Any]] = []

    def add(category: str, kind: str, match_start: int, value: str) -> None:
        if len(findings) < MAX_FINDINGS:
            findings.append(
                {
                    "category": category,
                    "kind": kind,
                    "start": match_start,
                    "end": match_start + len(value),
                    "sample": _mask(value),
                }
            )

    _scan_validated(sample, add)
    _scan_contextual(sample, add)

    findings.sort(key=lambda row: (row["start"], row["category"]))
    counts: dict[str, int] = {}
    for row in findings:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    return {
        "categories": sorted(counts),
        "counts": counts,
        "findings": findings,
        "scanned_chars": len(sample),
        "truncated": len(findings) >= MAX_FINDINGS,
    }


__all__ = ["MAX_FINDINGS", "MAX_TEXT_CHARS", "classify_text"]
