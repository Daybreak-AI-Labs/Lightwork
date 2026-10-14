"""Durable, fail-closed reconnect cursors for dashboard live transports."""
from __future__ import annotations

import hashlib
import json
import re

MAX_EVENT_ID = (1 << 63) - 1
_NUMERIC_ID = re.compile(r"^[0-9]+$")
_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{64}$")


def _numeric_event_id(value: object, *, field: str) -> int:
    """Parse one exact decimal event id without Python's coercive ``int`` grammar."""
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if _NUMERIC_ID.fullmatch(raw) is None:
            raise ValueError(f"{field} must be an integer")
        # The maximum supported value has 19 decimal digits.  Rejecting a
        # longer representation also avoids CPython's unbounded-integer parser
        # becoming work an unauthenticated reconnect header can amplify.
        if len(raw) > len(str(MAX_EVENT_ID)):
            raise ValueError(f"{field} is outside the supported range")
        parsed = int(raw)
    else:
        raise ValueError(f"{field} must be an integer")
    if parsed < 0 or parsed > MAX_EVENT_ID:
        raise ValueError(f"{field} is outside the supported range")
    return parsed


def numeric_cursor(since: int | str = 0, last_event_id: str | None = None) -> int:
    """Resolve query/header event ids without silently replaying from zero."""
    value = _numeric_event_id(since, field="event cursor")
    if last_event_id is None:
        return value
    if not isinstance(last_event_id, str):
        raise ValueError("Last-Event-ID must be an integer")
    if not last_event_id.strip():
        return value
    header = _numeric_event_id(last_event_id, field="Last-Event-ID")
    return max(value, header)


def snapshot_cursor(last_event_id: str | None) -> str:
    """Validate an opaque durable-snapshot SSE cursor."""
    if last_event_id is None:
        return ""
    if not isinstance(last_event_id, str):
        raise ValueError("Last-Event-ID is not a valid snapshot cursor")
    if not last_event_id.strip():
        return ""
    value = last_event_id.strip().lower()
    if _SNAPSHOT_ID.fullmatch(value) is None:
        raise ValueError("Last-Event-ID is not a valid snapshot cursor")
    return value


def snapshot_id(payload: dict) -> str:
    """Content address one authoritative API snapshot, independent of process."""
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = ["MAX_EVENT_ID", "numeric_cursor", "snapshot_cursor", "snapshot_id"]
