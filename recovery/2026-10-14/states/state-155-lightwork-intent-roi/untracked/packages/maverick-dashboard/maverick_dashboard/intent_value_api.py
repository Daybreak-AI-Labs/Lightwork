"""Authorized intent/value reads and edits shared by dashboard and Copilot.

Workspace rollups contain totals only. Private task explanations require every
contributing goal and process run to be readable now; administration is never a
substitute for employee content access. This module performs no model calls.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Literal
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import auth
from .value_views import value_scope

router = APIRouter(prefix="/intent-value", tags=["intent-value"])
Scope = Literal["platform", "specialist", "agent", "process"]
Population = Literal["own", "workspace"]
_SAFE_BENCHMARK = frozenset({
    "intent_id", "label", "minutes_low", "minutes", "minutes_high", "hourly_rate",
    "currency", "version", "provenance", "basis", "size_factors", "updated_at",
    "revision", "size", "size_factor", "sized_minutes",
})
_METRICS = {
    "gross_labor_value": "Benchmark estimate for work Lightwork performed; not proven cash savings.",
    "execution_cost": "Recorded AI and execution costs. Missing costs remain unknown.",
    "net_benefit": "Estimated gross labor value less recorded execution cost; unavailable when costs are incomplete.",
    "opportunity_value": "Estimated labor value of observed work not yet performed by Lightwork.",
    "task_count": "Distinct work items after conversation continuity and explicit linking.",
    "executed_count": "Distinct work items with recorded Lightwork execution evidence.",
    "completed_count": "Distinct work items with recorded completed execution evidence.",
}


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    reason: str = Field(min_length=1, max_length=1000)


class CorrectionIn(EditIn):
    intent_id: str = Field(min_length=1, max_length=100)
    size: Literal["small", "standard", "large"] = "standard"
    confirmed: bool | None = None


class ContinuityIn(EditIn):
    target_task_id: str = Field(min_length=1, max_length=180)


class BenchmarkIn(EditIn):
    minutes_low: float = Field(ge=0, le=100000, allow_inf_nan=False)
    minutes: float = Field(ge=0, le=100000, allow_inf_nan=False)
    minutes_high: float = Field(ge=0, le=100000, allow_inf_nan=False)
    hourly_rate: float = Field(ge=0, le=100000, allow_inf_nan=False)


class SettingsIn(EditIn):
    enable: bool
    confirmation_required: bool = False


def _world():
    from ._shared import _world as shared
    return shared()


def _store():
    from maverick.intent_value_store import IntentValueStore
    return IntentValueStore()


def _authority(request, *, permission="view"):
    auth.require_permission(request, permission)
    auth.require_unscoped_board(request)


def _period(since=None, until=None):
    end = time.time() if until is None else float(until)
    start = end - 30 * 86400 if since is None else float(since)
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise HTTPException(422, "Choose a valid start and end date.")
    if end - start > 3660 * 86400:
        raise HTTPException(422, "Choose a reporting period of ten years or less.")
    return start, end


def _snapshot():
    try:
        return _store().snapshot()
    except Exception as exc:
        raise HTTPException(503, "Value records are unavailable. Retry after workspace storage recovers.") from exc


def _effective(state):
    from maverick.config import get_intent_value
    effective = dict(get_intent_value())
    effective.update(state.get("settings", {}))
    # A deployment switch that disables collection cannot be overridden locally.
    effective["enable"] = bool(get_intent_value().get("enable", True) and effective.get("enable", True))
    return effective


def settings_for(request):
    _authority(request)
    state = _snapshot()
    effective = _effective(state)
    return {"enable": effective.get("enable", True),
            "confirmation_required": effective.get("confirmation_required", False),
            "effective": effective, "revision": state.get("revision", 0),
            "can_edit": auth.has_permission(request, "admin")}


def _assert_task(request, world, task, *, mutation=False):
    """Authorize the whole continuity group, never merely its first member."""
    owner = auth.goal_owner_filter(request)
    if mutation and task.get("owner", "") != owner:
        raise HTTPException(404, "This work item is unavailable to your account.")
    goals = task.get("goal_ids", [])
    runs = task.get("flow_runs", [])
    if not goals and not runs:
        raise HTTPException(404, "This work item has no accessible source records.")
    for identifier in goals:
        goal = world.get_goal(int(identifier))
        if goal is None:
            raise HTTPException(404, "A contributing task is no longer available.")
        auth.assert_goal_access(request, goal)
        if mutation and (goal.owner or "") != owner:
            raise HTTPException(404, "This work item is unavailable to your account.")
    for reference in runs:
        from .api import _load_owned_run
        run = _load_owned_run(request, reference["run_id"])
        if run.flow_id != reference["flow_id"]:
            raise HTTPException(404, "A contributing process run is no longer available.")


def _can_read_task(request, world, task):
    try:
        _assert_task(request, world, task)
    except HTTPException as exc:
        if exc.status_code in {403, 404}:
            return False
        raise
    return True


def _compute(world, *, since, until, owner, scope_kind, scope_id, state):
    from maverick.intent_value import compute
    try:
        return compute(world, since=since, until=until, owner=owner,
                       scope_kind=scope_kind, scope_id=scope_id, state=state)
    except ValueError as exc:
        raise HTTPException(422, "Choose an available reporting scope and valid dates.") from exc
    except Exception as exc:
        raise HTTPException(503, "Value evidence is unavailable. Retry after workspace storage recovers.") from exc


def _safe_benchmarks(rows):
    return [{key: value for key, value in row.items() if key in _SAFE_BENCHMARK} for row in rows]


def _safe_groups(request, groups, *, aggregate):
    if not aggregate:
        return groups
    # Process names are private definition content. A workspace rollup never
    # turns those names into an administrator's cross-owner directory.
    from maverick.flow import store
    allowed_processes = {row["id"] for row in store.list_flow_summaries(
        owner=auth.goal_owner_filter(request), bounded=True)}
    result = {}
    for key, rows in groups.items():
        kept = []
        for row in rows:
            identifier = row.get("id", row.get("scope_id", ""))
            if key == "processes" and identifier not in allowed_processes:
                continue
            kept.append({name: value for name, value in row.items()
                         if name in {"id", "scope_id", "label", "name", "totals", "kind"}})
        result[key] = kept
    return result


def report_for(request, *, since=None, until=None, scope_kind="platform", scope_id=None,
               population=None):
    """Current authorized report. All view roles may request aggregate totals."""
    _authority(request)
    if scope_kind not in {"platform", "specialist", "agent", "process"}:
        raise HTTPException(422, "Choose platform, specialist, agent or process.")
    if scope_kind != "platform" and not scope_id:
        raise HTTPException(422, "Choose the specialist, agent or process to inspect.")
    if scope_kind == "platform" and scope_id:
        raise HTTPException(422, "The platform scope does not take an individual selection.")
    if population not in {None, "own", "workspace"}:
        raise HTTPException(422, "Choose your work or workspace totals.")
    access = value_scope(request)
    aggregate = (population or access["scope"]) == "workspace"
    owner = None if aggregate else auth.goal_owner_filter(request)
    start, end = _period(since, until)
    state = _snapshot()
    world = _world()
    report = _compute(world, since=start, until=end, owner=owner,
                      scope_kind=scope_kind, scope_id=scope_id, state=state)
    if scope_kind == "process":
        from maverick.flow import store
        from .api import _assert_flow_access
        _assert_flow_access(request, store.load_flow(scope_id))
    rows = [] if aggregate else [row for row in report.get("tasks", [])
                                if _can_read_task(request, world, row)]
    report["tasks"] = rows
    report["opportunities"] = [row for row in rows if row.get("state") == "opportunity"]
    # Free-text mutation reasons and titles never survive a workspace projection.
    report["history"] = []
    report["groups"] = _safe_groups(request, report.get("groups", {}), aggregate=aggregate)
    report["benchmarks"] = _safe_benchmarks(report.get("benchmarks", []))
    report["access"] = {"scope": "workspace" if aggregate else "own",
                        "can_operate": access["can_operate"],
                        "can_admin": auth.has_permission(request, "admin"),
                        "task_details_available": not aggregate}
    report["settings"] = _effective(state)
    report["generated_at"] = time.time()
    report["source"] = "Current workspace audit and task records; editable effort benchmarks"
    return report


def _task_matches(row, identifier):
    if row.get("task_id") == identifier:
        return True
    if identifier.startswith("goal:"):
        return identifier[5:].isdigit() and int(identifier[5:]) in row.get("goal_ids", [])
    if identifier.startswith("flow:"):
        return any(reference.get("run_id") == identifier[5:] for reference in row.get("flow_runs", []))
    return False


def detail_for(request, task_id, *, since=None, until=None, mutation=False):
    _authority(request, permission="operate" if mutation else "view")
    # Explain all retained evidence for a task by default, even after it ages
    # out of a reporting window. Explicit dates still retain the requested cut.
    start, end = _period(0 if since is None else since, until)
    state = _snapshot()
    world = _world()
    report = _compute(world, since=start, until=end, owner=None,
                      scope_kind="platform", scope_id=None, state=state)
    task = next((row for row in report.get("tasks", []) if _task_matches(row, task_id)), None)
    if task is None:
        raise HTTPException(404, "This work item is unavailable or outside the selected period.")
    _assert_task(request, world, task, mutation=mutation)
    return {"task": task, "revision": state.get("revision", 0),
            "period": report["period"], "settings": _effective(state)}


def _actor(request):
    return auth.caller_principal(request) or "local:dashboard"


def _write(call):
    from maverick.governed_records import RecordConflict
    try:
        return call()
    except RecordConflict as exc:
        raise HTTPException(409, "These estimates changed. Refresh and review them before saving again.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "The change could not be confirmed. Retry the same request after storage and audit recover.") from exc


def _result(state, operation_id):
    return {"revision": state.get("revision", 0), "operation_id": operation_id,
            "saved": True, "receipt": _store().receipt_for(operation_id)}


def correction_for(request, task_id, *, intent_id, size, reason, expected_revision,
                   operation_id, confirmed=None):
    body = CorrectionIn(intent_id=intent_id, size=size, reason=reason,
                        expected_revision=expected_revision, operation_id=operation_id, confirmed=confirmed)
    detail = detail_for(request, task_id, mutation=True)
    if body.confirmed is not None and not detail["settings"].get("confirmation_required", False):
        raise HTTPException(409, "Completion confirmation is turned off. An administrator can enable it in Value settings.")
    state = _write(lambda: _store().correct_task(
        detail["task"]["task_id"], actor=_actor(request), **body.model_dump()))
    return _result(state, operation_id)


def link_for(request, source_task_id, *, target_task_id, reason, expected_revision, operation_id):
    body = ContinuityIn(target_task_id=target_task_id, reason=reason,
                        expected_revision=expected_revision, operation_id=operation_id)
    source = detail_for(request, source_task_id, mutation=True)["task"]
    target = detail_for(request, target_task_id, mutation=True)["task"]
    state = _write(lambda: _store().link_tasks(
        source["task_id"], target["task_id"], reason=body.reason, actor=_actor(request),
        expected_revision=body.expected_revision, operation_id=body.operation_id))
    return _result(state, operation_id)


def benchmark_for(request, intent_id, *, minutes_low, minutes, minutes_high, hourly_rate,
                  reason, expected_revision, operation_id):
    _authority(request, permission="admin")
    body = BenchmarkIn(minutes_low=minutes_low, minutes=minutes, minutes_high=minutes_high,
                       hourly_rate=hourly_rate, reason=reason, expected_revision=expected_revision,
                       operation_id=operation_id)
    state = _write(lambda: _store().save_benchmark(intent_id, actor=_actor(request), **body.model_dump()))
    return _result(state, operation_id)


def save_settings_for(request, *, enable, confirmation_required, reason, expected_revision, operation_id):
    _authority(request, permission="admin")
    body = SettingsIn(enable=enable, confirmation_required=confirmation_required, reason=reason,
                      expected_revision=expected_revision, operation_id=operation_id)
    from maverick.config import get_intent_value
    if body.enable and not get_intent_value().get("enable", True):
        raise HTTPException(409, "This installation has disabled intent value. Its deployment setting must be changed before enabling it for this workspace.")
    state = _write(lambda: _store().save_settings(actor=_actor(request), **body.model_dump()))
    return _result(state, operation_id)


def receipt_for(request, operation_id):
    _authority(request)
    receipt = _store().receipt_for(operation_id)
    if not receipt or receipt.get("actor") != _actor(request):
        raise HTTPException(404, "This change receipt is unavailable to your account.")
    return receipt


def discovery_for(request, task_id):
    detail = detail_for(request, task_id)
    task = detail["task"]
    can_operate = auth.has_permission(request, "operate")
    recommendations = []
    if can_operate:
        from maverick import flow
        from maverick.flow import store
        from .product_operations_task_launch import published_specialists
        terms = set(re.findall(r"[a-z]{3,}", str(task.get("intent_label", "")).lower()))
        stopwords = {"prepare", "draft", "review", "analyze", "create", "business", "unknown", "work"}
        terms -= stopwords
        for choice in published_specialists(request)["choices"]:
            content = (choice["label"] + " " + choice["value"]).lower().replace("_", " ")
            score = sum(term in content for term in terms)
            if score:
                query = urlencode({"specialist": choice["value"], "title": task.get("intent_label", ""),
                                   "intent_value_source": task["task_id"]})
                recommendations.append({"kind": "specialist", "id": choice["value"],
                    "label": choice["label"], "url": "/chat?" + query,
                    "reason": "Published specialist matching this work type. Review the task before starting.",
                    "score": score})
        if flow.enabled():
            from .api import _can_access_flow
            for definition in store.list_flows():
                if not _can_access_flow(request, definition):
                    continue
                published = store.load_published_bundle(definition.id)
                if published is None:
                    continue
                content = definition.name.lower().replace("_", " ")
                score = sum(term in content for term in terms)
                if score:
                    recommendations.append({"kind": "process", "id": definition.id,
                        "label": definition.name,
                        "url": "/flows/designer/" + quote(definition.id, safe="") + "?" +
                               urlencode({"intent_value_source": task["task_id"]}),
                        "reason": "Your published process matches this work type. Review its inputs and execution before starting.",
                        "score": score})
    recommendations.sort(key=lambda row: (-row["score"], row["label"]))
    return {"task_id": task["task_id"], "recommendations": recommendations[:12],
            "can_operate": can_operate, "execution_started": False,
            "notice": "Opening a recommendation starts no work. Existing permissions, review and spending controls still apply."}


@router.get("")
def report(request: Request, since: float | None = None, until: float | None = None,
           scope_kind: Scope = "platform", scope_id: str | None = None,
           population: Population | None = None):
    return report_for(request, since=since, until=until, scope_kind=scope_kind,
                      scope_id=scope_id, population=population)


@router.get("/taxonomy")
def taxonomy(request: Request):
    _authority(request)
    from maverick.intent_value_taxonomy import catalog
    return {"intents": catalog() + [{"intent_id": "unknown", "label": "Uncertain or other work"}]}


@router.get("/settings")
def settings(request: Request):
    return settings_for(request)


@router.put("/settings")
def save_settings(request: Request, body: SettingsIn):
    return save_settings_for(request, **body.model_dump())


@router.get("/benchmarks")
def benchmarks(request: Request):
    _authority(request)
    from maverick.intent_value_taxonomy import benchmark, catalog
    state = _snapshot()
    return {"benchmarks": _safe_benchmarks([benchmark(row["intent_id"], state.get("benchmarks", {}))
                                            for row in catalog()]), "revision": state.get("revision", 0)}


@router.put("/benchmarks/{intent_id}")
def save_benchmark(request: Request, intent_id: str, body: BenchmarkIn):
    return benchmark_for(request, intent_id, **body.model_dump())


@router.get("/metrics/{metric}")
def metric_detail(request: Request, metric: str, since: float | None = None,
                  until: float | None = None, scope_kind: Scope = "platform",
                  scope_id: str | None = None, population: Population | None = None):
    if metric not in _METRICS:
        raise HTTPException(404, "This metric is unavailable.")
    value = report_for(request, since=since, until=until, scope_kind=scope_kind,
                       scope_id=scope_id, population=population)
    return {"metric": metric, "value": value["totals"].get(metric), "explanation": _METRICS[metric],
            "period": value["period"], "scope": value["scope"], "tasks": value["tasks"],
            "access": value["access"], "coverage": value.get("coverage", {}),
            "revision": value["revision"], "benchmarks": value["benchmarks"]}


@router.get("/tasks/{task_id}")
def task_detail(request: Request, task_id: str, since: float | None = None, until: float | None = None):
    return detail_for(request, task_id, since=since, until=until)


@router.post("/tasks/{task_id}/correction")
def correct_task(request: Request, task_id: str, body: CorrectionIn):
    return correction_for(request, task_id, **body.model_dump())


@router.post("/tasks/{task_id}/continuity")
def link_task(request: Request, task_id: str, body: ContinuityIn):
    return link_for(request, task_id, **body.model_dump())


@router.get("/tasks/{task_id}/discovery")
def task_discovery(request: Request, task_id: str):
    return discovery_for(request, task_id)


@router.get("/receipts/{operation_id}")
def change_receipt(request: Request, operation_id: str):
    return receipt_for(request, operation_id)
