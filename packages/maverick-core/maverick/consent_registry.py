"""Consumer consent receipts and preferences: the durable record of choice.

The record-keeping half of a consent-management program: per-subject consent
receipts (purpose, policy version, channel, granted/withdrawn, digest) and a
current-preference projection, importable from a OneTrust consent export and
exportable for audit. Subject identifiers are stored as salted digests — the
register proves WHAT was consented to and WHEN without becoming a directory
of the people themselves; the caller keeps the mapping.

Stated plainly: this is NOT a cookie banner, an IAB TCF-certified CMP, or a
preference-center UI — it is the governed registry those front ends write
to and the audit trail a regulator asks for. Never legal advice.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .governed_records import GovernedRecordStore

RECEIPT_SCHEMA = "lightwork.consent-receipt.v1"
STATUSES = ("granted", "withdrawn")
CHANNELS = ("web", "mobile", "email", "phone", "paper", "api", "import")

_RECEIPTS = GovernedRecordStore("consent_registry", "CCR", "consent_receipt")
_MAX_LIST = 500
_MAX_HISTORY = 40


class ConsentRegistryError(RuntimeError):
    """Base failure for the governed consent registry."""


class ConsentRegistryConfigError(ConsentRegistryError):
    """The feature is not enabled or configured safely."""


def get_consent_registry_config() -> dict[str, Any]:
    from .config import load_global_config

    try:
        config = load_global_config()
    except Exception as exc:
        raise ConsentRegistryConfigError(
            "consent_registry configuration is unavailable"
        ) from exc
    section = (config or {}).get("consent_registry") or {}
    if not isinstance(section, dict):
        raise ConsentRegistryConfigError("consent_registry must be a table")
    flag = section.get("enable", False)
    if not isinstance(flag, bool):
        raise ConsentRegistryConfigError(
            "consent_registry.enable must be a boolean"
        )
    salt = str(section.get("subject_salt") or "").strip()
    if flag and (len(salt) < 16 or len(salt) > 256):
        raise ConsentRegistryConfigError(
            "consent_registry.subject_salt must be 16-256 characters when "
            "the registry is enabled (it pseudonymizes subject identifiers)"
        )
    return {"enable": flag, "subject_salt": salt}


def enabled() -> bool:
    try:
        return get_consent_registry_config()["enable"] is True
    except Exception:
        return False


def _require_enabled() -> dict[str, Any]:
    settings = get_consent_registry_config()
    if settings["enable"] is not True:
        raise ConsentRegistryConfigError(
            "the consent registry requires [consent_registry] enable = true"
        )
    return settings


def _text(value: object, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError(f"{label} must be 1-{limit} characters")
    return text


def subject_digest(subject_ref: str) -> str:
    """The salted, one-way subject identifier this register stores."""

    settings = _require_enabled()
    reference = _text(subject_ref, "subject_ref", 512)
    return hashlib.sha256(
        f"consent-subject:{settings['subject_salt']}:{reference}".encode()
    ).hexdigest()[:32]


def _record_id(subject: str, purpose: str) -> str:
    return (
        "CCR-"
        + hashlib.sha256(f"{subject}:{purpose}".encode()).hexdigest()[:24]
    )


def record_consent(
    subject_ref: str,
    *,
    purpose: str,
    status: str,
    policy_version: str,
    channel: str,
    actor: str,
    recorded_at: float | None = None,
) -> dict[str, Any]:
    """Append one consent event; the record keeps a bounded history.

    The receipt digest binds subject digest, purpose, status, policy
    version, and timestamp, so an exported receipt is self-checking.
    """

    _require_enabled()
    if status not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of: {', '.join(CHANNELS)}")
    purpose_key = _text(purpose, "purpose", 128).lower().replace(" ", "_")
    version = _text(policy_version, "policy_version", 64)
    operator = _text(actor, "actor", 256)
    stamp = time.time() if recorded_at is None else float(recorded_at)
    if stamp <= 0:
        raise ValueError("recorded_at must be positive")
    subject = subject_digest(subject_ref)
    event = {
        "status": status,
        "policy_version": version,
        "channel": channel,
        "recorded_at": stamp,
        "recorded_by": operator,
    }
    event["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            {"subject": subject, "purpose": purpose_key, **event},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    record_id = _record_id(subject, purpose_key)

    def mutate(value: dict[str, Any]) -> None:
        history = list(value.get("history") or [])
        history.append(event)
        value["history"] = history[-_MAX_HISTORY:]
        value["current_status"] = status
        value["policy_version"] = version
        value["last_recorded_at"] = stamp

    current = _RECEIPTS.get(record_id)
    if current is None:
        record = {
            "id": record_id,
            "schema": RECEIPT_SCHEMA,
            "subject_digest": subject,
            "purpose": purpose_key,
            "current_status": status,
            "policy_version": version,
            "history": [event],
            "first_recorded_at": stamp,
            "last_recorded_at": stamp,
        }
        return dict(
            _RECEIPTS.create(record, action="record_consent", actor=operator)
        )
    saved = _RECEIPTS.update(
        record_id,
        mutate,
        expected_revision=int(current.get("revision") or 0),
        action="record_consent",
        actor=operator,
    )
    if saved is None:
        raise ConsentRegistryError("the consent record changed during save")
    return dict(saved)


def withdraw_consent(
    subject_ref: str,
    *,
    purpose: str,
    policy_version: str,
    channel: str,
    actor: str,
) -> dict[str, Any]:
    return record_consent(
        subject_ref,
        purpose=purpose,
        status="withdrawn",
        policy_version=policy_version,
        channel=channel,
        actor=actor,
    )


def subject_preferences(subject_ref: str) -> dict[str, Any]:
    """The subject's current per-purpose choices — the preference projection."""

    _require_enabled()
    subject = subject_digest(subject_ref)
    purposes: dict[str, Any] = {}
    for row in _RECEIPTS.list(limit=_MAX_LIST * 4):
        if row.get("subject_digest") != subject:
            continue
        purposes[str(row.get("purpose"))] = {
            "status": row.get("current_status"),
            "policy_version": row.get("policy_version"),
            "last_recorded_at": row.get("last_recorded_at"),
        }
    return {"subject_digest": subject, "purposes": dict(sorted(purposes.items()))}


def consent_posture() -> dict[str, Any]:
    _require_enabled()
    rows = _RECEIPTS.list(limit=_MAX_LIST * 4)
    by_purpose: dict[str, dict[str, int]] = {}
    for row in rows:
        purpose = str(row.get("purpose") or "unknown")
        bucket = by_purpose.setdefault(purpose, {"granted": 0, "withdrawn": 0})
        status = str(row.get("current_status") or "")
        if status in bucket:
            bucket[status] += 1
    return {
        "records": len(rows),
        "by_purpose": dict(sorted(by_purpose.items())),
    }


def export_receipts(*, limit: int = 500) -> list[dict[str, Any]]:
    """Audit export: bounded, digest-only, newest last-activity first."""

    _require_enabled()
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
        raise ValueError("limit must be between 1 and 2000")
    rows = _RECEIPTS.list(limit=limit)
    rows.sort(key=lambda row: -float(row.get("last_recorded_at") or 0.0))
    return [
        {
            "subject_digest": row.get("subject_digest"),
            "purpose": row.get("purpose"),
            "current_status": row.get("current_status"),
            "policy_version": row.get("policy_version"),
            "history": row.get("history"),
        }
        for row in rows
    ]


#: OneTrust consent-export column aliases (case-insensitive).
_ONETRUST_CONSENT_HEADERS = {
    "subject": ("data subject identifier", "identifier", "subject",
                "email", "data subject"),
    "purpose": ("purpose", "purpose name", "collection point purpose",
                "processing purpose"),
    "status": ("status", "consent status", "transaction type"),
    "policy_version": ("purpose version", "policy version", "version",
                       "notice version"),
    "recorded_at": ("transaction date", "consent date", "date",
                    "last updated"),
}

_STATUS_ALIASES = {
    "granted": "granted", "confirmed": "granted", "active": "granted",
    "opt-in": "granted", "opt in": "granted", "given": "granted",
    "withdrawn": "withdrawn", "opt-out": "withdrawn", "opt out": "withdrawn",
    "expired": "withdrawn", "revoked": "withdrawn",
}


def import_onetrust_consents(
    csv_text: str, *, imported_by: str = "",
) -> dict[str, int]:
    """Import a OneTrust consent export; unknown statuses are skipped."""

    _require_enabled()
    import csv as _csv
    import io

    reader = _csv.DictReader(io.StringIO(csv_text or ""))
    imported = 0
    skipped = 0
    for row in list(reader)[:10_000]:
        low = {
            (key or "").strip().lower(): (value or "").strip()
            for key, value in row.items()
        }
        entry: dict[str, str] = {}
        for field, aliases in _ONETRUST_CONSENT_HEADERS.items():
            for alias in aliases:
                if low.get(alias):
                    entry[field] = low[alias]
                    break
        status = _STATUS_ALIASES.get(entry.get("status", "").lower())
        if not entry.get("subject") or not entry.get("purpose") or not status:
            skipped += 1
            continue
        recorded_at = None
        raw_when = entry.get("recorded_at", "")
        if raw_when:
            from contextlib import suppress

            with suppress(ValueError):
                from datetime import datetime, timezone

                normalized = raw_when.replace("Z", "+00:00")
                parsed = datetime.fromisoformat(normalized)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                recorded_at = parsed.timestamp()
        try:
            record_consent(
                entry["subject"],
                purpose=entry["purpose"],
                status=status,
                policy_version=entry.get("policy_version") or "imported",
                channel="import",
                actor=imported_by or "import:onetrust",
                recorded_at=recorded_at,
            )
            imported += 1
        except Exception:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


__all__ = [
    "CHANNELS",
    "ConsentRegistryConfigError",
    "ConsentRegistryError",
    "RECEIPT_SCHEMA",
    "STATUSES",
    "consent_posture",
    "enabled",
    "export_receipts",
    "get_consent_registry_config",
    "import_onetrust_consents",
    "record_consent",
    "subject_digest",
    "subject_preferences",
    "withdraw_consent",
]
