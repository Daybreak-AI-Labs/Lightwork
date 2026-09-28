"""Teach a flow: the ``maverick flow operator`` and ``flow recipe`` commands in the app.

Every learning call is the command's own pure kernel function, run off the
request thread with bounded input; nothing here saves, publishes or runs a
flow. Changes come back as a draft the page saves through the normal flow
save (version checks, owner stamping); publishing stays separate.

* Rules for a step (``flow operator``): ``learn`` / ``learn-composed``
  (:mod:`maverick.flow.learned_operator`, :mod:`maverick.flow.composed_operator`),
  ``evaluate`` and ``refine`` (:mod:`maverick.flow.operator_artifacts`),
  ``attach`` / ``remove`` (:mod:`maverick.flow.evolve`). ``question`` and
  ``answer`` use the existing reviewed-question routes.
* Flows from reviewed runs (``flow recipe``): ``learn`` (plain, transforms or
  choices) and ``refine`` over the flow's accepted examples, ``draft``
  (:func:`maverick.flow.recipe_flow.make_flow`), ``verify-bundle``,
  ``learn-verified`` / ``learn-workflow-verified`` / ``learn-memory-verified``,
  ``refine-verified`` and ``draft-verified`` (:mod:`maverick.flow.recipe_lineage`).
  Signed copies are checked against this workspace's own trusted keys unless
  a trust file is chosen; the workspace is never typed. Data descriptions are
  taken from the examples' capture contracts, never typed.

Access: ``operate``, flows enabled, and the per-flow access check of the flow
pages; examples are read through the episode review service with its own
fresh owner, workspace and role checks.
"""
from __future__ import annotations

import json
import re
import secrets
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool

from .auth import require_permission

router = APIRouter(tags=["flow-teaching"])
Scalar = Annotated[StrictInt | StrictStr | StrictBool, Field()]
_NAME = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
MAX_BODY = 1_000_000
MAX_LINEAGE_BODY = 8 * 1024 * 1024
MAX_RUNS = 50


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputDecl(_In):
    name: str = Field(pattern=_NAME)
    kind: Literal["integer", "string", "boolean"]
    min: StrictInt | None = None
    max: StrictInt | None = None
    max_length: StrictInt | None = Field(default=None, ge=1, le=1024)


class ExampleIn(_In):
    inputs: dict[str, Scalar] = Field(max_length=6)
    output: Scalar


class LearnIn(_In):
    inputs: list[InputDecl] = Field(min_length=1, max_length=6)
    output_kind: Literal["integer", "string", "boolean"]
    constants: list[Scalar] = Field(default_factory=list, max_length=8)
    examples: list[ExampleIn] = Field(min_length=2, max_length=64)
    combined: bool = False
    max_operations: int = Field(default=3, ge=1, le=4)
    operations: list[StrictStr] | None = Field(default=None, max_length=16)


class ArtifactIn(_In):
    artifact: dict[str, Any]


class TryIn(ArtifactIn):
    inputs: dict[str, Scalar] = Field(max_length=6)


class AddExamplesIn(ArtifactIn):
    examples: list[ExampleIn] = Field(min_length=1, max_length=64)


class AttachIn(ArtifactIn):
    node_id: str = Field(min_length=1, max_length=256)


class RemoveIn(_In):
    node_id: str = Field(min_length=1, max_length=256)


class RecipeLearnIn(_In):
    run_ids: list[str] = Field(min_length=2, max_length=32)
    tools: list[str] = Field(min_length=1, max_length=64)
    style: Literal["plain", "transforms", "choices"] = "plain"
    max_operations: int = Field(default=2, ge=1, le=4)


class RecipeRefineIn(ArtifactIn):
    run_ids: list[str] = Field(min_length=1, max_length=32)


class RecipeDraftIn(ArtifactIn):
    name: str = Field(min_length=1, max_length=200)
    teaching: bool = False


class BundleCheckIn(_In):
    bundle: dict[str, Any]
    trust: dict[str, Any] | None = None


class VerifiedLearnIn(_In):
    bundles: list[dict[str, Any]] = Field(min_length=2, max_length=32)
    tools: list[str] = Field(min_length=1, max_length=8)
    style: Literal["plain", "choices", "workflow", "memory"] = "plain"
    max_operations: int = Field(default=2, ge=1, le=4)
    max_steps: int = Field(default=8, ge=1, le=8)
    trust: dict[str, Any] | None = None


class VerifiedRefineIn(_In):
    package: dict[str, Any]
    bundles: list[dict[str, Any]] = Field(min_length=1, max_length=32)
    trust: dict[str, Any] | None = None


class VerifiedInspectIn(_In):
    package: dict[str, Any]
    name: str = Field(default="Recipe draft", min_length=1, max_length=200)
    teaching: bool = False
    trust: dict[str, Any] | None = None


# ---- shared helpers ---------------------------------------------------------

def _gate(request: Request) -> None:
    from .api import _require_flows

    require_permission(request, "operate")
    _require_flows()


def _flow(request: Request, flow_id: str):
    from maverick.flow import store

    from .api import _assert_flow_access

    _gate(request)
    flow = store.load_flow(flow_id[:256])
    if flow is None:
        raise HTTPException(404, "no such flow")
    _assert_flow_access(request, flow)
    return flow


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate field")
        out[key] = value
    return out


async def _body(request: Request, model: type[BaseModel], limit: int = MAX_BODY):
    from .app import _read_limited_request_body

    raw = await _read_limited_request_body(request, max_bytes=limit,
                                          too_large_detail="This is too large. Use fewer or smaller items.")
    try:
        return model.model_validate(json.loads(raw, object_pairs_hook=_pairs))
    except (ValueError, TypeError, RecursionError, ValidationError) as exc:
        raise HTTPException(422, "Some of the details aren't valid. Check them and try again.") from exc


_PLAIN = (
    ("two to 64 examples", "Add at least two examples (up to 64)."),
    ("conflicting outputs", "Two examples have the same inputs but different answers."),
    ("two distinct input", "Use at least two different sets of inputs."),
    ("outside numeric bounds", "An example is outside the number limits you set for its inputs."),
    ("outside string bounds", "An example's text is longer than the limit you set."),
    ("exactly match the declared", "Every example needs a value for each input."),
    ("one to six named inputs", "Use one to six inputs."),
    ("bounds are reversed", "An input's smallest number is larger than its largest."),
    ("two to 32 reviewed episodes", "Choose at least two reviewed runs (up to 32)."),
    ("identical tool sequences", "The chosen runs didn't take the same actions in the same order."),
    ("outside allowed_tools", "A chosen run used an action you didn't allow."),
    ("two to eight steps", "Each run needs two to eight actions."),
    ("teaching requires", "Teaching pauses are only available for flows that choose between two actions."),
    ("no program in the supported grammar", "No simple rule fits all of these examples. Add a value the rule may compare "
                                            "against, or allow rules that combine operations."),
)


def _plain(exc: Exception, fallback: str) -> str:
    text = str(exc)
    for needle, message in _PLAIN:
        if needle in text:
            return message
    if "exceed" in text or "limit" in text:
        return "This is too large to learn from. Use fewer or smaller examples."
    return fallback


async def _compute(fn, fallback: str):
    try:
        return await run_in_threadpool(fn)
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(403, "This learning feature is turned off or not allowed for you here.") from exc
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise HTTPException(400, _plain(exc, fallback)) from exc


def _rule_summary(artifact: dict) -> dict:
    from maverick.flow.operator_artifacts import candidate_count

    spec = artifact.get("spec") or {}
    return {"rules": candidate_count(artifact), "examples": int(artifact.get("example_count") or 0),
            "inputs": [{"name": n, "kind": d.get("type")} for n, d in sorted((spec.get("inputs") or {}).items())],
            "output_kind": spec.get("output_type"), "combined": artifact.get("schema") != "lightwork.learned-operator.v1"}


def _spec(body: LearnIn) -> dict:
    inputs = {}
    for decl in body.inputs:
        if decl.name in inputs:
            raise HTTPException(422, "Each input needs a different name.")
        if decl.kind == "integer":
            if decl.min is None or decl.max is None:
                raise HTTPException(422, "Give each number input a smallest and largest value.")
            inputs[decl.name] = {"type": "integer", "min": decl.min, "max": decl.max}
        elif decl.kind == "string":
            inputs[decl.name] = {"type": "string", "max_length": decl.max_length or 200}
        else:
            inputs[decl.name] = {"type": "boolean"}
    return {"inputs": inputs, "output_type": body.output_kind, "constants": list(body.constants)}


def _examples(rows: list[ExampleIn]) -> list[dict]:
    return [{"inputs": dict(row.inputs), "output": row.output} for row in rows]


def _draft_out(flow, note: str) -> dict:
    data = flow.to_dict()
    return {"draft": data, "saved": False, "published": False, "note": note}


# ---- page -------------------------------------------------------------------

@router.get("/flows/{flow_id}/teach", response_class=HTMLResponse)
def flow_teach_page(request: Request, flow_id: str) -> HTMLResponse:
    from maverick.config import get_flows

    from .app import templates

    flow = _flow(request, flow_id)
    flags = get_flows()
    return templates.TemplateResponse(request, "flow_teach.html", {
        "flow": flow, "questions_on": flags.get("learned_operator_questions") is True,
        "examples_on": flags.get("action_episode_capture") is True,
        "lineage_on": flags.get("action_recipe_lineage") is True,
    }, headers={"Cache-Control": "private, no-store"})


@router.get("/api/v1/flows/{flow_id}/teach")
def flow_teach_overview(request: Request, flow_id: str) -> dict:
    """The flow's AI steps a rule can be taught for, and which already have one."""
    from maverick.flow.ir import NODE_ACTION, NODE_AGENT

    flow = _flow(request, flow_id)
    tools = sorted({node.tool for node in flow.nodes.values() if node.kind == NODE_ACTION and node.tool})[:64]
    steps = []
    for node in flow.nodes.values():
        if node.kind != NODE_AGENT:
            continue
        rule = None
        if node.learned_operator:
            try:
                rule = _rule_summary(node.learned_operator["artifact"])
            except Exception:
                rule = {"rules": 0, "examples": 0, "inputs": [], "output_kind": None, "unreadable": True}
        steps.append({"id": node.id, "label": node.label or node.brief[:80] or node.id,
                      "ready": bool(node.brief and node.output), "rule": rule})
    return {"flow": {"id": flow.id, "name": flow.name, "version": flow.version, "revision": flow.revision},
            "steps": steps, "tools": tools}


# ---- rules for a step (flow operator) -----------------------------------------

@router.post("/api/v1/flows/{flow_id}/teach/rule/learn")
async def learn_rule(request: Request, flow_id: str) -> dict:
    """``flow operator learn`` / ``learn-composed``: every rule that fits the examples."""
    _flow(request, flow_id)
    body = await _body(request, LearnIn)
    spec, examples = _spec(body), _examples(body.examples)

    def work():
        if body.combined:
            from maverick.flow.composed_operator import synthesize as composed

            ops = None if body.operations is None else [o.strip() for o in body.operations]
            return composed(spec, examples, max_operations=body.max_operations, operators=ops)
        from maverick.flow.learned_operator import synthesize

        return synthesize(spec, examples)

    artifact = await _compute(work, "Lightwork couldn't find a rule that fits these examples. Check them and try again.")
    return {"artifact": artifact, "summary": _rule_summary(artifact)}


_DECISION = {
    "agreement": "Every rule that fits your examples gives this answer.",
    "ambiguity": "The rules that fit your examples disagree here, so the step would ask its assistant instead.",
    "outside_domain": "These inputs are outside the limits you set, so the step would ask its assistant instead.",
    "invalid_artifact": "This learned rule can't be checked. Learn it again.",
}


@router.post("/api/v1/flows/{flow_id}/teach/rule/try")
async def try_rule(request: Request, flow_id: str) -> dict:
    """``flow operator evaluate``: what the rule would answer. Runs no assistant or tool."""
    from maverick.flow.operator_artifacts import evaluate

    _flow(request, flow_id)
    body = await _body(request, TryIn)
    decision = await _compute(lambda: evaluate(body.artifact, dict(body.inputs)), "This rule can't be tried.")
    return {"answered": bool(decision.applied), "value": decision.value if decision.applied else None,
            "explanation": _DECISION.get(decision.reason, "The step would ask its assistant instead."),
            "rules": int(decision.candidate_count)}


@router.post("/api/v1/flows/{flow_id}/teach/rule/add-examples")
async def add_rule_examples(request: Request, flow_id: str) -> dict:
    """``flow operator refine``: rebuild with more examples, keeping the earlier ones."""
    from maverick.flow.operator_artifacts import refine

    _flow(request, flow_id)
    body = await _body(request, AddExamplesIn)
    artifact = await _compute(lambda: refine(body.artifact, _examples(body.examples)),
                              "These examples couldn't be added. Check them and try again.")
    return {"artifact": artifact, "summary": _rule_summary(artifact)}


@router.post("/api/v1/flows/{flow_id}/teach/rule/attach")
async def attach_rule(request: Request, flow_id: str) -> dict:
    """``flow operator attach``: an unsaved draft where the step uses the rule when it's sure."""
    from maverick.flow.evolve import attach_learned_operator

    flow = _flow(request, flow_id)
    body = await _body(request, AttachIn)

    def work():
        if body.node_id not in flow.nodes:
            raise ValueError("unknown step")
        return attach_learned_operator(flow, body.node_id, body.artifact)

    draft = await _compute(work, "The rule can't be added to this step. The step needs instructions and a "
                                 "place to keep its answer.")
    return _draft_out(draft, "The step uses the learned rule when every matching rule agrees, and asks its "
                             "assistant otherwise. Save to keep this change; publishing stays separate.")


@router.post("/api/v1/flows/{flow_id}/teach/rule/remove")
async def remove_rule(request: Request, flow_id: str) -> dict:
    """``flow operator remove``: an unsaved draft where the step always asks its assistant again."""
    from maverick.flow.evolve import remove_learned_operator

    flow = _flow(request, flow_id)
    body = await _body(request, RemoveIn)
    if body.node_id not in flow.nodes:
        raise HTTPException(404, "no such step")
    draft = await _compute(lambda: remove_learned_operator(flow, body.node_id), "The rule can't be removed.")
    return _draft_out(draft, "The step goes back to asking its assistant every time. Save to keep this change.")


# ---- flows from reviewed runs (flow recipe) -----------------------------------

def _episode_service(request: Request, flow_id: str):
    from .api_flow_action_episodes import _call, _service

    return _call(lambda: _service(request, flow_id))


@router.get("/api/v1/flows/{flow_id}/teach/examples")
def reviewed_examples(request: Request, flow_id: str) -> dict:
    """This flow's recent runs and whether each is a reviewed example to learn from."""
    from maverick.flow import store

    from .auth import goal_owner_filter

    _flow(request, flow_id)
    try:
        service, ctx = _episode_service(request, flow_id)
    except HTTPException as exc:
        return {"available": False, "runs": [], "message": "Reviewed examples aren't available for this flow"
                + (" (an administrator can turn on example collection)." if exc.status_code == 403 else ".")}
    rows = []
    for run in store.list_run_metadata(flow_id=flow_id, owner=goal_owner_filter(request), limit=MAX_RUNS):
        run_id = str(run.run_id or "")
        if not run_id:
            continue
        try:
            record = service.get(ctx, flow_id, run_id)
        except Exception:
            record = None
        status = (record or {}).get("status") or "none"
        rows.append({"run_id": run_id, "started": run.created or None, "run_status": run.status,
                     "status": status, "can_learn": status == "accepted",
                     "can_sign": status == "accepted" and bool((record or {}).get("grant_sha256")),
                     "revision": (record or {}).get("revision"), "episode": (record or {}).get("episode_sha256"),
                     "grant": (record or {}).get("grant_sha256")})
    return {"available": True, "runs": rows}


def _gather(request: Request, flow_id: str, run_ids: list[str], *, contract: bool):
    service, ctx = _episode_service(request, flow_id)
    episodes, first_contract = [], None
    for run_id in run_ids:
        exported = service.export(ctx, flow_id, run_id[:256])
        episodes.append(exported["episode"])
        if contract and first_contract is None:
            first_contract = (service.get(ctx, flow_id, run_id[:256], detail=True).get("evidence") or {}).get("contract")
    return episodes, first_contract


@router.post("/api/v1/flows/{flow_id}/teach/recipe/learn")
async def learn_recipe(request: Request, flow_id: str) -> dict:
    """``flow recipe learn`` over reviewed runs, with ``--transforms`` or ``--choices`` as styles."""
    from maverick.flow.recipe_domain import domain_from_contract

    _flow(request, flow_id)
    body = await _body(request, RecipeLearnIn)

    def work():
        episodes, contract = _gather(request, flow_id, body.run_ids, contract=body.style != "plain")
        tools = sorted(set(body.tools))
        if body.style == "plain":
            from maverick.flow.action_recipe import synthesize

            return synthesize(episodes, allowed_tools=tools)
        domain = domain_from_contract(body.style, contract or {}, episodes)
        if body.style == "choices":
            from maverick.flow.action_choice import synthesize_choice as learner
        else:
            from maverick.flow.action_recipe_transforms import synthesize_transformed as learner
        return learner(episodes, allowed_tools=tools, domain=domain, max_operations=body.max_operations)

    artifact = await _compute(work, "Lightwork couldn't learn a flow from these runs.")
    return {"artifact": artifact, "summary": _recipe_summary(artifact)}


def _recipe_summary(artifact: dict) -> dict:
    episodes = artifact.get("episodes") or []
    first = episodes[0].get("steps") if episodes else []
    return {"runs": int(artifact.get("episode_count") or len(episodes)),
            "actions": [str(step.get("tool")) for step in (first or [])][:8],
            "alternatives": int(artifact.get("candidate_binding_count") or 0),
            "choice": artifact.get("schema") == "lightwork.action-choice.v1"}


@router.post("/api/v1/flows/{flow_id}/teach/recipe/refine")
async def refine_recipe(request: Request, flow_id: str) -> dict:
    """``flow recipe refine``: add more reviewed runs, keeping the earlier ones."""
    from maverick.flow.action_recipe import refine

    _flow(request, flow_id)
    body = await _body(request, RecipeRefineIn)

    def work():
        episodes, _ = _gather(request, flow_id, body.run_ids, contract=False)
        return refine(body.artifact, episodes)

    artifact = await _compute(work, "These runs couldn't be added.")
    return {"artifact": artifact, "summary": _recipe_summary(artifact)}


def _new_flow_id(name: str) -> str:
    from maverick.flow import store

    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "learned-flow"
    for _ in range(8):
        candidate = f"{base}-{secrets.token_hex(3)}"
        if store.load_flow(candidate) is None:
            return candidate
    raise HTTPException(409, "A new flow couldn't be named. Try again.")


@router.post("/api/v1/flows/{flow_id}/teach/recipe/draft")
async def draft_recipe(request: Request, flow_id: str) -> dict:
    """``flow recipe draft``: a new unsaved flow with an approval before every action."""
    from maverick.flow.ir import partition_draft_validation_errors
    from maverick.flow.recipe_flow import make_flow

    _flow(request, flow_id)
    body = await _body(request, RecipeDraftIn)
    new_id = _new_flow_id(body.name)

    def work():
        flow = make_flow(body.artifact, flow_id=new_id, name=body.name.strip(), teaching=body.teaching)
        blocking, unclassified = partition_draft_validation_errors(flow.validate())
        if blocking:
            raise ValueError("draft has blocking problems")
        return flow, unclassified

    flow, unclassified = await _compute(work, "A flow couldn't be made from this recipe.")
    note = "Every action waits for a person's approval. Save to keep it; publishing and running stay separate."
    if unclassified:
        note += " Some actions still need their safety level set before it can run."
    return _draft_out(flow, note)


# ---- signed copies and approved recipes (flow recipe *-verified) -----------------

def _trust_and_workspace(trust: dict | None):
    from maverick.flow.action_provenance import local_trust
    from maverick.paths import current_tenant_id_strict

    tenant = current_tenant_id_strict()
    if not tenant:
        raise HTTPException(409, "Choose a workspace first.")
    return (trust if trust is not None else local_trust(tenant)), tenant


_CHECK = {"verified": "Confirmed", "valid": "Confirmed", "accepted": "Confirmed", "not_checked": "Not checked here",
          "current": "Current", "expired": "Expired"}


@router.post("/api/v1/flow-teach/signed-copy/check")
async def check_signed_copy(request: Request) -> dict:
    """``flow recipe verify-bundle``: is this signed example genuine and unchanged?"""
    import time

    from maverick.flow.action_provenance import verify_bundle

    _gate(request)
    body = await _body(request, BundleCheckIn, MAX_LINEAGE_BODY)

    def work():
        trust, tenant = _trust_and_workspace(body.trust)
        return verify_bundle(body.bundle, trust=trust, expected_tenant=tenant, now=time.time())

    try:
        result = await run_in_threadpool(work)
    except HTTPException:
        raise
    except (ValueError, TypeError, PermissionError, KeyError):
        return {"genuine": False, "message": "This signed example couldn't be confirmed. It may be from another "
                                             "workspace, signed by a key that isn't trusted here, or changed."}
    return {"genuine": True, "signature": _CHECK.get(str(result.get("signature_status")), "Confirmed"),
            "contents": _CHECK.get(str(result.get("commitments_status")), "Confirmed"),
            "time": _CHECK.get(str(result.get("time_status")), str(result.get("time_status"))),
            "withdrawal": "Not checked here: whether it was later withdrawn is checked when it's used."}


@router.post("/api/v1/flow-teach/signed/learn")
async def learn_from_signed(request: Request):
    """``learn-verified`` (plain or choices) or ``learn-workflow/memory-verified``."""
    import time

    from maverick.flow.recipe_domain import domain_from_contract

    _gate(request)
    body = await _body(request, VerifiedLearnIn, MAX_LINEAGE_BODY)
    tools = sorted(set(body.tools))
    if body.style in ("workflow", "memory"):
        from .api_flow_recipe_lineage import (
            WorkflowLearningIn,
            _call,
            _learn_workflow,
            _response,
            _service,
        )

        service, ctx = _call(lambda: _service(request))
        episodes = [b.get("body", {}).get("episode") or {} for b in body.bundles]
        contract = body.bundles[0].get("body", {}).get("contract") or {}
        try:
            domain = domain_from_contract(body.style, contract, episodes)
            payload = WorkflowLearningIn(bundles=body.bundles, allowed_tools=tools, domain=domain,
                                         max_operations=min(body.max_operations, 2), max_steps=body.max_steps)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            raise HTTPException(400, "These signed examples don't describe their data, so this style can't be "
                                     "used. Try the simple style.") from exc
        result = await run_in_threadpool(_call, lambda: _learn_workflow(service, ctx, payload,
                                                                         memory=body.style == "memory"))
        return _response(result)

    def work():
        from maverick.flow import recipe_lineage

        trust, tenant = _trust_and_workspace(body.trust)
        domain = None
        if body.style == "choices":
            episodes = [b.get("body", {}).get("episode") or {} for b in body.bundles]
            domain = domain_from_contract("choices", body.bundles[0].get("body", {}).get("contract") or {}, episodes)
        learner = (recipe_lineage.learn_choice_from_verified_bundles if body.style == "choices"
                   else recipe_lineage.learn_from_verified_bundles)
        return learner(body.bundles, trust=trust, expected_tenant=tenant, now=time.time(), allowed_tools=tools,
                       domain=domain, max_operations=body.max_operations, operators=None, constants=None)

    package = await _compute(work, "Lightwork couldn't learn from these signed examples. Check they come from "
                                   "this workspace and were approved for learning.")
    return {"package": package, "summary": _recipe_summary(package.get("artifact") or {}), "approved": False}


@router.post("/api/v1/flow-teach/signed/refine")
async def refine_signed(request: Request) -> dict:
    """``flow recipe refine-verified``: keep the earlier signed examples and add more."""
    import time

    from maverick.flow.recipe_lineage import refine_package

    _gate(request)
    body = await _body(request, VerifiedRefineIn, MAX_LINEAGE_BODY)

    def work():
        trust, tenant = _trust_and_workspace(body.trust)
        return refine_package(body.package, body.bundles, trust=trust, expected_tenant=tenant, now=time.time())

    package = await _compute(work, "These signed examples couldn't be added.")
    return {"package": package, "summary": _recipe_summary(package.get("artifact") or {}), "approved": False}


@router.post("/api/v1/flow-teach/signed/inspect")
async def inspect_signed(request: Request) -> dict:
    """``flow recipe draft-verified``: look at a learned package as a flow, without approving it."""
    import time

    from maverick.flow.recipe_flow import make_flow
    from maverick.flow.recipe_lineage import validate_package

    _gate(request)
    body = await _body(request, VerifiedInspectIn, MAX_LINEAGE_BODY)

    def work():
        trust, tenant = _trust_and_workspace(body.trust)
        validate_package(body.package, trust=trust, expected_tenant=tenant, now=time.time())
        flow = make_flow(body.package["artifact"], flow_id=_new_flow_id(body.name), name=body.name.strip(),
                         teaching=body.teaching)
        return flow

    flow = await _compute(work, "This learned package couldn't be checked here.")
    steps = [{"kind": node.kind, "label": node.label or node.tool or node.prompt[:80] or node.id}
             for node in flow.nodes.values()][:40]
    return {"steps": steps, "draft": flow.to_dict(), "saved": False,
            "note": "For inspection only. Approve the package for governed use to make a flow you can save."}


@router.get("/api/v1/flow-teach/approved")
def approved_recipes(request: Request) -> dict:
    """Recipes you approved for governed use in this workspace (details stay sealed)."""
    from maverick.flow.recipe_lineage import _RECORDS
    from maverick.governed_operations import digest

    from .api_flow_recipe_lineage import _call, _service

    _gate(request)
    _service_obj, ctx = _call(lambda: _service(request))
    tenant = digest(ctx.tenant)
    rows = []
    for row in _RECORDS.list(limit=1000):
        if row.get("owner") != ctx.actor or row.get("tenant_sha256") != tenant:
            continue
        rows.append({"id": row.get("id"), "status": row.get("status"),
                     "approved_at": row.get("admitted_at"), "use_until": row.get("use_until")})
    rows.sort(key=lambda r: r.get("approved_at") or 0, reverse=True)
    return {"recipes": rows[:100]}
