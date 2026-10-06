"""Self-hosted API reference using FastAPI's supported Swagger UI renderer."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.openapi.docs import (
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter()
_STATIC = Path(__file__).parent / "static"
_ASSETS = {
    "swagger-ui-5.33.1-bundle.js": "application/javascript; charset=utf-8",
    "swagger-ui-5.33.1.css": "text/css; charset=utf-8",
    "swagger-ui-5.33.1-LICENSE.txt": "text/plain; charset=utf-8",
    "swagger-ui-5.33.1-NOTICE.txt": "text/plain; charset=utf-8",
    "swagger-ui-bundle.js.LICENSE.txt": "text/plain; charset=utf-8",
}


@router.get("/docs", include_in_schema=False)
def documentation(request: Request) -> HTMLResponse:
    root = request.scope.get("root_path", "").rstrip("/")
    app = request.app
    parameters = dict(app.swagger_ui_parameters or {})
    parameters.setdefault("filter", True)
    # Validate/render the current local schema without sending it to an
    # external Swagger validator. Interactive calls keep their native gates.
    parameters["validatorUrl"] = None
    redirect = app.swagger_ui_oauth2_redirect_url
    return get_swagger_ui_html(
        openapi_url=root + app.openapi_url,
        title=app.title + " - Swagger UI",
        swagger_js_url=root + "/static/swagger-ui-5.33.1-bundle.js",
        swagger_css_url=root + "/static/swagger-ui-5.33.1.css",
        swagger_favicon_url=root + "/static/daybreak-logo.jpg",
        oauth2_redirect_url=root + redirect if redirect else None,
        init_oauth=app.swagger_ui_init_oauth,
        swagger_ui_parameters=parameters,
    )


@router.get("/docs/oauth2-redirect", include_in_schema=False)
def oauth_redirect() -> HTMLResponse:
    return get_swagger_ui_oauth2_redirect_html()


def _asset(name: str, media_type: str):
    def serve() -> FileResponse:
        return FileResponse(
            _STATIC / name,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=300"},
        )

    serve.__name__ = "documentation_" + name.replace("-", "_").replace(".", "_")
    return serve


for _name, _media_type in _ASSETS.items():
    router.add_api_route(
        "/static/" + _name,
        _asset(_name, _media_type),
        methods=["GET"],
        include_in_schema=False,
    )
