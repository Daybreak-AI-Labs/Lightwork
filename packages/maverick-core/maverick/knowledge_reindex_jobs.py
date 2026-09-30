"""Durable, bounded knowledge-index rebuilds with no partial live publication."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .budget import Budget
from .file_lock import (
    atomic_read_text,
    atomic_write_text,
    cross_process_lock,
    ensure_private_directory,
)
from .paths import data_dir

_TERMINAL = {"completed", "cancelled", "failed", "changed"}


def policy() -> dict:
    from .config import load_config
    cfg = load_config().get("knowledge", {})
    if cfg.get("reindex_jobs", False) is not True:
        raise ValueError("An administrator can enable bounded rebuild jobs in Knowledge settings.")
    return {"max_chunks": min(100_000, max(1, int(cfg.get("reindex_max_chunks", 10_000)))),
            "max_wall_seconds": min(3600, max(1, int(cfg.get("reindex_max_wall_seconds", 300))))}


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _rows_sha(rows) -> str:
    return _sha([(row.chunk_id, row.text, row.meta) for row in rows])


def _root(context: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", context):
        raise ValueError("Reload this workspace before rebuilding.")
    root = data_dir() / "knowledge-reindex" / context
    ensure_private_directory(root)
    return root


def _path(context: str, job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Choose a recorded rebuild job.")
    return _root(context) / (job_id + ".json")


def _read(path):
    return json.loads(atomic_read_text(path, encoding="utf-8"))


def _save(path, value):
    atomic_write_text(path, json.dumps(value, sort_keys=True))


def _public(job):
    return {key: job[key] for key in ("job_id", "collection", "state", "count", "done", "message", "created", "updated", "max_wall_seconds", "cost_ceiling", "cancel_requested")}


def _local_embedder(embedder) -> bool:
    from maverick_knowledge.embed import DeterministicEmbedder
    from maverick_knowledge.local_embed import LocalEmbedder
    return type(embedder) in {DeterministicEmbedder, LocalEmbedder}


def preview(kb, collection: str) -> dict:
    from maverick_knowledge.embedding_identity import identity
    limits = policy()
    if not callable(getattr(kb.store, "read_collection", None)) or not callable(getattr(kb.store, "replace_embeddings", None)):
        raise ValueError("This store cannot publish a complete replacement safely. Choose SQLite or pgvector in Knowledge settings before rebuilding.")
    if not _local_embedder(kb.embedder):
        raise ValueError("Paid rebuild is blocked because this embedding adapter does not provide a verified per-request price and usage ceiling. Choose an installed local embedding model in Knowledge settings, or provide a supported adapter with verified pricing and usage admission; this action never changes your provider settings.")
    if collection not in kb.collections():
        raise ValueError("This collection is no longer available. Refresh the knowledge library.")
    rows = kb.store.read_collection(collection, limit=limits["max_chunks"])
    if len(rows) > limits["max_chunks"]:
        raise ValueError("This collection exceeds the administrator's rebuild size limit.")
    descriptor, fingerprint = identity(kb.embedder)
    if sum(len(row.text) for row in rows) > 32 * 1024 * 1024 or len(rows) * descriptor["dimension"] > 2_000_000:
        raise ValueError("This rebuild exceeds the bounded text or vector workspace. Split the collection before reviewing a smaller rebuild.")
    material = {"collection": collection, "count": len(rows), "source_sha": _rows_sha(rows),
                "embedding": fingerprint, "limits": limits}
    return {**material, "review": _sha(material), "cost_ceiling": 0.0, "price_basis": "Local computation: no paid embedding requests. Hardware cost is not estimated.",
            "model": descriptor.get("model", "Configured local model")}


def start(kb, *, context: str, job_id: str, collection: str, review: str) -> dict:
    path = _path(context, job_id)
    with cross_process_lock(path, strict=True):
        if path.exists():
            prior = _read(path)
            if prior["review"] != review or prior["collection"] != collection:
                raise ValueError("This request already belongs to a different rebuild review.")
            return _public(prior)
        if sum(not _read(item).get("archived", False) for item in path.parent.glob("*.json")) >= 500:
            raise ValueError("Archive finished rebuilds in the Knowledge library before starting another job. Their operation receipts remain recorded.")
        current = preview(kb, collection)
        if current["review"] != review:
            raise ValueError("The collection, model or limits changed. Review the rebuild again.")
        now = time.time()
        job = {**current, "context": context, "job_id": job_id, "state": "queued", "done": 0,
               "message": "Ready to rebuild. The current index remains available.", "created": now,
               "updated": now, "cancel_requested": False, "max_wall_seconds": current["limits"]["max_wall_seconds"],
               "elapsed": 0.0}
        _save(path, job)
        return _public(job)


def list_jobs(context: str, *, include_archived: bool = False) -> list[dict]:
    records = [_read(path) for path in _root(context).glob("*.json")]
    jobs = [_public(record) for record in records if include_archived or not record.get("archived", False)]
    return sorted(jobs, key=lambda job: job["created"], reverse=True)


def archive(context: str, job_id: str) -> dict:
    path = _path(context, job_id)
    with cross_process_lock(path, strict=True):
        job = _read(path)
        if job["state"] not in _TERMINAL:
            raise ValueError("Only a finished rebuild can be archived. Cancel active work first.")
        job.update(archived=True, updated=time.time())
        _save(path, job)
        shadow = path.with_suffix(".shadow")
        if shadow.exists():
            shadow.unlink()
        return _public(job)


def cancel(context: str, job_id: str) -> dict:
    path = _path(context, job_id)
    with cross_process_lock(path, strict=True):
        job = _read(path)
        if job["state"] not in _TERMINAL:
            job["cancel_requested"] = True
            job["message"] = "Cancellation requested; the current batch finishes before stopping."
            if job["state"] == "queued":
                job["state"] = "cancelled"
            job["updated"] = time.time()
            _save(path, job)
        return _public(job)


def _update(path, **changes):
    with cross_process_lock(path, strict=True):
        job = _read(path)
        job.update(changes, updated=time.time())
        _save(path, job)
        return job


def _checkpoint(path, authority, started, job):
    authority()
    limits = policy()
    current = _read(path)
    if current["cancel_requested"]:
        _update(path, state="cancelled", message="Rebuild cancelled. The previous live index was preserved.")
        return False
    elapsed = job["elapsed"] + time.monotonic() - started
    if elapsed >= min(job["max_wall_seconds"], limits["max_wall_seconds"]) or time.time() >= job.get("deadline", float("inf")):
        raise ValueError("The rebuild reached its elapsed-time limit; the live index was preserved.")
    if current["count"] > limits["max_chunks"]:
        raise ValueError("The administrator lowered the rebuild size limit.")
    return True


def run(kb, *, context: str, job_id: str, authority) -> dict:
    """An explicit start/resume owns the run lock; no startup or failure auto-retry."""
    path = _path(context, job_id)
    with cross_process_lock(path.with_suffix(".run"), strict=True):
        job = _read(path)
        if job["state"] in _TERMINAL:
            return _public(job)
        if "deadline" not in job:
            job = _update(path, deadline=time.time() + max(0, job["max_wall_seconds"] - job["elapsed"]))
        started = time.monotonic()
        try:
            _execute(kb, path, job, started, authority)
        except Exception:
            # Unknown store outcomes remain inspectable. A new review must not
            # silently retry an interrupted publish or provider operation.
            _update(path, state="failed", message="Rebuild stopped before a confirmed replacement. Inspect the collection and this job before starting a new review.")
        finally:
            _update(path, elapsed=job["elapsed"] + time.monotonic() - started)
        return _public(_read(path))


def _execute(kb, path, job, started, authority):
    from maverick_knowledge.embedding_identity import (
        FINGERPRINT_KEY,
        IDENTITY_KEY,
        identity,
        validate_batch,
    )

    from .world_model import _dec_field, _enc_field

    if not _checkpoint(path, authority, started, job):
        return
    current = preview(kb, job["collection"])
    rows = kb.store.read_collection(job["collection"], limit=job["limits"]["max_chunks"])
    if job.get("result_sha") == _rows_sha(rows):
        _update(path, state="completed", done=job["count"], message="The complete replacement was verified after interruption.")
        return
    if current["review"] != job["review"] or _rows_sha(rows) != job["source_sha"]:
        _update(path, state="changed", message="The collection, model or limits changed. The old job will not overwrite it; begin a new review.")
        return
    descriptor, fingerprint = identity(kb.embedder)
    shadow_path = path.with_suffix(".shadow")
    vectors = json.loads(_dec_field(atomic_read_text(shadow_path, encoding="utf-8"))) if shadow_path.exists() else []
    if len(vectors) > len(rows):
        raise ValueError("Invalid shadow checkpoint")
    budget = Budget(max_dollars=0.0, max_wall_seconds=max(0.1, job["max_wall_seconds"] - job["elapsed"]), max_tool_calls=max(1, (len(rows) + 63) // 64))
    _update(path, state="running", done=len(vectors), message="Building a replacement; the live index is unchanged.")
    for index in range(len(vectors), len(rows), 64):
        if not _checkpoint(path, authority, started, job):
            return
        budget.record_tool_call()
        batch = rows[index:index + 64]
        result = kb.embedder.embed([row.text for row in batch])
        validate_batch(result, len(batch), descriptor["dimension"])
        budget.check()
        vectors.extend(result)
        atomic_write_text(shadow_path, _enc_field(json.dumps(vectors)))
        _update(path, done=len(vectors))
    if not _checkpoint(path, authority, started, job):
        return
    if identity(kb.embedder)[1] != fingerprint:
        raise ValueError("Embedding identity changed")
    items = [(row.chunk_id, row.text, vector, {**row.meta, IDENTITY_KEY: descriptor, FINGERPRINT_KEY: fingerprint}) for row, vector in zip(rows, vectors, strict=True)]
    result_sha = _sha([(item[0], item[1], item[3]) for item in items])
    # Hold cancellation lock through the atomic compare-and-publish boundary.
    with cross_process_lock(path, strict=True):
        authority()
        policy()
        latest = _read(path)
        if latest["cancel_requested"]:
            latest.update(state="cancelled", message="Rebuild cancelled. The previous live index was preserved.")
        else:
            latest.update(result_sha=result_sha, state="publishing", message="Publishing the complete replacement.")
            _save(path, latest)
            kb.store.replace_embeddings(job["collection"], rows, items)
            kb._require_embedding_identity(job["collection"], fingerprint)
            latest.update(state="completed", done=len(items), message="Rebuilt and verified the complete search index.")
        latest["updated"] = time.time()
        _save(path, latest)
