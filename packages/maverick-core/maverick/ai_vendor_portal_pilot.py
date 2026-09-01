"""Consent-gated local acceptance pilot for the external AI vendor portal.

The pilot exercises the real :mod:`maverick.ai_vendor_portal` and
:mod:`maverick.ai_vendor_risk` authorities against operator-declared synthetic
vendors and documents.  It is deliberately not a substitute for a live
vendor pilot: the resulting evidence says exactly which local controls ran and
keeps live vendor and ecosystem acceptance marked as not run.

No invitation token, vendor name, respondent, tenant identifier, document
bytes, extracted quote, or questionnaire answer is emitted in the pilot
evidence.  Run with ``python -m maverick.ai_vendor_portal_pilot --help``.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import secrets
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ai_vendor_portal as portal
from . import ai_vendor_risk as vendor_risk
from .paths import tenant_scope

EVIDENCE_SCHEMA = "lightwork.ai-vendor-portal-pilot-evidence.v1"
QUESTIONNAIRE_SCHEMA = "lightwork.ai-vendor-questionnaire.v1"
DOCUMENT_EVIDENCE_SCHEMA = "lightwork.document-extraction-evidence.v1"
PILOT_BOUNDARY = (
    "Local consenting test-estate exercise with operator-declared synthetic "
    "vendors and documents; not live vendor, customer, or ecosystem acceptance."
)

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_REF_RE = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}\Z")
_RUN_RE = re.compile(r"VPIL-[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
_HUMAN_RE = re.compile(r"user:[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}\Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_CASES = 20
_MAX_DOCUMENT_BYTES = 1024 * 1024

_QUESTIONNAIRE_KEYS = frozenset(
    {
        "schema",
        "template_revision",
        "template_digest",
        "title",
        "description",
        "questions",
        "notice",
    }
)
_QUESTION_KEYS = frozenset(
    {"id", "section", "text", "risk_answer", "severity", "guidance"}
)
_DOCUMENT_KEYS = frozenset(
    {
        "schema",
        "tenant_sha256",
        "document_evidence",
        "answers",
        "unanswered",
        "truncated",
        "review_required",
    }
)
_DOCUMENT_EVIDENCE_KEYS = frozenset(
    {
        "schema",
        "source",
        "source_binding_sha256",
        "document_sha256",
        "document_size_bytes",
        "extracted_text_sha256",
        "resolved_mime",
        "method",
        "scope",
        "confidence",
        "review_required",
        "binding_sha256",
    }
)
_DOCUMENT_EVIDENCE_OPTIONAL_KEYS = frozenset({"referenced_streams"})
_DOCUMENT_ANSWER_KEYS = frozenset(
    {"answer", "note", "confidence", "citations"}
)
_CITATION_KEYS = frozenset(
    {
        "source_binding_sha256",
        "document_sha256",
        "extracted_text_sha256",
        "quote_sha256",
        "display_quote_sha256",
        "start",
        "end",
        "quote",
        "redacted",
        "confidence",
    }
)
_PORTAL_QUESTIONNAIRE_KEYS = frozenset(
    {
        "invitation_id",
        "vendor",
        "expires_at",
        "submission_status",
        "questionnaire",
        "notice",
    }
)
_INVITATION_KEYS = frozenset(
    {
        "invitation_id",
        "vendor",
        "questionnaire_digest",
        "status",
        "expires_at",
        "created_by",
        "submission_id",
        "revocation",
        "revision",
        "notice",
    }
)
_SUBMISSION_KEYS = frozenset(
    {
        "submission_id",
        "invitation_id",
        "vendor",
        "questionnaire_digest",
        "answers",
        "respondent_ref",
        "attested",
        "response_sha256",
        "submitted_at",
        "status",
        "imported_assessment",
        "revision",
        "notice",
    }
)


class VendorPortalPilotError(RuntimeError):
    """Base error for a local vendor-portal pilot."""


class PilotConsentError(VendorPortalPilotError, ValueError):
    """The operator did not provide the required scoped consent declaration."""


class PilotContractDrift(VendorPortalPilotError):
    """A portal, questionnaire, or document shape changed unexpectedly."""


class PilotInvariantError(VendorPortalPilotError):
    """The real workflow did not preserve a required acceptance invariant."""


def _canonical(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PilotInvariantError("pilot evidence must be finite JSON") from exc
    if len(encoded) > 2 * 1024 * 1024:
        raise PilotInvariantError("pilot evidence exceeds its byte bound")
    return encoded


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise PilotContractDrift(f"{label} is not a lowercase SHA-256 digest")
    return value


def _bounded_text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise PilotContractDrift(f"{label} must be a string")
    text = value.strip()
    if not text or len(text) > limit or _CONTROL_RE.search(text):
        raise PilotContractDrift(f"{label} is invalid")
    return text


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != set(expected):
        raise PilotContractDrift(f"{label} fields drifted")


def _finite_number(value: object, label: str, *, minimum: float = 0.0) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < minimum
    ):
        raise PilotContractDrift(f"{label} is invalid")
    return float(value)


def _opaque_ref(prefix: str, value: object) -> str:
    return prefix + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class PilotDeclaration:
    """Operator assertion binding consent and synthetic-only scope."""

    estate_ref: str
    authorized_by: str
    consent_record_sha256: str
    consent_confirmed: bool
    synthetic_documents_only: bool

    def validate(self) -> None:
        if not _REF_RE.fullmatch(str(self.estate_ref or "")):
            raise PilotConsentError("estate_ref must be a pseudonymous identifier")
        if not _HUMAN_RE.fullmatch(str(self.authorized_by or "")):
            raise PilotConsentError("authorized_by must be a user principal")
        if not _DIGEST_RE.fullmatch(str(self.consent_record_sha256 or "")):
            raise PilotConsentError("a consent record digest is required")
        if self.consent_confirmed is not True:
            raise PilotConsentError("test-estate contribution consent is required")
        if self.synthetic_documents_only is not True:
            raise PilotConsentError(
                "this local pilot accepts only operator-declared synthetic documents"
            )


@dataclass(frozen=True)
class SyntheticVendorCase:
    """One bounded, operator-declared synthetic vendor scenario."""

    case_ref: str
    vendor: str = field(repr=False)
    respondent_ref: str = field(repr=False)
    document: bytes = field(repr=False)
    mime: str = "text/plain"
    expected_document_answers: Mapping[str, str] = field(default_factory=dict)
    portal_answers: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    synthetic: bool = True

    def validate(self, question_ids: frozenset[str]) -> None:
        if not _REF_RE.fullmatch(str(self.case_ref or "")):
            raise PilotConsentError("case_ref must be a pseudonymous identifier")
        if not self.synthetic:
            raise PilotConsentError("every local pilot case must be synthetic")
        _bounded_text(self.vendor, "synthetic vendor", 300)
        if not _REF_RE.fullmatch(str(self.respondent_ref or "")):
            raise PilotConsentError("respondent_ref must be pseudonymous")
        if not isinstance(self.document, bytes) or not 1 <= len(
            self.document
        ) <= _MAX_DOCUMENT_BYTES:
            raise PilotConsentError("synthetic document is empty or exceeds 1 MiB")
        _bounded_text(self.mime, "document MIME", 255)
        expected = dict(self.expected_document_answers)
        if not set(expected).issubset(question_ids):
            raise PilotConsentError("expected document answer identity is invalid")
        if any(answer not in {"yes", "no", "unknown"} for answer in expected.values()):
            raise PilotConsentError("expected document answer is invalid")
        if set(self.portal_answers) != set(question_ids):
            raise PilotConsentError("portal answers must cover the exact questionnaire")
        for question_id, raw in self.portal_answers.items():
            if not isinstance(raw, Mapping) or set(raw) != {"answer", "note"}:
                raise PilotConsentError(
                    f"portal answer {question_id!r} fields are invalid"
                )
            if raw.get("answer") not in {"yes", "no", "na", "unknown"}:
                raise PilotConsentError(
                    f"portal answer {question_id!r} is invalid"
                )
            _bounded_text(raw.get("note"), "portal answer note", 1000)


def validate_questionnaire_contract(
    value: object,
    *,
    expected_digest: str | None = None,
) -> dict[str, Any]:
    """Validate the exact public questionnaire shape and optional pinned digest."""

    if not isinstance(value, Mapping):
        raise PilotContractDrift("questionnaire is not an object")
    row = dict(value)
    _exact_keys(row, _QUESTIONNAIRE_KEYS, "questionnaire")
    if row.get("schema") != QUESTIONNAIRE_SCHEMA:
        raise PilotContractDrift("questionnaire schema drifted")
    if row.get("template_revision") != 1:
        raise PilotContractDrift("questionnaire revision drifted")
    digest = _digest(row.get("template_digest"), "questionnaire digest")
    if expected_digest is not None and digest != _digest(
        expected_digest, "expected questionnaire digest"
    ):
        raise PilotContractDrift("questionnaire digest drifted")
    _bounded_text(row.get("title"), "questionnaire title", 500)
    _bounded_text(row.get("description"), "questionnaire description", 4000)
    _bounded_text(row.get("notice"), "questionnaire notice", 4000)
    questions = row.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 64:
        raise PilotContractDrift("questionnaire questions are invalid")
    question_ids: list[str] = []
    for raw in questions:
        if not isinstance(raw, Mapping):
            raise PilotContractDrift("questionnaire item is not an object")
        question = dict(raw)
        _exact_keys(question, _QUESTION_KEYS, "questionnaire item")
        question_id = _bounded_text(question.get("id"), "question id", 128)
        if not _REF_RE.fullmatch(question_id):
            raise PilotContractDrift("question id is invalid")
        for key, limit in (
            ("section", 300),
            ("text", 4000),
            ("guidance", 4000),
        ):
            _bounded_text(question.get(key), f"question {key}", limit)
        if question.get("risk_answer") not in {"yes", "no", "na"}:
            raise PilotContractDrift("question risk answer is invalid")
        if question.get("severity") not in {"low", "medium", "high", "critical"}:
            raise PilotContractDrift("question severity is invalid")
        question_ids.append(question_id)
    if len(set(question_ids)) != len(question_ids):
        raise PilotContractDrift("questionnaire contains duplicate question ids")
    return {
        "schema": row["schema"],
        "template_revision": row["template_revision"],
        "template_digest": digest,
        "question_ids": tuple(question_ids),
        "contract_sha256": _sha256(row),
    }


def _validated_document_evidence(
    value: object,
    *,
    expected_document_sha256: str | None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotContractDrift("document evidence is not an object")
    evidence = dict(value)
    keys = set(evidence)
    allowed = set(_DOCUMENT_EVIDENCE_KEYS | _DOCUMENT_EVIDENCE_OPTIONAL_KEYS)
    if not set(_DOCUMENT_EVIDENCE_KEYS).issubset(keys) or not keys.issubset(allowed):
        raise PilotContractDrift("document evidence fields drifted")
    if evidence.get("schema") != DOCUMENT_EVIDENCE_SCHEMA:
        raise PilotContractDrift("document evidence schema drifted")
    for key in (
        "source_binding_sha256",
        "document_sha256",
        "extracted_text_sha256",
        "binding_sha256",
    ):
        _digest(evidence.get(key), f"document evidence {key}")
    if expected_document_sha256 is not None and evidence["document_sha256"] != _digest(
        expected_document_sha256, "expected document digest"
    ):
        raise PilotContractDrift("document byte binding drifted")
    size = _finite_number(evidence.get("document_size_bytes"), "document size")
    if not float(size).is_integer() or size > _MAX_DOCUMENT_BYTES:
        raise PilotContractDrift("document size is outside the pilot bound")
    for key, limit in (
        ("source", 64),
        ("resolved_mime", 255),
        ("method", 64),
        ("scope", 64),
        ("confidence", 32),
    ):
        _bounded_text(evidence.get(key), f"document evidence {key}", limit)
    if evidence.get("review_required") is not True:
        raise PilotContractDrift("document evidence lost its review requirement")
    if "referenced_streams" in evidence:
        streams = evidence["referenced_streams"]
        if isinstance(streams, bool) or not isinstance(streams, int) or streams < 0:
            raise PilotContractDrift("document referenced stream count is invalid")
    expected_binding = _sha256(
        {key: item for key, item in evidence.items() if key != "binding_sha256"}
    )
    if evidence["binding_sha256"] != expected_binding:
        raise PilotContractDrift("document evidence binding digest drifted")
    return evidence


def _validate_document_citation(
    value: object,
    *,
    evidence: Mapping[str, Any],
    confidence: float,
) -> None:
    if not isinstance(value, Mapping):
        raise PilotContractDrift("document citation is not an object")
    citation = dict(value)
    _exact_keys(citation, _CITATION_KEYS, "document citation")
    for key in (
        "source_binding_sha256",
        "document_sha256",
        "extracted_text_sha256",
        "quote_sha256",
        "display_quote_sha256",
    ):
        _digest(citation.get(key), f"document citation {key}")
    for key in (
        "source_binding_sha256",
        "document_sha256",
        "extracted_text_sha256",
    ):
        if citation[key] != evidence[key]:
            raise PilotContractDrift("document citation binding drifted")
    quote = citation.get("quote")
    if not isinstance(quote, str) or len(quote) > 320 or _CONTROL_RE.search(quote):
        raise PilotContractDrift("document citation quote is invalid")
    if hashlib.sha256(quote.encode("utf-8")).hexdigest() != citation[
        "display_quote_sha256"
    ]:
        raise PilotContractDrift("document citation display digest drifted")
    start, end = citation.get("start"), citation.get("end")
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or start < 0
        or end < start
    ):
        raise PilotContractDrift("document citation span is invalid")
    cited_confidence = _finite_number(
        citation.get("confidence"), "citation confidence"
    )
    if cited_confidence != confidence or not isinstance(
        citation.get("redacted"), bool
    ):
        raise PilotContractDrift("document citation metadata drifted")


def _validated_document_answer(
    value: object,
    *,
    evidence: Mapping[str, Any],
) -> str:
    if not isinstance(value, Mapping):
        raise PilotContractDrift("document answer is not an object")
    answer = dict(value)
    _exact_keys(answer, _DOCUMENT_ANSWER_KEYS, "document answer")
    choice = answer.get("answer")
    if choice not in {"yes", "no", "unknown"}:
        raise PilotContractDrift("document answer value is invalid")
    _bounded_text(answer.get("note"), "document answer note", 500)
    confidence = _finite_number(answer.get("confidence"), "answer confidence")
    if confidence > 0.88 or (choice == "unknown") != (confidence == 0.0):
        raise PilotContractDrift("document answer confidence drifted")
    citations = answer.get("citations")
    if not isinstance(citations, list) or not 1 <= len(citations) <= 3:
        raise PilotContractDrift("document answer citations are invalid")
    for citation in citations:
        _validate_document_citation(
            citation,
            evidence=evidence,
            confidence=confidence,
        )
    return str(choice)


def validate_document_contract(
    value: object,
    *,
    question_ids: frozenset[str],
    expected_document_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate one exact document-extraction response and its digest bindings."""

    if not isinstance(value, Mapping):
        raise PilotContractDrift("document extraction is not an object")
    row = dict(value)
    _exact_keys(row, _DOCUMENT_KEYS, "document extraction")
    if row.get("schema") != vendor_risk.AI_VENDOR_DOCUMENT_SCHEMA:
        raise PilotContractDrift("document extraction schema drifted")
    _digest(row.get("tenant_sha256"), "document tenant digest")
    if row.get("review_required") is not True or not isinstance(
        row.get("truncated"), bool
    ):
        raise PilotContractDrift("document review contract drifted")
    evidence = _validated_document_evidence(
        row.get("document_evidence"),
        expected_document_sha256=expected_document_sha256,
    )
    raw_answers = row.get("answers")
    if not isinstance(raw_answers, Mapping) or not set(raw_answers).issubset(
        question_ids
    ):
        raise PilotContractDrift("document answers are invalid")
    answers = {
        str(question_id): _validated_document_answer(raw_answer, evidence=evidence)
        for question_id, raw_answer in raw_answers.items()
    }
    unanswered = row.get("unanswered")
    expected_unanswered = sorted(question_ids - set(answers))
    if (
        not isinstance(unanswered, list)
        or any(not isinstance(item, str) for item in unanswered)
        or unanswered != expected_unanswered
    ):
        raise PilotContractDrift("document unanswered contract drifted")
    return {
        "schema": row["schema"],
        "contract_sha256": _sha256(row),
        "document_sha256": evidence["document_sha256"],
        "evidence_binding_sha256": evidence["binding_sha256"],
        "answers": answers,
        "answered": len(answers),
        "unanswered": len(unanswered),
        "review_required": True,
    }


def _validate_portal_questionnaire(
    value: object,
    *,
    expected_digest: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotContractDrift("portal questionnaire response is not an object")
    row = dict(value)
    _exact_keys(row, _PORTAL_QUESTIONNAIRE_KEYS, "portal questionnaire response")
    if row.get("submission_status") != "active":
        raise PilotInvariantError("new invitation is not active")
    _bounded_text(row.get("invitation_id"), "invitation id", 64)
    _bounded_text(row.get("vendor"), "vendor", 300)
    _bounded_text(row.get("notice"), "portal notice", 4000)
    _finite_number(row.get("expires_at"), "invitation expiry", minimum=1.0)
    return validate_questionnaire_contract(
        row.get("questionnaire"), expected_digest=expected_digest
    )


def _validate_invitation(value: object, *, expected_status: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotContractDrift("invitation is not an object")
    row = dict(value)
    _exact_keys(row, _INVITATION_KEYS, "invitation")
    if row.get("status") != expected_status:
        raise PilotInvariantError("invitation status is unexpected")
    _digest(row.get("questionnaire_digest"), "invitation questionnaire digest")
    _bounded_text(row.get("invitation_id"), "invitation id", 64)
    _bounded_text(row.get("vendor"), "vendor", 300)
    if isinstance(row.get("revision"), bool) or not isinstance(row.get("revision"), int):
        raise PilotContractDrift("invitation revision is invalid")
    return row


def _validate_submission(value: object, *, expected_status: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotContractDrift("submission is not an object")
    row = dict(value)
    _exact_keys(row, _SUBMISSION_KEYS, "submission")
    if row.get("status") != expected_status or row.get("attested") is not True:
        raise PilotInvariantError("submission state is unexpected")
    _digest(row.get("questionnaire_digest"), "submission questionnaire digest")
    _digest(row.get("response_sha256"), "submission response digest")
    _bounded_text(row.get("submission_id"), "submission id", 64)
    _bounded_text(row.get("invitation_id"), "submission invitation id", 64)
    if isinstance(row.get("revision"), bool) or not isinstance(row.get("revision"), int):
        raise PilotContractDrift("submission revision is invalid")
    return row


def _portal_answers(values: Mapping[str, str]) -> dict[str, dict[str, str]]:
    return {
        question_id: {
            "answer": answer,
            "note": "Synthetic vendor attestation for local acceptance testing.",
        }
        for question_id, answer in values.items()
    }


def representative_synthetic_cases() -> tuple[SyntheticVendorCase, ...]:
    """Return two bounded fixtures: documented controls and disclosed gaps."""

    safe = {
        "aiv_provenance": "yes",
        "aiv_training_data": "yes",
        "aiv_subprocessors": "yes",
        "aiv_evaluation": "yes",
        "aiv_incidents": "no",
        "aiv_eu_role": "yes",
        "aiv_retention": "yes",
        "aiv_human_oversight": "yes",
    }
    gaps = {
        # The deliberately ambiguous provenance and role language fires both
        # conservative lexical signals, so the extractor must return unknown
        # rather than silently promote a negative phrase to a verified fact.
        "aiv_provenance": "unknown",
        "aiv_training_data": "no",
        "aiv_subprocessors": "no",
        "aiv_evaluation": "no",
        "aiv_incidents": "yes",
        "aiv_eu_role": "unknown",
        "aiv_retention": "no",
        "aiv_human_oversight": "no",
    }
    return (
        SyntheticVendorCase(
            case_ref="documented-controls",
            vendor="Synthetic Documented Controls Vendor",
            respondent_ref="security-office",
            document=(
                b"Model provenance is documented with the foundation model provider and "
                b"version. Training-data sources are documented and licensed. Customer "
                b"prompts are never used for training. The subprocessor list is published "
                b"and updated with advance notice. The vendor provides evaluation reports "
                b"and red-team evidence. No material AI incidents have occurred in the "
                b"last 24 months. Under the EU AI Act, the vendor acts as provider and "
                b"customers act as deployers. Prompts are retained for 30 days and then "
                b"deleted. Human reviewers can override and stop generated outputs."
            ),
            expected_document_answers=safe,
            portal_answers=_portal_answers(safe),
        ),
        SyntheticVendorCase(
            case_ref="disclosed-limitations",
            vendor="Synthetic Disclosed Limitations Vendor",
            respondent_ref="risk-office",
            document=(
                b"System provenance is unknown. Customer data may be used for training. "
                b"Subprocessors are not disclosed. Evaluation results are unavailable. "
                b"A material AI incident occurred and remains under investigation. Under "
                b"the EU AI Act the provider role is not determined. Prompts are retained "
                b"indefinitely. The service operates without human oversight."
            ),
            expected_document_answers=gaps,
            portal_answers=_portal_answers(gaps),
        ),
    )


def _require_rejected(callable_: Any, label: str) -> None:
    try:
        callable_()
    except PilotContractDrift:
        return
    raise PilotInvariantError(f"{label} mutation was not rejected")


def _drift_probes(
    questionnaire: Mapping[str, Any],
    document: Mapping[str, Any],
    *,
    question_ids: frozenset[str],
) -> dict[str, bool]:
    digest = str(questionnaire["template_digest"])
    changed_digest = "0" * 64 if digest != "0" * 64 else "1" * 64
    questionnaire_field = copy.deepcopy(dict(questionnaire))
    questionnaire_field["unexpected"] = True
    questionnaire_digest = copy.deepcopy(dict(questionnaire))
    questionnaire_digest["template_digest"] = changed_digest
    document_field = copy.deepcopy(dict(document))
    document_field["unexpected"] = True
    citation_binding = copy.deepcopy(dict(document))
    first_answer = next(iter(citation_binding["answers"].values()))
    first_answer["citations"][0]["document_sha256"] = "0" * 64

    _require_rejected(
        lambda: validate_questionnaire_contract(
            questionnaire_field, expected_digest=digest
        ),
        "questionnaire field",
    )
    _require_rejected(
        lambda: validate_questionnaire_contract(
            questionnaire_digest, expected_digest=digest
        ),
        "questionnaire digest",
    )
    _require_rejected(
        lambda: validate_document_contract(
            document_field, question_ids=question_ids
        ),
        "document field",
    )
    _require_rejected(
        lambda: validate_document_contract(
            citation_binding, question_ids=question_ids
        ),
        "citation binding",
    )
    return {
        "questionnaire_unknown_field_rejected": True,
        "questionnaire_digest_mismatch_rejected": True,
        "document_unknown_field_rejected": True,
        "document_citation_binding_mismatch_rejected": True,
    }


def _case_result(
    *,
    case: SyntheticVendorCase,
    run_id: str,
    declaration: PilotDeclaration,
    tenant: str,
    isolation_tenant: str,
    questionnaire_digest: str,
    question_ids: frozenset[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    case_vendor = f"{case.vendor} {run_id}"
    with tenant_scope(tenant=tenant):
        extracted = vendor_risk.extract_answers_from_document(
            case.document,
            case.mime,
            source="synthetic-pilot",
            document_id=case.case_ref,
            ref={"estate_ref": declaration.estate_ref, "case_ref": case.case_ref},
        )
        document_contract = validate_document_contract(
            extracted,
            question_ids=question_ids,
            expected_document_sha256=hashlib.sha256(case.document).hexdigest(),
        )
        if document_contract["answers"] != dict(case.expected_document_answers):
            raise PilotInvariantError("representative document answers drifted")
        created = portal.create_invitation(
            vendor=case_vendor,
            actor=declaration.authorized_by,
            expires_in_seconds=3_600,
        )
        invitation = _validate_invitation(
            created.get("invitation"), expected_status="active"
        )
        token = created.get("token")
        if not isinstance(token, str):
            raise PilotInvariantError("invitation token was not disclosed once")
        portal_questionnaire = portal.questionnaire_for_token(token)
        observed_contract = _validate_portal_questionnaire(
            portal_questionnaire,
            expected_digest=questionnaire_digest,
        )
        if invitation["questionnaire_digest"] != observed_contract["template_digest"]:
            raise PilotInvariantError("invitation questionnaire binding drifted")

    with tenant_scope(tenant=isolation_tenant):
        try:
            portal.questionnaire_for_token(token)
        except portal.AIVendorPortalAuthError:
            token_isolated = True
        else:
            raise PilotInvariantError("invitation token crossed tenant authority")
        if portal.get_invitation(invitation["invitation_id"]) is not None:
            raise PilotInvariantError("invitation metadata crossed tenant authority")

    idempotency_key = f"{run_id}:{case.case_ref}:submission"
    with tenant_scope(tenant=tenant):
        first = _validate_submission(
            portal.submit_response(
                token,
                answers=case.portal_answers,
                respondent_ref=case.respondent_ref,
                attested=True,
                idempotency_key=idempotency_key,
            ),
            expected_status="pending_import",
        )
        retry = _validate_submission(
            portal.submit_response(
                token,
                answers=case.portal_answers,
                respondent_ref=case.respondent_ref,
                attested=True,
                idempotency_key=idempotency_key,
            ),
            expected_status="pending_import",
        )
        if first != retry:
            raise PilotInvariantError("exact vendor response retry was not idempotent")
        imported = portal.import_submission(
            first["submission_id"],
            actor=declaration.authorized_by,
            expected_revision=first["revision"],
        )
        replay = portal.import_submission(
            first["submission_id"],
            actor=declaration.authorized_by,
            expected_revision=first["revision"],
        )
        if imported.get("idempotent_replay") is not False or replay.get(
            "idempotent_replay"
        ) is not True:
            raise PilotInvariantError("submission import replay was not idempotent")
        assessment = imported.get("assessment")
        if not isinstance(assessment, Mapping):
            raise PilotInvariantError("downstream assessment was not returned")
        if (
            assessment.get("status") != "pending_review"
            or assessment.get("review_required") is not True
            or assessment.get("review") is not None
        ):
            raise PilotInvariantError("downstream human review was bypassed")
        sources = assessment.get("answer_sources")
        if not isinstance(sources, Mapping) or {
            source.get("kind")
            for source in sources.values()
            if isinstance(source, Mapping)
        } != {"vendor_attested"}:
            raise PilotInvariantError("vendor attestation provenance was lost")
        imported_submission = _validate_submission(
            replay.get("submission"), expected_status="imported"
        )

    with tenant_scope(tenant=isolation_tenant):
        if portal.get_submission(first["submission_id"]) is not None:
            raise PilotInvariantError("submission crossed tenant authority")
        if vendor_risk.get_vendor_assessment(str(assessment["id"])) is not None:
            raise PilotInvariantError("assessment crossed tenant authority")

    result = {
        "case_ref": case.case_ref,
        "vendor_ref": _opaque_ref("VENDOR-", case_vendor),
        "document_sha256": document_contract["document_sha256"],
        "document_contract_sha256": document_contract["contract_sha256"],
        "document_evidence_binding_sha256": document_contract[
            "evidence_binding_sha256"
        ],
        "document_answered": document_contract["answered"],
        "document_unanswered": document_contract["unanswered"],
        "invitation_ref": _opaque_ref("INV-", invitation["invitation_id"]),
        "submission_ref": _opaque_ref("SUB-", first["submission_id"]),
        "assessment_ref": _opaque_ref("ASM-", assessment["id"]),
        "response_sha256": first["response_sha256"],
        "exact_submission_retry": True,
        "import_idempotent_replay": True,
        "tenant_token_isolation": token_isolated,
        "tenant_record_isolation": True,
        "human_review_pending": True,
        "vendor_attestation_only": True,
        "final_submission_revision": imported_submission["revision"],
    }
    return result, extracted


def _revocation_drill(
    *,
    declaration: PilotDeclaration,
    run_id: str,
    tenant: str,
    isolation_tenant: str,
) -> dict[str, Any]:
    with tenant_scope(tenant=tenant):
        created = portal.create_invitation(
            vendor=f"Synthetic Revocation Drill {run_id}",
            actor=declaration.authorized_by,
            expires_in_seconds=3_600,
        )
        invitation = _validate_invitation(
            created.get("invitation"), expected_status="active"
        )
        token = str(created.get("token") or "")
    with tenant_scope(tenant=isolation_tenant):
        try:
            portal.questionnaire_for_token(token)
        except portal.AIVendorPortalAuthError:
            cross_tenant_rejected = True
        else:
            raise PilotInvariantError("revocation token crossed tenant authority")
    with tenant_scope(tenant=tenant):
        revoked = _validate_invitation(
            portal.revoke_invitation(
                invitation["invitation_id"],
                reason="Synthetic pilot revocation drill completed.",
                actor=declaration.authorized_by,
                expected_revision=invitation["revision"],
            ),
            expected_status="revoked",
        )
        try:
            portal.questionnaire_for_token(token)
        except portal.AIVendorPortalAuthError:
            revoked_token_rejected = True
        else:
            raise PilotInvariantError("revoked invitation remained usable")
    return {
        "invitation_ref": _opaque_ref("INV-", revoked["invitation_id"]),
        "cross_tenant_access_rejected": cross_tenant_rejected,
        "revoked_token_rejected": revoked_token_rejected,
        "revocation_revision": revoked["revision"],
    }


def run_local_vendor_portal_pilot(
    *,
    declaration: PilotDeclaration,
    tenant: str,
    isolation_tenant: str,
    cases: Sequence[SyntheticVendorCase] | None = None,
    expected_questionnaire_digest: str | None = None,
    run_id: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Run the local pilot and return unsigned, checksum-bound evidence.

    The embedded SHA-256 detects accidental changes when the expected artifact
    digest is held separately.  It is not a signature or tamper-evidence against
    an actor who can rewrite the artifact and recompute the checksum.
    """

    declaration.validate()
    active_tenant = _bounded_text(tenant, "tenant", 128)
    foreign_tenant = _bounded_text(isolation_tenant, "isolation tenant", 128)
    if active_tenant == foreign_tenant:
        raise PilotConsentError("tenant and isolation_tenant must differ")
    operation_id = run_id or "VPIL-" + secrets.token_hex(12)
    if not _RUN_RE.fullmatch(str(operation_id)):
        raise PilotConsentError("run_id is invalid")
    selected = tuple(
        representative_synthetic_cases() if cases is None else cases
    )
    if not 1 <= len(selected) <= _MAX_CASES:
        raise PilotConsentError(f"pilot requires between 1 and {_MAX_CASES} cases")

    with tenant_scope(tenant=active_tenant):
        baseline = vendor_risk.questionnaire()
    contract = validate_questionnaire_contract(
        baseline,
        expected_digest=expected_questionnaire_digest,
    )
    question_ids = frozenset(contract["question_ids"])
    if question_ids != {question.id for question in vendor_risk.AI_VENDOR_QUESTIONS}:
        raise PilotContractDrift("questionnaire identity drifted from runtime policy")
    for case in selected:
        case.validate(question_ids)
    if len({case.case_ref for case in selected}) != len(selected):
        raise PilotConsentError("pilot case references must be unique")

    case_results: list[dict[str, Any]] = []
    first_document: dict[str, Any] | None = None
    for case in selected:
        result, extracted = _case_result(
            case=case,
            run_id=operation_id,
            declaration=declaration,
            tenant=active_tenant,
            isolation_tenant=foreign_tenant,
            questionnaire_digest=contract["template_digest"],
            question_ids=question_ids,
        )
        case_results.append(result)
        if first_document is None:
            first_document = extracted
    if first_document is None:
        raise PilotInvariantError("pilot did not produce a document contract")

    probes = _drift_probes(
        baseline,
        first_document,
        question_ids=question_ids,
    )
    revocation = _revocation_drill(
        declaration=declaration,
        run_id=operation_id,
        tenant=active_tenant,
        isolation_tenant=foreign_tenant,
    )
    generated_at = time.time() if now is None else float(now)
    if not math.isfinite(generated_at) or generated_at <= 0:
        raise PilotConsentError("now must be a positive finite timestamp")
    evidence: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "run_id": operation_id,
        "status": "passed_local_synthetic",
        "generated_at": generated_at,
        "scope": {
            "mode": "local_synthetic",
            "estate_ref_sha256": hashlib.sha256(
                declaration.estate_ref.encode("utf-8")
            ).hexdigest(),
            "tenant_sha256": hashlib.sha256(active_tenant.encode("utf-8")).hexdigest(),
            "isolation_tenant_sha256": hashlib.sha256(
                foreign_tenant.encode("utf-8")
            ).hexdigest(),
            "operator_attested_consent": True,
            "consent_record_sha256": declaration.consent_record_sha256,
            "synthetic_documents_only": True,
            "case_count": len(case_results),
        },
        "contracts": {
            "questionnaire_schema": contract["schema"],
            "questionnaire_revision": contract["template_revision"],
            "questionnaire_digest": contract["template_digest"],
            "questionnaire_contract_sha256": contract["contract_sha256"],
            "questionnaire_pin_state": (
                "operator_pinned"
                if expected_questionnaire_digest is not None
                else "baseline_captured_this_run"
            ),
            "document_schema": vendor_risk.AI_VENDOR_DOCUMENT_SCHEMA,
            "document_contract_profile_sha256": _sha256(
                {
                    "document": sorted(_DOCUMENT_KEYS),
                    "evidence": sorted(_DOCUMENT_EVIDENCE_KEYS),
                    "evidence_optional": sorted(_DOCUMENT_EVIDENCE_OPTIONAL_KEYS),
                    "answer": sorted(_DOCUMENT_ANSWER_KEYS),
                    "citation": sorted(_CITATION_KEYS),
                }
            ),
            "drift_probes": probes,
        },
        "operations": {
            "representative_document_contracts": "passed",
            "portal_questionnaire_contract": "passed",
            "exact_submission_retry": "passed",
            "import_idempotent_replay": "passed_same_process",
            "invitation_revocation": "passed",
            "cross_tenant_isolation": "passed",
            "downstream_human_review_import": "passed_pending_human_decision",
            "local_ecosystem_workflow": "passed",
        },
        "cases": case_results,
        "revocation": revocation,
        "acceptance_boundary": {
            "notice": PILOT_BOUNDARY,
            "live_vendor_acceptance": "not_run",
            "live_customer_acceptance": "not_run",
            "live_ecosystem_acceptance": "not_run",
            "independent_document_verification": "not_run",
            "legal_or_certification_outcome": False,
            "evidence_integrity": "unsigned_local_checksum_only",
            "tamper_evident_claimed": False,
        },
    }
    evidence["evidence_sha256"] = _sha256(evidence)
    if not verify_pilot_evidence(evidence):
        raise PilotInvariantError("generated pilot evidence did not verify")
    return evidence


def verify_pilot_evidence(value: object) -> bool:
    """Verify the envelope's unsigned local checksum and fixed boundaries."""

    if not isinstance(value, Mapping):
        return False
    row = dict(value)
    digest = row.pop("evidence_sha256", None)
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        return False
    try:
        if _sha256(row) != digest:
            return False
    except VendorPortalPilotError:
        return False
    return (
        row.get("schema") == EVIDENCE_SCHEMA
        and row.get("status") == "passed_local_synthetic"
        and isinstance(row.get("scope"), Mapping)
        and row["scope"].get("operator_attested_consent") is True
        and row["scope"].get("synthetic_documents_only") is True
        and isinstance(row.get("acceptance_boundary"), Mapping)
        and row["acceptance_boundary"].get("live_vendor_acceptance") == "not_run"
        and row["acceptance_boundary"].get("live_ecosystem_acceptance") == "not_run"
        and row["acceptance_boundary"].get("evidence_integrity")
        == "unsigned_local_checksum_only"
        and row["acceptance_boundary"].get("tamper_evident_claimed") is False
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--isolation-tenant", required=True)
    parser.add_argument("--estate-ref", required=True)
    parser.add_argument("--authorized-by", required=True)
    parser.add_argument("--consent-record-sha256", required=True)
    parser.add_argument("--expected-questionnaire-digest")
    parser.add_argument("--run-id")
    parser.add_argument("--consent-confirmed", action="store_true")
    parser.add_argument("--synthetic-documents-only", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    declaration = PilotDeclaration(
        estate_ref=args.estate_ref,
        authorized_by=args.authorized_by,
        consent_record_sha256=args.consent_record_sha256,
        consent_confirmed=args.consent_confirmed,
        synthetic_documents_only=args.synthetic_documents_only,
    )
    try:
        evidence = run_local_vendor_portal_pilot(
            declaration=declaration,
            tenant=args.tenant,
            isolation_tenant=args.isolation_tenant,
            expected_questionnaire_digest=args.expected_questionnaire_digest,
            run_id=args.run_id,
        )
    except Exception as exc:
        failure = {
            "schema": EVIDENCE_SCHEMA,
            "status": "failed",
            "error_class": type(exc).__name__,
            "boundary": PILOT_BOUNDARY,
        }
        print(json.dumps(failure, sort_keys=True, separators=(",", ":")))
        return 2
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    if args.output is None:
        print(encoded)
    else:
        try:
            with args.output.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded + "\n")
        except FileExistsError:
            print(
                json.dumps(
                    {
                        "schema": EVIDENCE_SCHEMA,
                        "status": "failed",
                        "error_class": "OutputExists",
                        "boundary": PILOT_BOUNDARY,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DOCUMENT_EVIDENCE_SCHEMA",
    "EVIDENCE_SCHEMA",
    "PILOT_BOUNDARY",
    "PilotConsentError",
    "PilotContractDrift",
    "PilotDeclaration",
    "PilotInvariantError",
    "SyntheticVendorCase",
    "VendorPortalPilotError",
    "main",
    "representative_synthetic_cases",
    "run_local_vendor_portal_pilot",
    "validate_document_contract",
    "validate_questionnaire_contract",
    "verify_pilot_evidence",
]
