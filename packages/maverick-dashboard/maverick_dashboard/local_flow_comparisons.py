"""Owner-authorized native paired replays of retained real rule-only work.

Expected answers are explicit customer labels on selected known cases, not an
independent truth oracle. Every arm uses the complete immutable definition and
original retained inputs; no historical intermediate inputs are invented.
"""
from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from maverick.budget import Budget
from maverick.candidate_promotion import CandidatePromotionConflict, CandidatePromotionDenied
from maverick.flow import execution, runner, store
from maverick.flow import learned_operator as operator
from maverick.governed_records import GovernedRecordStore
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    ValidationError,
)

from . import local_flow_promotions as release
from .candidate_promotion_api import _binding, _call

router = APIRouter()
PAGE = "/learning/flow-checks"
API = "/api/v1/research/flow-comparisons"
_ID = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
_COMPARISON = re.compile(r"FCP-[a-f0-9]{32}\Z")
PROTOCOL = "Exact complete rule-only definitions, same retained initial inputs, customer-labeled known cases, terminal named output; no held-out or business-lift claim."


def records():
    # This extends the installed single-computer release connection, rather
    # than silently composing a different shared-storage deployment adapter.
    release._records()
    return GovernedRecordStore("local-flow-comparisons", "FCP", "local_flow_comparison", required_backend="local")


def _domain(flow):
    release._pure(flow)
    visiting, visited = set(), set()

    def visit(identity):
        if not identity or identity in visited:
            return
        if identity in visiting:
            raise CandidatePromotionDenied("A comparison needs routes that finish without cycling back. Review the workflow's routes first.")
        visiting.add(identity)
        node = flow.nodes[identity]
        if node.retries or node.retry_backoff or node.timeout:
            raise CandidatePromotionDenied("Turn off step retries and waiting for this local comparison. It must use bounded rules without repeated work.")
        text = json.dumps([node.condition, node.cases], ensure_ascii=False)
        if len(node.condition) > 1024 or len(node.cases) > 32 or len(text) > 8192 or re.search(r"\b[A-Za-z][A-Za-z0-9_]*\s*\(", text):
            raise CandidatePromotionDenied("This comparison supports short routes that compare ordinary values directly. Transform functions, time calculations and secret lookups are outside its bounded scope.")
        if len(node.assignments) > 16:
            raise CandidatePromotionDenied("Use at most sixteen ordinary value assignments per step for this bounded comparison.")
        for value in node.assignments.values():
            if (type(value) not in (str, int, bool) or type(value) is int and abs(value) > 10**12
                    or type(value) is str and (len(value) > 1024 or "{{" in value
                    and not re.fullmatch(r"\{\{\s*[A-Za-z][A-Za-z0-9_]*\s*\}\}", value))):
                raise CandidatePromotionDenied("This comparison supports ordinary scalar assignments and copying one named value. Grouped values and expanding or transformed templates are outside its bounded scope.")
        for target in (node.next, node.if_true, node.if_false, node.on_error, *(case.get("to") for case in node.cases)):
            visit(target)
        visiting.remove(identity)
        visited.add(identity)

    visit(flow.start)


def _current(request, flow_id, *, changed=True):
    from maverick.config import candidate_promotion_enabled

    from .api import _assert_flow_access, _require_flows, _require_learned_flow_evidence

    tenant, owner, context = _binding(request, Response())
    if not candidate_promotion_enabled() or not getattr(request.app.state, "local_flow_promotions_installed", False):
        raise HTTPException(409, "Enable release decisions and the installed local workflow release connection before comparing these rules.")
    _require_flows()
    candidate = release._current(tenant, owner, flow_id)
    _assert_flow_access(request, candidate)
    _require_learned_flow_evidence(candidate)
    _domain(candidate)
    published = store.load_published_bundle(flow_id)
    if published is None or published[0].owner != owner or published[0].revision != candidate.revision:
        raise HTTPException(409, "Publish a compatible rule-only baseline first, then save the changed draft.")
    baseline, publication = published
    _domain(baseline)
    _require_learned_flow_evidence(baseline)
    root, bundle, _size = release._bundle(candidate)
    if changed and bundle == publication["release_digest"]:
        raise HTTPException(409, "The saved draft is already published. Save a changed draft before comparing it.")
    return tenant, owner, context, candidate, baseline, publication, root, bundle


def _outputs(candidate, baseline):
    def fields(flow):
        return {node.output: {"node": node.id, "label": node.label or "Taught result", "kind": node.learned_operator["artifact"].get("spec", {}).get("output_type")}
                for node in flow.nodes.values() if node.kind == "agent" and node.output and node.learned_operator
                and node.learned_operator["artifact"].get("schema") == operator.SCHEMA}
    left, right = fields(baseline), fields(candidate)
    return {name: {"baseline_node": left[name]["node"], **right[name]} for name in right
            if name in left and right[name]["kind"] == left[name]["kind"]}


def _input(data):
    # Bound traversal before encoding retained values. No prompt, file or
    # credential loader is consulted; this is exactly the run's initial data.
    remaining = [64]
    def walk(value, depth=0):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 3:
            raise ValueError("These original values exceed the local comparison limit. Choose a smaller retained run.")
        if type(value) is dict:
            if len(value) > 16 or any(type(key) is not str or len(key) > 64 for key in value):
                raise ValueError("These original values cannot be used in this bounded comparison.")
            for item in value.values():
                walk(item, depth + 1)
        elif type(value) not in (str, int, bool) or type(value) is str and len(value) > 1024 or type(value) is int and abs(value) > 10**12:
            raise ValueError("Choose a run with bounded text, whole-number or yes/no input values.")
    if type(data) is not dict:
        raise ValueError("The original run values are unavailable.")
    if any(type(value) not in (str, int, bool) for value in data.values()):
        raise ValueError("Choose a run with directly named text, whole-number or yes/no values. Grouped inputs are outside this bounded comparison.")
    walk(data)
    if len(release._bytes(data)) > 8192:
        raise ValueError("The original run values exceed the 8 KB comparison limit.")
    return copy.deepcopy(data)


def _source(owner, flow, publication, run_id, output):
    run = store.load_run(run_id, owner=owner)
    if (run is None or run.flow_id != flow.id or run.owner != owner or run.dry_run
            or run.status not in {runner.STATUS_COMPLETED, runner.STATUS_FAILED}
            or run.definition_revision != flow.revision or run.definition_digest != publication["definition_digest"]
            or run.release_id != publication["release_id"] or run.subflow_digests):
        raise CandidatePromotionDenied("Choose a real finished run of this exact published baseline that still belongs to your account.")
    try:
        inputs = _input(run.input_data)
    except ValueError as exc:
        raise CandidatePromotionDenied(str(exc)) from exc
    if output in inputs:
        raise CandidatePromotionDenied("This result was also supplied as an initial value. Choose another result so a comparison cannot count an unanswered input as a produced answer.")
    value = run.data.get(output)
    if type(value) not in (str, int, bool) or type(value) is str and len(value) > 1024:
        value = None
    commitment = release._sha([run.run_id, run.owner, run.created, run.updated, run.status, inputs,
                               value, run.nodes, run.definition_digest, run.definition_revision, run.release_id])
    return {"run_id": run_id, "version": commitment, "inputs": inputs, "original_result": value,
            "created": run.created, "original_status": run.status}


def options(request, flow_id):
    _tenant, owner, context, candidate, baseline, publication, root, bundle = _current(request, flow_id, changed=False)
    outputs = _outputs(candidate, baseline)
    rows = store.list_run_metadata(flow_id=flow_id, owner=owner, limit=200)
    cases = [{"id": row.run_id, "when": row.created, "state": row.status} for row in rows
             if not row.dry_run and row.status in {runner.STATUS_COMPLETED, runner.STATUS_FAILED}
             and row.definition_digest == publication["definition_digest"] and row.release_id == publication["release_id"]
             and row.definition_revision == candidate.revision]
    retained = records().list(limit=1000)
    comparisons = [{"id": row["id"], "when": row["created_at"], "state": row["state"]}
                   for row in retained if row.get("owner") == owner and row.get("flow_id") == flow_id]
    return {"context": context, "name": candidate.name, "flow_id": flow_id, "version": candidate.version,
            "can_compare": bundle != publication["release_digest"],
            "revision": candidate.revision, "root": root, "bundle": bundle,
            "baseline_version": baseline.version, "baseline_release": publication["release_id"],
            "outputs": [{"value": name, "label": item["label"] + " · final result", "kind": item["kind"]} for name, item in outputs.items()],
            "cases": cases, "comparisons": comparisons[-50:],
            "scope": "Up to five selected known cases from the latest 200 retained runs. Both complete saved rule versions run locally; agreement with your expected answers is not held-out accuracy or business lift."}


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(pattern=_ID)
    source_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected: StrictInt | StrictBool | Annotated[StrictStr, StringConstraints(max_length=1024)]


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: bool
    authorize_case_use: bool
    operation: str = Field(pattern=r"^[a-f0-9]{32}$")
    flow_id: str = Field(pattern=_ID)
    version: int = Field(ge=1)
    revision: str = Field(min_length=1, max_length=128)
    baseline_release: str = Field(pattern=r"^[a-f0-9]{64}$")
    output: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    cases: list[Case] = Field(min_length=1, max_length=5)


def _freeze(request, body):
    tenant, owner, context, candidate, baseline, publication, root, bundle = _current(request, body.flow_id)
    if (body.authorize_case_use is not True or body.version != candidate.version or body.revision != candidate.revision
            or body.baseline_release != publication["release_id"] or body.output not in _outputs(candidate, baseline)):
        raise CandidatePromotionConflict("The selected versions or case-use authorization changed. Refresh and review the original cases again.")
    output = _outputs(candidate, baseline)[body.output]
    cases = []
    if len({case.run_id for case in body.cases}) != len(body.cases):
        raise ValueError("Choose each original run only once.")
    for case in body.cases:
        source = _source(owner, baseline, publication, case.run_id, body.output)
        if source["version"] != case.source_version:
            raise CandidatePromotionConflict("An original run changed. Refresh that case and review its expected answer again.")
        expected = case.expected
        if output["kind"] == "integer" and type(expected) is str and re.fullmatch(r"-?(?:0|[1-9][0-9]{0,24})", expected):
            expected = int(expected)
        try:
            operator._scalar(expected, output["kind"], output=True)
        except ValueError as exc:
            raise CandidatePromotionConflict("The expected answer does not match this result's accepted answer type or size. Review the answer in the native form.") from exc
        cases.append({**source, "expected": expected})
    material = {"tenant": tenant, "owner": owner, "context": context, "flow_id": candidate.id,
                "version": candidate.version, "flow_revision": candidate.revision, "root": root, "bundle_sha": bundle,
                "baseline": publication, "output": body.output, "step": output,
                "cases": cases, "protocol": PROTOCOL, "case_use": "owner_authorized_evaluation_and_exact_release_review"}
    return {"id": "FCP-" + body.operation, **material, "binding": release._sha(material), "state": "prepared", "results": []}


def _check(row):
    release._owner_authority(row["tenant"], row["owner"])
    candidate = store.load_flow(row["flow_id"])
    if candidate is None or candidate.owner != row["owner"] or candidate.revision != row["flow_revision"]:
        raise CandidatePromotionDenied("This workflow is no longer available to the original case owner.")
    baseline = store.load_flow_snapshot(row["baseline"]["definition_digest"])
    if baseline is None or baseline.owner != row["owner"] or baseline.revision != row["flow_revision"]:
        raise CandidatePromotionDenied("The retained baseline is unavailable to its original owner.")
    for case in row["cases"]:
        source = _source(row["owner"], baseline, row["baseline"], case["run_id"], row["output"])
        if source["version"] != case["version"]:
            raise CandidatePromotionConflict("An original case changed. This comparison cannot support a new release decision.")
    if row["state"] == "done":
        for pair in row["results"]:
            for arm in pair.values():
                recorded = store.load_run(arm["run_id"], owner=row["owner"])
                if recorded is None or _run_receipt(recorded) != arm["receipt"]:
                    raise CandidatePromotionConflict("A recorded comparison replay changed or is unavailable. It cannot support a new release decision.")
    return candidate, baseline


def _run_receipt(run):
    return release._sha([run.run_id, run.owner, run.flow_id, run.created, run.updated, run.status,
                         run.dry_run, run.input_data, run.data, run.nodes, run.definition_digest,
                         run.definition_version, run.definition_revision, run.release_digest, run.release_id])


def summary(row):
    results = row["results"]
    return {"cases": len(row["cases"]), "baseline_matches": sum(item["baseline"]["matches"] for item in results),
            "candidate_matches": sum(item["candidate"]["matches"] for item in results),
            "baseline_elapsed_ms": round(sum(item["baseline"]["elapsed_ms"] for item in results), 3),
            "candidate_elapsed_ms": round(sum(item["candidate"]["elapsed_ms"] for item in results), 3),
            "scope": "Agreement with explicit customer expected answers on selected known cases. Local replay times include native recording overhead; they do not measure customer savings, provider latency or production throughput.",
            "when": row.get("finished_at") or row["created_at"]}


def validate_release(identifier, target):
    row = records().get(identifier)
    if (row is None or row["state"] != "done" or row["owner"] != target["owner"] or row["tenant"] != target["tenant"]
            or row["root"] != target["root"] or row["bundle_sha"] != target["bundle_sha"]
            or row["baseline"] != target["baseline"] or row["flow_revision"] != target["flow_revision"]):
        raise CandidatePromotionConflict("The selected comparison no longer matches this exact release. Complete and select a current comparison.")
    _check(row)
    return row


def run(request, body):
    _binding(request, Response(), body)
    frozen = _freeze(request, body)
    service = records()
    row = service.get(frozen["id"])
    if row is not None:
        if row["binding"] != frozen["binding"]:
            raise CandidatePromotionConflict("This original comparison request belongs to different cases or answers. Review a new comparison.")
        return view(request, row["id"])
    row = service.create(frozen, action="flow.comparison.authorize", actor=frozen["owner"], max_records=1000)
    row = service.update(row["id"], lambda value: value.update(state="running"), expected_revision=row["revision"],
                         action="flow.comparison.start", actor=row["owner"])
    results = []
    budget = Budget(max_wall_seconds=10, max_dollars=0, max_tool_calls=0)
    def refuse(*_args):
        raise ValueError("This comparison never calls AI or a connected action.")
    def execution_guard():
        budget.check()
        release._owner_authority(row["tenant"], row["owner"])
    try:
        admission = _current(request, row["flow_id"])
        if admission[5] != row["baseline"] or admission[6] != row["root"]:
            raise CandidatePromotionConflict("The saved or published version changed before the comparison started.")
        _check(row)
        candidate = store.load_flow_snapshot(row["root"])
        baseline = store.load_flow_snapshot(row["baseline"]["definition_digest"])
        for index, case in enumerate(row["cases"]):
            pair = {}
            # Alternate ordering to make the actual local timing evidence
            # explicit, rather than always favoring a warmed candidate cache.
            for arm in (("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")):
                budget.check()
                started = time.perf_counter()
                flow = baseline if arm == "baseline" else candidate
                native = execution.execute(flow, owner=row["owner"], data=case["inputs"],
                    run_id=row["id"] + "-" + arm[0] + str(index + 1), agent_runner=refuse, action_runner=refuse,
                    record_outcomes=False, agent_budget_dollars=0,
                    origin="known-case-rule-comparison", execution_guard=execution_guard)
                budget.check()
                value = native.data.get(row["output"])
                node = row["step"]["baseline_node"] if arm == "baseline" else row["step"]["node"]
                produced = native.status == runner.STATUS_COMPLETED and native.nodes.get(node, {}).get("status") == "done"
                pair[arm] = {"run_id": native.run_id, "status": native.status,
                             "result": value if type(value) in (str, int, bool) else None,
                             "matches": produced and type(value) is type(case["expected"]) and value == case["expected"],
                             "elapsed_ms": (time.perf_counter() - started) * 1000,
                             "definition": native.definition_digest, "receipt": _run_receipt(native)}
            results.append(pair)
        _check(row)
        current = store.load_flow(row["flow_id"])
        if release._bundle(current)[0] != row["root"]:
            raise CandidatePromotionConflict("The draft changed while its comparison ran. Saved answers cannot authorize a different version.")
        row = service.update(row["id"], lambda value: value.update(state="done", results=results, finished_at=time.time()),
                             expected_revision=row["revision"], action="flow.comparison.complete", actor=row["owner"])
    except Exception as exc:
        # The original request is retained. A lost result never restarts work;
        # a later read distinguishes completed evidence from unavailable work.
        row = service.update(row["id"], lambda value: value.update(state="unavailable", results=[], finished_at=time.time()),
                             expected_revision=row["revision"], action="flow.comparison.unavailable", actor=row["owner"])
        raise HTTPException(409, "The original comparison did not produce complete current evidence. Its request remains recorded; no AI or connected work was started. Refresh before reviewing a new comparison.") from exc
    return view(request, row["id"])


def view(request, identifier):
    _tenant, owner, _context = _binding(request, Response())
    row = records().get(identifier) if _COMPARISON.fullmatch(identifier) else None
    if row is None or row["owner"] != owner:
        raise HTTPException(404, "That comparison is unavailable to this account.")
    _candidate, _baseline = _check(row)
    def scalar(value):
        return "Yes" if value is True else "No" if value is False else str(value) if value is not None else "No result recorded"
    cases = []
    for index, case in enumerate(row["cases"]):
        pair = copy.deepcopy(row["results"][index]) if index < len(row["results"]) else None
        if pair:
            for arm in pair.values():
                arm["result"] = scalar(arm["result"])
                arm.pop("definition", None)
                arm.pop("receipt", None)
        cases.append({"when": case["created"], "original_result": scalar(case["original_result"]),
                      "expected": scalar(case["expected"]), "inputs": case["inputs"], "results": pair})
    return {"id": row["id"], "state": row["state"], "name": _candidate.name, "flow_id": row["flow_id"],
            "version": row["version"], "current": release._bundle(_candidate)[0] == row["root"],
            "summary": summary(row), "cases": cases,
            "detail": "Completed local comparison." if row["state"] == "done" else "The original request is retained without complete results. No work was repeated."}


@router.get(PAGE)
def page(request: Request):
    from .app import templates
    _binding(request, Response())
    return templates.TemplateResponse(request, "flow_comparisons.html", {}, headers={"Cache-Control": "no-store, private"})


@router.get(API)
def listing(request: Request, flow_id: str = ""):
    if flow_id:
        return release._native_call(lambda: options(request, flow_id))
    _tenant, owner, context = _binding(request, Response())
    flows = store.list_flow_summaries(owner=owner, bounded=True)
    if len(flows) > 100:
        raise HTTPException(409, "Open a saved workflow's Compare real cases link to select it directly; this workspace exceeds the 100-workflow picker limit.")
    return {"context": context, "flows": [{"id": flow["id"], "name": flow["name"]} for flow in flows]}


@router.get(API + "/case")
def case_detail(request: Request, flow_id: str, run_id: str, output: str):
    def read():
        _tenant, owner, _context, candidate, baseline, publication, _root, _bundle = _current(request, flow_id)
        if output not in _outputs(candidate, baseline):
            raise ValueError("Choose a current result shared by these two versions.")
        source = _source(owner, baseline, publication, run_id, output)
        value = source["original_result"]
        source["original_result"] = "Yes" if value is True else "No" if value is False else str(value) if value is not None else "No result recorded"
        return source
    return release._native_call(read)


@router.post(API)
async def start(request: Request):
    _binding(request, Response())
    retained = bytearray()
    async for part in request.stream():
        if len(retained) + len(part) > 16384:
            raise HTTPException(413, "This comparison request exceeds its supported size. Select up to five bounded expected answers in the native form.")
        retained.extend(part)
    try:
        body = Start.model_validate_json(retained)
    except ValidationError as exc:
        raise HTTPException(422, "Review the selected runs and bounded expected answers in the native comparison form.") from exc
    return await _call(run, request, body)


@router.get(API + "/{identifier}")
def read(request: Request, identifier: str):
    return release._native_call(lambda: view(request, identifier))


@router.get("/static/lightwork-local-flow-comparisons.js")
def asset():
    return FileResponse(Path(__file__).parent / "static" / "lightwork-local-flow-comparisons.js", media_type="application/javascript")
