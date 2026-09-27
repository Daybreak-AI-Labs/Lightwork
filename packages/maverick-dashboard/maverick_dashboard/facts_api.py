"""Remove a saved fact from the Facts page.

``GET``/``POST /api/v1/facts`` list and save facts; this adds the matching
delete so a person can remove a detail Lightwork remembered without the
command line. Same permission as saving a fact (``operate``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from .auth import require_permission

router = APIRouter(prefix="/facts", tags=["facts"])


@router.delete("/{key:path}", status_code=204)
async def delete_fact(request: Request, key: str) -> Response:
    """Delete one fact by its exact name; 404 when there is no such fact."""
    require_permission(request, "operate")
    name = (key or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="fact name is required")
    from .api import _world

    removed = await run_in_threadpool(lambda: _world().delete_fact(name))
    if not removed:
        raise HTTPException(status_code=404, detail="no such fact")
    return Response(status_code=204)


__all__ = ["router"]
