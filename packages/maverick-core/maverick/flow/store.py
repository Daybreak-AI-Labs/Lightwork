"""Persistence for flow definitions and their (resumable) runs.

Definitions and run state are plain JSON under the tenant-scoped data dir, 0600.
A run row carries everything :func:`~.runner.run_flow` needs to resume -- status,
the threaded ``data``, the node it paused at, and a delay's ``resume_at`` -- so a
paused flow survives a restart and a human approval days later. Best-effort I/O
that degrades to an empty list rather than raising into a request.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256

from .ir import Flow

_MAX_FLOW_DEFINITION_BYTES = 1_000_000


def _flows_dir():
    from ..paths import data_dir
    return data_dir("flows")


def _history_dir(flow_id: str):
    from ..paths import data_dir
    return data_dir("flows", "history", _safe_id(flow_id))


def _runs_dir():
    from ..paths import data_dir
    return data_dir("flows", "runs")


def _objects_dir():
    from ..paths import data_dir
    return data_dir("flows", "objects")


def _idempotency_dir():
    from ..paths import data_dir
    return data_dir("flows", "idempotency")


def _schema_dir():
    from ..paths import data_dir
    return data_dir("flows", "schema")


def load_flow_schema(flow_id: str) -> dict:
    """The accumulated per-output-key shape hints for a flow (empty if none).
    Field names + types only -- never values (see :mod:`.schema_infer`)."""
    return _read_json(_schema_dir() / f"{_safe_id(flow_id)}.json") or {}


def record_flow_schema(
    flow_id: str, shapes: dict, *, revision: str | None = None,
) -> None:
    """Merge freshly-observed output shapes into the flow's accumulated hints
    and persist them. Best-effort: any error is swallowed HERE, the one owner
    of that policy (a schema hint is never worth failing a run over)."""
    if not shapes:
        return
    try:
        # An old in-flight generation may finish after delete/recreate.  Couple
        # the revision check and write to the definition lock so its learned
        # field names cannot bleed into the replacement flow's designer.
        with _definitions_lock():
            current = load_flow(flow_id)
            if (revision is not None
                    and (current is None or current.revision != str(revision))):
                return
            from .schema_infer import merge_shapes
            merged = merge_shapes(load_flow_schema(flow_id), shapes)
            _write_json(_schema_dir() / f"{_safe_id(flow_id)}.json", merged)
    except Exception:  # pragma: no cover -- best-effort
        pass


def _write_json(path, obj: dict) -> None:
    from ..file_lock import atomic_write_text
    atomic_write_text(path, json.dumps(obj, sort_keys=True))


def _read_json(path) -> dict | None:
    try:
        from ..file_lock import atomic_read_text
        d = json.loads(atomic_read_text(path))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ---- flow definitions -------------------------------------------------------

@contextmanager
def _definitions_lock():
    """Serialize definition edits with content-addressed run snapshots."""
    from ..file_lock import cross_process_lock
    with cross_process_lock(_flows_dir() / ".definitions", strict=True):
        yield


class FlowVersionConflict(RuntimeError):
    """A definition changed after the caller read its expected version."""


def _assert_safe_definition(payload: dict) -> None:
    """Refuse oversized definitions and raw credentials before persistence."""
    remaining = _MAX_FLOW_DEFINITION_BYTES

    def charge(value, *, depth: int = 0) -> None:
        nonlocal remaining
        if depth > 32:
            raise ValueError("flow definition nesting is too deep")
        remaining -= 8  # container/scalar JSON punctuation and type overhead
        if remaining < 0:
            raise ValueError("flow definition exceeds the storage limit")
        if isinstance(value, str):
            if len(value) > _MAX_FLOW_DEFINITION_BYTES:
                raise ValueError("flow definition exceeds the storage limit")
            remaining -= len(value.encode("utf-8"))
        elif isinstance(value, dict):
            for key, item in value.items():
                charge(str(key), depth=depth + 1)
                charge(item, depth=depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                charge(item, depth=depth + 1)
        if remaining < 0:
            raise ValueError("flow definition exceeds the storage limit")

    charge(payload)
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    if len(raw.encode("utf-8")) > _MAX_FLOW_DEFINITION_BYTES:
        raise ValueError("flow definition exceeds the storage limit")
    from ..safety.secret_detector import scan

    def contains_secret(value) -> bool:
        if isinstance(value, str):
            return bool(scan(value))
        if isinstance(value, dict):
            return any(
                contains_secret(str(key)) or contains_secret(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(contains_secret(item) for item in value)
        return False

    if contains_secret(payload):
        raise ValueError(
            "flow definitions may not contain raw credentials; "
            "use {{secret('NAME')}} instead"
        )


def save_flow(
    flow: Flow,
    *,
    expected_version: int | None = None,
    expected_revision: str | None = None,
) -> Flow:
    """Persist ``flow`` as the current definition, snapshotting the prior current
    into version history first so any earlier version can be rolled back to.

    The store owns the version number: each save stamps ``version = prior + 1``
    (a brand-new flow starts at 1). Returns the saved flow with its stamped
    version. Forward-only history -- a rollback is itself a new version, so
    nothing is ever destroyed."""
    # Reject unsafe content before any history/version side effect, then check
    # the exact stamped payload again under the definition lock.
    _assert_safe_definition(flow.to_dict())
    with _definitions_lock():
        prior = load_flow(flow.id)
        actual_version = int(prior.version) if prior is not None else 0
        if expected_version is not None and actual_version != int(expected_version):
            raise FlowVersionConflict(
                f"flow {flow.id!r} changed (expected version {expected_version}, "
                f"current {actual_version})")
        actual_revision = str(getattr(prior, "revision", "") or "")
        if expected_revision is not None and actual_revision != str(expected_revision):
            raise FlowVersionConflict(
                f"flow {flow.id!r} was replaced or changed generation")
        if prior is not None:
            flow.version = int(prior.version) + 1
            # A generation token never changes during ordinary edits/rollbacks.
            # It closes the delete+recreate ABA hole that a resettable integer
            # version cannot detect.
            flow.revision = actual_revision or _new_revision()
        else:
            # Version numbers are store-owned. Never let a client create v-7 or
            # v999 and weaken the history/CAS invariants.
            flow.version = 1
            flow.revision = _new_revision()
        payload = flow.to_dict()
        _assert_safe_definition(payload)
        if prior is not None:
            # Snapshot the version being replaced under its own number, then advance.
            _write_json(
                _history_dir(flow.id) / f"v{int(prior.version)}.json",
                prior.to_dict(),
            )
        _write_json(_flows_dir() / f"{_safe_id(flow.id)}.json", payload)
    return flow


def _new_revision() -> str:
    from uuid import uuid4

    return uuid4().hex


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


def rollback_flow(
    flow_id: str,
    version: int | None = None,
    *,
    expected_version: int | None = None,
    expected_revision: str | None = None,
) -> Flow | None:
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
    return save_flow(
        target.copy(),
        expected_version=expected_version,
        expected_revision=expected_revision,
    )   # re-save the old content as a fresh version


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


def list_flow_summaries() -> list[dict]:
    """Cheap listing for index pages -- id/name/node count/single-agent flag
    plucked from the raw JSON, WITHOUT building the full recursive Flow IR per
    file (list_flows constructs every nested body just to be counted)."""
    d = _flows_dir()
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        raw = _read_json(p) or {}
        if not raw.get("id"):
            continue
        nodes = [n for n in (raw.get("nodes") or []) if isinstance(n, dict)]
        out.append({
            "id": str(raw["id"]), "name": str(raw.get("name") or ""),
            "nodes": len(nodes),
            "single_agent": (len(nodes) == 1 and nodes[0].get("kind") == "agent"
                             and not nodes[0].get("next")),
        })
    return out


def delete_flow(
    flow_id: str,
    *,
    expected_version: int | None = None,
    expected_revision: str | None = None,
) -> bool:
    """Delete one definition, optionally guarded by its exact generation.

    API callers must pass both CAS tokens read during authorization.  Checking
    them under the same definition lock as unlink closes the delete/recreate ABA
    race where a stale request could otherwise remove a new owner's flow.
    """
    with _definitions_lock():
        current = load_flow(flow_id)
        if current is None:
            return False
        if expected_version is not None and int(current.version) != int(expected_version):
            raise FlowVersionConflict(
                f"flow {flow_id!r} changed (expected version {expected_version}, "
                f"current {current.version})")
        if (expected_revision is not None
                and str(current.revision or "") != str(expected_revision)):
            raise FlowVersionConflict(
                f"flow {flow_id!r} was replaced or changed generation")
        p = _flows_dir() / f"{_safe_id(flow_id)}.json"
        try:
            p.unlink()
        except OSError:
            return False
        # Drop version history too, so a later flow re-created with this id doesn't
        # inherit stale snapshots (best-effort -- a missing history dir is fine).
        import shutil
        shutil.rmtree(_history_dir(flow_id), ignore_errors=True)
        try:
            (_schema_dir() / f"{_safe_id(flow_id)}.json").unlink()
        except OSError:
            pass
        return True


def _safe_id(flow_id: str) -> str:
    """An injective-enough filesystem key that cannot traverse or alias.

    Already-safe historical IDs keep their filename.  Any normalization or
    truncation receives a digest of the original value, so e.g. ``a/b`` can no
    longer address the same definition as ``a-b``.
    """
    import re
    raw = str(flow_id or "").strip()
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-") or "flow"
    if s == raw and len(s) <= 120:
        return s
    suffix = sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{s[:99]}-{suffix}"


class FlowSnapshotError(RuntimeError):
    """An immutable execution-plan object is missing or fails its digest."""


def _canonical_flow_bytes(raw: dict) -> bytes:
    return json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _store_flow_object(raw: dict) -> str:
    digest = sha256(_canonical_flow_bytes(raw)).hexdigest()
    path = _objects_dir() / f"{digest}.json"
    if path.exists():
        existing = _read_json(path)
        if existing is None or sha256(_canonical_flow_bytes(existing)).hexdigest() != digest:
            raise FlowSnapshotError(f"flow snapshot {digest} is corrupt")
        return digest
    _write_json(path, raw)
    return digest


def _snapshot_flow_bundle_locked(flow: Flow) -> tuple[str, int, dict[str, str | None]]:
    """Snapshot a root and its transitive children under ``_definitions_lock``."""
    manifest: dict[str, str | None] = {}
    # A subflow is executable code/data owned by its definition owner.  Flow ids
    # are not capabilities: an authenticated caller who guesses another user's
    # id must not be able to smuggle that private graph into an otherwise-owned
    # root.  Enforce the invariant at the immutable snapshot boundary so every
    # producer (manual, cron, event, retry, and direct execution) inherits it.
    # Ownerless definitions are a separate namespace in authenticated mode and
    # therefore may only compose with other ownerless definitions.
    root_owner = str(flow.owner or "")

    def walk(raw: dict) -> None:
        for node in raw.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            body = node.get("body")
            if isinstance(body, dict):
                walk(body)
            for branch in node.get("branches") or []:
                if isinstance(branch, dict):
                    walk(branch)
            if str(node.get("kind")) != "subflow":
                continue
            ref = str(node.get("flow_ref") or "").strip()
            if not ref or ref in manifest:
                continue
            child = load_flow(ref)
            if child is None:
                manifest[ref] = None
                continue
            if str(child.owner or "") != root_owner:
                raise FlowSnapshotError(
                    f"subflow {ref!r} is unavailable to this flow owner"
                )
            child_raw = child.to_dict()
            manifest[ref] = _store_flow_object(child_raw)
            walk(child_raw)

    root = flow.to_dict()
    root_digest = _store_flow_object(root)
    walk(root)
    return root_digest, int(flow.version), manifest


def snapshot_flow_bundle(flow: Flow) -> tuple[str, int, dict[str, str | None]]:
    """Pin ``flow`` and every static subflow reference as immutable CAS objects.

    The manifest records missing children explicitly as ``None``.  That matters:
    a subflow created after a human reviewed/approved the parent must not become
    executable retroactively.  Definition writes/deletes share the same lock, so
    a bundle observes one coherent set of current definitions.
    """
    with _definitions_lock():
        return _snapshot_flow_bundle_locked(flow)


def snapshot_current_flow_bundle(flow_id: str) -> tuple[str, int, dict[str, str | None]]:
    """Atomically load and snapshot the current root plus its child graph.

    Loading the root before taking the definition lock can mix an old parent
    with newer child definitions. Queue producers use this helper so the entire
    execution plan is observed at one definition instant.
    """
    with _definitions_lock():
        flow = load_flow(flow_id)
        if flow is None:
            raise FlowSnapshotError(f"flow {flow_id!r} is unavailable")
        return _snapshot_flow_bundle_locked(flow)


def load_flow_snapshot(digest: str) -> Flow:
    """Load a CAS flow object and fail closed on deletion or tampering."""
    import re

    digest = str(digest or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise FlowSnapshotError("invalid flow snapshot digest")
    raw = _read_json(_objects_dir() / f"{digest}.json")
    if raw is None:
        raise FlowSnapshotError(f"flow snapshot {digest} is missing")
    actual = sha256(_canonical_flow_bytes(raw)).hexdigest()
    if actual != digest:
        raise FlowSnapshotError(f"flow snapshot {digest} failed integrity verification")
    try:
        flow = Flow.from_dict(raw)
    except Exception as exc:
        raise FlowSnapshotError(f"flow snapshot {digest} is invalid") from exc
    errors = flow.validate()
    if errors:
        raise FlowSnapshotError(
            "flow snapshot is structurally invalid: " + "; ".join(errors[:3]))
    return flow


def validate_snapshot_bundle_owners(
    root: Flow,
    manifest: dict[str, str | None],
) -> None:
    """Reject a pinned bundle that crosses definition-owner boundaries.

    New bundles are checked while they are created, but durable paused runs may
    predate that invariant. Re-checking immutable child objects on delivery
    prevents an old crafted run from becoming a permanent authorization bypass.
    Missing children remain represented by ``None`` and fail later as before.
    """
    root_owner = str(root.owner or "")
    for ref, digest in manifest.items():
        if digest is None:
            continue
        child = load_flow_snapshot(digest)
        if child.id != str(ref):
            raise FlowSnapshotError(
                f"subflow snapshot identity mismatch for {str(ref)!r}"
            )
        if str(child.owner or "") != root_owner:
            raise FlowSnapshotError(
                f"subflow {str(ref)!r} is unavailable to this flow owner"
            )


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
    # The data the run STARTED with (the trigger payload), kept so a failed run
    # can be retried from scratch with the same inputs.
    input_data: dict = field(default_factory=dict)
    # Optional idempotency key (flow_id + a stable event id) so a re-delivered
    # trigger event doesn't spawn a duplicate run.
    idem_key: str = ""
    # What fired this run: "manual" | "cron:<flow>" | "event:<trigger>" |
    # "form:<token>" | "retry:<run>" -- so a viewer can see why it ran and the
    # loop can learn which triggers produce value.
    origin: str = "manual"
    # Persisted on the reservation so an outbox repair can never recover a
    # mock/dry run as a real side-effecting execution.
    dry_run: bool = False
    # Agent spend reserved for this run (dollars), so a viewer can see run cost.
    cost_dollars: float = 0.0
    # Snapshot of the paused human task (choices/form/assignee) taken AT PAUSE
    # time, like `prompt` -- so the run viewer renders what the paused node
    # actually declared, even if the flow is edited while the run waits.
    human: dict = field(default_factory=dict)
    # Content-addressed immutable execution plan captured when the run is
    # enqueued.  The run row stays small; graph bytes deduplicate in objects/.
    definition_digest: str = ""
    definition_version: int = 0
    # Stable definition-generation token.  Version resets after delete/recreate;
    # revision does not, so learning and diagnostics can never attribute an old
    # owner's evidence to a new flow that happens to reuse the same id.
    definition_revision: str = ""
    subflow_digests: dict[str, str | None] = field(default_factory=dict)
    # Execution identity is part of the durable reservation, not queue metadata.
    # This prevents an at-least-once delivery, delayed resume, or outbox repair
    # from dropping per-user tool ACL and specialist-suite restrictions.
    execution_channel: str = ""
    execution_user_id: str = ""
    allowed_suites: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "flow_id": self.flow_id, "status": self.status,
            "data": self.data, "cursor": self.cursor, "resume_at": self.resume_at,
            "prompt": self.prompt, "error": self.error, "owner": self.owner,
            "created": self.created, "updated": self.updated, "nodes": self.nodes,
            "input_data": self.input_data, "idem_key": self.idem_key,
            "origin": self.origin, "dry_run": self.dry_run,
            "cost_dollars": self.cost_dollars,
            "human": self.human,
            "definition_digest": self.definition_digest,
            "definition_version": self.definition_version,
            "definition_revision": self.definition_revision,
            "subflow_digests": self.subflow_digests,
            "execution_channel": self.execution_channel,
            "execution_user_id": self.execution_user_id,
            "allowed_suites": self.allowed_suites,
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
            input_data=dict(d.get("input_data") or {}), idem_key=str(d.get("idem_key", "")),
            origin=str(d.get("origin", "manual") or "manual"),
            dry_run=bool(d.get("dry_run", False)),
            cost_dollars=float(d.get("cost_dollars", 0.0) or 0.0),
            human=dict(d.get("human") or {}),
            definition_digest=str(d.get("definition_digest") or ""),
            definition_version=int(d.get("definition_version") or 0),
            definition_revision=str(d.get("definition_revision") or ""),
            subflow_digests={
                str(k): (str(v) if v is not None else None)
                for k, v in (d.get("subflow_digests") or {}).items()
            },
            execution_channel=str(d.get("execution_channel") or "")[:80],
            execution_user_id=str(d.get("execution_user_id") or "")[:500],
            allowed_suites=(
                sorted({str(v) for v in d.get("allowed_suites", []) if str(v)})
                if isinstance(d.get("allowed_suites"), list)
                else None
            ),
        )


def find_run_by_idem(
    flow_id: str,
    idem_key: str,
    *,
    within: int | None = 500,
    revision: str | None = None,
) -> FlowRun | None:
    """The most recent run for ``flow_id`` with this idempotency key, if any --
    the dedup check that stops a re-delivered event from firing a second run."""
    if not idem_key:
        return None
    claim = _read_json(_idempotency_claim_path(flow_id, idem_key))
    if claim:
        claimed = load_run(str(claim.get("run_id") or ""))
        if (claimed is not None
                and claimed.flow_id == flow_id
                and claimed.idem_key == idem_key
                and (revision is None or claimed.definition_revision == revision)):
            return claimed
    for r in list_runs(flow_id=flow_id, limit=within):
        if (r.idem_key == idem_key
                and (revision is None or r.definition_revision == revision)):
            return r
    return None


def _idempotency_claim_path(flow_id: str, idem_key: str):
    material = f"{flow_id}\0{idem_key}".encode()
    return _idempotency_dir() / f"{sha256(material).hexdigest()}.json"


@contextmanager
def idempotency_lock(flow_id: str, idem_key: str):
    """Serialize first-writer-wins reservation for one flow/idempotency key."""
    from ..file_lock import cross_process_lock
    with cross_process_lock(
        _idempotency_claim_path(flow_id, idem_key), strict=True,
    ):
        yield


def save_idempotency_claim(
    flow_id: str, idem_key: str, run_id: str, *, overwrite: bool = True,
) -> bool:
    if not idem_key:
        return False
    path = _idempotency_claim_path(flow_id, idem_key)
    if not overwrite and path.exists():
        return False
    _write_json(path, {"flow_id": flow_id, "run_id": run_id})
    return True


def new_run_id() -> str:
    from uuid import uuid4
    return uuid4().hex


def save_run(run: FlowRun) -> None:
    now = time.time()
    if not run.created:
        run.created = now
    run.updated = now
    from .redact import cap, redact
    d = run.to_dict()
    # Bound first (so an attacker cannot make detection scan unbounded strings),
    # then deep-redact both secret-looking keys and secret-bearing ordinary
    # values. Persisted state is a trust boundary, not merely the API response.
    d["data"] = redact(cap(d.get("data") or {}))
    d["input_data"] = redact(cap(d.get("input_data") or {}))
    run.data = dict(d["data"])
    run.input_data = dict(d["input_data"])
    if d.get("error"):
        try:
            from ..safety.secret_detector import redact
            d["error"] = redact(str(d["error"]))[0][:2000]
        except Exception:
            # An exception string is untrusted and may contain connector
            # credentials. If the redactor is unavailable, preserve the fact of
            # failure without persisting the potentially secret details.
            d["error"] = "execution failed (details redacted)"
        run.error = str(d["error"])
    _write_json(_runs_dir() / f"{_safe_id(run.run_id)}.json", d)


def load_run(run_id: str) -> FlowRun | None:
    d = _read_json(_runs_dir() / f"{_safe_id(run_id)}.json")
    return FlowRun.from_dict(d) if d else None


def delete_run(run_id: str) -> bool:
    try:
        (_runs_dir() / f"{_safe_id(run_id)}.json").unlink()
        return True
    except OSError:
        return False


@contextmanager
def run_lock(run_id: str):
    """Serialize a run's state transition across threads and processes.

    Resume is a compare-and-act operation: load the paused row, verify it is
    resumable, execute downstream work, then publish the new state.  Locking
    only the final JSON write still lets two workers both observe ``paused`` and
    perform the same external side effect.  The stable sidecar used here spans
    that whole transition on POSIX and Windows.
    """
    from ..file_lock import cross_process_lock

    target = _runs_dir() / f"{_safe_id(run_id)}.json"
    with cross_process_lock(target, strict=True):
        yield


def list_runs(*, flow_id: str | None = None, owner: str | None = None,
              limit: int | None = 50) -> list[FlowRun]:
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
    if limit is None:
        return runs
    return runs[: max(0, int(limit))]


__all__: list[str] = [
    "save_flow", "load_flow", "list_flows", "delete_flow",
    "list_versions", "load_version", "rollback_flow",
    "FlowVersionConflict", "FlowSnapshotError", "snapshot_flow_bundle",
    "snapshot_current_flow_bundle", "load_flow_snapshot",
    "FlowRun", "new_run_id", "save_run", "load_run", "delete_run", "run_lock",
    "list_runs", "find_run_by_idem", "idempotency_lock", "save_idempotency_claim",
    "load_flow_schema", "record_flow_schema",
]
