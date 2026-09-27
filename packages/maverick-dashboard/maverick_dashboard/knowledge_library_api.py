"""Knowledge library: see collections, load starter content, rebuild search.

These were command-line only (``maverick knowledge collections|seed|reindex``).
They act on the active workspace's knowledge base only; loading starter
content and rebuilding the search index need administrator access.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .auth import require_permission

router = APIRouter(prefix="/knowledge/library", tags=["knowledge-library"])


class SeedIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    collection: str | None = Field(default=None, min_length=1, max_length=128)


def _open() -> Any | None:
    from maverick.knowledge_admin import open_knowledge_base

    return open_knowledge_base()


def _overview() -> dict[str, object]:
    from maverick.knowledge_seed import available_corpora

    starters = available_corpora()
    kb = _open()
    if kb is None:
        return {"enabled": False, "collections": [], "starter_content": starters}
    try:
        collections = []
        for name in kb.collections():
            count = kb.store.count(name) if hasattr(kb.store, "count") else None
            collections.append({"name": name, "chunks": count})
    finally:
        kb.close()
    present = {item["name"] for item in collections}
    for starter in starters:
        starter["loaded"] = starter["collection"] in present
    return {"enabled": True, "collections": collections, "starter_content": starters}


def _seed(collection: str | None) -> dict[str, int]:
    from maverick.knowledge_seed import available_corpora, seed_corpora

    if collection is not None and collection not in {
        item["collection"] for item in available_corpora()
    }:
        raise LookupError("unknown starter collection")
    kb = _open()
    if kb is None:
        raise RuntimeError("knowledge is turned off")
    try:
        return seed_corpora(kb, only=collection)
    finally:
        kb.close()


def _reindex(collection: str) -> int:
    from maverick.knowledge_admin import open_knowledge_base

    kb = open_knowledge_base(require_existing=True)
    if kb is None:
        raise RuntimeError("knowledge is turned off")
    try:
        if collection not in kb.collections():
            raise LookupError("unknown collection")
        return int(kb.reindex(collection))
    finally:
        kb.close()


async def _call(function, *args):
    try:
        return await run_in_threadpool(function, *args)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="knowledge collection not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="knowledge is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="knowledge request is invalid") from exc
    except Exception as exc:  # noqa: BLE001 - never disclose store internals
        raise HTTPException(status_code=503, detail="knowledge is unavailable") from exc


@router.get("")
async def overview(request: Request) -> dict[str, object]:
    """Collections in this workspace and the starter content that can be loaded."""
    require_permission(request, "view")
    return await _call(_overview)


@router.post("/seed")
async def seed(request: Request, body: SeedIn) -> dict[str, object]:
    """Load (or refresh) the shipped starter content; repeat loads replace, not duplicate."""
    require_permission(request, "admin")
    report = await _call(_seed, body.collection)
    return {"loaded": report, "chunks": sum(report.values())}


@router.post("/collections/{collection}/reindex")
async def reindex(request: Request, collection: str) -> dict[str, object]:
    """Rebuild one collection's search index with the configured search model."""
    require_permission(request, "admin")
    count = await _call(_reindex, collection[:128])
    return {"collection": collection, "chunks": count}


__all__ = ["router"]
