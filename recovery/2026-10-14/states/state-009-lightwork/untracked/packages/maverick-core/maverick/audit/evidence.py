"""Strict, self-verifying customer audit-evidence bundles.

The ordinary JSONL/CEF exporter is an integration surface.  This module is the
proof surface: it reads identity-bound snapshots, verifies every source row and
the cross-file anchor ledger before selection, includes the complete selected
day segments so a verifier can prove no matching row was omitted, and signs the
whole packet.  Any unreadable, malformed, unsigned, or unanchored completed
segment refuses the packet instead of becoming an apparently empty export.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import reader
from .events import is_valid_day
from .export import event_matches

SCHEMA = "lightwork.audit-evidence.v1"
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
# The first-profile container has a 2 GiB memory limit. Verification holds the
# authenticated source bytes plus parsed/canonical forms, so keep issuance far
# below that ceiling and require larger matters to be exported in date batches.
_MAX_SEGMENT_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_ROWS = 100_000
_MAX_SEGMENTS = 4000
_MAX_ANCHOR_ROWS = 10_000
_MAX_PUBLIC_KEYS = 256


class AuditEvidenceError(RuntimeError):
    """A complete, tenant-bound, integrity-verifiable packet cannot be made."""


def _canonical_rows(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _selector_id(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise AuditEvidenceError(
            f"{label} must be 1-128 ASCII letters, digits, dot, colon, underscore, or dash"
        )
    return value


def _selection(
    *,
    day: str | None,
    all_days: bool,
    since: str | None,
    until: str | None,
    goal_id: int | None,
    incident_id: str | None,
    action_id: str | None,
) -> dict[str, Any]:
    for label, value in (("day", day), ("since", since), ("until", until)):
        if value is not None and not is_valid_day(value):
            raise AuditEvidenceError(f"{label} must be a real YYYY-MM-DD date")
    if since and until and since > until:
        raise AuditEvidenceError("since must not be after until")
    modes = int(day is not None) + int(bool(all_days)) + int(
        since is not None or until is not None
    )
    if modes != 1:
        raise AuditEvidenceError(
            "choose exactly one evidence range: day, all_days, or since/until"
        )
    if goal_id is not None and (
        isinstance(goal_id, bool) or not isinstance(goal_id, int) or goal_id <= 0
    ):
        raise AuditEvidenceError("goal_id must be a positive integer")
    return {
        "day": day,
        "all_days": bool(all_days),
        "since": since,
        "until": until,
        "goal_id": goal_id,
        "incident_id": _selector_id(incident_id, "incident_id"),
        "action_id": _selector_id(action_id, "action_id"),
    }


def _read_verified(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    from .worm import WormUnavailable, _read_custodied_file, _verified_chain_records

    try:
        raw = _read_custodied_file(
            path,
            mode=0o600,
            max_bytes=_MAX_SEGMENT_BYTES,
        )
        rows = _verified_chain_records(raw)
    except (OSError, UnicodeError, WormUnavailable, ValueError) as exc:
        raise AuditEvidenceError(
            f"signed audit evidence is unavailable or invalid: {path.name}"
        ) from exc
    if not rows:
        raise AuditEvidenceError(f"selected audit segment is empty: {path.name}")
    return raw, rows


def build_evidence_bundle(  # noqa: C901 - one fail-closed evidence transaction
    *,
    tenant: str | None,
    day: str | None = None,
    all_days: bool = False,
    since: str | None = None,
    until: str | None = None,
    goal_id: int | None = None,
    incident_id: str | None = None,
    action_id: str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build and sign one exact tenant's verified evidence packet."""
    selection = _selection(
        day=day,
        all_days=all_days,
        since=since,
        until=until,
        goal_id=goal_id,
        incident_id=incident_id,
        action_id=action_id,
    )
    try:
        from ..paths import current_tenant_id_strict

        active_tenant = current_tenant_id_strict()
    # failure-policy: fail_closed -- unresolved tenant identity refuses evidence
    except Exception as exc:
        raise AuditEvidenceError("an explicit active tenant is required") from exc
    if not tenant:
        tenant = active_tenant
    if not isinstance(tenant, str) or not tenant.strip() or len(tenant) > 128:
        raise AuditEvidenceError("tenant must be a bounded non-empty identifier")
    if tenant != active_tenant:
        raise AuditEvidenceError(
            "evidence tenant must equal the active tenant; switch tenant context first"
        )
    paths = reader.event_paths(
        day=day,
        all_days=all_days,
        since=since,
        until=until,
        tenant=tenant,
    )
    paths = [path for path in paths if path.is_file()]
    if not paths:
        raise AuditEvidenceError("the selected tenant evidence range has no day files")
    if len(paths) > _MAX_SEGMENTS:
        raise AuditEvidenceError("selected evidence exceeds the segment ceiling")

    audit_dir = reader.resolve_audit_dir(tenant)
    total_bytes = 0
    total_rows = 0
    segments: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    for path in paths:
        raw, rows = _read_verified(path)
        total_bytes += len(raw)
        total_rows += len(rows)
        if total_bytes > _MAX_TOTAL_BYTES or total_rows > _MAX_ROWS:
            raise AuditEvidenceError("selected evidence exceeds the bounded export ceiling")
        canonical = _canonical_rows(rows)
        segment_index = len(segments)
        segments.append(
            {
                "day": path.stem,
                "row_count": len(rows),
                "tip_hash": rows[-1]["hash"],
                "content_sha256": hashlib.sha256(canonical).hexdigest(),
                "rows": rows,
            }
        )
        for row_index, event in enumerate(rows):
            if event_matches(
                event,
                goal_id=goal_id,
                incident_id=incident_id,
                action_id=action_id,
            ):
                matches.append(
                    {"segment": segment_index, "row": row_index, "hash": event["hash"]}
                )

    from .signing import (
        ANCHOR_FILENAME,
        trusted_audit_public_keys,
        verify_anchors,
    )

    breaks = verify_anchors(audit_dir)
    if breaks:
        raise AuditEvidenceError("cross-file anchor verification failed")
    anchor_path = audit_dir / ANCHOR_FILENAME
    anchor_rows: list[dict[str, Any]] = []
    if anchor_path.exists():
        raw, anchor_rows = _read_verified(anchor_path)
        if len(anchor_rows) > _MAX_ANCHOR_ROWS:
            raise AuditEvidenceError("anchor ledger exceeds the evidence ceiling")
        total_bytes += len(raw)
        if total_bytes > _MAX_TOTAL_BYTES:
            raise AuditEvidenceError("selected evidence exceeds the bounded export ceiling")

    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    latest_anchors = {
        str(row.get("day")): row
        for row in anchor_rows
        if row.get("kind") == "anchor" and isinstance(row.get("day"), str)
    }
    for segment in segments:
        if segment["day"] >= current.date().isoformat():
            continue
        anchor = latest_anchors.get(segment["day"])
        if not anchor or (
            anchor.get("tip_hash") != segment["tip_hash"]
            or anchor.get("row_count") != segment["row_count"]
        ):
            raise AuditEvidenceError(
                f"completed segment lacks its exact anchor: {segment['day']}"
            )

    try:
        public_keys = trusted_audit_public_keys()
    # failure-policy: fail_closed -- unavailable trust roots refuse evidence
    except Exception as exc:
        raise AuditEvidenceError("trusted audit public-key registry is unavailable") from exc
    used_keys = {
        str(row.get("key_id"))
        for segment in segments
        for row in segment["rows"]
    } | {str(row.get("key_id")) for row in anchor_rows}
    if len(used_keys) > _MAX_PUBLIC_KEYS:
        raise AuditEvidenceError("evidence key history exceeds the packet ceiling")
    if not used_keys or not used_keys.issubset(public_keys):
        raise AuditEvidenceError("evidence uses an untrusted or unavailable signing key")

    unsigned = {
        "schema": SCHEMA,
        "generated_at": current.isoformat().replace("+00:00", "Z"),
        "tenant": tenant,
        "selection": selection,
        "segments": segments,
        "anchors": {
            "status": "verified" if anchor_rows else "current_day_only",
            "rows": anchor_rows,
        },
        "public_keys": {key: public_keys[key] for key in sorted(used_keys)},
        "matches": matches,
        "summary": {
            "segment_count": len(segments),
            "source_row_count": total_rows,
            "match_count": len(matches),
        },
        "limitations": [
            "The packet proves the included tenant day-segment bytes and exact selector result; it does not prove that every real-world action entered the audit system.",
            "Publisher identity requires the outer public key through an independent trusted channel.",
        ],
    }
    try:
        from ..attestation import sign

        return sign(unsigned)
    # failure-policy: fail_closed -- unsigned evidence is never returned
    except Exception as exc:
        raise AuditEvidenceError("evidence packet signing failed") from exc


__all__ = ["AuditEvidenceError", "SCHEMA", "build_evidence_bundle"]
