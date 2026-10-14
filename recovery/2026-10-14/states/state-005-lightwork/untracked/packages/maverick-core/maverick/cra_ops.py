"""Governed product-security operations for the EU Cyber Resilience Act.

This module is operational support, not an automated legal decision maker.  A
human must determine product applicability and whether an observation is an
actively exploited vulnerability or severe product-security incident.  Only
then are the Article 14 clocks projected.  Report packets are immutable,
content-bound, human-approved records; Lightwork records filing receipts but
does not autonomously submit to a regulator.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from .governed_records import GovernedRecordStore
from .privacy_ops import _actor_label
from .sbom_ingest import parse_sbom

_PRODUCTS = GovernedRecordStore("cra_products", "CRP", "cra_product")
_RELEASES = GovernedRecordStore("cra_releases", "CRR", "cra_release")
_CASES = GovernedRecordStore("cra_cases", "CRC", "cra_case")
_REPORTS = GovernedRecordStore("cra_reports", "CRN", "cra_report")
_VEX = GovernedRecordStore("cra_vex", "CRV", "cra_vex")

_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|access[_-]?token|authorization|credential|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_PRODUCT_DECISIONS = frozenset({"applicable", "not_applicable"})
_CASE_DECISIONS = frozenset({"reportable", "not_reportable"})
_CASE_TYPES = frozenset({"actively_exploited_vulnerability", "severe_incident"})
_REPORT_STAGES = frozenset({"early_warning", "full_notification", "final_report"})
_VEX_STATUSES = frozenset({
    "affected",
    "fixed",
    "not_affected",
    "under_investigation",
})
_VEX_DECISIONS = frozenset({"approved", "rejected"})
_MAX_REPORT_BYTES = 256 * 1024
_MAX_FUTURE_SKEW_SECONDS = 5 * 60
_FILING_RECEIPT_SCHEMA = "lightwork.cra-filing-receipt.v1"
_FILING_RECEIPT_DOMAIN = b"lightwork.cra-filing-receipt.v1\x00"
_FILING_RECEIPT_PAYLOAD_FIELDS = frozenset({
    "schema",
    "report_id",
    "packet_sha256",
    "stage",
    "case_id",
    "tenant_id",
    "submitted_at",
    "channel",
    "receipt_id",
})
_FILING_RECEIPT_ENVELOPE_FIELDS = (
    _FILING_RECEIPT_PAYLOAD_FIELDS | {"key_id", "signature"}
)
_ED25519_PUBLIC_KEY = re.compile(r"[0-9a-fA-F]{64}\Z")
_ED25519_SIGNATURE = re.compile(r"[0-9a-fA-F]{128}\Z")
_KEY_ID = re.compile(r"[0-9a-f]{16}\Z")

CRA_SOURCE_URL = "https://eur-lex.europa.eu/eli/reg/2024/2847/oj"
CRA_REPORTING_EFFECTIVE = "2026-09-11"
_CRA_REPORTING_EFFECTIVE_AT = datetime(
    2026,
    9,
    11,
    tzinfo=timezone.utc,
).timestamp()
CRA_CLOCKS: tuple[dict[str, Any], ...] = (
    {
        "case_type": "actively_exploited_vulnerability",
        "stage": "early_warning",
        "anchor": "awareness",
        "amount": 24,
        "unit": "hours",
        "article": "14(2)(a)",
    },
    {
        "case_type": "actively_exploited_vulnerability",
        "stage": "full_notification",
        "anchor": "awareness",
        "amount": 72,
        "unit": "hours",
        "article": "14(2)(b)",
    },
    {
        "case_type": "actively_exploited_vulnerability",
        "stage": "final_report",
        "anchor": "corrective_measure_available",
        "amount": 14,
        "unit": "calendar_days",
        "article": "14(2)(c)",
    },
    {
        "case_type": "severe_incident",
        "stage": "early_warning",
        "anchor": "awareness",
        "amount": 24,
        "unit": "hours",
        "article": "14(4)(a)",
    },
    {
        "case_type": "severe_incident",
        "stage": "full_notification",
        "anchor": "awareness",
        "amount": 72,
        "unit": "hours",
        "article": "14(4)(b)",
    },
    {
        "case_type": "severe_incident",
        "stage": "final_report",
        "anchor": "full_notification_submission",
        "amount": 1,
        "unit": "calendar_months",
        "article": "14(4)(c)",
    },
)


@runtime_checkable
class FilingReceiptSigner(Protocol):
    """Minimal signing seam implemented by a trusted filing connector.

    The connector owns its private key.  Core receives only the key id and
    signature, then verifies them against deployment-global trust anchors.
    """

    key_id: str

    def sign(self, message: bytes) -> str:
        """Return a lowercase hexadecimal Ed25519 signature."""


def enabled() -> bool:
    """Return the strict deployment-global feature gate."""
    try:
        from .config import config_source_errors, load_global_config

        cfg = load_global_config()
        if config_source_errors(include_tenant=False):
            return False
        if "cra_ops" not in cfg:
            return False
        section = cfg.get("cra_ops")
        return isinstance(section, dict) and section.get("enable") is True
    except Exception:  # pragma: no cover - policy reads fail closed
        return False


def _require_enabled() -> None:
    """Deny every CRA mutation/authority operation unless policy is healthy."""
    if not enabled():
        raise RuntimeError("CRA operations are disabled by deployment policy")


def _required(value: object, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > limit:
        raise ValueError(f"{label} exceeds the {limit}-character limit")
    return text


def _optional(value: object, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError(f"{label} exceeds the {limit}-character limit")
    return text


def _timestamp(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive timestamp") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be a positive timestamp")
    return result


def _not_materially_future(value: object, label: str) -> float:
    result = _timestamp(value, label)
    if result > time.time() + _MAX_FUTURE_SKEW_SECONDS:
        raise ValueError(
            f"{label} must not be more than {_MAX_FUTURE_SKEW_SECONDS} seconds "
            "in the future"
        )
    return result


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _tenant_binding() -> str:
    """Stable tenant identity included in every trusted filing receipt."""
    from .paths import current_tenant_id_strict

    return current_tenant_id_strict() or "legacy-root"


def _filing_receipt_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("filing receipt envelope must be an object")
    if set(value) != _FILING_RECEIPT_PAYLOAD_FIELDS:
        raise ValueError("filing receipt payload fields do not match the v1 schema")
    stage = str(value.get("stage") or "").strip().lower()
    if stage not in _REPORT_STAGES:
        raise ValueError(f"stage must be one of {sorted(_REPORT_STAGES)}")
    schema = _required(value.get("schema"), "schema", 128)
    if schema != _FILING_RECEIPT_SCHEMA:
        raise ValueError("filing receipt schema is unsupported")
    return {
        "schema": schema,
        "report_id": _required(value.get("report_id"), "report_id", 120),
        "packet_sha256": _sha256(value.get("packet_sha256"), "packet_sha256"),
        "stage": stage,
        "case_id": _required(value.get("case_id"), "case_id", 120),
        "tenant_id": _required(value.get("tenant_id"), "tenant_id", 300),
        "submitted_at": _not_materially_future(
            value.get("submitted_at"),
            "submitted_at",
        ),
        "channel": _required(value.get("channel"), "channel", 500),
        "receipt_id": _required(value.get("receipt_id"), "receipt_id", 1000),
    }


def filing_receipt_signing_message(payload: dict[str, Any]) -> bytes:
    """Canonical, domain-separated bytes a trusted connector must sign."""
    return _FILING_RECEIPT_DOMAIN + _canonical(_filing_receipt_payload(payload))


def build_filing_receipt_envelope(
    *,
    report_id: str,
    packet_sha256: str,
    stage: str,
    case_id: str,
    submitted_at: float,
    channel: str,
    receipt_id: str,
    signer: FilingReceiptSigner,
) -> dict[str, Any]:
    """Build a signed receipt for a connector holding a trusted private key."""
    _require_enabled()
    if not isinstance(signer, FilingReceiptSigner):
        raise ValueError("signer does not implement the filing receipt protocol")
    payload = _filing_receipt_payload({
        "schema": _FILING_RECEIPT_SCHEMA,
        "report_id": report_id,
        "packet_sha256": packet_sha256,
        "stage": stage,
        "case_id": case_id,
        "tenant_id": _tenant_binding(),
        "submitted_at": submitted_at,
        "channel": channel,
        "receipt_id": receipt_id,
    })
    key_id = _required(signer.key_id, "key_id", 16).lower()
    if not _KEY_ID.fullmatch(key_id):
        raise ValueError("key_id must be a 16-character lowercase hex digest")
    signature = _required(
        signer.sign(filing_receipt_signing_message(payload)),
        "signature",
        128,
    ).lower()
    if not _ED25519_SIGNATURE.fullmatch(signature):
        raise ValueError("signature must be a 64-byte hexadecimal Ed25519 signature")
    return {**payload, "key_id": key_id, "signature": signature}


def _trusted_filing_receipt_keys() -> dict[str, str]:
    """Resolve trusted filing keys from deployment-global policy only."""
    from .config import config_source_errors, load_global_config

    cfg = load_global_config()
    if config_source_errors(include_tenant=False):
        raise RuntimeError("global CRA trust policy is unavailable")
    section = cfg.get("cra_ops")
    raw = (
        section.get("filing_receipt_trusted_public_keys")
        if isinstance(section, dict)
        else None
    )
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(
            "no deployment-global CRA filing receipt trust anchors are configured"
        )
    trusted: dict[str, str] = {}
    for value in raw:
        if not isinstance(value, str) or not _ED25519_PUBLIC_KEY.fullmatch(value):
            raise RuntimeError("CRA filing receipt trust anchor is malformed")
        public_key = value.lower()
        key_id = hashlib.sha256(bytes.fromhex(public_key)).hexdigest()[:16]
        trusted[key_id] = public_key
    if not trusted:  # pragma: no cover - guarded by the non-empty raw list
        raise RuntimeError("CRA filing receipt trust anchors are empty")
    return trusted


def _stable_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_digest(value)[:24]}"


def _sha256(value: object, label: str) -> str:
    text = _required(value, label, 64).lower()
    if not _SHA256.fullmatch(text):
        raise ValueError(f"{label} must be a 64-character SHA-256 digest")
    return text


def _actor(value: object, label: str = "actor") -> str:
    return _actor_label(_required(value, label, 4096))


def _bounded_json(value: object) -> object:
    """Copy a report payload while rejecting secret-shaped keys and excess."""
    nodes = 0

    def _copy(current: object, depth: int) -> object:
        nonlocal nodes
        nodes += 1
        if nodes > 4096 or depth > 32:
            raise ValueError("report content exceeds the structure limit")
        if current is None or isinstance(current, bool):
            return current
        if isinstance(current, int):
            return current
        if isinstance(current, float):
            if not math.isfinite(current):
                raise ValueError("report content contains a non-finite number")
            return current
        if isinstance(current, str):
            if len(current) > 16_000:
                raise ValueError("report string exceeds the 16000-character limit")
            return current
        if isinstance(current, list):
            if len(current) > 512:
                raise ValueError("report list exceeds the 512-item limit")
            return [_copy(item, depth + 1) for item in current]
        if isinstance(current, dict):
            if len(current) > 256:
                raise ValueError("report object exceeds the 256-field limit")
            out: dict[str, object] = {}
            for raw_key, raw_value in current.items():
                key = _required(raw_key, "report field", 128)
                if _SENSITIVE_KEY.search(key):
                    raise ValueError(
                        f"secret-shaped report field {key!r} is not permitted"
                    )
                out[key] = _copy(raw_value, depth + 1)
            return {key: out[key] for key in sorted(out)}
        raise ValueError("report content must contain only JSON values")

    copied = _copy(value, 1)
    if len(_canonical(copied)) > _MAX_REPORT_BYTES:
        raise ValueError("report content exceeds the byte limit")
    return copied


def _get_required(store: GovernedRecordStore, record_id: str, label: str) -> dict:
    record = store.get(str(record_id or ""))
    if record is None:
        raise ValueError(f"{label} does not exist")
    return record


def _product_applicability_binding(
    product: dict[str, Any],
) -> tuple[int, str]:
    """Return the exact persisted human applicability authority or refuse it."""
    applicability = product.get("applicability")
    if not isinstance(applicability, dict):
        raise ValueError("CRA product applicability authority is malformed")
    if applicability.get("status") != "applicable":
        raise ValueError(
            "CRA product must be human-determined applicable before case action"
        )
    if not str(applicability.get("decided_by") or "").strip():
        raise ValueError("CRA product applicability has no accountable reviewer")
    if not str(applicability.get("rationale") or "").strip():
        raise ValueError("CRA product applicability has no rationale")
    decided_at = applicability.get("decided_at")
    if (
        isinstance(decided_at, bool)
        or not isinstance(decided_at, (int, float))
        or not math.isfinite(float(decided_at))
        or float(decided_at) <= 0
    ):
        raise ValueError("CRA product applicability has an invalid decision time")
    revision = product.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("CRA product applicability has no valid revision")
    binding = {
        "product_id": product.get("id"),
        "product_revision": revision,
        "applicability": applicability,
    }
    return revision, _digest(binding)


def _case_binding(record: dict[str, Any]) -> str:
    return _digest({
        key: record.get(key)
        for key in (
            "product_id",
            "product_applicability_revision",
            "product_applicability_sha256",
            "release_id",
            "release_binding_sha256",
            "case_type",
            "external_id",
            "awareness_at",
        )
    })


def _validated_release_for_case(case: dict[str, Any]) -> dict[str, Any]:
    """Enforce current product applicability plus the exact release/SBOM bind."""
    product = _get_required(_PRODUCTS, str(case.get("product_id") or ""), "product")
    product_revision, applicability_sha256 = _product_applicability_binding(product)
    if (
        case.get("product_applicability_revision") != product_revision
        or case.get("product_applicability_sha256") != applicability_sha256
    ):
        raise ValueError(
            "CRA case is stale for the current product applicability authority"
        )
    release = _get_required(_RELEASES, str(case.get("release_id") or ""), "release")
    if release.get("product_id") != case.get("product_id"):
        raise ValueError("CRA case product does not match its current release")
    _validated_release_record(
        release,
        expected_product_id=str(case.get("product_id") or ""),
        require_sbom=True,
    )
    if case.get("release_binding_sha256") != release.get("release_binding_sha256"):
        raise ValueError("CRA case is stale for the current exact release/SBOM binding")
    if case.get("case_binding_sha256") != _case_binding(case):
        raise ValueError("CRA case authority binding does not verify")
    return release


def create_product(
    *,
    name: str,
    description: str,
    manufacturer: str,
    product_kind: str,
    support_until: float,
    actor: str,
) -> dict[str, Any]:
    """Register a product without inferring whether the CRA applies."""
    _require_enabled()
    record = {
        "id": _PRODUCTS.new_id(),
        "schema": "lightwork.cra-product.v1",
        "name": _required(name, "name", 300),
        "description": _required(description, "description", 8000),
        "manufacturer": _required(manufacturer, "manufacturer", 500),
        "product_kind": _required(product_kind, "product_kind", 80),
        "support_until": _timestamp(support_until, "support_until"),
        "status": "active",
        "applicability": {
            "status": "undetermined",
            "rationale": "",
            "decided_by": "",
            "decided_at": None,
        },
    }
    return _PRODUCTS.create(record, action="create", actor=_actor(actor))


def decide_product_applicability(
    product_id: str,
    *,
    decision: str,
    rationale: str,
    reviewer: str,
    expected_revision: int,
) -> dict[str, Any] | None:
    _require_enabled()
    choice = str(decision or "").strip().lower()
    if choice not in _PRODUCT_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(_PRODUCT_DECISIONS)}")
    why = _required(rationale, "rationale", 8000)
    human = _actor(reviewer, "reviewer")

    def _mutate(record: dict[str, Any]) -> None:
        current = dict(record.get("applicability") or {})
        if current.get("status") != "undetermined":
            raise ValueError("product applicability has already been decided")
        record["applicability"] = {
            "status": choice,
            "rationale": why,
            "decided_by": human,
            "decided_at": time.time(),
            "source_url": CRA_SOURCE_URL,
        }

    return _PRODUCTS.update(
        product_id,
        _mutate,
        expected_revision=expected_revision,
        action=f"applicability_{choice}",
        actor=human,
    )


def _release_binding(record: dict[str, Any]) -> str:
    sbom = record.get("sbom")
    return _digest({
        "product_id": record.get("product_id"),
        "version": record.get("version"),
        "artifact_sha256": record.get("artifact_sha256"),
        "released_at": record.get("released_at"),
        "sbom_document_sha256": (
            sbom.get("document_sha256") if isinstance(sbom, dict) else ""
        ),
        "sbom_normalised_sha256": (
            sbom.get("normalised_sha256") if isinstance(sbom, dict) else ""
        ),
    })


def _validated_release_record(
    release: dict[str, Any],
    *,
    expected_product_id: str = "",
    require_sbom: bool = False,
) -> dict[str, Any]:
    """Verify a release and its normalized SBOM before authority is projected."""
    if expected_product_id and release.get("product_id") != expected_product_id:
        raise ValueError("release does not belong to the requested CRA product")
    _sha256(release.get("artifact_sha256"), "artifact_sha256")
    _timestamp(release.get("released_at"), "released_at")
    sbom = release.get("sbom")
    if sbom is None:
        if require_sbom:
            raise ValueError("release must have an immutable SBOM before CRA use")
    elif not isinstance(sbom, dict):
        raise ValueError("release SBOM authority is malformed")
    else:
        document_sha256 = _sha256(
            sbom.get("document_sha256"),
            "SBOM document_sha256",
        )
        normalised_sha256 = _sha256(
            sbom.get("normalised_sha256"),
            "SBOM normalised_sha256",
        )
        normalised = {
            key: value for key, value in sbom.items()
            if key != "normalised_sha256"
        }
        if normalised_sha256 != _digest(normalised):
            raise ValueError("release normalized SBOM binding does not verify")
        components = sbom.get("components")
        dependencies = sbom.get("dependencies")
        if (
            not isinstance(components, list)
            or not isinstance(dependencies, list)
            or sbom.get("component_count") != len(components)
            or sbom.get("dependency_count") != len(dependencies)
            or not document_sha256
        ):
            raise ValueError("release normalized SBOM shape does not verify")
    binding = _release_binding(release)
    if release.get("release_binding_sha256") != binding:
        raise ValueError("release/SBOM binding does not verify")
    return release


def create_release(
    *,
    product_id: str,
    version: str,
    artifact_sha256: str,
    released_at: float,
    actor: str,
) -> dict[str, Any]:
    _require_enabled()
    product = _get_required(_PRODUCTS, product_id, "product")
    release_version = _required(version, "version", 300)
    record = {
        "id": _stable_id(
            "CRR",
            {"product_id": product["id"], "version": release_version},
        ),
        "schema": "lightwork.cra-release.v1",
        "product_id": product["id"],
        "version": release_version,
        "artifact_sha256": _sha256(artifact_sha256, "artifact_sha256"),
        "released_at": _timestamp(released_at, "released_at"),
        "sbom": None,
        "sbom_source": "",
    }
    record["release_binding_sha256"] = _release_binding(record)
    return _RELEASES.create(record, action="create", actor=_actor(actor))


def attach_sbom(
    release_id: str,
    document: bytes | str | dict[str, Any],
    *,
    source: str,
    actor: str,
    expected_revision: int,
) -> dict[str, Any] | None:
    _require_enabled()
    normalised = parse_sbom(document)
    source_name = _required(source, "source", 500)
    human = _actor(actor)

    def _mutate(record: dict[str, Any]) -> None:
        _validated_release_record(record)
        if record.get("sbom") is not None:
            raise ValueError("release SBOM is immutable once attached")
        record["sbom"] = normalised
        record["sbom_source"] = source_name
        record["sbom_attached_at"] = time.time()
        record["release_binding_sha256"] = _release_binding(record)

    return _RELEASES.update(
        release_id,
        _mutate,
        expected_revision=expected_revision,
        action="attach_sbom",
        actor=human,
    )


def open_case(
    *,
    release_id: str,
    case_type: str,
    title: str,
    summary: str,
    awareness_at: float,
    external_id: str,
    actor: str,
) -> dict[str, Any]:
    _require_enabled()
    release = _get_required(_RELEASES, release_id, "release")
    product = _get_required(_PRODUCTS, str(release.get("product_id") or ""), "product")
    product_revision, applicability_sha256 = _product_applicability_binding(product)
    _validated_release_record(
        release,
        expected_product_id=product["id"],
        require_sbom=True,
    )
    kind = str(case_type or "").strip().lower()
    if kind not in _CASE_TYPES:
        raise ValueError(f"case_type must be one of {sorted(_CASE_TYPES)}")
    external = _required(external_id, "external_id", 500)
    awareness = _not_materially_future(awareness_at, "awareness_at")
    if awareness < float(release.get("released_at") or 0.0):
        raise ValueError("awareness_at must not precede the bound release")
    record = {
        "id": _stable_id(
            "CRC",
            {
                "release_id": release["id"],
                "case_type": kind,
                "external_id": external,
            },
        ),
        "schema": "lightwork.cra-case.v1",
        "product_id": release["product_id"],
        "product_applicability_revision": product_revision,
        "product_applicability_sha256": applicability_sha256,
        "release_id": release["id"],
        "release_binding_sha256": release["release_binding_sha256"],
        "case_type": kind,
        "title": _required(title, "title", 500),
        "summary": _required(summary, "summary", 16_000),
        "external_id": external,
        "awareness_at": awareness,
        "status": "under_review",
        "reportability": {
            "status": "undetermined",
            "rationale": "",
            "decided_by": "",
            "decided_at": None,
        },
        "anchors": {},
        "statutory_effective_override": None,
    }
    record["case_binding_sha256"] = _case_binding(record)
    return _CASES.create(record, action="open", actor=_actor(actor))


def decide_case_reportability(
    case_id: str,
    *,
    decision: str,
    rationale: str,
    reviewer: str,
    expected_revision: int,
) -> dict[str, Any] | None:
    _require_enabled()
    choice = str(decision or "").strip().lower()
    if choice not in _CASE_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(_CASE_DECISIONS)}")
    why = _required(rationale, "rationale", 8000)
    human = _actor(reviewer, "reviewer")

    def _mutate(record: dict[str, Any]) -> None:
        _validated_release_for_case(record)
        if float(record.get("awareness_at") or 0.0) > time.time():
            raise ValueError("case awareness must occur before reportability is decided")
        if (record.get("reportability") or {}).get("status") != "undetermined":
            raise ValueError("case reportability has already been decided")
        record["reportability"] = {
            "status": choice,
            "rationale": why,
            "decided_by": human,
            "decided_at": time.time(),
            "source_url": CRA_SOURCE_URL,
            "source_article": "Article 14",
        }
        record["status"] = "reportable" if choice == "reportable" else "closed"

    return _CASES.update(
        case_id,
        _mutate,
        expected_revision=expected_revision,
        action=f"reportability_{choice}",
        actor=human,
    )


def override_case_effective_date(
    case_id: str,
    *,
    rationale: str,
    counsel: str,
    expected_revision: int,
) -> dict[str, Any] | None:
    """Persist counsel's explicit decision to operationalise a pre-effective case."""
    _require_enabled()
    why = _required(rationale, "rationale", 8000)
    human = _actor(counsel, "counsel")

    def _mutate(record: dict[str, Any]) -> None:
        _validated_release_for_case(record)
        awareness = float(record.get("awareness_at") or 0.0)
        if awareness >= _CRA_REPORTING_EFFECTIVE_AT:
            raise ValueError("case is already within the statutory effective period")
        if awareness > time.time():
            raise ValueError("case awareness must occur before counsel override")
        if isinstance(record.get("statutory_effective_override"), dict):
            raise ValueError("case statutory effective-date override already exists")
        record["statutory_effective_override"] = {
            "status": "counsel_override",
            "rationale": why,
            "counsel": human,
            "decided_at": time.time(),
            "case_binding_sha256": record.get("case_binding_sha256"),
            "source_url": CRA_SOURCE_URL,
        }

    return _CASES.update(
        case_id,
        _mutate,
        expected_revision=expected_revision,
        action="override_reporting_effective_date",
        actor=human,
    )


def record_case_anchor(
    case_id: str,
    *,
    anchor: str,
    occurred_at: float,
    evidence: str,
    actor: str,
    expected_revision: int,
) -> dict[str, Any] | None:
    _require_enabled()
    anchor_name = str(anchor or "").strip().lower()
    if anchor_name != "corrective_measure_available":
        raise ValueError("unsupported CRA case anchor")
    when = _not_materially_future(occurred_at, "occurred_at")
    proof = _required(evidence, "evidence", 8000)
    human = _actor(actor)

    def _mutate(record: dict[str, Any]) -> None:
        _validated_release_for_case(record)
        if record.get("case_type") != "actively_exploited_vulnerability":
            raise ValueError(
                "corrective_measure_available applies only to vulnerability cases"
            )
        if (record.get("reportability") or {}).get("status") != "reportable":
            raise ValueError("case must be human-determined reportable")
        if when < float(record.get("awareness_at") or 0.0):
            raise ValueError("case anchor must not precede awareness")
        anchors = dict(record.get("anchors") or {})
        if anchor_name in anchors:
            raise ValueError("CRA case anchor has already been recorded")
        anchors[anchor_name] = {
            "occurred_at": when,
            "evidence": proof,
            "recorded_by": human,
            "recorded_at": time.time(),
        }
        record["anchors"] = anchors

    return _CASES.update(
        case_id,
        _mutate,
        expected_revision=expected_revision,
        action=f"record_{anchor_name}",
        actor=human,
    )


def _evidence_ids(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise ValueError("evidence_node_ids must be a list")
    if len(values) > 512:
        raise ValueError("evidence_node_ids exceed the 512-item limit")
    return sorted({
        _required(value, "evidence node id", 300)
        for value in values
    })


def _evidence_payload(node: dict[str, Any]) -> dict[str, Any]:
    return {
        key: node.get(key)
        for key in (
            "source",
            "source_id",
            "evidence_type",
            "title",
            "summary",
            "controls",
            "attributes",
            "links",
            "valid_until",
        )
    }


def _node_attributed_to_case(node: dict[str, Any], case: dict[str, Any]) -> bool:
    expected = {
        "cra_case": str(case["id"]),
        "cra_release": str(case["release_id"]),
        "cra_product": str(case["product_id"]),
    }
    matched = False
    for link in node.get("links") or []:
        if not isinstance(link, dict):
            raise ValueError("evidence node contains a malformed attribution link")
        target_type = str(link.get("target_type") or "")
        if target_type not in expected:
            continue
        if str(link.get("target_id") or "") != expected[target_type]:
            raise ValueError("evidence node is attributed to a different CRA authority")
        matched = True
    attributes = node.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise ValueError("evidence node attributes are malformed")
    for field, target_type in (
        ("case_id", "cra_case"),
        ("release_id", "cra_release"),
        ("product_id", "cra_product"),
    ):
        if field not in attributes:
            continue
        if str(attributes.get(field) or "") != expected[target_type]:
            raise ValueError("evidence node is attributed to a different CRA authority")
        matched = True
    return matched


def _validated_evidence_bindings(
    case: dict[str, Any],
    node_ids: object,
) -> list[dict[str, Any]]:
    from . import evidence_graph

    bindings: list[dict[str, Any]] = []
    for node_id in _evidence_ids(node_ids):
        node = evidence_graph.get(node_id)
        if not isinstance(node, dict):
            raise ValueError(f"evidence node {node_id!r} does not exist in tenant scope")
        review = node.get("review")
        if (
            node.get("status") != "approved"
            or node.get("freshness") != "current"
            or not isinstance(review, dict)
            or review.get("status") != "approved"
        ):
            raise ValueError(f"evidence node {node_id!r} is not approved and current")
        revision = node.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError(f"evidence node {node_id!r} has an invalid revision")
        payload_sha256 = str(node.get("payload_sha256") or "").lower()
        if (
            not _SHA256.fullmatch(payload_sha256)
            or payload_sha256 != _digest(_evidence_payload(node))
        ):
            raise ValueError(f"evidence node {node_id!r} payload binding does not verify")
        if not _node_attributed_to_case(node, case):
            raise ValueError(
                f"evidence node {node_id!r} is not explicitly attributed to this "
                "CRA case, release, or product"
            )
        bindings.append({
            "id": node_id,
            "revision": revision,
            "payload_sha256": payload_sha256,
            "review_sha256": _digest(review),
        })
    return bindings


def _validated_report_case(record: dict[str, Any]) -> dict[str, Any]:
    """Resolve the report's current case/release authority without evidence policy."""
    case = _get_required(_CASES, str(record.get("case_id") or ""), "CRA case")
    _validated_release_for_case(case)
    if (
        record.get("product_id") != case.get("product_id")
        or record.get("release_id") != case.get("release_id")
        or record.get("case_binding_sha256") != case.get("case_binding_sha256")
        or record.get("release_binding_sha256") != case.get("release_binding_sha256")
    ):
        raise ValueError("report is stale for its exact case/release binding")
    if (case.get("reportability") or {}).get("status") != "reportable":
        raise ValueError("report case is not currently human-determined reportable")
    return case


def _revalidate_report_authority(record: dict[str, Any]) -> dict[str, Any]:
    case = _validated_report_case(record)
    bindings = _validated_evidence_bindings(case, record.get("evidence_node_ids") or [])
    if bindings != list(record.get("evidence_bindings") or []):
        raise ValueError("report evidence revisions or approval bindings are no longer current")
    return case


def _report_packet_binding(record: dict[str, Any]) -> dict[str, Any]:
    """Return the exact fields covered by a CRA report packet digest."""
    return {
        "case_id": record.get("case_id"),
        "case_binding_sha256": record.get("case_binding_sha256"),
        "release_binding_sha256": record.get("release_binding_sha256"),
        "stage": record.get("stage"),
        "content": record.get("content"),
        "evidence_node_ids": record.get("evidence_node_ids"),
        "evidence_bindings": record.get("evidence_bindings"),
    }


def _validated_packet_sha256(record: dict[str, Any]) -> str:
    """Return the persisted report digest only when it covers the current packet."""
    packet_sha256 = _sha256(record.get("packet_sha256"), "packet_sha256")
    if packet_sha256 != _digest(_report_packet_binding(record)):
        raise ValueError("report packet binding no longer verifies")
    return packet_sha256


def _validated_human_approval(record: dict[str, Any]) -> str:
    """Verify that a human approval covers the exact current report packet."""
    packet_sha256 = _validated_packet_sha256(record)
    review = record.get("review")
    if (
        record.get("status") not in {"approved", "submitted"}
        or not isinstance(review, dict)
        or review.get("status") != "approved"
        or review.get("approved_packet_sha256") != packet_sha256
        or not str(review.get("reviewed_by") or "").strip()
        or not str(review.get("rationale") or "").strip()
        or not isinstance(review.get("reviewed_at"), (int, float))
        or isinstance(review.get("reviewed_at"), bool)
        or not math.isfinite(float(review["reviewed_at"]))
        or float(review["reviewed_at"]) <= 0
    ):
        raise ValueError("report must be human-approved for its exact current packet")
    return packet_sha256


def _validated_filing_submission(record: dict[str, Any]) -> dict[str, Any]:
    """Reverify a persisted external filing receipt before it can affect clocks."""
    case = _validated_report_case(record)
    packet_sha256 = _validated_human_approval(record)
    submission = record.get("submission")
    if record.get("status") != "submitted" or not isinstance(submission, dict):
        raise ValueError("report has no verified external filing receipt")
    payload = _filing_receipt_payload({
        key: submission.get(key) for key in _FILING_RECEIPT_PAYLOAD_FIELDS
    })
    expected = {
        "report_id": record.get("id"),
        "packet_sha256": packet_sha256,
        "stage": record.get("stage"),
        "case_id": record.get("case_id"),
        "tenant_id": _tenant_binding(),
    }
    for field, expected_value in expected.items():
        if payload.get(field) != expected_value:
            raise ValueError(
                f"filing receipt {field} does not match the exact approved packet"
            )
    key_id = _required(submission.get("key_id"), "key_id", 16).lower()
    signature = _required(submission.get("signature"), "signature", 128).lower()
    if not _KEY_ID.fullmatch(key_id) or not _ED25519_SIGNATURE.fullmatch(signature):
        raise ValueError("persisted filing receipt signature metadata is malformed")
    public_key = _trusted_filing_receipt_keys().get(key_id)
    if public_key is None:
        raise ValueError("persisted filing receipt signer is no longer trusted")
    from .audit.signing import verify_ed25519

    if not verify_ed25519(
        public_key,
        signature,
        filing_receipt_signing_message(payload),
    ):
        raise ValueError("persisted filing receipt signature does not verify")
    envelope_sha256 = _digest({
        **payload,
        "key_id": key_id,
        "signature": signature,
    })
    review = record["review"]
    if (
        submission.get("verification_status") != "verified_external_receipt"
        or submission.get("envelope_sha256") != envelope_sha256
        or not str(submission.get("verified_by") or "").strip()
        or not isinstance(submission.get("verified_at"), (int, float))
        or isinstance(submission.get("verified_at"), bool)
        or not math.isfinite(float(submission["verified_at"]))
        or float(submission["verified_at"]) <= 0
        or float(payload["submitted_at"]) < float(case.get("awareness_at") or 0.0)
        or float(payload["submitted_at"]) < float(review.get("reviewed_at") or 0.0)
    ):
        raise ValueError("persisted filing receipt authority does not verify")
    return dict(submission)


def _validated_report_for_pack(record: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless an approved report remains currently authoritative."""
    _revalidate_report_authority(record)
    _validated_human_approval(record)
    if record.get("status") == "submitted":
        _validated_filing_submission(record)
    elif record.get("submission") is not None:
        raise ValueError("approved report contains an unverified submission claim")
    return _pack_record(record)


def _validate_report_content(stage: str, content: object) -> dict[str, Any]:
    copied = _bounded_json(content)
    if not isinstance(copied, dict) or not copied:
        raise ValueError("report content must be a non-empty object")
    required_any = {
        "early_warning": {"summary", "general_information"},
        "full_notification": {"nature", "general_information"},
        "final_report": {"description", "detailed_description"},
    }[stage]
    if not any(str(copied.get(key) or "").strip() for key in required_any):
        raise ValueError(
            f"{stage} content requires one of {sorted(required_any)}"
        )
    return copied


def draft_report(
    *,
    case_id: str,
    stage: str,
    content: dict[str, Any],
    evidence_node_ids: list[str],
    actor: str,
) -> dict[str, Any]:
    _require_enabled()
    case = _get_required(_CASES, case_id, "CRA case")
    _validated_release_for_case(case)
    report_stage = str(stage or "").strip().lower()
    if report_stage not in _REPORT_STAGES:
        raise ValueError(f"stage must be one of {sorted(_REPORT_STAGES)}")
    if (case.get("reportability") or {}).get("status") != "reportable":
        raise ValueError("case must be human-determined reportable")
    report_content = _validate_report_content(report_stage, content)
    evidence = _evidence_ids(evidence_node_ids)
    evidence_bindings = _validated_evidence_bindings(case, evidence)
    bound = {
        "case_id": case["id"],
        "case_binding_sha256": case["case_binding_sha256"],
        "release_binding_sha256": case["release_binding_sha256"],
        "stage": report_stage,
        "content": report_content,
        "evidence_node_ids": evidence,
        "evidence_bindings": evidence_bindings,
    }
    record = {
        "id": _REPORTS.new_id(),
        "schema": "lightwork.cra-report.v1",
        "product_id": case["product_id"],
        "release_id": case["release_id"],
        **bound,
        "packet_sha256": _digest(bound),
        "status": "pending_human_approval",
        "review": {
            "status": "pending",
            "rationale": "",
            "reviewed_by": "",
            "reviewed_at": None,
        },
        "submission": None,
    }
    return _REPORTS.create(record, action="draft", actor=_actor(actor))


def approve_report(
    report_id: str,
    *,
    packet_sha256: str,
    rationale: str,
    reviewer: str,
    expected_revision: int,
) -> dict[str, Any] | None:
    _require_enabled()
    approved_digest = _sha256(packet_sha256, "packet_sha256")
    why = _required(rationale, "rationale", 8000)
    human = _actor(reviewer, "reviewer")

    def _mutate(record: dict[str, Any]) -> None:
        _revalidate_report_authority(record)
        if record.get("status") != "pending_human_approval":
            raise ValueError("report is not awaiting human approval")
        current_digest = _validated_packet_sha256(record)
        if approved_digest != current_digest:
            raise ValueError("approval does not match the exact current report packet")
        record["status"] = "approved"
        record["review"] = {
            "status": "approved",
            "rationale": why,
            "reviewed_by": human,
            "reviewed_at": time.time(),
            "approved_packet_sha256": current_digest,
        }

    return _REPORTS.update(
        report_id,
        _mutate,
        expected_revision=expected_revision,
        action="approve",
        actor=human,
    )


def record_submission_receipt(
    report_id: str,
    *,
    submitted_at: float,
    channel: str,
    receipt_id: str,
    actor: str,
    expected_revision: int,
) -> dict[str, Any] | None:
    """Record an unverified local filing attestation without starting clocks.

    This compatibility method deliberately does not call the attestation a
    regulator receipt and never changes the report to ``submitted``.  Only
    :func:`record_verified_filing_receipt` can do that.
    """
    _require_enabled()
    when = _not_materially_future(submitted_at, "submitted_at")
    destination = _required(channel, "channel", 500)
    receipt = _required(receipt_id, "receipt_id", 1000)
    human = _actor(actor)

    def _mutate(record: dict[str, Any]) -> None:
        case = _revalidate_report_authority(record)
        review = dict(record.get("review") or {})
        packet_sha256 = _validated_human_approval(record)
        if (
            record.get("status") != "approved"
            or review.get("status") != "approved"
            or review.get("approved_packet_sha256") != packet_sha256
        ):
            raise ValueError("report must be human-approved for this exact packet")
        if when < float(case.get("awareness_at") or 0.0):
            raise ValueError("submitted_at must not precede case awareness")
        if when < float(review.get("reviewed_at") or 0.0):
            raise ValueError("submitted_at must not precede exact-packet approval")
        if record.get("local_submission_attestation") is not None:
            raise ValueError("local submission attestation has already been recorded")
        record["local_submission_attestation"] = {
            "verification_status": "unverified_local_attestation",
            "submitted_at": when,
            "channel": destination,
            "receipt_id": receipt,
            "recorded_by": human,
            "recorded_at": time.time(),
            "packet_sha256": packet_sha256,
        }

    return _REPORTS.update(
        report_id,
        _mutate,
        expected_revision=expected_revision,
        action="record_local_submission_attestation",
        actor=human,
    )


def record_verified_filing_receipt(
    report_id: str,
    *,
    envelope: dict[str, Any],
    actor: str,
    expected_revision: int,
) -> dict[str, Any] | None:
    """Verify a trusted external filing receipt and start eligible clocks."""
    _require_enabled()
    if not isinstance(envelope, dict):
        raise ValueError("filing receipt envelope must be an object")
    if set(envelope) != _FILING_RECEIPT_ENVELOPE_FIELDS:
        raise ValueError("filing receipt envelope fields do not match the v1 schema")
    payload = _filing_receipt_payload({
        key: envelope.get(key) for key in _FILING_RECEIPT_PAYLOAD_FIELDS
    })
    key_id = _required(envelope.get("key_id"), "key_id", 16).lower()
    signature = _required(envelope.get("signature"), "signature", 128).lower()
    if not _KEY_ID.fullmatch(key_id):
        raise ValueError("filing receipt key_id is malformed")
    if not _ED25519_SIGNATURE.fullmatch(signature):
        raise ValueError("filing receipt signature is malformed")
    trusted = _trusted_filing_receipt_keys()
    public_key = trusted.get(key_id)
    if public_key is None:
        raise ValueError("filing receipt signer is not trusted by deployment policy")
    from .audit.signing import verify_ed25519

    if not verify_ed25519(
        public_key,
        signature,
        filing_receipt_signing_message(payload),
    ):
        raise ValueError("filing receipt signature does not verify")
    human = _actor(actor)

    def _mutate(record: dict[str, Any]) -> None:
        case = _revalidate_report_authority(record)
        review = dict(record.get("review") or {})
        packet_sha256 = _validated_human_approval(record)
        if (
            record.get("status") != "approved"
            or review.get("status") != "approved"
            or review.get("approved_packet_sha256") != packet_sha256
        ):
            raise ValueError("report must be human-approved for this exact packet")
        expected = {
            "report_id": record.get("id"),
            "packet_sha256": packet_sha256,
            "stage": record.get("stage"),
            "case_id": record.get("case_id"),
            "tenant_id": _tenant_binding(),
        }
        for field, expected_value in expected.items():
            if payload.get(field) != expected_value:
                raise ValueError(
                    f"filing receipt {field} does not match the exact approved packet"
                )
        submitted_at = float(payload["submitted_at"])
        if submitted_at < float(case.get("awareness_at") or 0.0):
            raise ValueError("submitted_at must not precede case awareness")
        if submitted_at < float(review.get("reviewed_at") or 0.0):
            raise ValueError("submitted_at must not precede exact-packet approval")
        stage_order = ["early_warning", "full_notification", "final_report"]
        stage_index = stage_order.index(str(record.get("stage") or ""))
        existing = _submitted_by_stage(str(record.get("case_id") or ""))
        for other_stage, other_submission in existing.items():
            other_index = stage_order.index(other_stage)
            other_at = float(other_submission["submitted_at"])
            if other_index < stage_index and submitted_at < other_at:
                raise ValueError("filing receipt precedes an earlier report stage")
            if other_index > stage_index and submitted_at > other_at:
                raise ValueError("filing receipt follows an already filed later stage")
        if record.get("submission") is not None:
            raise ValueError("verified filing receipt has already been recorded")
        record["status"] = "submitted"
        record["submission"] = {
            **payload,
            "verification_status": "verified_external_receipt",
            "key_id": key_id,
            "signature": signature,
            "envelope_sha256": _digest({
                **payload,
                "key_id": key_id,
                "signature": signature,
            }),
            "verified_by": human,
            "verified_at": time.time(),
        }

    return _REPORTS.update(
        report_id,
        _mutate,
        expected_revision=expected_revision,
        action="record_verified_filing_receipt",
        actor=human,
    )


def create_vex(
    *,
    release_id: str,
    vulnerability_id: str,
    status: str,
    justification: str,
    impact_statement: str,
    actor: str,
) -> dict[str, Any]:
    _require_enabled()
    release = _get_required(_RELEASES, release_id, "release")
    _validated_release_record(release, require_sbom=True)
    vex_status = str(status or "").strip().lower()
    if vex_status not in _VEX_STATUSES:
        raise ValueError(f"status must be one of {sorted(_VEX_STATUSES)}")
    vulnerability = _required(vulnerability_id, "vulnerability_id", 500)
    bound = {
        "release_id": release["id"],
        "release_binding_sha256": release["release_binding_sha256"],
        "vulnerability_id": vulnerability,
        "status": vex_status,
        "justification": _required(justification, "justification", 1000),
        "impact_statement": _required(
            impact_statement,
            "impact_statement",
            16_000,
        ),
    }
    record = {
        "id": _stable_id(
            "CRV",
            {
                "release_id": release["id"],
                "vulnerability_id": vulnerability,
            },
        ),
        "schema": "lightwork.cra-vex.v1",
        "product_id": release["product_id"],
        **bound,
        "vex_sha256": _digest(bound),
        "review": {
            "status": "pending",
            "rationale": "",
            "reviewed_by": "",
            "reviewed_at": None,
        },
    }
    return _VEX.create(record, action="create", actor=_actor(actor))


def _vex_binding(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "release_id",
            "release_binding_sha256",
            "vulnerability_id",
            "status",
            "justification",
            "impact_statement",
        )
    }


def _validated_vex_authority(record: dict[str, Any]) -> dict[str, Any]:
    """Revalidate exact VEX, review, release, and SBOM authority for export."""
    release = _get_required(
        _RELEASES,
        str(record.get("release_id") or ""),
        "VEX release",
    )
    _validated_release_record(
        release,
        expected_product_id=str(record.get("product_id") or ""),
        require_sbom=True,
    )
    vex_sha256 = _sha256(record.get("vex_sha256"), "vex_sha256")
    review = record.get("review")
    if (
        record.get("release_binding_sha256")
        != release.get("release_binding_sha256")
        or vex_sha256 != _digest(_vex_binding(record))
        or not isinstance(review, dict)
        or review.get("status") != "approved"
        or review.get("vex_sha256") != vex_sha256
        or not str(review.get("reviewed_by") or "").strip()
        or not str(review.get("rationale") or "").strip()
        or not isinstance(review.get("reviewed_at"), (int, float))
        or isinstance(review.get("reviewed_at"), bool)
        or not math.isfinite(float(review["reviewed_at"]))
        or float(review["reviewed_at"]) <= 0
    ):
        raise ValueError("approved VEX authority binding does not verify")
    return _pack_record(record)


def decide_vex(
    vex_id: str,
    *,
    decision: str,
    rationale: str,
    reviewer: str,
    expected_revision: int,
) -> dict[str, Any] | None:
    _require_enabled()
    choice = str(decision or "").strip().lower()
    if choice not in _VEX_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(_VEX_DECISIONS)}")
    why = _required(rationale, "rationale", 8000)
    human = _actor(reviewer, "reviewer")

    def _mutate(record: dict[str, Any]) -> None:
        release = _get_required(
            _RELEASES,
            str(record.get("release_id") or ""),
            "VEX release",
        )
        _validated_release_record(
            release,
            expected_product_id=str(record.get("product_id") or ""),
            require_sbom=True,
        )
        vex_sha256 = _sha256(record.get("vex_sha256"), "vex_sha256")
        if (
            record.get("release_binding_sha256")
            != release.get("release_binding_sha256")
            or vex_sha256 != _digest(_vex_binding(record))
        ):
            raise ValueError("VEX authority binding does not verify")
        if (record.get("review") or {}).get("status") != "pending":
            raise ValueError("VEX has already been reviewed")
        record["review"] = {
            "status": choice,
            "rationale": why,
            "reviewed_by": human,
            "reviewed_at": time.time(),
            "vex_sha256": vex_sha256,
        }

    return _VEX.update(
        vex_id,
        _mutate,
        expected_revision=expected_revision,
        action=f"review_{choice}",
        actor=human,
    )


def _submitted_by_stage(case_id: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for report in _REPORTS.list():
        if report.get("case_id") != case_id or report.get("status") != "submitted":
            continue
        submission = _validated_filing_submission(report)
        stage = str(report.get("stage") or "")
        current = out.get(stage)
        if current is None or float(submission["submitted_at"]) < float(
            current["submitted_at"]
        ):
            out[stage] = submission
    return out


def _add_calendar_month(timestamp: float) -> float:
    current = datetime.fromtimestamp(timestamp, timezone.utc)
    year = current.year + (1 if current.month == 12 else 0)
    month = 1 if current.month == 12 else current.month + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return current.replace(year=year, month=month, day=day).timestamp()


def _deadline(anchor: float | None, clock: dict[str, Any]) -> float | None:
    if anchor is None:
        return None
    if clock["unit"] == "hours":
        return anchor + int(clock["amount"]) * 3600
    if clock["unit"] == "calendar_days":
        return anchor + int(clock["amount"]) * 86400
    if clock["unit"] == "calendar_months":
        result = anchor
        for _ in range(int(clock["amount"])):
            result = _add_calendar_month(result)
        return result
    raise ValueError("unsupported CRA clock unit")


def _clock_anchor(
    case: dict[str, Any],
    clock: dict[str, Any],
    submissions: dict[str, dict[str, Any]],
) -> float | None:
    name = clock["anchor"]
    if name == "awareness":
        return float(case["awareness_at"])
    if name == "corrective_measure_available":
        anchor = (case.get("anchors") or {}).get(name)
        return None if not isinstance(anchor, dict) else float(anchor["occurred_at"])
    if name == "full_notification_submission":
        submission = submissions.get("full_notification")
        return None if submission is None else float(submission["submitted_at"])
    return None


def case_status(case_id: str, *, now: float | None = None) -> dict[str, Any]:
    _require_enabled()
    case = _get_required(_CASES, case_id, "CRA case")
    current = time.time() if now is None else _timestamp(now, "now")
    row = dict(case)
    override = case.get("statutory_effective_override")
    override_valid = (
        isinstance(override, dict)
        and override.get("status") == "counsel_override"
        and override.get("case_binding_sha256") == case.get("case_binding_sha256")
        and bool(str(override.get("counsel") or "").strip())
        and bool(str(override.get("rationale") or "").strip())
    )
    within_effective_period = (
        float(case.get("awareness_at") or 0.0) >= _CRA_REPORTING_EFFECTIVE_AT
    )
    row["statutory_effectivity"] = {
        "status": (
            "effective"
            if within_effective_period
            else "counsel_override" if override_valid else "not_yet_effective"
        ),
        "effective_at": _CRA_REPORTING_EFFECTIVE_AT,
        "effective_date": CRA_REPORTING_EFFECTIVE,
        "override": override if override_valid else None,
    }
    if (case.get("reportability") or {}).get("status") != "reportable":
        row["clocks"] = []
        return row
    _validated_release_for_case(case)
    if not within_effective_period and not override_valid:
        row["clocks"] = []
        return row
    submissions = _submitted_by_stage(case["id"])
    clocks = []
    for spec in CRA_CLOCKS:
        if spec["case_type"] != case["case_type"]:
            continue
        stage = spec["stage"]
        anchor = _clock_anchor(case, spec, submissions)
        deadline_at = _deadline(anchor, spec)
        submission = submissions.get(stage)
        if submission is not None:
            state = "submitted"
        elif deadline_at is None:
            state = "awaiting_anchor"
        elif current > deadline_at:
            state = "overdue"
        else:
            state = "open"
        clocks.append({
            **spec,
            "anchor_at": anchor,
            "deadline_at": deadline_at,
            "state": state,
            "submission": submission,
            "source_url": CRA_SOURCE_URL,
            "effective": CRA_REPORTING_EFFECTIVE,
            "applicability": "Human legal determination required",
        })
    row["clocks"] = clocks
    return row


def get_product(product_id: str) -> dict[str, Any] | None:
    return _PRODUCTS.get(product_id)


def list_products() -> list[dict[str, Any]]:
    return _PRODUCTS.list()


def get_release(release_id: str) -> dict[str, Any] | None:
    return _RELEASES.get(release_id)


def list_releases(*, product_id: str = "") -> list[dict[str, Any]]:
    return [
        row for row in _RELEASES.list()
        if not product_id or row.get("product_id") == product_id
    ]


def get_case(case_id: str) -> dict[str, Any] | None:
    record = _CASES.get(case_id)
    return None if record is None else case_status(case_id)


def list_cases(*, product_id: str = "") -> list[dict[str, Any]]:
    return [
        case_status(row["id"])
        for row in _CASES.list()
        if not product_id or row.get("product_id") == product_id
    ]


def list_reports(*, case_id: str = "") -> list[dict[str, Any]]:
    return [
        row for row in _REPORTS.list()
        if not case_id or row.get("case_id") == case_id
    ]


def list_vex(*, release_id: str = "") -> list[dict[str, Any]]:
    return [
        row for row in _VEX.list()
        if not release_id or row.get("release_id") == release_id
    ]


def get_vex(vex_id: str) -> dict[str, Any] | None:
    return _VEX.get(vex_id)


def _pack_record(record: dict[str, Any]) -> dict[str, Any]:
    """Exclude delivery-only outbox state from externally shared packs."""
    return {
        key: value for key, value in record.items()
        if key != "_audit_pending"
    }


def render_product_security_pack(product_id: str) -> dict[str, Any]:
    """Render and sign an evidence pack; signature is honest when unavailable."""
    _require_enabled()
    product = _pack_record(_get_required(_PRODUCTS, product_id, "product"))
    releases = []
    for row in list_releases(product_id=product["id"]):
        _validated_release_record(
            row,
            expected_product_id=product["id"],
            require_sbom=True,
        )
        releases.append(_pack_record(row))
    release_ids = {release["id"] for release in releases}
    cases = []
    for row in _CASES.list():
        if row.get("release_id") not in release_ids:
            continue
        _validated_release_for_case(row)
        cases.append(_pack_record(case_status(row["id"])))
    reports = [
        _validated_report_for_pack(row)
        for row in _REPORTS.list()
        if row.get("release_id") in release_ids
        and row.get("status") in {"approved", "submitted"}
    ]
    vex = [
        _validated_vex_authority(row) for row in _VEX.list()
        if row.get("release_id") in release_ids
        and (row.get("review") or {}).get("status") == "approved"
    ]
    bound = {
        "kind": "lightwork-cra-product-security-pack",
        "version": 1,
        "product": product,
        "releases": releases,
        "cases": cases,
        "approved_reports": reports,
        "approved_vex": vex,
        "source": {
            "regulation": "Regulation (EU) 2024/2847",
            "article": "Article 14",
            "url": CRA_SOURCE_URL,
            "reporting_effective": CRA_REPORTING_EFFECTIVE,
        },
        "legal_notice": (
            "Human legal determination is required; this pack is operational "
            "evidence and does not constitute legal advice or a regulator filing."
        ),
    }
    pack = {
        **bound,
        "generated_at": time.time(),
        "pack_sha256": _digest(bound),
    }
    from .proof_pack import sign

    return sign(pack)


def collect_evidence(*, actor: str) -> list[dict[str, Any]]:
    """Project review-gated CRA metadata into the continuous evidence graph."""
    _require_enabled()
    from . import evidence_graph

    human = _actor(actor)
    imported = []
    for product in _PRODUCTS.list():
        decision = dict(product.get("applicability") or {})
        if decision.get("status") not in _PRODUCT_DECISIONS:
            continue
        imported.append(evidence_graph._ingest_cra_product_applicability(
            str(product["id"]),
            actor=human,
        ))
    for vex in _VEX.list():
        review = dict(vex.get("review") or {})
        if review.get("status") != "approved":
            continue
        imported.append(evidence_graph._ingest_cra_vex(
            str(vex["id"]),
            actor=human,
        ))
    return imported


__all__ = [
    "CRA_CLOCKS",
    "CRA_REPORTING_EFFECTIVE",
    "CRA_SOURCE_URL",
    "FilingReceiptSigner",
    "approve_report",
    "attach_sbom",
    "build_filing_receipt_envelope",
    "case_status",
    "collect_evidence",
    "create_product",
    "create_release",
    "create_vex",
    "decide_case_reportability",
    "decide_product_applicability",
    "decide_vex",
    "draft_report",
    "enabled",
    "filing_receipt_signing_message",
    "get_case",
    "get_product",
    "get_release",
    "get_vex",
    "list_cases",
    "list_products",
    "list_releases",
    "list_reports",
    "list_vex",
    "open_case",
    "override_case_effective_date",
    "record_case_anchor",
    "record_submission_receipt",
    "record_verified_filing_receipt",
    "render_product_security_pack",
]
