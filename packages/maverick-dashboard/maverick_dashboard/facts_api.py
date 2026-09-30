"""Scoped fact browsing, exact history and optimistic deletion."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Request, Response
from maverick.fact_revision import FactConflict, is_private_fact_key
from starlette.concurrency import run_in_threadpool

from .auth import require_permission
from .draft_api import context_for

router = APIRouter(prefix="/facts", tags=["facts"])


@router.get("/records")
def records(request: Request, response: Response, query: str = Query("", max_length=200),
            source: str = Query("", max_length=200), updated_before: float | None = None,
            offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    require_permission(request, "view")
    from .api import _world
    items = _world().fact_records(query=query, source=source, updated_before=updated_before,
                                  offset=offset, limit=limit + 1, shared_only=True)
    response.headers["Cache-Control"] = "private, no-store"
    return {"context": context_for(request), "items": items[:limit],
            "has_more": len(items) > limit, "offset": offset, "limit": limit}


@router.get("/record")
def record(request: Request, response: Response, key: str = Query(..., max_length=200)):
    require_permission(request, "view")
    if is_private_fact_key(key):
        raise HTTPException(404, "This fact is not available in shared workspace facts.")
    from .api import _world
    world = _world()
    item = world.fact_record(key)
    if item is None:
        raise HTTPException(404, "This fact is no longer saved in this workspace.")
    response.headers["Cache-Control"] = "private, no-store"
    return {"context": context_for(request), "item": item,
            "history": [asdict(version) for version in world.fact_history(key, limit=50, owned_only=True)],
            "history_notice": "Removing stops new recall. Recorded history may remain under workspace retention policy; it is not permanent erasure."}


@router.delete("/{key:path}", status_code=204)
async def delete_fact(request: Request, key: str, expected_revision: int = Query(..., ge=1),
                      context: str = Query(..., min_length=64, max_length=64)) -> Response:
    require_permission(request, "operate")
    name = (key or "").strip()
    if not name:
        raise HTTPException(400, "fact name is required")
    if is_private_fact_key(name):
        raise HTTPException(404, "This fact is not available in shared workspace facts.")
    if context != context_for(request):
        raise HTTPException(409, "Your account or workspace changed. Reload before removing.")
    from .api import _world
    try:
        removed = await run_in_threadpool(
            lambda: _world().delete_fact(name, expected_revision=expected_revision)
        )
    except FactConflict as exc:
        raise HTTPException(409, "This fact changed. Review its current value before removing.") from exc
    if not removed:
        raise HTTPException(404, "no such fact")
    return Response(status_code=204)


__all__ = ["router"]
