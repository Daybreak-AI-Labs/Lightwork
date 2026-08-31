"""Tenant-bound, digest-only multimodal evidence analysis authority.

The authority accepts raw artifacts only as ephemeral adapter input. Durable
records contain media metadata, SHA-256 pins, closed evidence codes, revisioned
criteria, deterministic adapter receipts, transparent score components, and a
provenance chain. They never contain artifact bytes, extracted text, prompts,
credentials, or a legal/compliance conclusion.

Extraction and analysis adapters are untrusted computation seams. Deployment
must independently attest exact deterministic, offline, data-only adapter
authorities. Core validates every returned pin and digest, limits outputs to
criteria-defined codes, computes summaries/scores/remediation itself, and
requires a distinct authorized human before a record can be released.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import time
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields, replace
from io import BytesIO
from pathlib import Path
from typing import Any

from .file_lock import cross_process_lock, ensure_private_directory, ensure_private_file
from .paths import canonical_tenant_id

ARTIFACT_AUTHORITY_SCHEMA = "lightwork.ai-evidence.artifact-authority.v1"
CRITERION_SCHEMA = "lightwork.ai-evidence.criterion.v1"
CRITERIA_AUTHORITY_SCHEMA = "lightwork.ai-evidence.criteria-authority.v1"
ADAPTER_AUTHORITY_SCHEMA = "lightwork.ai-evidence.adapter-authority.v1"
EXTRACTION_REQUEST_SCHEMA = "lightwork.ai-evidence.extraction-request.v1"
EXTRACTION_OUTPUT_SCHEMA = "lightwork.ai-evidence.extraction-output.v1"
FACT_SCHEMA = "lightwork.ai-evidence.fact.v1"
ANALYSIS_REQUEST_SCHEMA = "lightwork.ai-evidence.analysis-request.v1"
ASSESSMENT_SCHEMA = "lightwork.ai-evidence.assessment.v1"
ANALYSIS_OUTPUT_SCHEMA = "lightwork.ai-evidence.analysis-output.v1"
REVIEW_AUTHORITY_SCHEMA = "lightwork.ai-evidence.review-authority.v1"
ARTIFACT_RECORD_SCHEMA = "lightwork.ai-evidence.artifact-record.v1"
SUMMARY_SCHEMA = "lightwork.ai-evidence.summary.v1"
JUSTIFICATION_SCHEMA = "lightwork.ai-evidence.justification.v1"
GAP_SCHEMA = "lightwork.ai-evidence.gap-suggestion.v1"
PROVENANCE_SCHEMA = "lightwork.ai-evidence.provenance-link.v1"
RECORD_SCHEMA = "lightwork.ai-evidence.analysis-record.v1"
INTEGRITY_SCHEMA = "lightwork.ai-evidence.integrity-report.v1"

NON_CONCLUSION_NOTICE = (
    "This is a technical evidence aid requiring human review. It is not legal "
    "advice, a compliance determination, certification, or conformity assessment."
)

RAW_ARTIFACT_CONTENT_PERSISTED = False
EXTRACTED_TEXT_PERSISTED = False
LEGAL_OR_COMPLIANCE_CONCLUSION = False

MEDIA_PDF = "application/pdf"
MEDIA_JPEG = "image/jpeg"
MEDIA_PNG = "image/png"
MEDIA_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MEDIA_XLS = "application/vnd.ms-excel"
MEDIA_TEXT = "text/plain"
SUPPORTED_MEDIA_TYPES = frozenset(
    {MEDIA_PDF, MEDIA_JPEG, MEDIA_PNG, MEDIA_XLSX, MEDIA_XLS, MEDIA_TEXT}
)

_MEDIA_LIMITS = {
    MEDIA_PDF: 16 * 1024 * 1024,
    MEDIA_JPEG: 12 * 1024 * 1024,
    MEDIA_PNG: 12 * 1024 * 1024,
    MEDIA_XLSX: 20 * 1024 * 1024,
    MEDIA_XLS: 20 * 1024 * 1024,
    MEDIA_TEXT: 2 * 1024 * 1024,
}
_SCHEMA_VERSION = 1
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_CRITERIA = 128
_MAX_FACTS = 512
_MAX_ANALYSES = 10_000
_MAX_VALID_SECONDS = 366 * 24 * 60 * 60
_MAX_RETENTION_SECONDS = 10 * 366 * 24 * 60 * 60
_MIN_RETENTION_SECONDS = 60
_MAX_PURGE = 500
_ZERO_DIGEST = "0" * 64
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_DISPOSITIONS = frozenset(
    {"evidence_supported", "evidence_partial", "evidence_missing", "indeterminate"}
)
_RATIONALE_BY_DISPOSITION = {
    "evidence_supported": "direct_evidence",
    "evidence_partial": "partial_evidence",
    "evidence_missing": "no_matching_evidence",
    "indeterminate": "adapter_indeterminate",
}
_COVERAGE_BPS = {
    "evidence_supported": 10_000,
    "evidence_partial": 5_000,
    "evidence_missing": 0,
    "indeterminate": 0,
}
_REVIEW_CAPABILITIES = frozenset({"approve", "reject", "revoke"})
_STATUSES = frozenset(
    {"pending_review", "review_approved", "review_rejected", "revoked"}
)
_LOCAL_ADAPTER_ID = "lightwork-local-multimodal"
_LOCAL_ADAPTER_REVISION = 1
_LOCAL_IMPLEMENTATION_SHA256 = hashlib.sha256(
    b"lightwork.ai-evidence.bounded-local-extraction.v1"
).hexdigest()


class EvidenceAnalysisError(RuntimeError):
    """Base evidence-analysis error."""


class EvidenceAnalysisConfigError(EvidenceAnalysisError):
    """A gate, adapter, or independent authority is unavailable."""


class EvidenceAnalysisValidationError(EvidenceAnalysisError, ValueError):
    """Caller material is outside the closed evidence contract."""


class EvidenceAnalysisConflict(EvidenceAnalysisError):
    """An idempotency pin, revision, or independently resolved pin changed."""


class EvidenceAnalysisNotFound(EvidenceAnalysisError):
    """A tenant-local evidence analysis does not exist."""


class EvidenceAnalysisPermissionError(EvidenceAnalysisError):
    """Human review or release authority is missing or insufficient."""


class EvidenceAnalysisIntegrityError(EvidenceAnalysisError):
    """Adapter or persisted evidence failed integrity validation."""


def _disabled_gate() -> None:
    raise EvidenceAnalysisConfigError("AI evidence analysis is not enabled")


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    artifact_id: str
    media_type: str
    content: bytes
    source_ref: str
    source_revision: int
    acquired_at: float


@dataclass(frozen=True, slots=True)
class EvidenceArtifactAuthority:
    tenant_sha256: str
    artifact_id: str
    media_type: str
    content_sha256: str
    byte_length: int
    source_ref: str
    source_revision: int
    acquired_at: float
    valid_until: float
    revoked: bool
    authority_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceCriterion:
    criterion_id: str
    revision: int
    control_ref: str
    control_revision: int
    control_sha256: str
    legal_basis_ref: str
    legal_basis_revision: int
    legal_basis_sha256: str
    stakeholder_requirement_ref: str
    stakeholder_requirement_revision: int
    stakeholder_requirement_sha256: str
    expected_fact_code: str
    gap_code: str
    remediation_code: str
    weight_bps: int
    criterion_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceCriteriaAuthority:
    tenant_sha256: str
    criteria_set_id: str
    revision: int
    criteria: tuple[EvidenceCriterion, ...]
    valid_until: float
    revoked: bool
    authority_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceAdapterAuthority:
    stage: str
    adapter_id: str
    revision: int
    implementation_sha256: str
    configuration_sha256: str
    deterministic: bool
    network_access: bool
    untrusted_content_mode: str
    legal_conclusion_enabled: bool
    valid_until: float
    revoked: bool
    authority_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceArtifactRecord:
    artifact_id: str
    media_type: str
    content_sha256: str
    byte_length: int
    source_ref: str
    source_revision: int
    acquired_at: float
    artifact_authority_sha256: str
    descriptor_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceExtractionRequest:
    schema: str
    artifact: EvidenceArtifact
    artifact_record: EvidenceArtifactRecord
    criteria_authority: EvidenceCriteriaAuthority
    adapter_authority_sha256: str
    untrusted_content: bool
    instruction_mode: str
    request_sha256: str


@dataclass(frozen=True, slots=True)
class ExtractedEvidenceFact:
    fact_id: str
    fact_code: str
    locator_sha256: str
    value_sha256: str
    confidence_bps: int
    fact_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceExtractionOutput:
    schema: str
    request_sha256: str
    artifact_descriptor_sha256: str
    criteria_authority_sha256: str
    adapter_authority_sha256: str
    deterministic: bool
    facts: tuple[ExtractedEvidenceFact, ...]
    output_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceAnalysisRequest:
    schema: str
    artifact_record: EvidenceArtifactRecord
    criteria_authority: EvidenceCriteriaAuthority
    extraction: EvidenceExtractionOutput
    adapter_authority_sha256: str
    request_sha256: str


@dataclass(frozen=True, slots=True)
class CriterionEvidenceAssessment:
    criterion_id: str
    criterion_sha256: str
    disposition: str
    fact_ids: tuple[str, ...]
    confidence_bps: int
    rationale_code: str
    assessment_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceAnalysisAdapterOutput:
    schema: str
    request_sha256: str
    artifact_descriptor_sha256: str
    criteria_authority_sha256: str
    extraction_output_sha256: str
    adapter_authority_sha256: str
    deterministic: bool
    assessments: tuple[CriterionEvidenceAssessment, ...]
    output_sha256: str


@dataclass(frozen=True, slots=True)
class HumanReviewAuthority:
    tenant_sha256: str
    reviewer_sha256: str
    capabilities: tuple[str, ...]
    revision: int
    valid_until: float
    revoked: bool
    authority_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    total_criteria: int
    evidence_supported: int
    evidence_partial: int
    evidence_missing: int
    indeterminate: int
    extracted_fact_count: int
    summary_sha256: str


@dataclass(frozen=True, slots=True)
class JustificationComponent:
    criterion_id: str
    weight_bps: int
    coverage_bps: int
    confidence_bps: int
    provenance_bps: int
    component_score_bps: int
    component_sha256: str


@dataclass(frozen=True, slots=True)
class JustificationScore:
    methodology: str
    components: tuple[JustificationComponent, ...]
    score_bps: int
    maximum_bps: int
    score_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceGapSuggestion:
    criterion_id: str
    disposition: str
    gap_code: str
    remediation_code: str
    suggestion_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceProvenanceLink:
    sequence: int
    stage: str
    input_sha256: str
    output_sha256: str
    previous_link_sha256: str
    link_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceAnalysisRecord:
    analysis_id: str
    tenant_sha256: str
    artifact_authority: EvidenceArtifactAuthority
    artifact: EvidenceArtifactRecord
    criteria_authority: EvidenceCriteriaAuthority
    extraction_adapter_authority: EvidenceAdapterAuthority
    analysis_adapter_authority: EvidenceAdapterAuthority
    extraction: EvidenceExtractionOutput
    adapter_analysis: EvidenceAnalysisAdapterOutput
    summary: EvidenceSummary
    justification: JustificationScore
    gaps: tuple[EvidenceGapSuggestion, ...]
    provenance: tuple[EvidenceProvenanceLink, ...]
    provenance_sha256: str
    submitter_sha256: str
    status: str
    human_review_required: bool
    review_decision: str | None
    review_authority: HumanReviewAuthority | None
    reviewed_at: float | None
    revocation_reason_code: str | None
    revoked_at: float | None
    legal_or_compliance_conclusion: bool
    notice: str
    valid_until: float
    retained_until: float
    created_at: float
    updated_at: float
    revision: int
    previous_integrity_sha256: str | None
    integrity_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceAnalysisIntegrityReport:
    schema: str
    analyses: int
    history_records: int
    operations: int
    terminal_sha256: str
    verification_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceRetentionResult:
    analysis_ids: tuple[str, ...]
    history_records: int
    operations: int


def _canonical(value: object, *, maximum: int = _MAX_JSON_BYTES) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise EvidenceAnalysisValidationError("evidence material is not canonical JSON") from exc
    if not 1 <= len(encoded) <= maximum:
        raise EvidenceAnalysisValidationError("evidence material exceeds its byte bound")
    return encoded


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _same(left: object, right: object) -> bool:
    return isinstance(left, str) and isinstance(right, str) and hmac.compare_digest(left, right)


def _without_digest(value: object, field: str, schema: str) -> dict[str, Any]:
    payload = asdict(value)
    payload.pop(field)
    payload["schema"] = schema
    return payload


def artifact_authority_sha256(value: EvidenceArtifactAuthority) -> str:
    return _sha256(_without_digest(value, "authority_sha256", ARTIFACT_AUTHORITY_SCHEMA))


def criterion_sha256(value: EvidenceCriterion) -> str:
    return _sha256(_without_digest(value, "criterion_sha256", CRITERION_SCHEMA))


def criteria_authority_sha256(value: EvidenceCriteriaAuthority) -> str:
    return _sha256(_without_digest(value, "authority_sha256", CRITERIA_AUTHORITY_SCHEMA))


def adapter_authority_sha256(value: EvidenceAdapterAuthority) -> str:
    return _sha256(_without_digest(value, "authority_sha256", ADAPTER_AUTHORITY_SCHEMA))


def fact_sha256(value: ExtractedEvidenceFact) -> str:
    return _sha256(_without_digest(value, "fact_sha256", FACT_SCHEMA))


def extraction_output_sha256(value: EvidenceExtractionOutput) -> str:
    return _sha256(_without_digest(value, "output_sha256", EXTRACTION_OUTPUT_SCHEMA))


def assessment_sha256(value: CriterionEvidenceAssessment) -> str:
    return _sha256(_without_digest(value, "assessment_sha256", ASSESSMENT_SCHEMA))


def analysis_output_sha256(value: EvidenceAnalysisAdapterOutput) -> str:
    return _sha256(_without_digest(value, "output_sha256", ANALYSIS_OUTPUT_SCHEMA))


def review_authority_sha256(value: HumanReviewAuthority) -> str:
    return _sha256(_without_digest(value, "authority_sha256", REVIEW_AUTHORITY_SCHEMA))


def _ref(value: object, label: str, pattern: re.Pattern[str] = _REF_RE) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise EvidenceAnalysisValidationError(f"{label} must be a bounded opaque reference")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise EvidenceAnalysisValidationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EvidenceAnalysisValidationError(f"{label} is outside its bound")
    return value


def _timestamp(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceAnalysisValidationError(f"{label} must be a timestamp")
    selected = float(value)
    if not math.isfinite(selected) or selected <= 0:
        raise EvidenceAnalysisValidationError(f"{label} must be a positive finite timestamp")
    return selected


def _tenant(value: object) -> str:
    try:
        return canonical_tenant_id(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise EvidenceAnalysisValidationError("tenant_id is invalid") from exc


def _actor_sha256(tenant_id: str, actor_ref: object) -> str:
    actor = _ref(actor_ref, "actor_ref")
    return _text_sha256(f"lightwork.ai-evidence.actor.v1:{tenant_id}:{actor}")


def _idempotency_sha256(tenant_sha256: str, key: object) -> str:
    selected = _ref(key, "idempotency_key", _IDEMPOTENCY_RE)
    return _sha256(
        {
            "schema": "lightwork.ai-evidence.idempotency.v1",
            "tenant_sha256": tenant_sha256,
            "idempotency_key": selected,
        }
    )


def _now(clock: Callable[[], float]) -> float:
    try:
        return _timestamp(clock(), "clock")
    except (KeyboardInterrupt, SystemExit):
        raise
    except EvidenceAnalysisValidationError:
        raise
    except Exception as exc:
        raise EvidenceAnalysisConfigError("evidence-analysis clock is unavailable") from exc


def _lifetime(now: float, valid_until: object, retained_until: object) -> tuple[float, float]:
    valid = _timestamp(valid_until, "valid_until")
    retained = _timestamp(retained_until, "retained_until")
    if not now < valid <= now + _MAX_VALID_SECONDS:
        raise EvidenceAnalysisValidationError("analysis validity is outside its bound")
    if not max(valid, now + _MIN_RETENTION_SECONDS) <= retained <= now + _MAX_RETENTION_SECONDS:
        raise EvidenceAnalysisValidationError("analysis retention is outside its bound")
    return valid, retained


def _exact_mapping(value: object, model: type, label: str) -> dict[str, Any]:
    expected = {field.name for field in fields(model)}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise EvidenceAnalysisIntegrityError(f"stored {label} has an invalid shape")
    return dict(value)
