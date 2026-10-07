"""Bounded, identity-scoped action descriptions for the Designer's ordinary forms."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from maverick.flow.ir import action_tool_policy_error
from maverick.governed_operations import digest
from starlette.concurrency import run_in_threadpool

from .auth import execution_user_id_from_request, require_permission

router = APIRouter(prefix="/api/v1/workflow-action-fields", tags=["workflow-action-fields"])


@router.get("/{tool_name}")
async def action_fields(request: Request, tool_name: str):
    require_permission(request, "operate")
    from .api import _live_tool_schemas, _require_flows
    _require_flows()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,199}", tool_name) or action_tool_policy_error(tool_name):
        raise HTTPException(404, "This action is unavailable. Choose an action from the current list.")
    user_id = execution_user_id_from_request(request)
    schemas = await run_in_threadpool(_live_tool_schemas, channel="api" if user_id else None, user_id=user_id)
    schema = schemas.get(tool_name)
    if not isinstance(schema, dict):
        raise HTTPException(404, "This action is unavailable. Choose an action from the current list.")
    try:
        if len(json.dumps(schema, allow_nan=False).encode()) > 65536:
            raise ValueError("description too large")
        binding = digest(schema)
    except (TypeError, ValueError, RecursionError) as exc:
        raise HTTPException(409, "This action's fields cannot be displayed safely. Choose another action or ask the connection owner to update its field description.") from exc
    return JSONResponse({"tool": tool_name, "schema": schema, "revision": binding},
                        headers={"Cache-Control": "private, no-store"})
