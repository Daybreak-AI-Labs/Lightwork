"""Retained workspace feedback compatibility and owner-scoped signal counts.

New customer feedback is saved as an encrypted, exact-answer-bound native
receipt by ``result_feedback_receipts``. This module reads the older workspace
log without claiming its rows identify the currently displayed answer or an
original reviewed operation. Its legacy writer is retained for compatibility;
the reviewed dashboard route does not use it. Counts combine both stores.

A human rating is a feedback signal, not proof of real business performance,
successful training, or the separate governed sign-off required by some work.
"""
from __future__ import annotations

import json
import time

from maverick.ndjson_log import append_row, clamp01
from maverick.paths import data_dir

_MAX_ROWS = 5000  # bound the tail we read back; the file itself grows unbounded


def _path():
    # The old deployment-global file has no trustworthy workspace lineage.
    # Never adopt its rows by matching a colliding goal number or owner name.
    return data_dir("dashboard", "goal-feedback-v2.ndjson")


def record(goal_id: int, owner: str, rating: str, *, value: float,
           note: str = "", by: str = "") -> dict:
    """Append one feedback row (latest per goal wins on read) and return it.
    Never raises."""
    row = {
        "ts": time.time(),
        "goal_id": int(goal_id),
        "owner": str(owner or ""),
        "rating": str(rating),          # "up" | "down" (display), value carries the reward
        "value": clamp01(value),
        "note": str(note)[:500],
        "by": str(by or ""),
    }
    append_row(_path(), row)
    return row


def _read_rows() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    # Bound both records and bytes when reading the retained workspace log.
    with path.open("rb") as stream:
        end = stream.seek(0, 2)
        start = max(0, end - 4 * 1024 * 1024)
        stream.seek(start)
        raw = stream.read(4 * 1024 * 1024)
    if start:
        raw = raw.partition(b"\n")[2]
    rows = []
    for line in raw.splitlines()[-_MAX_ROWS:]:
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def latest_for_goal(goal_id: int, *, owner: str) -> dict | None:
    """The most recent feedback row for a goal, or ``None``."""
    gid = int(goal_id)
    hit = None
    for r in _read_rows():  # rows are append-order, so the last match is newest
        if r.get("goal_id") == gid and r.get("owner") == owner:
            hit = r
    return hit


def count(*, owner: str | None = None, world=None) -> int:
    """How many distinct goals have human feedback (accumulated human signal).
    ``owner=None`` counts all; a specific owner counts only its own."""
    seen: set[int] = set()
    for r in _read_rows():
        if owner is not None and r.get("owner", "") != owner:
            continue
        try:
            seen.add(int(r.get("goal_id")))
        except (TypeError, ValueError):
            continue
    if world is not None:
        from maverick.result_feedback_receipts import rated_goals
        seen.update(rated_goals(world, owner=owner))
    return len(seen)


__all__ = ["record", "latest_for_goal", "count"]
