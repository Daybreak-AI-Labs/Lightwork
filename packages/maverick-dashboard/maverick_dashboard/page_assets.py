"""Static scripts for the administration pages rebuilt in plain language.

Each file gets an explicit route (the dashboard serves only registered
assets); unknown names are never matched here.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()
_STATIC = Path(__file__).parent / "static"
ASSETS = (
    "lightwork-settings-admin.js",
    "lightwork-tenants.js",
    "lightwork-compliance.js",
    "lightwork-compliance-scoping.js",
    "lightwork-compliance-scope-actions.js",
    "lightwork-dsar-detail.js",
    "lightwork-privacy-notice-governance.js",
    "lightwork-privacy-regulatory-intelligence.js",
    "lightwork-privacy-rights-consumer-delivery.js",
    "lightwork-privacy-rights-orchestration.js",
    "lightwork-settings-all.js",
)
STYLES = (
    "lightwork-settings-all.css",
)


def _route(name: str):
    async def serve() -> FileResponse:
        return FileResponse(_STATIC / name, media_type="application/javascript; charset=utf-8",
                            headers={"Cache-Control": "public, max-age=300"})
    serve.__name__ = "asset_" + name.replace("-", "_").replace(".", "_")
    return serve


def _style_route(name: str):
    async def serve() -> FileResponse:
        return FileResponse(_STATIC / name, media_type="text/css; charset=utf-8",
                            headers={"Cache-Control": "public, max-age=300"})
    serve.__name__ = "asset_" + name.replace("-", "_").replace(".", "_")
    return serve


for _name in ASSETS:
    router.add_api_route("/static/" + _name, _route(_name), methods=["GET"], include_in_schema=False)
for _name in STYLES:
    router.add_api_route("/static/" + _name, _style_route(_name), methods=["GET"], include_in_schema=False)
