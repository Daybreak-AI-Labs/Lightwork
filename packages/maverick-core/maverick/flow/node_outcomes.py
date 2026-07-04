"""Per-node grounded outcomes for a flow.

Because a flow has structure, an outcome can attach to the *node* that produced
it, not just the whole run. Accumulating ``(flow_id, node_id) -> outcome`` gives
the self-rewrite pass (:mod:`.evolve`) the evidence to say "this agent node's
choice is right 95% of the time -- make it a deterministic rule" or "this action
node fails a third of the time -- let an agent handle the variation". That
per-node signal is the thing a pure flow engine or a pure agent can't produce.

Append-only NDJSON (the consequence-store shape): 0600, best-effort, bounded tail.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict

_MAX_ROWS = 100_000


def _path():
    from ..paths import data_dir
    return data_dir("flows", "node-outcomes.ndjson")


def record(flow_id: str, node_id: str, kind: str, value: float) -> None:
    """Append one node outcome. ``value`` in [0, 1]. Best-effort; never raises."""
    row = {
        "ts": time.time(), "flow_id": str(flow_id), "node_id": str(node_id),
        "kind": str(kind), "value": max(0.0, min(1.0, float(value))),
    }
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:  # pragma: no cover -- telemetry is best-effort
        return


def _read_rows() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    out = []
    for raw in lines[-_MAX_ROWS:]:
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                out.append(d)
        except (ValueError, TypeError):
            continue
    return out


def stats(flow_id: str) -> dict[str, dict]:
    """Per-node aggregate for a flow: ``{node_id: {n, mean, kind}}``."""
    sums: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    kinds: dict[str, str] = {}
    for r in _read_rows():
        if r.get("flow_id") != flow_id:
            continue
        nid = str(r.get("node_id"))
        try:
            sums[nid] += float(r.get("value"))
        except (TypeError, ValueError):
            continue
        counts[nid] += 1
        kinds[nid] = str(r.get("kind") or kinds.get(nid, ""))
    return {
        nid: {"n": counts[nid], "mean": sums[nid] / counts[nid], "kind": kinds.get(nid, "")}
        for nid in counts
    }


__all__ = ["record", "stats"]
