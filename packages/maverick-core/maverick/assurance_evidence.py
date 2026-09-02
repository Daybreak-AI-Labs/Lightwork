"""Closed, clause-oriented evidence-reference bundles for external review.

These helpers package references to already-created evidence. They never read
or copy a trust receipt, audit row, prompt, model output, secret, identity, or
provider payload. The caller supplies only fixed-shape references and SHA-256
digests; obligation provenance is independently re-derived from the released
regulatory compiler.

The mappings deliberately identify operational evidence relationships. They do
not reproduce licensed framework text and do not decide legal applicability,
control effectiveness, compliance, certification, or audit outcomes.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from . import regulatory_compiler
from .policy_exports import PolicyExportError, normalize_profiles

EVIDENCE_BUNDLE_SCHEMA = "lightwork.framework-clause-evidence-bundle.v1"
OPERATIONAL_MAPPING_NOTICE = (
    "This package is a closed operational evidence mapping. It does not "
    "reproduce framework text and is not legal advice, a compliance "
    "determination, a certification, an attestation, or an audit conclusion."
)

_DIGEST_RE = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z", re.IGNORECASE)
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
_MAX_REFS = 128
_MAX_ROWS = 1_024
_DISPOSITIONS = frozenset(
    {
        "allow",
        "attempted",
        "blocked",
        "deny",
        "executed",
        "failed",
        "require_human",
    }
)

# Keep source identifiers and target clauses as public metadata only. The
# mapping intentionally does not contain (or paraphrase) framework clause text.
CLAUSE_MAPPINGS: tuple[dict[str, str], ...] = (
    {
        "id": "eu_ai_act_art_12",
        "profile_key": "eu_ai_act",
        "framework": "EU AI Act",
        "clause": "Art. 12",
        "source_title": "Regulation (EU) 2024/1689",
        "source_url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
    },
    {
        "id": "eu_ai_act_art_14",
        "profile_key": "eu_ai_act",
        "framework": "EU AI Act",
        "clause": "Art. 14",
        "source_title": "Regulation (EU) 2024/1689",
        "source_url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
    },
    {
        "id": "soc2_cc7_2",
        "profile_key": "soc2",
        "framework": "SOC 2 TSC",
        "clause": "CC7.2",
        "source_title": "AICPA SOC suite of services",
        "source_url": (
            "https://www.aicpa-cima.com/resources/landing/"
            "system-and-organization-controls-soc-suite-of-services"
        ),
    },
    {
        "id": "soc2_cc8_1",
        "profile_key": "soc2",
        "framework": "SOC 2 TSC",
        "clause": "CC8.1",
        "source_title": "AICPA SOC suite of services",
        "source_url": (
            "https://www.aicpa-cima.com/resources/landing/"
            "system-and-organization-controls-soc-suite-of-services"
        ),
    },
    {
        "id": "iso_42001_a6_2_8",
        "profile_key": "iso_42001",
        "framework": "ISO/IEC 42001:2023",
        "clause": "A.6.2.8",
        "source_title": "ISO/IEC 42001:2023",
        "source_url": "https://www.iso.org/standard/81230.html",
    },
)


class AssuranceEvidenceError(ValueError):
    """An evidence reference is not safe to include in a closed bundle."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sequence(value: object, label: str, maximum: int) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AssuranceEvidenceError(f"{label} must be a sequence")
    if len(value) > maximum:
        raise AssuranceEvidenceError(f"{label} exceeds the maximum of {maximum}")
    return value


def _reference(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise AssuranceEvidenceError(f"{label} must be a string")
    text = value.strip()
    if not _REF_RE.fullmatch(text):
        raise AssuranceEvidenceError(f"{label} is invalid")
    return text


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise AssuranceEvidenceError(f"{label} must be a SHA-256 digest")
    text = value.strip().lower()
    if not _DIGEST_RE.fullmatch(text):
        raise AssuranceEvidenceError(f"{label} must be a SHA-256 digest")
    return "sha256:" + text.removeprefix("sha256:")


def _closed_reference_list(
    value: object,
    *,
    label: str,
    reference_key: str,
    digest_key: str,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in _sequence(value, label, _MAX_REFS):
        if not isinstance(item, Mapping) or set(item) != {reference_key, digest_key}:
            raise AssuranceEvidenceError(
                f"{label} entries must contain only {reference_key} and {digest_key}"
            )
        normalized = {
            reference_key: _reference(item[reference_key], reference_key),
            digest_key: _digest(item[digest_key], digest_key),
        }
        identity = (normalized[reference_key], normalized[digest_key])
        if identity in seen:
            raise AssuranceEvidenceError(f"{label} contains a duplicate reference")
        seen.add(identity)
        entries.append(normalized)
    return sorted(entries, key=lambda item: (item[reference_key], item[digest_key]))


def _compiler_catalog(profiles: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    description = regulatory_compiler.describe(profiles)
    if not isinstance(description, Mapping) or tuple(description.get("profiles", ())) != profiles:
        raise AssuranceEvidenceError("compiler description does not preserve selected profiles")
    raw = description.get("obligations")
    if not isinstance(raw, list):
        raise AssuranceEvidenceError("compiler description omitted obligations")
    catalog: dict[str, dict[str, str]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise AssuranceEvidenceError("compiler emitted a malformed obligation")
        obligation_id = item.get("id")
        regulation = item.get("regulation")
        article = item.get("article")
        source_url = item.get("source_url")
        if (
            not isinstance(obligation_id, str)
            or not obligation_id
            or obligation_id in catalog
            or not isinstance(regulation, str)
            or not regulation
            or not isinstance(article, str)
            or not article
            or not isinstance(source_url, str)
            or not source_url.startswith("https://")
        ):
            raise AssuranceEvidenceError("compiler obligation provenance is invalid")
        catalog[obligation_id] = {
            "obligation_id": obligation_id,
            "regulation": regulation,
            "article": article,
            "source_url": source_url,
        }
    return dict(description), catalog


def _obligation_rows(value: object, catalog: Mapping[str, Mapping[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    expected = {
        "audit_row_sha256",
        "obligation_id",
        "regulation",
        "article",
        "disposition",
    }
    for item in _sequence(value, "obligation_rows", _MAX_ROWS):
        if not isinstance(item, Mapping) or set(item) != expected:
            raise AssuranceEvidenceError(
                "obligation rows must be closed metadata only; raw audit content is not allowed"
            )
        obligation_id = item["obligation_id"]
        if not isinstance(obligation_id, str) or obligation_id not in catalog:
            raise AssuranceEvidenceError("obligation row does not reference a compiled obligation")
        source = catalog[obligation_id]
        if item["regulation"] != source["regulation"] or item["article"] != source["article"]:
            raise AssuranceEvidenceError("obligation row provenance does not match the compiler")
        disposition = item["disposition"]
        if not isinstance(disposition, str) or disposition not in _DISPOSITIONS:
            raise AssuranceEvidenceError("obligation row disposition is unsupported")
        normalized = {
            "audit_row_sha256": _digest(item["audit_row_sha256"], "audit_row_sha256"),
            "obligation_id": obligation_id,
            "regulation": source["regulation"],
            "article": source["article"],
            "source_url": source["source_url"],
            "disposition": disposition,
        }
        identity = (normalized["audit_row_sha256"], obligation_id)
        if identity in seen:
            raise AssuranceEvidenceError("obligation rows contain a duplicate citation")
        seen.add(identity)
        rows.append(normalized)
    return sorted(
        rows,
        key=lambda item: (item["regulation"], item["article"], item["obligation_id"], item["audit_row_sha256"]),
    )


def _selected_mappings(profiles: tuple[str, ...]) -> list[dict[str, str]]:
    selected = [mapping for mapping in CLAUSE_MAPPINGS if mapping["profile_key"] in profiles]
    if not selected:
        raise AssuranceEvidenceError(
            "the selected profiles do not have a supported framework-clause mapping"
        )
    return selected


def build_framework_clause_evidence_bundle(
    profiles: Sequence[str],
    *,
    receipt_refs: Sequence[Mapping[str, str]],
    obligation_rows: Sequence[Mapping[str, str]],
    verification_reports: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    """Assemble deterministic, content-free evidence references per target clause.

    An absent direct compiler clause is reported as an empty
    ``direct_compiler_obligation_ids`` list.  Adjacent runtime evidence stays
    visible with its original compiler tag, never relabelled as the requested
    framework clause.
    """
    try:
        selected_profiles = normalize_profiles(profiles)
    except PolicyExportError as exc:
        raise AssuranceEvidenceError(str(exc)) from exc
    description, catalog = _compiler_catalog(selected_profiles)
    receipts = _closed_reference_list(
        receipt_refs,
        label="receipt_refs",
        reference_key="receipt_ref",
        digest_key="receipt_sha256",
    )
    reports = _closed_reference_list(
        verification_reports,
        label="verification_reports",
        reference_key="verification_report_ref",
        digest_key="verification_report_sha256",
    )
    rows = _obligation_rows(obligation_rows, catalog)
    clauses: list[dict[str, Any]] = []
    for mapping in _selected_mappings(selected_profiles):
        compiler_sources = {
            item["source_url"]
            for item in catalog.values()
            if item["regulation"] == mapping["framework"]
        }
        if mapping["source_url"] not in compiler_sources:
            raise AssuranceEvidenceError(
                "framework-clause mapping source does not match compiler provenance"
            )
        framework_rows = [
            row for row in rows if row["regulation"] == mapping["framework"]
        ]
        direct_ids = sorted(
            obligation_id
            for obligation_id, item in catalog.items()
            if item["regulation"] == mapping["framework"]
            and item["article"] == mapping["clause"]
        )
        clauses.append(
            {
                "clause_id": mapping["id"],
                "framework": mapping["framework"],
                "clause": mapping["clause"],
                "source": {
                    "title": mapping["source_title"],
                    "url": mapping["source_url"],
                },
                "mapping_kind": "operational_evidence_mapping",
                "framework_text_included": False,
                "direct_compiler_obligation_ids": direct_ids,
                # These retain their compiler's original article and source;
                # their presence is not a statement they satisfy this clause.
                "runtime_obligation_rows": framework_rows,
                "receipt_refs": receipts,
                "verification_reports": reports,
            }
        )
    unsigned = {
        "schema": EVIDENCE_BUNDLE_SCHEMA,
        "notice": OPERATIONAL_MAPPING_NOTICE,
        "profiles": list(selected_profiles),
        "compiler_description_sha256": _sha256(description),
        "clauses": clauses,
    }
    # A deterministic content digest helps bind this *unsigned* handoff
    # package to a receipt/signature flow without claiming it is signed itself.
    return {**unsigned, "bundle_sha256": "sha256:" + _sha256(unsigned)}


def render_framework_clause_evidence_bundle(
    profiles: Sequence[str],
    *,
    receipt_refs: Sequence[Mapping[str, str]],
    obligation_rows: Sequence[Mapping[str, str]],
    verification_reports: Sequence[Mapping[str, str]],
) -> str:
    """Render the closed evidence bundle as canonical UTF-8 JSON text."""
    return json.dumps(
        build_framework_clause_evidence_bundle(
            profiles,
            receipt_refs=receipt_refs,
            obligation_rows=obligation_rows,
            verification_reports=verification_reports,
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ) + "\n"


__all__ = [
    "AssuranceEvidenceError",
    "CLAUSE_MAPPINGS",
    "EVIDENCE_BUNDLE_SCHEMA",
    "OPERATIONAL_MAPPING_NOTICE",
    "build_framework_clause_evidence_bundle",
    "render_framework_clause_evidence_bundle",
]
