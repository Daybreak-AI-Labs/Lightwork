"""DSAR fulfillment: verify identity, collect, redact, deliver — governed.

The register in :mod:`maverick.privacy_ops` tracks a request; this module
EXECUTES it, as a governed pipeline with an audit answer for every stage:

* **Identity** — no response ships until a named human records HOW the
  requester was verified. The method vocabulary is fixed; "we just
  emailed it" is not in it.
* **Collection** — the platform's own holdings are collected via
  :func:`maverick.dsar.export_subject_data`; external sources (vendor
  exports, mailbox pulls) are logged with references so the response's
  completeness is inspectable.
* **Redaction** — the delivered bundle is passed through the
  deterministic redactor so OTHER people's identifiers never ride along;
  the subject's own reference is preserved.
* **Delivery** — a single-use style token link (stored only as a SHA-256
  digest, with expiry). The bundle is assembled AT DELIVERY TIME from the
  live stores — no second copy of personal data is persisted here.

Decision support and record-keeping for the operator's own program;
never identity assurance by itself, and never legal advice.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from .governed_records import GovernedRecordStore

SCHEMA = "lightwork.dsar-fulfillment.v1"
IDENTITY_METHODS = (
    "account_login", "document_check", "email_challenge",
    "in_person", "verified_by_processor", "other",
)
#: Categories scrubbed from a response so third parties don't ride along.
RESPONSE_REDACTION_CATEGORIES = [
    "email", "phone", "national_id", "financial", "ip_address", "location",
]
_MAX_RECORDS = 10_000
_SCAN_LIMIT = _MAX_RECORDS + 1
_MAX_SOURCES = 50
_TOKEN_TTL_MAX_HOURS = 24 * 14

_STORE = GovernedRecordStore("dsar_fulfillment", "DSF", "dsar_fulfillment")


class DsarFulfillmentError(RuntimeError):
    """Base failure for DSAR fulfillment."""


class DsarFulfillmentConfigError(DsarFulfillmentError):
    """The feature is not enabled."""


def get_dsar_fulfillment_config() -> dict[str, Any]:
    from .config import load_global_config

    try:
        config = load_global_config()
    except Exception as exc:
        raise DsarFulfillmentConfigError(
            "dsar_fulfillment configuration is unavailable") from exc
    section = (config or {}).get("dsar_fulfillment") or {}
    if not isinstance(section, dict):
        raise DsarFulfillmentConfigError("dsar_fulfillment must be a table")
    flag = section.get("enable", False)
    if not isinstance(flag, bool):
        raise DsarFulfillmentConfigError(
            "dsar_fulfillment.enable must be a boolean")
    return {"enable": flag}


def enabled() -> bool:
    try:
        return get_dsar_fulfillment_config()["enable"] is True
    except Exception:
        return False


def _require_enabled() -> None:
    if not enabled():
        raise DsarFulfillmentConfigError(
            "DSAR fulfillment requires [dsar_fulfillment] enable = true")


def _text(value: object, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError(f"{label} must be 1-{limit} characters")
    return text


def _fulfillment_id(request_id: str) -> str:
    return "DSF-" + hashlib.sha256(request_id.encode()).hexdigest()[:24]


def _dsar_record_or_raise(request_id: str) -> dict[str, Any]:
    from . import privacy_ops

    record = privacy_ops.get_dsar(request_id)
    if record is None:
        raise KeyError(f"no such DSAR request: {request_id}")
    return record


def open_fulfillment(request_id: str, *, actor: str) -> dict[str, Any]:
    """Open (or return) the fulfillment record for one DSAR request."""

    _require_enabled()
    operator = _text(actor, "actor", 256)
    request = _text(request_id, "request_id", 64)
    dsar = _dsar_record_or_raise(request)
    record_id = _fulfillment_id(request)
    current = _STORE.get(record_id)
    if current is not None:
        return dict(current)
    record = {
        "id": record_id,
        "schema": SCHEMA,
        "request_id": request,
        "subject_id": str(dsar.get("subject_id") or ""),
        "identity": {"status": "unverified"},
        "sources": [],
        "response": None,
        "opened_by": operator,
    }
    return dict(
        _STORE.create(
            record, action="open_fulfillment", actor=operator,
            max_records=_MAX_RECORDS,
        )
    )


def record_identity_verification(
    fulfillment_id: str,
    *,
    method: str,
    reference: str,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    """A named human records HOW the requester's identity was verified."""

    _require_enabled()
    if method not in IDENTITY_METHODS:
        raise ValueError(
            f"method must be one of: {', '.join(IDENTITY_METHODS)}")
    operator = _text(actor, "actor", 256)
    where = _text(reference, "reference", 512)

    def mutate(value: dict[str, Any]) -> None:
        value["identity"] = {
            "status": "verified",
            "method": method,
            "reference": where,
            "verified_by": operator,
            "verified_at": time.time(),
        }

    saved = _STORE.update(
        _text(fulfillment_id, "fulfillment_id", 64), mutate,
        expected_revision=int(expected_revision),
        action="record_identity_verification", actor=operator,
    )
    if saved is None:
        raise KeyError(f"no such fulfillment: {fulfillment_id}")
    return dict(saved)


def record_collection_source(
    fulfillment_id: str,
    *,
    source: str,
    reference: str,
    items: int,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Log one collection source (a vendor export, a mailbox pull, ...)."""

    _require_enabled()
    operator = _text(actor, "actor", 256)
    name = _text(source, "source", 128)
    where = _text(reference, "reference", 512)
    if not isinstance(items, int) or isinstance(items, bool) or items < 0:
        raise ValueError("items must be a non-negative integer")

    def mutate(value: dict[str, Any]) -> None:
        sources = list(value.get("sources") or [])
        if len(sources) >= _MAX_SOURCES:
            raise ValueError(f"at most {_MAX_SOURCES} sources per request")
        sources.append(
            {
                "source": name,
                "reference": where,
                "items": items,
                "collected_by": operator,
                "collected_at": time.time(),
            }
        )
        value["sources"] = sources

    saved = _STORE.update(
        _text(fulfillment_id, "fulfillment_id", 64), mutate,
        expected_revision=int(expected_revision),
        action="record_collection_source", actor=operator,
    )
    if saved is None:
        raise KeyError(f"no such fulfillment: {fulfillment_id}")
    return dict(saved)


def prepare_response(
    fulfillment_id: str,
    *,
    actor: str,
    expected_revision: int,
    expires_hours: int = 72,
) -> dict[str, Any]:
    """Mint the token-gated response link. Identity must be verified first.

    The token is returned ONCE and stored only as a SHA-256 digest; the
    response itself is assembled at delivery time, so no second copy of
    the subject's data is persisted by this module.
    """

    _require_enabled()
    operator = _text(actor, "actor", 256)
    if (
        not isinstance(expires_hours, int)
        or isinstance(expires_hours, bool)
        or not 1 <= expires_hours <= _TOKEN_TTL_MAX_HOURS
    ):
        raise ValueError(
            f"expires_hours must be between 1 and {_TOKEN_TTL_MAX_HOURS}")
    token = secrets.token_urlsafe(24)
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()

    def mutate(value: dict[str, Any]) -> None:
        identity = value.get("identity") or {}
        if identity.get("status") != "verified":
            raise DsarFulfillmentError(
                "identity must be verified before a response link is minted")
        value["response"] = {
            "token_sha256": digest,
            "minted_by": operator,
            "minted_at": now,
            "expires_at": now + expires_hours * 3600,
            "delivered_at": None,
        }

    saved = _STORE.update(
        _text(fulfillment_id, "fulfillment_id", 64), mutate,
        expected_revision=int(expected_revision),
        action="prepare_response", actor=operator,
    )
    if saved is None:
        raise KeyError(f"no such fulfillment: {fulfillment_id}")
    return {
        "id": saved["id"],
        "revision": saved["revision"],
        "response_path": f"/dsar/response/{token}",
        "expires_at": saved["response"]["expires_at"],
    }


def _redact_bundle(bundle: Any, subject_id: str) -> Any:
    """Scrub third-party identifiers; the subject's own reference stays."""

    from .redaction import redact_text

    sentinel = "\x00LIGHTWORK-SUBJECT\x00"
    text = json.dumps(bundle, default=str)
    if subject_id:
        text = text.replace(subject_id, sentinel)
    redacted = redact_text(
        text, categories=RESPONSE_REDACTION_CATEGORIES)["text"]
    redacted = redacted.replace(sentinel, subject_id)
    return json.loads(redacted)


def response_for_token(token: str) -> dict[str, Any] | None:
    """Resolve one response token; ``None`` for unknown/expired/undelivered.

    Assembles the platform export live and redacts third-party
    identifiers before anything leaves. Marks first delivery.
    """

    _require_enabled()
    raw = str(token or "").strip()
    if not raw or len(raw) > 128:
        return None
    digest = hashlib.sha256(raw.encode()).hexdigest()
    for row in _STORE.list(limit=_SCAN_LIMIT):
        response = row.get("response") or {}
        if response.get("token_sha256") != digest:
            continue
        if time.time() > float(response.get("expires_at") or 0.0):
            return None
        from . import dsar as dsar_export

        subject = str(row.get("subject_id") or "")
        bundle = dsar_export.export_subject_data(subject)
        scrubbed = _redact_bundle(bundle, subject)
        if response.get("delivered_at") is None:
            def mark(value: dict[str, Any]) -> None:
                inner = dict(value.get("response") or {})
                inner["delivered_at"] = time.time()
                value["response"] = inner

            _STORE.update(
                str(row["id"]), mark,
                expected_revision=int(row.get("revision") or 0),
                action="deliver_response", actor="dsar-response-link",
            )
        return {
            "request_id": row.get("request_id"),
            "subject_id": subject,
            "sources": list(row.get("sources") or []),
            "bundle": scrubbed,
            "redaction_notice": (
                "Third-party identifiers were redacted deterministically; "
                "the subject's own reference is preserved."
            ),
        }
    return None


def get_fulfillment(request_id: str) -> dict[str, Any] | None:
    _require_enabled()
    record = _STORE.get(_fulfillment_id(_text(request_id, "request_id", 64)))
    if record is None:
        return None
    safe = dict(record)
    response = safe.get("response")
    if isinstance(response, dict):
        safe["response"] = {
            key: value for key, value in response.items()
            if key != "token_sha256"
        }
    return safe


__all__ = [
    "IDENTITY_METHODS",
    "RESPONSE_REDACTION_CATEGORIES",
    "SCHEMA",
    "DsarFulfillmentConfigError",
    "DsarFulfillmentError",
    "enabled",
    "get_dsar_fulfillment_config",
    "get_fulfillment",
    "open_fulfillment",
    "prepare_response",
    "record_collection_source",
    "record_identity_verification",
    "response_for_token",
]
