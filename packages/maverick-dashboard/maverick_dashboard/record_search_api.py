"""Context-bound business record discovery, without a shared search cache."""
from __future__ import annotations

import hmac
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from maverick import config
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .auth import require_permission
from .draft_api import context_for
from .record_search import search
from .record_search_sources import sources_for

router = APIRouter(prefix="/api/v1/records", tags=["record-search"])


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(default="", max_length=200)
    kind: Literal["all", "vendor", "evidence", "assessment", "decision", "task", "report"] = "all"
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=20, ge=1, le=50)
    snapshot: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")


def page_context(request):
    try:
        if config.config_source_errors(include_tenant=True) or config.get_features().get("record_search") is not True:
            return {"record_search_context": ""}
        require_permission(request, "view")
        return {"record_search_context": context_for(request)}
    except Exception:
        return {"record_search_context": ""}


@router.post("/search")
async def search_records(request: Request, response: Response, body: SearchIn):
    require_permission(request, "view")
    context = page_context(request).get("record_search_context")
    if not context:
        raise HTTPException(403, "Record search is disabled in Settings or configuration is unavailable.")
    if not hmac.compare_digest(request.headers.get("X-Lightwork-Search-Context", ""), context):
        raise HTTPException(409, "The account or workspace changed. Reload before searching records.")
    response.headers["Cache-Control"] = "private, no-store"
    try:
        result = await run_in_threadpool(search, sources_for(request, body.kind), query=body.query,
            kind=body.kind, offset=body.offset, limit=body.limit, expected_snapshot=body.snapshot)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**result, "context": context}
