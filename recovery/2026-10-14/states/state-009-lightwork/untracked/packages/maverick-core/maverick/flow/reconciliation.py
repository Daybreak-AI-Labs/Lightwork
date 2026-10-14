"""Durable operator adjudication for indeterminate flow effects.

An interrupted high-risk node is quarantined because the external system may
have committed while Lightwork lost the acknowledgement. This module turns that
quarantine into an explicit incident case: bounded evidence, deterministic
deadlines, an escalation target, and an immutable signed-audit-backed operator
resolution. It never discovers state by repeating the original mutation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import store

CASE_SCHEMA = "lightwork.flow-indeterminate-case.v1"
RESOLUTION_SCHEMA = "lightwork.flow-indeterminate-resolution.v1"
DISPOSITIONS = frozenset(
    {"committed", "not_committed", "compensated", "accepted_loss"}
)
DEFAULT_RECONCILE_SECONDS = 15 * 60
DEFAULT_ESCALATE_SECONDS = 60 * 60
MAX_EVIDENCE_REFERENCES = 20

_HEX_32_RE = re.compile(r"[0-9a-f]{32}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_METHOD_RE = re.compile(r"[a-z][a-z0-9_.-]{1,63}")
_REFERENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:/._@+~-]{0,511}")
_RECORD_FIELDS = frozenset(
    {
        "schema",
        "resolution_id",
        "request_sha256",
        "run_id",
        "flow_id",
        "owner",
        "cursor",
        "case_sha256",
        "disposition",
        "detected_at",
        "reconcile_by",
        "escalate_by",
        "resolved_at",
        "timed_out",
        "escalated",
        "escalation_target",
        "adjudicated_by",
        "authority_source",
        "reconciliation_method",
        "reconciliation_summary",
        "evidence_references",
        "evidence_sha256",
        "external_reference",
        "compensating_action",
        "replacement_authorized",
        "record_sha256",
    }
)


class IndeterminateCaseError(RuntimeError):
    """An indeterminate case or its proposed resolution is invalid."""


class IndeterminateCaseAlreadyResolved(IndeterminateCaseError):
    """A different immutable resolution already closed this case."""


def _canonical(value: Mapping[str, Any] | dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise IndeterminateCaseError("indeterminate case is not canonical JSON") from exc


def _digest(value: Mapping[str, Any] | dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _integer_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise IndeterminateCaseError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise IndeterminateCaseError(
            f"{name} must be between {minimum} and {maximum} seconds"
        )
    return value


def _safe_text(value: object, label: str, *, maximum: int, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise IndeterminateCaseError(f"{label} must be text")
    text = value.strip()
    if (not empty and not text) or len(text) > maximum:
        raise IndeterminateCaseError(f"{label} has invalid length")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise IndeterminateCaseError(f"{label} contains control characters")
    if text:
        try:
            from ..safety.secret_detector import scan

            if scan(text):
                raise IndeterminateCaseError(f"{label} appears to contain a secret")
        except IndeterminateCaseError:
            raise
        except Exception as exc:
            raise IndeterminateCaseError(
                f"{label} could not be screened for secrets"
            ) from exc
    return text


def escalation_policy() -> dict[str, Any]:
    reconcile = _integer_env(
        "MAVERICK_INDETERMINATE_RECONCILE_SECONDS",
        DEFAULT_RECONCILE_SECONDS,
        minimum=60,
        maximum=24 * 60 * 60,
    )
    escalate = _integer_env(
        "MAVERICK_INDETERMINATE_ESCALATE_SECONDS",
        DEFAULT_ESCALATE_SECONDS,
        minimum=60,
        maximum=7 * 24 * 60 * 60,
    )
    if escalate <= reconcile:
        raise IndeterminateCaseError(
            "indeterminate escalation deadline must follow reconciliation deadline"
        )
    target = os.environ.get(
        "MAVERICK_INDETERMINATE_ESCALATION_TARGET",
        "local incident owner",
    ).strip()
    target = _safe_text(target, "escalation target", maximum=256)
    return {
        "reconcile_seconds": reconcile,
        "escalate_seconds": escalate,
        "escalation_target": target,
    }


def _reference(value: object, label: str, *, empty: bool = False) -> str:
    text = _safe_text(value, label, maximum=512, empty=empty)
    if text and _REFERENCE_RE.fullmatch(text) is None:
        raise IndeterminateCaseError(
            f"{label} must be an opaque identifier without query or fragment data"
        )
    return text


def _resolution_dir() -> Path:
    from ..paths import data_dir

    return data_dir("flows", "indeterminate-resolutions")


def _resolution_path(run_id: str) -> Path:
    if _HEX_32_RE.fullmatch(str(run_id)) is None and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", str(run_id)
    ):
        raise IndeterminateCaseError("flow run id is invalid")
    return _resolution_dir() / f"{run_id}.json"


def _case_material(run: store.FlowRun) -> dict[str, Any]:
    detected_at = float(run.updated or run.created or 0.0)
    policy = escalation_policy()
    material = {
        "schema": CASE_SCHEMA,
        "run_id": run.run_id,
        "flow_id": run.flow_id,
        "owner": run.owner,
        "cursor": str(run.cursor or ""),
        "detected_at": detected_at,
        "error": str(run.error or "")[:500],
        "input_sha256": _digest({"input_data": run.input_data}),
        "state_sha256": _digest({"data": run.data}),
        "node_trace_sha256": _digest({"nodes": run.nodes}),
        "reconcile_by": detected_at + int(policy["reconcile_seconds"]),
        "escalate_by": detected_at + int(policy["escalate_seconds"]),
        "escalation_target": policy["escalation_target"],
    }
    material["case_sha256"] = _digest(material)
    return material


def case_for_run(run: store.FlowRun, *, now: float | None = None) -> dict[str, Any]:
    """Return the bounded evidence and operator actions for one quarantine."""
    if run.status != "indeterminate":
        raise IndeterminateCaseError("flow run is not indeterminate")
    case = _case_material(run)
    at = time.time() if now is None else float(now)
    case.update(
        {
            "reconciliation_overdue": at >= float(case["reconcile_by"]),
            "escalation_overdue": at >= float(case["escalate_by"]),
            "operator_actions": [
                "freeze all automatic retry and preserve connector/provider evidence",
                "locate the prepared operation by stable idempotency key or external reference",
                "query the provider's authoritative status without repeating the mutation",
                "capture a digest and opaque references for the evidence reviewed",
                "perform any compensating action as a separate approved operation",
                "escalate when the configured deadline expires and record a final adjudication",
            ],
            "allowed_dispositions": sorted(DISPOSITIONS),
            "resolution": load_resolution(run.run_id),
        }
    )
    return case


def _request_material(
    *,
    resolution_id: str,
    disposition: str,
    reconciliation_method: str,
    reconciliation_summary: str,
    evidence_references: Sequence[str],
    evidence_sha256: str,
    external_reference: str,
    compensating_action: str,
    replacement_authorized: bool,
) -> dict[str, Any]:
    return {
        "resolution_id": resolution_id,
        "disposition": disposition,
        "reconciliation_method": reconciliation_method,
        "reconciliation_summary": reconciliation_summary,
        "evidence_references": list(evidence_references),
        "evidence_sha256": evidence_sha256,
        "external_reference": external_reference,
        "compensating_action": compensating_action,
        "replacement_authorized": replacement_authorized,
    }


def _validate_request(
    *,
    resolution_id: object,
    disposition: object,
    reconciliation_method: object,
    reconciliation_summary: object,
    evidence_references: object,
    evidence_sha256: object,
    external_reference: object,
    compensating_action: object,
    replacement_authorized: object,
) -> dict[str, Any]:
    if not isinstance(resolution_id, str) or _HEX_32_RE.fullmatch(resolution_id) is None:
        raise IndeterminateCaseError("resolution_id must be 32 lowercase hex characters")
    if not isinstance(disposition, str) or disposition not in DISPOSITIONS:
        raise IndeterminateCaseError("indeterminate disposition is unsupported")
    method = str(reconciliation_method or "")
    if _METHOD_RE.fullmatch(method) is None:
        raise IndeterminateCaseError("reconciliation method is invalid")
    summary = _safe_text(
        reconciliation_summary,
        "reconciliation summary",
        maximum=2000,
    )
    if (
        not isinstance(evidence_references, Sequence)
        or isinstance(evidence_references, (str, bytes))
        or not 1 <= len(evidence_references) <= MAX_EVIDENCE_REFERENCES
    ):
        raise IndeterminateCaseError("one to twenty evidence references are required")
    references = [
        _reference(value, "evidence reference") for value in evidence_references
    ]
    if len(references) != len(set(references)):
        raise IndeterminateCaseError("evidence references contain duplicates")
    evidence_digest = str(evidence_sha256 or "")
    if _SHA256_RE.fullmatch(evidence_digest) is None:
        raise IndeterminateCaseError("evidence_sha256 must be lowercase SHA-256")
    external = _reference(
        external_reference,
        "external reference",
        empty=True,
    )
    compensation = _safe_text(
        compensating_action,
        "compensating action",
        maximum=2000,
    )
    if not isinstance(replacement_authorized, bool):
        raise IndeterminateCaseError("replacement_authorized must be Boolean")
    if disposition == "committed" and not external:
        raise IndeterminateCaseError("committed resolution requires an external reference")
    if disposition != "not_committed" and replacement_authorized:
        raise IndeterminateCaseError(
            "only a proven not-committed resolution may authorize replacement"
        )
    return _request_material(
        resolution_id=resolution_id,
        disposition=disposition,
        reconciliation_method=method,
        reconciliation_summary=summary,
        evidence_references=references,
        evidence_sha256=evidence_digest,
        external_reference=external,
        compensating_action=compensation,
        replacement_authorized=replacement_authorized,
    )


def _validate_record(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _RECORD_FIELDS:
        raise IndeterminateCaseError("indeterminate resolution record shape is invalid")
    record = dict(raw)
    digest = str(record.pop("record_sha256", ""))
    if record.get("schema") != RESOLUTION_SCHEMA:
        raise IndeterminateCaseError("indeterminate resolution schema is unsupported")
    if _SHA256_RE.fullmatch(digest) is None or _digest(record) != digest:
        raise IndeterminateCaseError("indeterminate resolution integrity check failed")
    record["record_sha256"] = digest
    return record


def load_resolution(run_id: str) -> dict[str, Any] | None:
    path = _resolution_path(run_id)
    try:
        backend = store.production_authority_backend()
    except Exception as exc:
        raise IndeterminateCaseError(
            "indeterminate resolution authority is unavailable"
        ) from exc
    if backend == "postgres":
        try:
            raw = store.load_indeterminate_resolution(run_id)
        except Exception as exc:
            raise IndeterminateCaseError(
                "indeterminate resolution record is unreadable"
            ) from exc
        return _validate_record(raw) if raw is not None else None
    if not path.exists():
        return None
    try:
        from ..file_lock import atomic_read_text, ensure_private_file

        ensure_private_file(path)
        raw = json.loads(atomic_read_text(path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndeterminateCaseError(
            "indeterminate resolution record is unreadable"
        ) from exc
    return _validate_record(raw)


def _adjudicator_allowed(adjudicated_by: str) -> None:
    configured = {
        value.strip()
        for value in os.environ.get("MAVERICK_INDETERMINATE_ADJUDICATORS", "").split(",")
        if value.strip()
    }
    try:
        from ..production_scope import production_surface_required

        production = production_surface_required()
    except Exception as exc:
        raise IndeterminateCaseError("production adjudicator policy is unavailable") from exc
    if production and not configured:
        raise IndeterminateCaseError("production indeterminate adjudicators are not configured")
    if configured and adjudicated_by not in configured:
        raise IndeterminateCaseError("principal lacks indeterminate adjudication authority")


@dataclass(frozen=True)
class ResolutionRequest:
    resolution_id: str
    disposition: str
    reconciliation_method: str
    reconciliation_summary: str
    evidence_references: tuple[str, ...]
    evidence_sha256: str
    external_reference: str
    compensating_action: str
    replacement_authorized: bool = False


def resolve_case(
    run_id: str,
    request: ResolutionRequest,
    *,
    adjudicated_by: str,
    authority_source: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Permanently resolve a quarantine after a required signed audit write."""
    actor = _safe_text(adjudicated_by, "adjudicator", maximum=500)
    source = _safe_text(authority_source, "authority source", maximum=80)
    _adjudicator_allowed(actor)
    validated = _validate_request(
        resolution_id=request.resolution_id,
        disposition=request.disposition,
        reconciliation_method=request.reconciliation_method,
        reconciliation_summary=request.reconciliation_summary,
        evidence_references=request.evidence_references,
        evidence_sha256=request.evidence_sha256,
        external_reference=request.external_reference,
        compensating_action=request.compensating_action,
        replacement_authorized=request.replacement_authorized,
    )
    request_digest = _digest(validated)
    with store.run_lock(run_id):
        existing = load_resolution(run_id)
        if existing is not None:
            if (
                existing["resolution_id"] == validated["resolution_id"]
                and existing["request_sha256"] == request_digest
            ):
                return existing
            raise IndeterminateCaseAlreadyResolved(
                "indeterminate case already has a different permanent resolution"
            )
        run = store.load_run(run_id)
        if run is None:
            raise IndeterminateCaseError("no such flow run")
        if run.status != "indeterminate":
            raise IndeterminateCaseError("flow run is not indeterminate")
        case = _case_material(run)
        resolved_at = time.time() if now is None else float(now)
        timed_out = resolved_at >= float(case["reconcile_by"])
        escalated = resolved_at >= float(case["escalate_by"])
        record: dict[str, Any] = {
            "schema": RESOLUTION_SCHEMA,
            "resolution_id": validated["resolution_id"],
            "request_sha256": request_digest,
            "run_id": run.run_id,
            "flow_id": run.flow_id,
            "owner": run.owner,
            "cursor": str(run.cursor or ""),
            "case_sha256": case["case_sha256"],
            "disposition": validated["disposition"],
            "detected_at": case["detected_at"],
            "reconcile_by": case["reconcile_by"],
            "escalate_by": case["escalate_by"],
            "resolved_at": resolved_at,
            "timed_out": timed_out,
            "escalated": escalated,
            "escalation_target": case["escalation_target"],
            "adjudicated_by": actor,
            "authority_source": source,
            "reconciliation_method": validated["reconciliation_method"],
            "reconciliation_summary": validated["reconciliation_summary"],
            "evidence_references": validated["evidence_references"],
            "evidence_sha256": validated["evidence_sha256"],
            "external_reference": validated["external_reference"],
            "compensating_action": validated["compensating_action"],
            "replacement_authorized": validated["replacement_authorized"],
        }
        record["record_sha256"] = _digest(record)

        from ..audit import EventKind, required_audit_event

        required_audit_event(
            EventKind.FLOW_INDETERMINATE_RESOLVED,
            agent=actor,
            run_id=run.run_id,
            flow_id=run.flow_id,
            resolution_id=record["resolution_id"],
            disposition=record["disposition"],
            case_sha256=record["case_sha256"],
            evidence_sha256=record["evidence_sha256"],
            record_sha256=record["record_sha256"],
            authority_source=source,
            timed_out=timed_out,
            escalated=escalated,
            replacement_authorized=record["replacement_authorized"],
        )
        if store.production_authority_backend() == "postgres":
            try:
                store.save_indeterminate_resolution(run_id, record)
            except Exception as exc:
                raise IndeterminateCaseError(
                    "indeterminate resolution record could not be committed"
                ) from exc
        else:
            from ..file_lock import atomic_write_text, ensure_private_directory

            path = _resolution_path(run_id)
            ensure_private_directory(path.parent)
            atomic_write_text(path, json.dumps(record, sort_keys=True))
        return _validate_record(record)


def replacement_is_authorized(run_id: str) -> bool:
    record = load_resolution(run_id)
    return bool(
        record
        and record["disposition"] == "not_committed"
        and record["replacement_authorized"] is True
    )


__all__ = [
    "CASE_SCHEMA",
    "DISPOSITIONS",
    "IndeterminateCaseAlreadyResolved",
    "IndeterminateCaseError",
    "RESOLUTION_SCHEMA",
    "ResolutionRequest",
    "case_for_run",
    "escalation_policy",
    "load_resolution",
    "replacement_is_authorized",
    "resolve_case",
]
