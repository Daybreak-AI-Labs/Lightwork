"""One place that mounts the action routes behind the operations pages
(Code, Immune, Audit, Oversight, Learning, Security, Research, Benchmarks,
Ekko): the buttons that replace CLI-only commands. Each module keeps its own
permission checks; this file only wires them into the app."""
from __future__ import annotations

from fastapi import APIRouter

from . import code_actions_api, immune_actions_api, learning_actions_api

api_router = APIRouter()
api_router.include_router(code_actions_api.router)
api_router.include_router(immune_actions_api.router)
api_router.include_router(learning_actions_api.router)

asset_router = APIRouter()
asset_router.include_router(code_actions_api.asset_router)

__all__ = ["api_router", "asset_router"]
