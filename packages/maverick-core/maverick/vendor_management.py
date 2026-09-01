"""Unified third-party vendor management: one register over every record.

The displacement gap between our specialist reviews (DPA reviews, paper
reviews, AI vendor assessments, vendor-risk questionnaires) and a
OneTrust-style third-party program is the REGISTER: one governed row per
vendor carrying criticality, lifecycle status, links to every specialist
record by reference, outbound questionnaire requests (hosted-form tokens the
vendor answers without an account), and bounded monitoring notes. This
module is that register — CAS-guarded, tenant-scoped, and import-friendly
(OneTrust vendor CSV comes in with provenance and stays editable).

Decision support for the operator's own third-party program — never a
security rating, a certification of any vendor, or advice.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Mapping
from typing import Any

from .governed_records import GovernedRecordStore

VENDOR_SCHEMA = "lightwork.vendor-record.v1"
CRITICALITIES = ("low", "medium", "high", "critical")
STATUSES = ("active", "under_review", "offboarded")
LINK_KINDS = (
    "dpa_review",
    "paper_review",
    "ai_vendor_assessment",
    "assessment",
    "contract",
    "other",
)

CONTRACT_KINDS = ("dpa", "msa", "sla", "addendum", "other")
#: Severity ladder for questionnaire scoring; "clear" = no risk findings.
_SEVERITY_ORDER = ("low", "medium", "high", "critical")

_VENDORS = GovernedRecordStore("vendor_management", "VMR", "vendor_record")
#: Store capacity and the matching full-scan window. Projections that carry
#: correctness (token lookup, renewals, posture) must scan the WHOLE store:
#: a smaller window silently drops exactly the stale vendors most likely to
#: hold overdue paper, and 404s valid questionnaire links.
_MAX_RECORDS = 10_000
_SCAN_LIMIT = _MAX_RECORDS + 1
_MAX_LIST = 500
_MAX_LINKS = 50
_MAX_NOTES = 50
_MAX_QUESTIONNAIRES = 20
_MAX_CONTRACTS = 20


class VendorManagementError(RuntimeError):
    """Base failure for governed vendor management."""


class VendorManagementConfigError(VendorManagementError):
    """The feature is not enabled or configured safely."""


def get_vendor_management_config() -> dict[str, Any]:
    from .config import load_global_config

    try:
        config = load_global_config()
    except Exception as exc:
        raise VendorManagementConfigError(
            "vendor_management configuration is unavailable"
        ) from exc
    section = (config or {}).get("vendor_management") or {}
    if not isinstance(section, dict):
        raise VendorManagementConfigError("vendor_management must be a table")
    flag = section.get("enable", False)
    if not isinstance(flag, bool):
        raise VendorManagementConfigError(
            "vendor_management.enable must be a boolean"
        )
    return {"enable": flag}


def enabled() -> bool:
    try:
        return get_vendor_management_config()["enable"] is True
    except Exception:
        return False


def _require_enabled() -> None:
    if not enabled():
        raise VendorManagementConfigError(
            "vendor management requires [vendor_management] enable = true"
        )


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _text(value: object, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError(f"{label} must be 1-{limit} characters")
    return text


def _vendor_id(name: str) -> str:
    return f"VMR-{_sha256(name.strip().lower())[:24]}"


def register_vendor(
    name: str,
    *,
    category: str,
    criticality: str,
    owner: str,
    actor: str,
    source: str = "manual",
) -> dict[str, Any]:
    """Register one vendor; the same name is one record, never a duplicate."""

    _require_enabled()
    if criticality not in CRITICALITIES:
        raise ValueError(f"criticality must be one of: {', '.join(CRITICALITIES)}")
    vendor_name = _text(name, "name", 256)
    operator = _text(actor, "actor", 256)
    record = {
        "id": _vendor_id(vendor_name),
        "schema": VENDOR_SCHEMA,
        "name": vendor_name,
        "category": _text(category, "category", 128),
        "criticality": criticality,
        "owner": _text(owner, "owner", 256),
        "status": "active",
        "links": [],
        "questionnaires": [],
        "monitoring_notes": [],
        "source": _text(source, "source", 64),
        "registered_by": operator,
        "registered_at": time.time(),
    }
    return dict(
        _VENDORS.create(
            record, action="register_vendor", actor=operator,
            max_records=_MAX_RECORDS,
        )
    )


def set_vendor_status(
    vendor_id: str,
    status: str,
    *,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    _require_enabled()
    if status not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    operator = _text(actor, "actor", 256)

    def mutate(value: dict[str, Any]) -> None:
        value["status"] = status

    saved = _VENDORS.update(
        _text(vendor_id, "vendor_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="set_vendor_status",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"vendor is not registered: {vendor_id}")
    return dict(saved)


def link_vendor_record(
    vendor_id: str,
    *,
    kind: str,
    reference: str,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Attach one specialist record (by reference) to the vendor row."""

    _require_enabled()
    if kind not in LINK_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(LINK_KINDS)}")
    operator = _text(actor, "actor", 256)
    where = _text(reference, "reference", 512)

    def mutate(value: dict[str, Any]) -> None:
        links = list(value.get("links") or [])
        links.append(
            {
                "kind": kind,
                "reference": where,
                "linked_by": operator,
                "linked_at": time.time(),
            }
        )
        value["links"] = links[-_MAX_LINKS:]

    saved = _VENDORS.update(
        _text(vendor_id, "vendor_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="link_vendor_record",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"vendor is not registered: {vendor_id}")
    return dict(saved)


def request_questionnaire(
    vendor_id: str,
    *,
    assessment_type: str,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Mint a hosted-form questionnaire request the vendor answers by link.

    The unguessable token addresses the platform's public ``/form/{token}``
    endpoint; responses are read back with :func:`questionnaire_responses`.
    The assessment type must be a registered questionnaire template.
    """

    _require_enabled()
    from . import form_store
    from .assessment import TEMPLATES

    kind = str(assessment_type or "").strip().lower()
    if kind not in TEMPLATES:
        raise ValueError(
            f"assessment_type must be a registered template, one of: "
            f"{', '.join(sorted(TEMPLATES))}"
        )
    # The hosted form persists through form_store, which keeps at most
    # _MAX_FIELDS fields per submission (one is reserved for the free-text
    # note). Minting a questionnaire whose answers would be silently
    # truncated — and then scored as "clear" on the surviving subset — is
    # refused outright rather than quietly corrupting an audit record.
    field_cap = int(getattr(form_store, "_MAX_FIELDS", 50)) - 1
    questions = len(TEMPLATES[kind].questions)
    if questions > field_cap:
        raise ValueError(
            f"the {kind} template has {questions} questions, but hosted-form "
            f"submissions keep at most {field_cap} answers; choose a smaller "
            "template for the questionnaire link"
        )
    operator = _text(actor, "actor", 256)
    token = secrets.token_urlsafe(24)

    def mutate(value: dict[str, Any]) -> None:
        rows = list(value.get("questionnaires") or [])
        if len(rows) >= _MAX_QUESTIONNAIRES:
            raise VendorManagementError(
                "this vendor already has the maximum number of questionnaires"
            )
        rows.append(
            {
                "token": token,
                "assessment_type": kind,
                "status": "sent",
                "requested_by": operator,
                "requested_at": time.time(),
            }
        )
        value["questionnaires"] = rows

    saved = _VENDORS.update(
        _text(vendor_id, "vendor_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="request_questionnaire",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"vendor is not registered: {vendor_id}")
    return {**dict(saved), "form_path": f"/form/{token}"}


def questionnaire_responses(vendor_id: str) -> list[dict[str, Any]]:
    """The vendor's questionnaire requests with any hosted-form submissions."""

    _require_enabled()
    record = _VENDORS.get(_text(vendor_id, "vendor_id", 64))
    if record is None:
        raise KeyError(f"vendor is not registered: {vendor_id}")
    from . import form_store

    out = []
    for row in record.get("questionnaires") or []:
        token = str(row.get("token") or "")
        submissions: list[dict[str, Any]] = []
        try:
            # Cursor "0" = everything; "" would be a baseline-only call
            # that returns no rows at all.
            submissions, _cursor = form_store.since(token, "0")
        except Exception:
            submissions = []
        kind = str(row.get("assessment_type") or "")
        out.append(
            {
                "assessment_type": kind,
                "status": "answered" if submissions else row.get("status"),
                "requested_at": row.get("requested_at"),
                "requested_by": row.get("requested_by"),
                "form_path": f"/form/{token}",
                "submissions": submissions[:10],
                # Latest submission scored against the template's risk
                # answers -- the questionnaire's inherent-risk read-back.
                "score": (
                    _score_submission(kind, submissions[-1])
                    if submissions else None
                ),
            }
        )
    return out


def record_monitoring_note(
    vendor_id: str,
    note: str,
    *,
    severity: str,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    _require_enabled()
    if severity not in ("info", "warning", "critical"):
        raise ValueError("severity must be info, warning, or critical")
    operator = _text(actor, "actor", 256)
    body = _text(note, "note", 2000)

    def mutate(value: dict[str, Any]) -> None:
        notes = list(value.get("monitoring_notes") or [])
        notes.append(
            {
                "note": body,
                "severity": severity,
                "recorded_by": operator,
                "recorded_at": time.time(),
            }
        )
        value["monitoring_notes"] = notes[-_MAX_NOTES:]

    saved = _VENDORS.update(
        _text(vendor_id, "vendor_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="record_monitoring_note",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"vendor is not registered: {vendor_id}")
    return dict(saved)


def record_vendor_contract(
    vendor_id: str,
    *,
    kind: str,
    reference: str,
    renews_at: float,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Record one contract (DPA, MSA, ...) with its renewal date.

    The renewal date is what the governance-alerts digest watches: a
    contract past ``renews_at`` is overdue, one inside the horizon is due
    soon. Recording the same kind again replaces that kind's row — the
    register keeps the current paper, not a history.
    """

    _require_enabled()
    if kind not in CONTRACT_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(CONTRACT_KINDS)}")
    operator = _text(actor, "actor", 256)
    where = _text(reference, "reference", 512)
    if (
        isinstance(renews_at, bool)
        or not isinstance(renews_at, (int, float))
        or not float(renews_at) > 0
    ):
        raise ValueError("renews_at must be a positive epoch timestamp")

    def mutate(value: dict[str, Any]) -> None:
        rows = [
            row for row in (value.get("contracts") or [])
            if row.get("kind") != kind
        ]
        rows.append(
            {
                "kind": kind,
                "reference": where,
                "renews_at": float(renews_at),
                "recorded_by": operator,
                "recorded_at": time.time(),
            }
        )
        value["contracts"] = rows[-_MAX_CONTRACTS:]

    saved = _VENDORS.update(
        _text(vendor_id, "vendor_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="record_vendor_contract",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"vendor is not registered: {vendor_id}")
    return dict(saved)


def contract_renewals(
    *, now: float | None = None, due_soon_days: int = 30,
) -> dict[str, list[dict[str, Any]]]:
    """Contracts past renewal and inside the due-soon horizon, register-wide."""

    _require_enabled()
    if (
        isinstance(due_soon_days, bool)
        or not isinstance(due_soon_days, int)
        or not 1 <= due_soon_days <= 365
    ):
        raise ValueError("due_soon_days must be between 1 and 365")
    current = time.time() if now is None else float(now)
    horizon = current + due_soon_days * 86_400.0
    overdue: list[dict[str, Any]] = []
    due_soon: list[dict[str, Any]] = []
    for row in _VENDORS.list(limit=_SCAN_LIMIT):
        for contract in row.get("contracts") or []:
            renews = float(contract.get("renews_at") or 0.0)
            if renews <= 0:
                continue
            entry = {
                "vendor_id": row.get("id"),
                "vendor": row.get("name"),
                "kind": contract.get("kind"),
                "renews_at": renews,
            }
            if renews < current:
                overdue.append(entry)
            elif renews <= horizon:
                due_soon.append(entry)
    overdue.sort(key=lambda entry: entry["renews_at"])
    due_soon.sort(key=lambda entry: entry["renews_at"])
    return {"overdue": overdue[:50], "due_soon": due_soon[:50]}


def _template_questions(assessment_type: str):
    from .assessment import TEMPLATES

    template = TEMPLATES.get(str(assessment_type or "").strip().lower())
    return () if template is None else template.questions


def _score_submission(
    assessment_type: str, fields: Mapping[str, Any],
) -> dict[str, Any]:
    """Score one hosted-form submission against its template's risk answers.

    The same answer vocabulary as :meth:`assessment.Assessment.evaluate`:
    the template's ``risk_answer`` raises a ``risk`` finding, ``unknown``
    raises an ``unverified`` finding (diligence gap), and ``na`` clears the
    question. The rating is the worst finding severity, ``clear`` when
    everything answered came back safe. Deterministic and explainable:
    every finding names its question.
    """

    findings: list[dict[str, Any]] = []
    answered = 0
    questions = _template_questions(assessment_type)
    for question in questions:
        raw = fields.get(question.id)
        if raw is None or str(raw).strip() == "":
            continue
        answered += 1
        response = str(raw).strip().lower()
        if response == "na":
            continue
        if response == str(question.risk_answer).strip().lower():
            findings.append(
                {
                    "qid": question.id,
                    "section": question.section,
                    "severity": question.severity,
                    "kind": "risk",
                }
            )
        elif response == "unknown":
            findings.append(
                {
                    "qid": question.id,
                    "section": question.section,
                    "severity": question.severity,
                    "kind": "unverified",
                }
            )
    rating = "clear"
    for severity in _SEVERITY_ORDER:
        if any(finding["severity"] == severity for finding in findings):
            rating = severity
    return {
        "answered": answered,
        "total_questions": len(questions),
        "findings": findings,
        "rating": rating,
    }


def questionnaire_for_token(token: str) -> dict[str, Any] | None:
    """Resolve a hosted-form token to its vendor questionnaire, or None.

    Powers the public questionnaire page: given only the unguessable
    token, return the template's questions so the vendor can answer
    without an account. Never returns other vendors' data — one token,
    one questionnaire.
    """

    if not enabled():
        return None
    needle = str(token or "").strip()
    if not needle:
        return None
    for row in _VENDORS.list(limit=_SCAN_LIMIT):
        for entry in row.get("questionnaires") or []:
            if str(entry.get("token") or "") != needle:
                continue
            kind = str(entry.get("assessment_type") or "")
            return {
                "vendor_id": row.get("id"),
                "vendor": row.get("name"),
                "assessment_type": kind,
                "questions": [
                    {
                        "qid": question.id,
                        "section": question.section,
                        "text": question.text,
                    }
                    for question in _template_questions(kind)
                ],
            }
    return None


def get_vendor(vendor_id: str) -> dict[str, Any] | None:
    _require_enabled()
    record = _VENDORS.get(_text(vendor_id, "vendor_id", 64))
    return None if record is None else dict(record)


def list_vendors(*, status: str | None = None) -> list[dict[str, Any]]:
    _require_enabled()
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    rows = _VENDORS.list(limit=_SCAN_LIMIT)
    rows.sort(key=lambda row: str(row.get("name") or "").lower())
    return [
        dict(row) for row in rows
        if status is None or row.get("status") == status
    ]


def vendor_posture() -> dict[str, Any]:
    _require_enabled()
    rows = _VENDORS.list(limit=_SCAN_LIMIT)
    by_status = dict.fromkeys(STATUSES, 0)
    by_criticality = dict.fromkeys(CRITICALITIES, 0)
    critical_alerts = 0
    for row in rows:
        status = str(row.get("status") or "")
        if status in by_status:
            by_status[status] += 1
        level = str(row.get("criticality") or "")
        if level in by_criticality:
            by_criticality[level] += 1
        critical_alerts += sum(
            1
            for note in (row.get("monitoring_notes") or [])
            if isinstance(note, Mapping) and note.get("severity") == "critical"
        )
    return {
        "vendors": len(rows),
        "by_status": by_status,
        "by_criticality": by_criticality,
        "critical_monitoring_notes": critical_alerts,
    }


#: OneTrust third-party/vendor export column aliases (their names drift
#: across versions and templates; matching is case-insensitive).
_ONETRUST_VENDOR_HEADERS = {
    "name": ("vendor name", "third party name", "organization name",
             "vendor", "name"),
    "category": ("vendor type", "category", "service category", "type"),
    "criticality": ("criticality", "risk level", "inherent risk",
                    "tier", "vendor tier"),
    "owner": ("business owner", "relationship owner", "owner",
              "internal owner"),
    "status": ("status", "lifecycle status", "vendor status"),
}

_CRITICALITY_ALIASES = {
    "low": "low", "tier 3": "low", "tier3": "low",
    "medium": "medium", "moderate": "medium", "tier 2": "medium",
    "tier2": "medium",
    "high": "high", "tier 1": "high", "tier1": "high",
    "critical": "critical", "very high": "critical",
}


def import_onetrust_vendors(
    csv_text: str, *, imported_by: str = "",
) -> list[dict[str, Any]]:
    """Import a OneTrust third-party export into the vendor register.

    Tolerant of column drift; rows without a recognizable vendor name are
    skipped rather than guessed at, unknown criticalities default to
    ``medium``, and a vendor already registered is left untouched (their
    record here is the record of truth once it exists).
    """

    _require_enabled()
    from .csv_alias import alias_rows

    imported = []
    for entry in alias_rows(csv_text, _ONETRUST_VENDOR_HEADERS, limit=2000):
        if not entry.get("name"):
            continue
        criticality = _CRITICALITY_ALIASES.get(
            entry.get("criticality", "").lower(), "medium",
        )
        try:
            saved = register_vendor(
                entry["name"],
                category=entry.get("category") or "unclassified",
                criticality=criticality,
                owner=entry.get("owner") or "unassigned",
                actor=imported_by or "import:onetrust",
                source="onetrust",
            )
        except Exception:
            continue
        imported.append(saved)
    return imported


__all__ = [
    "CONTRACT_KINDS",
    "CRITICALITIES",
    "LINK_KINDS",
    "STATUSES",
    "VENDOR_SCHEMA",
    "VendorManagementConfigError",
    "VendorManagementError",
    "contract_renewals",
    "enabled",
    "get_vendor",
    "get_vendor_management_config",
    "import_onetrust_vendors",
    "link_vendor_record",
    "list_vendors",
    "questionnaire_for_token",
    "questionnaire_responses",
    "record_monitoring_note",
    "record_vendor_contract",
    "register_vendor",
    "request_questionnaire",
    "set_vendor_status",
    "vendor_posture",
]
