"""Context-bound business record discovery, without a shared search cache."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Literal
from urllib.parse import quote

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
    mode: Literal["metadata", "knowledge"] = "metadata"
    purpose: str = Field(default="", max_length=64, pattern=r"^(?:[a-z][a-z0-9_.-]{0,63})?$")
    cursor: str | None = Field(default=None, max_length=2048)
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
    if body.mode == "knowledge":
        return await _approved_knowledge(request, response, body, context)
    try:
        result = await run_in_threadpool(search, sources_for(request, body.kind), query=body.query,
            kind=body.kind, offset=body.offset, limit=body.limit, expected_snapshot=body.snapshot)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if context_for(request) != context:
        raise HTTPException(409, "Access changed during search. Reload before using the results.")
    require_permission(request, "view")
    return {**result, "context": context, "mode": "metadata"}


async def _approved_knowledge(request, response, body, context):
    """Reuse current exact-source authority; never create a blended private index."""
    from .knowledge_access_api import SearchIn as KnowledgeSearchIn
    from .knowledge_access_api import search as knowledge_search
    if not body.purpose:
        raise HTTPException(422, "Choose a permitted use before searching approved content.")
    value = await knowledge_search(KnowledgeSearchIn(context=context, query=body.query,
                                   purpose=body.purpose, limit=body.limit, cursor=body.cursor), request, response)
    if context_for(request) != context:
        raise HTTPException(409, "Access changed during search. Reload before using the results.")
    items = []
    for item in value["items"]:
        ref = item["reference"]
        link = quote(json.dumps({"reference": ref, "purpose": body.purpose}, separators=(",", ":")), safe="")
        items.append({"kind": "knowledge", "id": ref["asset_id"], "title": item["title"],
                      "revision": ref["version"], "state": "Eligible for " + body.purpose + "; rechecked when opened",
                      "href": "/knowledge-access#source=" + link,
                      "summary": item.get("summary", ""), "fresh_until": item["eligibility"]["fresh_until"]})
    return {"context": context, "mode": "knowledge", "items": items,
            "total": len(items), "complete": value["complete"], "next_offset": None,
            "next_cursor": value["next_cursor"],
            "snapshot": hashlib.sha256(json.dumps([context, body.query, body.purpose]).encode()).hexdigest(),
            "coverage": [{"source": "approved knowledge content", "state": "current" if value["complete"] else "partial"}],
            "notice": "Only sources eligible for this account and use contribute results. Counts cover this returned page. Metadata and content search do not call a model."}
