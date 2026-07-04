"""Append-only log of applied flow self-rewrites.

When a proposal from :mod:`.evolve` is actually applied to a flow (a node's kind
swapped), we record the event here with the timestamp and the version it produced.
That timestamp is the boundary the measurement pass (:func:`.evolve.measure`)
splits a node's outcome series around -- outcomes before the apply are the old
behaviour, outcomes at/after it are the new -- which is what lets the loop prove
whether a rewrite actually helped instead of just asserting it.

Append-only NDJSON (the node-outcomes / consequence store shape): 0600,
best-effort, bounded tail. Never raises into a request.
"""
from __future__ import annotations

import time

from ..ndjson_log import append_row, tail_rows

_MAX_ROWS = 50_000


def _path():
    from ..paths import data_dir
    return data_dir("flows", "evolution.ndjson")


def record_apply(flow_id: str, node_id: str, from_kind: str, to_kind: str,
                 version: int, *, source: str = "manual") -> None:
    """Record that ``node_id`` in ``flow_id`` was swapped ``from_kind -> to_kind``,
    producing definition ``version``. ``source`` is ``manual`` (a human applied a
    proposal) or ``auto`` (the sweep did). Best-effort; never raises."""
    append_row(_path(), {
        "ts": time.time(), "flow_id": str(flow_id), "node_id": str(node_id),
        "from_kind": str(from_kind), "to_kind": str(to_kind),
        "version": int(version), "source": str(source),
    })


def _rows() -> list[dict]:
    return tail_rows(_path(), _MAX_ROWS)


def history(flow_id: str, node_id: str | None = None) -> list[dict]:
    """Apply events for a flow (optionally one node), newest first."""
    rows = [r for r in _rows() if r.get("flow_id") == flow_id
            and (node_id is None or str(r.get("node_id")) == str(node_id))]
    rows.sort(key=lambda r: r.get("ts") or 0.0, reverse=True)
    return rows


def last_apply(flow_id: str, node_id: str) -> dict | None:
    """The most recent apply event for a node, or ``None`` if it was never
    changed -- i.e. there's no before/after boundary to measure yet."""
    rows = history(flow_id, node_id)
    return rows[0] if rows else None


__all__ = ["record_apply", "history", "last_apply"]
