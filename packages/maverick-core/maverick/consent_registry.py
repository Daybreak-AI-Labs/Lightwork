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

NOTICE_SCHEMA = "lightwork.privacy-notice.v1"

_RECEIPTS = GovernedRecordStore("consent_registry", "CCR", "consent_receipt")
_NOTICES = GovernedRecordStore("consent_notices", "CCN", "privacy_notice")
_MAX_LIST = 500
_MAX_HISTORY = 40
_MAX_NOTICE_BYTES = 200_000
_SCAN_MAX_BYTES = 2 * 1024 * 1024
_SCAN_TIMEOUT_SECONDS = 15.0

#: Well-known tracker/analytics hosts for the first-party site scan. A
#: bounded, explainable list — presence means "this page loads that host",
#: nothing more.
_TRACKER_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "googlesyndication.com",
    "doubleclick.net",
    "connect.facebook.net",
    "analytics.tiktok.com",
    "snap.licdn.com",
    "px.ads.linkedin.com",
    "hotjar.com",
    "mixpanel.com",
    "segment.com",
    "amplitude.com",
    "fullstory.com",
    "clarity.ms",
    "quantserve.com",
    "criteo.com",
    "adsrvr.org",
)


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


def _notice_id(version: str) -> str:
    return (
        "CCN-" + hashlib.sha256(version.lower().encode()).hexdigest()[:24]
    )


def publish_notice(
    version: str, text: str, *, actor: str,
) -> dict[str, Any]:
    """Publish one immutable privacy-notice version the receipts can bind to.

    The notice text is stored with its digest so a consent receipt's
    ``policy_version`` resolves to the exact wording the subject was shown.
    A published version never changes — a revision is a new version.
    """

    _require_enabled()
    label = _text(version, "version", 64)
    operator = _text(actor, "actor", 256)
    body = str(text or "")
    if not body.strip():
        raise ValueError("text must not be empty")
    if len(body.encode("utf-8")) > _MAX_NOTICE_BYTES:
        raise ValueError(
            f"text must be at most {_MAX_NOTICE_BYTES} bytes"
        )
    record = {
        "id": _notice_id(label),
        "schema": NOTICE_SCHEMA,
        "version": label,
        "text": body,
        "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "published_by": operator,
        "published_at": time.time(),
    }
    return dict(
        _NOTICES.create(
            record, action="publish_notice", actor=operator, max_records=500,
        )
    )


def get_notice(version: str) -> dict[str, Any] | None:
    _require_enabled()
    record = _NOTICES.get(_notice_id(_text(version, "version", 64)))
    return None if record is None else dict(record)


def list_notices() -> list[dict[str, Any]]:
    """Metadata-only notice listing, newest first (text via get_notice)."""

    _require_enabled()
    rows = _NOTICES.list(limit=_MAX_LIST)
    rows.sort(key=lambda row: -float(row.get("published_at") or 0.0))
    return [
        {
            "version": row.get("version"),
            "text_sha256": row.get("text_sha256"),
            "published_by": row.get("published_by"),
            "published_at": row.get("published_at"),
        }
        for row in rows
    ]


def notice_coverage() -> dict[str, Any]:
    """Which receipt policy versions resolve to a registered notice.

    An uncovered version is a receipt that binds wording the register
    cannot produce — the gap an auditor asks about first.
    """

    _require_enabled()
    registered = {
        str(row.get("version") or "") for row in _NOTICES.list(limit=_MAX_LIST)
    }
    used: set[str] = set()
    for row in _RECEIPTS.list(limit=_MAX_LIST * 4):
        for event in row.get("history") or []:
            used.add(str(event.get("policy_version") or ""))
    used.discard("")
    uncovered = sorted(used - registered)
    return {
        "receipt_versions": sorted(used),
        "registered_versions": sorted(registered),
        "uncovered_versions": uncovered,
        "covered": not uncovered,
    }


def _default_site_fetch(url: str) -> tuple[dict[str, Any], str]:
    """GET through the SSRF-pinned, enterprise-egress-guarded client."""

    from .enterprise import enterprise_egress_denial
    from .tools._ssrf import BlockedHost, safe_client

    deny = enterprise_egress_denial(url, tool="consent_registry")
    if deny:
        raise ConsentRegistryError(deny)
    try:
        with safe_client(url, timeout=_SCAN_TIMEOUT_SECONDS) as client:
            response = client.get(url)
    except BlockedHost as exc:
        raise ConsentRegistryError("blocked host (SSRF guard)") from exc
    if response.status_code != 200:
        raise ConsentRegistryError(
            f"site returned HTTP {response.status_code}"
        )
    text = response.text or ""
    if len(text.encode("utf-8", "ignore")) > _SCAN_MAX_BYTES:
        text = text.encode("utf-8", "ignore")[:_SCAN_MAX_BYTES].decode(
            "utf-8", "ignore"
        )
    headers: dict[str, Any] = {"set-cookie": []}
    getter = getattr(response.headers, "get_list", None)
    if callable(getter):
        headers["set-cookie"] = list(getter("set-cookie"))
    elif response.headers.get("set-cookie"):
        headers["set-cookie"] = [response.headers["set-cookie"]]
    return headers, text


def _cookie_row(header: str) -> dict[str, Any]:
    parts = [part.strip() for part in str(header or "").split(";")]
    name = parts[0].split("=", 1)[0].strip() if parts else ""
    attributes = {part.split("=", 1)[0].strip().lower() for part in parts[1:]}
    return {
        "name": name[:128],
        "secure": "secure" in attributes,
        "http_only": "httponly" in attributes,
        "same_site": next(
            (
                part.split("=", 1)[1].strip()
                for part in parts[1:]
                if part.lower().startswith("samesite=")
            ),
            "",
        ),
    }


def scan_site(
    url: str,
    *,
    fetcher: Any = None,
) -> dict[str, Any]:
    """Scan ONE page of the operator's own site for cookies and trackers.

    Reports the response's Set-Cookie headers and the third-party hosts its
    script/iframe/img tags load, flagging the well-known trackers. Feeds
    the consent program's purpose inventory. Header- and markup-level on a
    single page — not a crawler, not a browser, and never a CMP.
    """

    import re as _re
    from urllib.parse import urlsplit

    _require_enabled()
    target = _text(url, "url", 2048)
    split = urlsplit(target)
    if split.scheme != "https" or not split.hostname:
        raise ValueError("url must be an https URL")
    site_host = split.hostname.lower()
    fetch = fetcher or _default_site_fetch
    headers, body = fetch(target)
    cookies = [
        _cookie_row(header)
        for header in (headers.get("set-cookie") or [])[:100]
        if str(header or "").strip()
    ]
    hosts: set[str] = set()
    pattern = _re.compile(
        r"<(?:script|iframe|img)[^>]+src=[\"']([^\"']+)[\"']", _re.IGNORECASE
    )
    for match in pattern.findall(body or "")[:2000]:
        candidate = urlsplit(match if "//" in match else f"//{match}",
                             scheme="https")
        host = (candidate.hostname or "").lower()
        if host and host != site_host and not host.endswith(f".{site_host}"):
            hosts.add(host)
    third_party = sorted(hosts)[:200]
    trackers = sorted(
        host
        for host in third_party
        if any(
            host == known or host.endswith(f".{known}")
            for known in _TRACKER_HOSTS
        )
    )
    return {
        "url": target,
        "site_host": site_host,
        "cookies": cookies,
        "third_party_hosts": third_party,
        "known_trackers": trackers,
        "scanned_at": time.time(),
    }


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
    "NOTICE_SCHEMA",
    "RECEIPT_SCHEMA",
    "STATUSES",
    "consent_posture",
    "enabled",
    "export_receipts",
    "get_consent_registry_config",
    "get_notice",
    "import_onetrust_consents",
    "list_notices",
    "notice_coverage",
    "publish_notice",
    "record_consent",
    "scan_site",
    "subject_digest",
    "subject_preferences",
    "withdraw_consent",
]
