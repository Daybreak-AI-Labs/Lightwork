"""Fail closed when the normalized public OneTrust capability matrix drifts.

This is a coverage/integrity gate, not evidence of product parity.  It verifies
that the dated public-source snapshot has one unambiguous row for every tracked
identifier, coherent status totals, locked statuses for material current
outcomes, explicit residual language, the five-stage evidence legend, and the
official source families used by the comparison. Runtime/provider/customer
acceptance remains separate evidence.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

EXPECTED_FAMILIES = {
    "PX": 16,
    "AG": 27,
    "CP": 25,
    "DUG": 10,
    "PA": 24,
    "TRC": 25,
    "TPM": 22,
}
EXPECTED_STATUSES = {
    "Implemented": 75,
    "Partial": 67,
    "Missing": 0,
    "External / unverifiable": 7,
}
EXPECTED_FAMILY_STATUSES = {
    "PX": {"Implemented": 7, "Partial": 8, "Missing": 0, "External / unverifiable": 1},
    "AG": {"Implemented": 12, "Partial": 14, "Missing": 0, "External / unverifiable": 1},
    "CP": {"Implemented": 10, "Partial": 13, "Missing": 0, "External / unverifiable": 2},
    "DUG": {"Implemented": 8, "Partial": 2, "Missing": 0, "External / unverifiable": 0},
    "PA": {"Implemented": 12, "Partial": 11, "Missing": 0, "External / unverifiable": 1},
    "TRC": {"Implemented": 16, "Partial": 8, "Missing": 0, "External / unverifiable": 1},
    "TPM": {"Implemented": 10, "Partial": 11, "Missing": 0, "External / unverifiable": 1},
}
REQUIRED_NAMED_DELTAS = {
    "PX-15",
    "PX-16",
    "AG-25",
    "AG-26",
    "AG-27",
    "CP-21",
    "CP-22",
    "CP-23",
    "CP-24",
    "CP-25",
    "PA-19",
    "PA-20",
    "PA-21",
    "PA-22",
    "PA-23",
    "PA-24",
    "TRC-24",
    "TRC-25",
    "TPM-21",
    "TPM-22",
}
REQUIRED_ROW_STATUSES = {
    "PX-01": "Implemented",
    "PX-02": "Partial",
    "PX-10": "Partial",
    "PX-15": "Implemented",
    "PX-16": "Implemented",
    "AG-20": "Partial",
    "AG-27": "Implemented",
    "CP-01": "Partial",
    "CP-03": "Implemented",
    "CP-04": "Partial",
    "CP-06": "Partial",
    "CP-13": "Partial",
    "CP-21": "Implemented",
    "CP-22": "Implemented",
    "CP-23": "Implemented",
    "CP-24": "Implemented",
    "CP-25": "Implemented",
    "CP-16": "Partial",
    "CP-17": "Partial",
    "CP-18": "Partial",
    "CP-19": "Partial",
    "PA-19": "Partial",
    "PA-08": "Implemented",
    "PA-21": "Implemented",
    "PA-22": "Implemented",
    "PA-23": "Implemented",
    "PA-24": "Implemented",
    "PA-04": "Partial",
    "PA-05": "Partial",
    "PA-06": "Partial",
    "PA-11": "Implemented",
    "PA-14": "Implemented",
    "PA-15": "Partial",
    "TRC-24": "Partial",
    "TRC-25": "Implemented",
    "TRC-16": "Implemented",
    "TPM-21": "Partial",
    "TPM-06": "Implemented",
    "TPM-18": "Implemented",
    "TPM-22": "Implemented",
}
REQUIRED_OFFICIAL_SOURCES = {
    "https://www.onetrust.com/products/",
    "https://www.onetrust.com/platform/",
    "https://www.onetrust.com/solutions/ai-governance/",
    "https://www.onetrust.com/solutions/consent-and-preferences/",
    "https://www.onetrust.com/solutions/data-use-governance/",
    "https://www.onetrust.com/solutions/privacy-automation/",
    "https://www.onetrust.com/solutions/tech-risk-and-compliance/",
    "https://www.onetrust.com/solutions/third-party-management/",
    "https://developer.onetrust.com/onetrust/changelog/onetrust-20260210-released",
    "https://developer.onetrust.com/onetrust/changelog/onetrust-20260310-released",
    "https://developer.onetrust.com/onetrust/changelog/onetrust-20260610-released",
    "https://developer.onetrust.com/onetrust/changelog/onetrust-20260710-released",
    "https://developer.onetrust.com/onetrust/changelog/onetrust-20260810-released",
    "https://developer.onetrust.com/onetrust/reference/createcommentusingpost.md",
    "https://developer.onetrust.com/onetrust/reference/getcommentsforassessmentusingget.md",
    "https://developer.onetrust.com/onetrust/reference/createdroprequestusingpost.md",
    "https://developer.onetrust.com/onetrust/reference/updatedropusingput.md",
}
REQUIRED_EVIDENCE_STAGES = {
    "**Core**",
    "**Mounted**",
    "**Configured / ready**",
    "**Live interoperable**",
    "**Operated / accepted**",
}

_ROW_RE = re.compile(r"^\| (?P<identifier>(?:PX|AG|CP|DUG|PA|TRC|TPM)-\d{2}) \|")
_STATUS_RE = re.compile(r"\*\*(Implemented|Partial|Missing|External / unverifiable)\*\*")
_RESIDUAL_MARKERS = (
    " no ",
    " not ",
    " absent",
    " lack",
    " missing",
    " incomplete",
    " remain",
    " require",
    " unverified",
    " unaccept",
    " unproven",
    " unavailable",
    " cannot ",
    " does not ",
    " do not ",
    " depend",
    " neither ",
    " without ",
)


@dataclass(frozen=True)
class MatrixRow:
    identifier: str
    outcome: str
    lightwork_evidence: str
    status: str
    qualification: str


def _parse_row(line: str, line_number: int) -> MatrixRow:
    match = _ROW_RE.match(line)
    if match is None:
        raise ValueError(f"line {line_number}: invalid capability-row identifier")
    parts = [part.strip() for part in line.split("|")]
    if len(parts) != 6:
        raise ValueError(f"line {line_number}: capability rows must contain exactly four columns")
    outcome, evidence, status_cell = parts[2], parts[3], parts[4]
    status_match = _STATUS_RE.search(status_cell)
    if status_match is None:
        raise ValueError(f"line {line_number}: missing closed status token")
    status = status_match.group(1)
    qualification = _STATUS_RE.sub("", status_cell, count=1).lstrip(" —-").strip()
    if not outcome or not evidence or not qualification:
        raise ValueError(f"line {line_number}: empty outcome, evidence, or qualification")
    if status != "Implemented":
        normalized = f" {qualification.lower()} "
        if not any(marker in normalized for marker in _RESIDUAL_MARKERS):
            raise ValueError(
                f"line {line_number}: non-implemented row lacks explicit residual language"
            )
    return MatrixRow(match.group("identifier"), outcome, evidence, status, qualification)


def verify_matrix(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    rows = [
        _parse_row(line, line_number)
        for line_number, line in enumerate(text.splitlines(), start=1)
        if _ROW_RE.match(line)
    ]
    identifiers = [row.identifier for row in rows]
    duplicates = sorted(
        identifier for identifier, count in Counter(identifiers).items() if count != 1
    )
    if duplicates:
        raise ValueError(f"duplicate capability identifiers: {duplicates}")

    expected_identifiers = {
        f"{family}-{number:02d}"
        for family, count in EXPECTED_FAMILIES.items()
        for number in range(1, count + 1)
    }
    missing = sorted(expected_identifiers - set(identifiers))
    unexpected = sorted(set(identifiers) - expected_identifiers)
    if missing or unexpected:
        raise ValueError(f"identifier drift: missing={missing}, unexpected={unexpected}")

    missing_named_deltas = sorted(REQUIRED_NAMED_DELTAS - set(identifiers))
    if missing_named_deltas:
        raise ValueError(f"named 2026 deltas disappeared: {missing_named_deltas}")

    row_statuses = {row.identifier: row.status for row in rows}
    required_status_drift = {
        identifier: {"expected": expected, "actual": row_statuses.get(identifier)}
        for identifier, expected in REQUIRED_ROW_STATUSES.items()
        if row_statuses.get(identifier) != expected
    }
    if required_status_drift:
        raise ValueError(f"required row status drift: {required_status_drift}")

    status_counts = Counter(row.status for row in rows)
    if dict(status_counts) != {key: value for key, value in EXPECTED_STATUSES.items() if value}:
        raise ValueError(
            f"status drift: expected={EXPECTED_STATUSES}, actual={dict(status_counts)}"
        )

    family_statuses: dict[str, dict[str, int]] = {}
    for family, expected in EXPECTED_FAMILY_STATUSES.items():
        actual_counts = Counter(
            row.status for row in rows if row.identifier.startswith(f"{family}-")
        )
        actual = {status: actual_counts.get(status, 0) for status in EXPECTED_STATUSES}
        if actual != expected:
            raise ValueError(
                f"family status drift for {family}: expected={expected}, actual={actual}"
            )
        family_statuses[family] = actual

    missing_sources = sorted(source for source in REQUIRED_OFFICIAL_SOURCES if source not in text)
    if missing_sources:
        raise ValueError(f"official source set drift: missing={missing_sources}")

    missing_evidence_stages = sorted(
        stage for stage in REQUIRED_EVIDENCE_STAGES if stage not in text
    )
    if missing_evidence_stages:
        raise ValueError(f"evidence-stage legend drift: missing={missing_evidence_stages}")

    return {
        "schema": "lightwork.onetrust-public-matrix-verification.v1",
        "matrix": str(path),
        "rows": len(rows),
        "families": EXPECTED_FAMILIES,
        "family_statuses": family_statuses,
        "statuses": EXPECTED_STATUSES,
        "named_delta_ids": sorted(REQUIRED_NAMED_DELTAS),
        "required_row_statuses": REQUIRED_ROW_STATUSES,
        "official_sources": len(REQUIRED_OFFICIAL_SOURCES),
        "evidence_stages": sorted(REQUIRED_EVIDENCE_STAGES),
        "result": "verified_structure_only",
        "parity_claimed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "matrix",
        type=Path,
        help="path to the OneTrust capability matrix (held with the business material)",
    )
    args = parser.parse_args()
    try:
        result = verify_matrix(args.matrix)
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"result": "failed", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
