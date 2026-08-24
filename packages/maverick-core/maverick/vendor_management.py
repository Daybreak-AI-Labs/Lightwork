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

_VENDORS = GovernedRecordStore("vendor_management", "VMR", "vendor_record")
_MAX_LIST = 500
_MAX_LINKS = 50
_MAX_NOTES = 50
_MAX_QUESTIONNAIRES = 20


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
        _VENDORS.create(record, action="register_vendor", actor=operator)
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
    from .assessment import TEMPLATES

    kind = str(assessment_type or "").strip().lower()
    if kind not in TEMPLATES:
        raise ValueError(
            f"assessment_type must be a registered template, one of: "
            f"{', '.join(sorted(TEMPLATES))}"
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
        out.append(
            {
                "assessment_type": row.get("assessment_type"),
                "status": "answered" if submissions else row.get("status"),
                "requested_at": row.get("requested_at"),
                "requested_by": row.get("requested_by"),
                "form_path": f"/form/{token}",
                "submissions": submissions[:10],
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


def get_vendor(vendor_id: str) -> dict[str, Any] | None:
    _require_enabled()
    record = _VENDORS.get(_text(vendor_id, "vendor_id", 64))
    return None if record is None else dict(record)


def list_vendors(*, status: str | None = None) -> list[dict[str, Any]]:
    _require_enabled()
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    rows = _VENDORS.list(limit=_MAX_LIST)
    rows.sort(key=lambda row: str(row.get("name") or "").lower())
    return [
        dict(row) for row in rows
        if status is None or row.get("status") == status
    ]


def vendor_posture() -> dict[str, Any]:
    _require_enabled()
    rows = _VENDORS.list(limit=_MAX_LIST)
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
    import csv as _csv
    import io

    reader = _csv.DictReader(io.StringIO(csv_text or ""))
    imported = []
    for row in list(reader)[:2000]:
        low = {
            (key or "").strip().lower(): (value or "").strip()
            for key, value in row.items()
        }
        entry: dict[str, str] = {}
        for field, aliases in _ONETRUST_VENDOR_HEADERS.items():
            for alias in aliases:
                if low.get(alias):
                    entry[field] = low[alias]
                    break
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
    "CRITICALITIES",
    "LINK_KINDS",
    "STATUSES",
    "VENDOR_SCHEMA",
    "VendorManagementConfigError",
    "VendorManagementError",
    "enabled",
    "get_vendor",
    "get_vendor_management_config",
    "import_onetrust_vendors",
    "link_vendor_record",
    "list_vendors",
    "questionnaire_responses",
    "record_monitoring_note",
    "register_vendor",
    "request_questionnaire",
    "set_vendor_status",
    "vendor_posture",
]
