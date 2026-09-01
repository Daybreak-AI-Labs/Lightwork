"""Route identity and canonical-link regressions found during user-path QA."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from maverick_dashboard.route_identity import stabilize_effective_route_names


def _effective_routes() -> Iterator[Any]:
    from maverick_dashboard.app import app

    for route in app.routes:
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        if callable(effective_route_contexts):
            yield from effective_route_contexts()
        else:
            yield route


def _effective_value(route: Any, attribute: str) -> Any:
    value = getattr(route, attribute, None)
    if value:
        return value
    starlette_route = getattr(route, "starlette_route", None)
    value = getattr(starlette_route, attribute, None)
    if value:
        return value
    original_route = getattr(route, "original_route", None)
    return getattr(original_route, attribute, None)


def _path_parameter_value(convertor: Any) -> Any:
    candidates = (
        "route-identity-sample",
        "1",
        "00000000-0000-4000-8000-000000000001",
    )
    for candidate in candidates:
        try:
            converted = convertor.convert(candidate)
            convertor.to_string(converted)
        except (AssertionError, TypeError, ValueError):
            continue
        return converted
    raise AssertionError(f"No reverse-routing sample for {type(convertor).__name__}")


def test_all_application_route_names_are_unique() -> None:
    routes = list(_effective_routes())
    names = [
        _effective_value(route, "name")
        for route in routes
        if isinstance(_effective_value(route, "name"), str)
    ]
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)

    assert len(names) > 1_000  # Protect against regressing to top-level-only traversal.
    assert len(names) == len(routes)
    assert duplicates == []


def test_all_effective_route_names_reverse_to_their_effective_paths() -> None:
    from maverick_dashboard.app import app

    checked = 0
    for route in _effective_routes():
        name = _effective_value(route, "name")
        path = _effective_value(route, "path")
        convertors = _effective_value(route, "param_convertors") or {}
        if not isinstance(name, str) or not isinstance(path, str):
            continue
        parameters = {
            parameter: _path_parameter_value(convertor)
            for parameter, convertor in convertors.items()
        }
        expected = route.url_path_for(name, **parameters)
        resolved = app.url_path_for(name, **parameters)

        assert str(resolved) == str(expected), name
        checked += 1

    assert checked > 1_000


def test_stabilizer_aligns_reverse_routing_and_request_scope_names() -> None:
    first_router = APIRouter()
    second_router = APIRouter()

    @first_router.get("/first", name="status")
    async def first_status(request: Request) -> dict[str, str]:
        return {"route_name": request.scope["route"].name}

    @second_router.get("/second", name="status")
    async def second_status(request: Request) -> dict[str, str]:
        return {"route_name": request.scope["route"].name}

    test_app = FastAPI()
    test_app.include_router(first_router)
    test_app.include_router(second_router)
    stabilize_effective_route_names(test_app)

    names_by_path: dict[str, str] = {}
    for route in test_app.routes:
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        contexts = effective_route_contexts() if callable(effective_route_contexts) else (route,)
        for context in contexts:
            path = _effective_value(context, "path")
            name = _effective_value(context, "name")
            if path in {"/first", "/second"}:
                names_by_path[path] = name

    assert len(set(names_by_path.values())) == 2
    client = TestClient(test_app)
    for path, name in names_by_path.items():
        assert str(test_app.url_path_for(name)) == path
        assert client.get(path).json() == {"route_name": name}


def test_external_agent_run_links_use_canonical_goal_route() -> None:
    from pathlib import Path

    from maverick_dashboard import app as dashboard

    template = Path(dashboard.__file__).parent / "templates" / "external_agent_detail.html"
    source = template.read_text(encoding="utf-8")

    assert 'href="/chat/goal/{{ e.goal_id }}"' in source
    assert 'href="/goals/{{ e.goal_id }}"' not in source


def test_flow_designer_create_and_edit_routes_have_distinct_names() -> None:
    from maverick_dashboard.app import app

    names_by_path = {
        route.path: route.name
        for route in app.routes
        if getattr(route, "path", "").startswith("/flows/designer")
    }

    assert names_by_path["/flows/designer"] == "flow_designer_create_page"
    assert names_by_path["/flows/designer/{flow_id}"] == "flow_designer_edit_page"
