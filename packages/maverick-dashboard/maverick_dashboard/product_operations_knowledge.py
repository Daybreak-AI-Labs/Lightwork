"""Copilot uses the same bounded rebuild service and durable operation identity."""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from maverick.governed_operations import digest
from pydantic import Field
from starlette.background import BackgroundTask

from .product_operations import Adapter, Outcome, Preview, Snapshot, StrictInput


class RebuildInput(StrictInput):
    collection: str = Field(min_length=1, max_length=128)


def _state(request):
    from maverick.knowledge_reindex_jobs import list_jobs

    from .knowledge_library_api import _overview, _reindex_authority
    context = _reindex_authority(request)
    return context, _overview(), list_jobs(context)


def rebuild_snapshot(request, selection):
    context, library, jobs = _state(request)
    collections = library["collections"]
    return Snapshot(digest([context, collections]), "Knowledge library", "Review and rebuild a complete search index within administrator limits.",
                    details={"jobs": jobs}, fields=[{"name": "collection", "label": "Collection", "kind": "select", "required": True,
                    "choices": [{"value": row["name"], "label": row["name"]} for row in collections]}],
                    blocked="Enable document search and add an authorized collection first." if not collections else "")


def rebuild_prepare(request, selection, arguments):
    from maverick.knowledge_reindex_jobs import preview

    from .knowledge_library_api import _reindex_authority, _reindex_call
    context = _reindex_authority(request)
    review = _reindex_call(preview, arguments["collection"])
    return Preview("Build a shadow index, then publish the complete replacement after current permission and source checks. Paid AI requests require verified cost admission.",
                   changes=[{"label": "Passages", "before": review["count"], "after": review["count"]},
                            {"label": "Provider cost ceiling", "before": "No rebuild", "after": "$0; local computation only"},
                            {"label": "Elapsed time limit", "before": "No rebuild", "after": str(review["limits"]["max_wall_seconds"]) + " seconds, checked between batches"}],
                   native={"context": context, "collection": arguments["collection"], "review": review["review"]})


def rebuild_recover(request, selection, arguments, native, operation):
    from maverick.knowledge_reindex_jobs import list_jobs

    from .knowledge_library_api import _reindex_authority
    _reindex_authority(request, native["context"])
    job = next((row for row in list_jobs(native["context"], include_archived=True) if row["job_id"] == operation), None)
    return Outcome("replayed", "This rebuild is already recorded. Use its native status to continue or cancel; no new job was started.", job) if job else None


def rebuild_execute(request, selection, arguments, native, operation):
    from maverick.knowledge_reindex_jobs import start

    from .knowledge_library_api import _reindex_authority, _reindex_call, _run_reindex
    _reindex_authority(request, native["context"])
    prior = rebuild_recover(request, selection, arguments, native, operation)
    if prior is not None:
        return prior
    job = _reindex_call(start, **native, job_id=operation)
    return Outcome("queued", "Rebuild recorded. Its progress survives leaving this conversation.", job,
                   background=BackgroundTask(_run_reindex, request, native["context"], operation))


class JobInput(StrictInput):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    action: Literal["status", "cancel", "resume", "archive"]


def job_snapshot(request, selection):
    context, _, jobs = _state(request)
    return Snapshot(digest([context, jobs]), "Knowledge rebuild history", "Inspect or control a recorded rebuild at a safe batch boundary.", fields=[
        {"name": "job_id", "label": "Rebuild", "kind": "select", "required": True, "choices": [{"value": job["job_id"], "label": job["collection"] + " / " + job["state"]} for job in jobs]},
        {"name": "action", "label": "Action", "kind": "select", "required": True, "choices": [{"value": value, "label": label} for value, label in (("status", "Refresh status"), ("cancel", "Cancel at safe boundary"), ("resume", "Continue interrupted job"), ("archive", "Archive finished rebuild"))]}], blocked="No rebuild job is recorded here." if not jobs else "")


def job_prepare(request, selection, arguments):
    context, _, jobs = _state(request)
    job = next((row for row in jobs if row["job_id"] == arguments["job_id"]), None)
    if job is None:
        raise HTTPException(404, "Rebuild unavailable.")
    return Preview("Review current progress before changing this rebuild. A published complete index is retained.",
                   changes=[{"label": job["collection"], "before": job["state"], "after": arguments["action"]}],
                   native={"context": context, **arguments})


def job_execute(request, selection, arguments, native, operation):
    from fastapi import BackgroundTasks
    from maverick.knowledge_reindex_jobs import list_jobs

    from .knowledge_library_api import JobActionIn, _reindex_authority, reindex_action
    _reindex_authority(request, native["context"])
    if native["action"] == "status":
        job = next((row for row in list_jobs(native["context"]) if row["job_id"] == native["job_id"]), None)
        if job is None:
            raise HTTPException(404, "Rebuild unavailable.")
        return Outcome("confirmed", "Recorded rebuild progress refreshed.", job)
    background = BackgroundTasks()
    value = reindex_action(request, native["job_id"], native["action"], JobActionIn(context=native["context"], confirmed=True), background)
    return Outcome("queued" if native["action"] == "resume" else "confirmed", "Rebuild action recorded. Refresh native progress for its confirmed outcome.", value["job"], background=background)


def entries():
    return (Adapter("knowledge.rebuild", "Rebuild document search", "learning", "/knowledge-access", ("/knowledge-access",), RebuildInput, rebuild_snapshot, rebuild_prepare, rebuild_execute, rebuild_recover),
            Adapter("knowledge.rebuild_job", "Review or control rebuild progress", "learning", "/knowledge-access", ("/knowledge-access",), JobInput, job_snapshot, job_prepare, job_execute))
