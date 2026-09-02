"""Operator API for Autonomous Shoring: the Book of Work + measured SLAs.

The Book of Work is a tenant-scoped governed store (``maverick.shoring``), not
a world-model migration. Reads (``GET`` book/sla) need ``view``; writes
(``POST``/``DELETE`` a function) need ``operate`` -- the same floors the
dashboard uses elsewhere. Mutating requests additionally pass the app's global
same-origin CSRF guard (as every other ``/api/v1`` mutation does), so a
cross-site page cannot drive the book.

SLA actuals and the exception-desk counts are computed READ-ONLY from real
completed work in the Operating Record; nothing here tunes the workforce.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from ._shared import _world
from .auth import require_permission

BOOK_SCHEMA = "lightwork.shoring-book.v1"
SLA_SCHEMA = "lightwork.shoring-sla.v1"

# The measurable SLA metrics. Kept in lockstep with maverick.shoring.METRICS so
# a new metric there is a deliberate, single-line change here too.
Metric = Literal["throughput_per_day", "max_exception_rate_pct",
                 "max_turnaround_hours"]

Key = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64,
                                       pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Bounded = Annotated[str, StringConstraints(strict=True, max_length=400)]
Lifecycle = Literal["scoping", "demonstrating", "running", "proving"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SLAIn(_StrictModel):
    name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]
    metric: Metric
    target: float = Field(ge=0, le=1_000_000)


class FunctionIn(_StrictModel):
    key: Key
    name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=400)]
    department: Bounded = ""
    scope: Bounded = ""
    status: Lifecycle = "scoping"
    owner: Bounded = ""
    slas: list[SLAIn] = Field(default_factory=list, max_length=12)


def _require_enabled() -> None:
    from maverick import shoring

    if not shoring.enabled():
        raise HTTPException(
            status_code=404,
            detail="autonomous shoring is disabled ([shoring] enable = true)")


def _book() -> dict:
    from maverick import shoring

    return {"schema": BOOK_SCHEMA, "functions": shoring.book_status(_world())}


def _sla(key: str) -> dict:
    from maverick import shoring

    fn = next((f for f in shoring.load() if f.key == key), None)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"no shored function {key!r}")
    return {"schema": SLA_SCHEMA, "function": shoring.function_status(fn, _world())}


def _upsert(body: FunctionIn) -> dict:
    from maverick import shoring

    fn = shoring.Function(
        key=body.key, name=body.name, department=body.department,
        scope=body.scope, status=body.status, owner=body.owner,
        slas=[shoring.SLATarget(s.name, s.metric, s.target) for s in body.slas],
    )
    shoring.upsert(fn)
    return {"schema": SLA_SCHEMA, "function": shoring.function_status(fn, _world())}


def _remove(key: str) -> dict:
    from maverick import shoring

    if not shoring.remove(key):
        raise HTTPException(status_code=404, detail=f"no shored function {key!r}")
    return {"removed": key}


async def _call(function, *args):
    try:
        return await run_in_threadpool(function, *args)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503,
                            detail=f"shoring store unavailable: {exc}") from exc


def create_shoring_router() -> APIRouter:
    router = APIRouter(prefix="/shoring", tags=["shoring"])

    @router.get("/book")
    async def book(request: Request):
        require_permission(request, "view")
        _require_enabled()
        return await _call(_book)

    @router.get("/sla")
    async def sla(request: Request, key: Key):
        require_permission(request, "view")
        _require_enabled()
        return await _call(_sla, key)

    @router.post("/function")
    async def upsert_function(request: Request, body: FunctionIn):
        # Writing the book of work is an operate action (and same-origin-gated
        # centrally like every /api/v1 mutation).
        require_permission(request, "operate")
        _require_enabled()
        return await _call(_upsert, body)

    @router.delete("/function/{key}")
    async def delete_function(request: Request, key: Key):
        require_permission(request, "operate")
        _require_enabled()
        return await _call(_remove, key)

    return router


router = create_shoring_router()

__all__ = ["create_shoring_router", "router", "FunctionIn", "SLAIn",
           "BOOK_SCHEMA", "SLA_SCHEMA"]
