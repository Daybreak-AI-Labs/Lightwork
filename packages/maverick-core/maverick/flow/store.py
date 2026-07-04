"""Persistence for flow definitions and their (resumable) runs.

Definitions and run state are plain JSON under the tenant-scoped data dir, 0600.
A run row carries everything :func:`~.runner.run_flow` needs to resume -- status,
the threaded ``data``, the node it paused at, and a delay's ``resume_at`` -- so a
paused flow survives a restart and a human approval days later. Best-effort I/O
that degrades to an empty list rather than raising into a request.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from .ir import Flow


def _flows_dir():
    from ..paths import data_dir
    return data_dir("flows")


def _history_dir(flow_id: str):
    from ..paths import data_dir
    return data_dir("flows", "history", _safe_id(flow_id))


def _runs_dir():
    from ..paths import data_dir
    return data_dir("flows", "runs")


def _write_json(path, obj: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, sort_keys=True)
    os.replace(tmp, path)


def _read_json(path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ---- flow definitions -------------------------------------------------------

def save_flow(flow: Flow) -> Flow:
    """Persist ``flow`` as the current definition, snapshotting the prior current
    into version history first so any earlier version can be rolled back to.

    The store owns the version number: each save stamps ``version = prior + 1``
    (a brand-new flow starts at 1). Returns the saved flow with its stamped
    version. Forward-only history -- a rollback is itself a new version, so
    nothing is ever destroyed."""
    prior = load_flow(flow.id)
    if prior is not None:
        # Snapshot the version being replaced under its own number, then advance.
        _write_json(_history_dir(flow.id) / f"v{int(prior.version)}.json", prior.to_dict())
        flow.version = int(prior.version) + 1
    else:
        flow.version = int(flow.version) if flow.version else 1
    _write_json(_flows_dir() / f"{_safe_id(flow.id)}.json", flow.to_dict())
    return flow


def load_flow(flow_id: str) -> Flow | None:
    d = _read_json(_flows_dir() / f"{_safe_id(flow_id)}.json")
    return Flow.from_dict(d) if d else None


def list_versions(flow_id: str) -> list[Flow]:
    """Every stored version of a flow (history snapshots + the current), oldest
    first. Empty if the flow is unknown."""
    out: list[Flow] = []
    hist = _history_dir(flow_id)
    if hist.exists():
        for p in hist.glob("v*.json"):
            raw = _read_json(p)
            if raw:
                out.append(Flow.from_dict(raw))
    cur = load_flow(flow_id)
    if cur is not None:
        out.append(cur)
    out.sort(key=lambda f: f.version)
    return out


def load_version(flow_id: str, version: int) -> Flow | None:
    """A specific historical (or current) version of a flow."""
    cur = load_flow(flow_id)
    if cur is not None and int(cur.version) == int(version):
        return cur
    raw = _read_json(_history_dir(flow_id) / f"v{int(version)}.json")
    return Flow.from_dict(raw) if raw else None


def rollback_flow(flow_id: str, version: int | None = None) -> Flow | None:
    """Restore a prior ``version`` (default: the one immediately before current)
    as a NEW current version -- non-destructive, so the rollback can itself be
    undone. Returns the new current flow, or ``None`` if the target is unknown."""
    cur = load_flow(flow_id)
    if cur is None:
        return None
    target_v = version if version is not None else int(cur.version) - 1
    target = load_version(flow_id, target_v)
    if target is None:
        return None
    return save_flow(target.copy())   # re-save the old content as a fresh version


def list_flows() -> list[Flow]:
    d = _flows_dir()
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        raw = _read_json(p)
        if raw:
            out.append(Flow.from_dict(raw))
    return out


def delete_flow(flow_id: str) -> bool:
    p = _flows_dir() / f"{_safe_id(flow_id)}.json"
    try:
        p.unlink()
    except OSError:
        return False
    # Drop version history too, so a later flow re-created with this id doesn't
    # inherit stale snapshots (best-effort -- a missing history dir is fine).
    import shutil
    shutil.rmtree(_history_dir(flow_id), ignore_errors=True)
    return True


def _safe_id(flow_id: str) -> str:
    """A filesystem-safe slug so a flow id can't traverse out of the flows dir."""
    import re
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", str(flow_id or "").strip())
    return s.strip("-")[:120] or "flow"


# ---- runs (resumable state) -------------------------------------------------

@dataclass
class FlowRun:
    run_id: str
    flow_id: str
    status: str
    data: dict = field(default_factory=dict)
    cursor: str | None = None
    resume_at: float | None = None
    prompt: str = ""
    error: str = ""
    owner: str = ""
    created: float = 0.0
    updated: float = 0.0
    # Per-node run trace: {node_id: {status, outcome}} accumulated as the run
    # executes, so the designer can overlay live status/result on the canvas.
    nodes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "flow_id": self.flow_id, "status": self.status,
            "data": self.data, "cursor": self.cursor, "resume_at": self.resume_at,
            "prompt": self.prompt, "error": self.error, "owner": self.owner,
            "created": self.created, "updated": self.updated, "nodes": self.nodes,
        }

    @staticmethod
    def from_dict(d: dict) -> FlowRun:
        return FlowRun(
            run_id=str(d.get("run_id", "")), flow_id=str(d.get("flow_id", "")),
            status=str(d.get("status", "")), data=dict(d.get("data") or {}),
            cursor=d.get("cursor"), resume_at=d.get("resume_at"),
            prompt=str(d.get("prompt", "")), error=str(d.get("error", "")),
            owner=str(d.get("owner", "")),
            created=float(d.get("created", 0.0)), updated=float(d.get("updated", 0.0)),
            nodes=dict(d.get("nodes") or {}),
        )


def new_run_id() -> str:
    from uuid import uuid4
    return uuid4().hex


def save_run(run: FlowRun) -> None:
    now = time.time()
    if not run.created:
        run.created = now
    run.updated = now
    _write_json(_runs_dir() / f"{_safe_id(run.run_id)}.json", run.to_dict())


def load_run(run_id: str) -> FlowRun | None:
    d = _read_json(_runs_dir() / f"{_safe_id(run_id)}.json")
    return FlowRun.from_dict(d) if d else None


def list_runs(*, flow_id: str | None = None, owner: str | None = None,
              limit: int = 50) -> list[FlowRun]:
    d = _runs_dir()
    if not d.exists():
        return []
    runs: list[FlowRun] = []
    for p in d.glob("*.json"):
        raw = _read_json(p)
        if not raw:
            continue
        run = FlowRun.from_dict(raw)
        if flow_id is not None and run.flow_id != flow_id:
            continue
        if owner is not None and run.owner != owner:
            continue
        runs.append(run)
    runs.sort(key=lambda r: r.updated, reverse=True)
    return runs[: max(0, int(limit))]


__all__: list[str] = [
    "save_flow", "load_flow", "list_flows", "delete_flow",
    "list_versions", "load_version", "rollback_flow",
    "FlowRun", "new_run_id", "save_run", "load_run", "list_runs",
]
