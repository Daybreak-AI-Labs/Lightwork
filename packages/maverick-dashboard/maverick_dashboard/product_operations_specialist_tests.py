"""Copilot controls for the same customer specialist answer checks as Workforce."""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from maverick.governed_operations import digest
from pydantic import Field

from . import auth
from . import specialist_test_sets as saved
from . import workforce_ops_api as native
from .draft_api import context_for
from .product_operations import Adapter, Outcome, Preview, Snapshot, StrictInput


class ChooseInput(StrictInput):
    specialist: str = Field(min_length=1, max_length=100)


class CreateInput(ChooseInput):
    task: str = Field(min_length=8, max_length=1000)
    must_mention: str = Field(default="", max_length=100)
    must_never_say: str = Field(default="", max_length=100)
    must_refuse: Literal["yes", "no"] = "no"
    must_cite: Literal["yes", "no"] = "no"


def _snapshot(request, selection, *, admin=False, paid=False):
    auth.require_permission(request, "admin" if admin else "view")
    blocked = ""
    if paid and not native._live_tests_enabled():
        blocked = "Enable bounded model tests in All settings before checking live answers."
    fields = [{"name": "specialist", "label": "Specialist", "kind": "text", "required": True,
               "value": str(selection.get("specialist") or "")[:100], "max_length": 100}]
    return Snapshot(digest({"context": context_for(request), "admin": admin, "paid": paid,
                            "enabled": not blocked}),
                    "Current answer-check controls in this account and workspace",
                    "Use your saved examples to check the current answers of an agent or specialist; live checks use no tools or company knowledge.",
                    fields=fields, blocked=blocked)


def read_snapshot(request, selection):
    return _snapshot(request, selection)


def read_prepare(request, selection, arguments):
    name = arguments["specialist"]
    native._test_profile(request, name)
    rows = saved.cases_for(name)
    runs = saved.runs_for(name)[:5]
    return Preview(f"{len(rows)} saved answer checks. Open Workforce for full answers and editing.",
                   native={"specialist": name, "cases": [
                       {"task": r["task"], "must_mention": r["includes"], "must_never_say": r["excludes"],
                        "must_refuse": r["refusal"], "must_cite": r["citation"]} for r in rows],
                           "recent_runs": [{"state": r["state"], "passed": r["passed"], "total": r["total"],
                                            "started_at": r["started_at"], "message": r.get("message", "")}
                                           for r in runs], "href": "/workforce?specialist=" + name})


def create_snapshot(request, selection):
    state = _snapshot(request, selection, admin=True)
    return Snapshot(state.revision, state.source,
                    "Save one test question and exact phrases the answer must or must not contain.",
                    fields=state.fields + [
                        {"name": "task", "label": "Question or request", "kind": "textarea", "required": True, "max_length": 1000},
                        {"name": "must_mention", "label": "Must mention (optional exact phrase)", "kind": "text", "max_length": 100},
                        {"name": "must_never_say", "label": "Must never say (optional exact phrase)", "kind": "text", "max_length": 100},
                        {"name": "must_refuse", "label": "Should refuse?", "kind": "select", "value": "no",
                         "choices": [{"value": "no", "label": "No"}, {"value": "yes", "label": "Yes"}]},
                        {"name": "must_cite", "label": "Should show a source reference?", "kind": "select", "value": "no",
                         "choices": [{"value": "no", "label": "No"}, {"value": "yes", "label": "Yes"}]}])


def _fields(arguments):
    body = native.SpecialistCaseIn(specialist=arguments["specialist"], task=arguments["task"],
        includes=[arguments["must_mention"]] if arguments["must_mention"].strip() else [],
        excludes=[arguments["must_never_say"]] if arguments["must_never_say"].strip() else [],
        refusal=arguments["must_refuse"] == "yes", citation=arguments["must_cite"] == "yes")
    return native._case_fields(body)


def create_prepare(request, selection, arguments):
    auth.require_permission(request, "admin")
    name = arguments["specialist"]
    native._test_profile(request, name)
    fields = _fields(arguments)
    return Preview("Save this answer check for the selected agent or specialist. Nothing is sent to an AI provider yet.",
                   [{"label": "Agent or specialist", "after": native._name(name)},
                    {"label": "Question", "after": fields["task"]},
                    {"label": "Passing answer", "after": "; ".join(
                        (["mentions " + ", ".join(fields["includes"])] if fields["includes"] else [])
                        + (["never says " + ", ".join(fields["excludes"])] if fields["excludes"] else [])
                        + (["refuses"] if fields["refusal"] else [])
                        + (["shows a source reference"] if fields["citation"] else []))}],
                   {"case_digest": digest(saved.cases_for(name))})


def create_recover(request, selection, arguments, prepared, operation_id):
    auth.require_permission(request, "admin")
    native._test_profile(request, arguments["specialist"])
    row = saved.case_by_id(operation_id)
    if row is None:
        return None
    if row["specialist"] != arguments["specialist"] or any(
            row.get(key) != value for key, value in _fields(arguments).items()):
        raise HTTPException(409, "This reviewed action no longer matches its saved test.")
    return Outcome("replayed", "The original answer check was already saved; no duplicate was added.",
                   {"test": row, "href": "/workforce?specialist=" + row["specialist"]})


def create_execute(request, selection, arguments, prepared, operation_id):
    auth.require_permission(request, "admin")
    name = arguments["specialist"]
    native._test_profile(request, name)
    if digest(saved.cases_for(name)) != prepared["case_digest"]:
        raise HTTPException(409, "The test list changed. Review it again before saving.")
    fields = _fields(arguments)
    native._audit(request, "specialist_test_save", specialist=name, test=operation_id)
    row = saved.save_once(name, fields, operation_id)
    return Outcome("confirmed", "The answer check was saved. It has not been run yet.",
                   {"test": row, "href": "/workforce?specialist=" + name})


def run_snapshot(request, selection):
    return _snapshot(request, selection, admin=True, paid=True)


def run_prepare(request, selection, arguments):
    auth.require_permission(request, "admin")
    name = arguments["specialist"]
    profile = native._test_profile(request, name)
    from maverick.llm import model_for_role

    rows = saved.snapshot(name)
    model = model_for_role(name)
    instructions = saved.instruction_digest(saved.answer_system(profile), model)
    return Preview(f"Check {len(rows)} saved answers using the current instructions and {model}. "
                   "At most $0.25 of model spend, no tools or outside systems, no automatic retry of uncertain calls.",
                   [{"label": "Agent or specialist", "after": native._name(name)},
                    {"label": "Questions", "after": len(rows)},
                    {"label": "Maximum spend", "after": "$0.25"}],
                   {"cases_digest": digest(rows), "instructions": instructions, "model": model})


def run_recover(request, selection, arguments, prepared, operation_id):
    auth.require_permission(request, "admin")
    native._test_profile(request, arguments["specialist"])
    row = saved.run_by_id(operation_id)
    if row is None or row["state"] == "prepared":
        return None
    if row["specialist"] != arguments["specialist"]:
        raise HTTPException(409, "The original run belongs to another agent or specialist.")
    return Outcome("replayed", "The original run was found. No paid request was repeated.",
                   {"run": row, "href": "/workforce?specialist=" + row["specialist"]})


def run_execute(request, selection, arguments, prepared, operation_id):
    name = arguments["specialist"]
    profile = native._test_profile(request, name)
    from maverick.llm import model_for_role

    existing = saved.run_by_id(operation_id)
    if existing is not None and existing["specialist"] != name:
        raise HTTPException(409, "The original run belongs to another agent or specialist.")
    if existing is not None and existing["state"] != "prepared":
        return Outcome("replayed", "The original run was found. No paid request was repeated.",
                       {"run": existing, "href": "/workforce?specialist=" + name})
    model = model_for_role(name)
    if (model != prepared["model"] or digest(saved.cases_for(name)) != prepared["cases_digest"]
            or saved.instruction_digest(saved.answer_system(profile), model) != prepared["instructions"]):
        raise HTTPException(409, "Its instructions or tests changed. Review again before spending.")
    started = native._start_customer_test_job(request, name, operation_id=operation_id)
    return Outcome("queued", "The answer checks started. Open Workforce to see the exact results; no uncertain call is replayed.",
                   {"job": started["job"], "href": "/workforce?specialist=" + name})


def entries():
    pages = ("/workforce", "/agents")
    return (
        Adapter("specialists.tests.read", "Read answer checks", "models", "/workforce", pages,
                ChooseInput, read_snapshot, read_prepare,
                aliases=("show specialist tests", "show agent tests", "show answer checks")),
        Adapter("specialists.tests.create", "Add an answer check", "models", "/workforce", pages,
                CreateInput, create_snapshot, create_prepare, create_execute, create_recover,
                aliases=("add an answer check", "create specialist test")),
        Adapter("specialists.tests.run", "Check answers", "models", "/workforce", pages,
                ChooseInput, run_snapshot, run_prepare, run_execute, run_recover,
                aliases=("run specialist tests", "check specialist answers", "check agent answers")),
    )
