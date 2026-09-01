"""Stable route identities for reverse routing and request observability.

FastAPI defaults a route's name to its endpoint function name.  That is useful
inside a small router, but this dashboard composes many routers whose handlers
share deliberately generic names such as ``status``, ``history``, and
``workbench``.  FastAPI 0.141 keeps included routers lazy, so inspecting only
``app.routes`` also misses nearly all of those collisions.

This module qualifies only colliding names.  Existing names that are already
unique remain unchanged, preserving intentional reverse-routing contracts.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI
from starlette.routing import BaseRoute

_DASHBOARD_MODULE_PREFIX = "maverick_dashboard."


@dataclass(frozen=True)
class _EffectiveRoute:
    original_route: BaseRoute
    owner: APIRouter
    name: str
    path: str
    methods: tuple[str, ...]
    endpoint_module: str
    endpoint_qualname: str


def iter_compatible_route_contexts(
    routes: Sequence[BaseRoute | Any],
) -> Iterator[Any]:
    """Yield effective leaf routes without FastAPI-version coupling.

    FastAPI 0.141 added the public ``fastapi.routing.iter_route_contexts``
    helper together with lazy included routers, but the dashboard's supported
    baseline starts at 0.110.  Newer lazy wrappers expose their own
    ``effective_route_contexts`` method; older releases already expose flat
    leaf routes.  The recursive ``original_router`` fallback also covers an
    included-router wrapper without importing any non-baseline FastAPI symbol.
    """
    for route in routes:
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        if callable(effective_route_contexts):
            yield from effective_route_contexts()
            continue
        included = getattr(route, "original_router", None)
        if isinstance(included, APIRouter):
            yield from iter_compatible_route_contexts(included.routes)
            continue
        yield route


def _iter_routers(router: APIRouter) -> Iterator[APIRouter]:
    """Yield each router in a lazy include tree once."""
    pending = [router]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for route in current.routes:
            included = getattr(route, "original_router", None)
            if isinstance(included, APIRouter):
                pending.append(included)


def _route_owners(router: APIRouter) -> dict[int, APIRouter]:
    owners: dict[int, APIRouter] = {}
    for current in _iter_routers(router):
        for route in current.routes:
            if not isinstance(getattr(route, "original_router", None), APIRouter):
                owners[id(route)] = current
    return owners


def _effective_value(context: Any, original_route: BaseRoute, attribute: str) -> Any:
    value = getattr(context, attribute, None)
    if value:
        return value
    starlette_route = getattr(context, "starlette_route", None)
    value = getattr(starlette_route, attribute, None)
    if value:
        return value
    return getattr(original_route, attribute, None)


def _endpoint_identity(context: Any, original_route: BaseRoute) -> tuple[str, str]:
    endpoint = _effective_value(context, original_route, "endpoint")
    module = getattr(endpoint, "__module__", type(endpoint).__module__)
    qualname = getattr(endpoint, "__qualname__", type(endpoint).__qualname__)
    if module.startswith(_DASHBOARD_MODULE_PREFIX):
        module = module.removeprefix(_DASHBOARD_MODULE_PREFIX)
    return module, qualname


def _effective_routes(app: FastAPI) -> list[_EffectiveRoute]:
    owners = _route_owners(app.router)
    effective: list[_EffectiveRoute] = []
    for context in iter_compatible_route_contexts(app.routes):
        original_route = getattr(context, "original_route", context)
        name = str(_effective_value(context, original_route, "name") or "route")
        path = str(_effective_value(context, original_route, "path") or "")
        methods = tuple(
            sorted(_effective_value(context, original_route, "methods") or ())
        )
        endpoint_module, endpoint_qualname = _endpoint_identity(
            context, original_route
        )
        effective.append(
            _EffectiveRoute(
                original_route=original_route,
                owner=owners[id(original_route)],
                name=name,
                path=path,
                methods=methods,
                endpoint_module=endpoint_module,
                endpoint_qualname=endpoint_qualname,
            )
        )
    return effective


def _qualified_name(route: _EffectiveRoute) -> str:
    return f"{route.endpoint_module}.{route.name}"


def _signature_name(route: _EffectiveRoute) -> str:
    methods = "_".join(method.lower() for method in route.methods) or "any"
    signature = "\0".join(
        (
            route.endpoint_module,
            route.endpoint_qualname,
            route.name,
            methods,
            route.path,
        )
    )
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
    return f"{_qualified_name(route)}__{methods}__{digest}"


def _target_names(routes: list[_EffectiveRoute]) -> list[str]:
    original_counts = Counter(route.name for route in routes)
    targets = [
        _qualified_name(route) if original_counts[route.name] > 1 else route.name
        for route in routes
    ]

    target_counts = Counter(targets)
    targets = [
        _signature_name(route) if target_counts[target] > 1 else target
        for route, target in zip(routes, targets, strict=True)
    ]
    duplicates = sorted(
        target for target, count in Counter(targets).items() if count > 1
    )
    if duplicates:
        joined = ", ".join(duplicates)
        raise RuntimeError(
            "Effective routes remain indistinguishable after stable qualification: "
            f"{joined}. Give those routes explicit names."
        )
    return targets


def stabilize_effective_route_names(app: FastAPI) -> None:
    """Give every effective route a stable, unique name.

    Included route objects are mutated as well as their lazy effective views so
    ``request.scope['route'].name`` and ``app.url_path_for`` agree.  A route
    object included more than once would require context-specific mutation and
    would leave request-scope observability ambiguous, so that topology fails
    closed instead of receiving registration-order suffixes.
    """
    routes = _effective_routes(app)
    targets = _target_names(routes)
    targets_by_original: defaultdict[int, set[str]] = defaultdict(set)
    for route, target in zip(routes, targets, strict=True):
        targets_by_original[id(route.original_route)].add(target)
    repeated = [
        targets for targets in targets_by_original.values() if len(targets) > 1
    ]
    if repeated:
        raise RuntimeError(
            "A route object is included through multiple effective paths. "
            "Use distinct router instances or explicit context-aware route names."
        )

    changed_owners: dict[int, APIRouter] = {}
    for route, target in zip(routes, targets, strict=True):
        if getattr(route.original_route, "name", None) == target:
            continue
        route.original_route.name = target
        changed_owners[id(route.owner)] = route.owner

    # Collecting effective contexts above populated FastAPI's lazy include
    # caches.  Bump only routers whose route names changed so the next traversal
    # rebuilds those contexts from the now-canonical original route objects.
    for owner in changed_owners.values():
        mark_routes_changed = getattr(owner, "_mark_routes_changed", None)
        if callable(mark_routes_changed):
            mark_routes_changed()

    app.openapi_schema = None
    observed_names = [route.name for route in _effective_routes(app)]
    duplicates = sorted(
        name for name, count in Counter(observed_names).items() if count > 1
    )
    if duplicates:
        raise RuntimeError(
            "Effective route-name stabilization failed: " + ", ".join(duplicates)
        )
