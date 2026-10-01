"""Static scripts for the administration pages rebuilt in plain language.

Each file gets an explicit route (the dashboard serves only registered
assets); unknown names are never matched here.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

router = APIRouter()
_STATIC = Path(__file__).parent / "static"
ASSETS = (
    "lightwork-today.js",
    "lightwork-employee-activation.js",
    "lightwork-budget-guidance.js",
    "lightwork-task-diagnostics.js",
    "lightwork-devices.js",
    "lightwork-read-aloud.js",
    "lightwork-theme-preview.js",
    "lightwork-i18n.js",
    "lightwork-model-guidance.js",
    "lightwork-model-tests.js",
    "lightwork-notification-health.js",
    "lightwork-personal-notifications.js",
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
    "lightwork-red-team-schedules.js",
    "lightwork-learning-loop.js",
    "lightwork-value-proof.js",
    "lightwork-assurance-forms.js",
    "lightwork-feature-actions.js",
    "lightwork-ops-panels.js",
    "lightwork-tax.js",
    "lightwork-task-changes.js",
    "lightwork-viewpoints.js",
)
STYLES: tuple[str, ...] = ("lightwork-i18n.css",)


@router.get("/static/lightwork-i18n-catalog/{language}/{revision}.js", include_in_schema=False)
def journey_catalog(language: str, revision: str, request: Request) -> Response:
    """Serve only shipped public copy, independently of session preferences."""
    from .i18n import LANGS
    from .i18n_journeys import browser_catalog_asset

    if language not in LANGS or len(revision) != 64:
        raise HTTPException(404, "No such product catalog")
    current, content = browser_catalog_asset(language)
    if revision != current:
        raise HTTPException(404, "No such product catalog")
    # GZip middleware can choose a different encoding of the same catalog.
    etag = f'W/"{current}"'
    headers = {"Cache-Control": "public, max-age=31536000, immutable",
               "ETag": etag, "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content, media_type="application/javascript; charset=utf-8", headers=headers)


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
