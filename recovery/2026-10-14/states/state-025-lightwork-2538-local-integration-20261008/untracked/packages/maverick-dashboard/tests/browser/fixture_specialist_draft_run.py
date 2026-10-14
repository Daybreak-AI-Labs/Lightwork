"""Synthetic worker for the real Agent Factory draft-task browser journey."""
from __future__ import annotations

import secrets
import threading

from fastapi import HTTPException, Request


def install(app, world, tenant: str, principal: str) -> None:
    from maverick import runner
    from maverick.paths import tenant_scope
    from maverick_dashboard import api as dashboard_api
    from maverick_dashboard import learning_jobs

    state = {"armed": False, "goal": None, "snapshot": None, "calls": 0,
             "expected_snapshot": None}
    original_runner = runner.run_goal_in_thread
    original_provider_gate = dashboard_api.require_provider_or_400
    original_job_start = learning_jobs.start
    lock = threading.Lock()

    def require_provider_or_400(*args, **kwargs):
        if not state["armed"]:
            return original_provider_gate(*args, **kwargs)

    def run_goal_in_thread(goal_id: int, **kwargs):
        if not state["armed"]:
            return original_runner(goal_id, **kwargs)
        with tenant_scope(tenant=tenant), lock:
            goal = world.get_goal(goal_id)
            if goal is None or goal.owner != principal or not goal.domain:
                raise AssertionError("The draft trial was not bound to its owner and specialist")
            snapshot = world.goal_domain_profile_snapshot(
                goal_id, owner=goal.owner, domain=goal.domain,
            )
            state.update(goal=goal_id, snapshot=snapshot, calls=state["calls"] + 1)
            expected = state["expected_snapshot"]
            expected_mismatch = goal.domain == "browser_chat_draft" and (
                not isinstance(expected, dict) or snapshot != expected
            )
            if (not isinstance(snapshot, dict)
                    or expected_mismatch
                    or snapshot.get("profile", {}).get("name") != goal.domain):
                actual_name = snapshot.get("profile", {}).get("name") if isinstance(snapshot, dict) else None
                expected_name = expected.get("profile", {}).get("name") if isinstance(expected, dict) else None
                raise AssertionError(
                    f"Draft profile mismatch: goal={goal.domain!r}, snapshot={actual_name!r}, expected={expected_name!r}"
                )
            world.set_goal_status(
                goal_id, "done",
                result="Synthetic local task result. No provider or connected service was called.",
            )
            return "done"

    def start_job(kind: str, label: str, fn, **kwargs):
        if kind.startswith("specialist_draft_"):
            kwargs["problem"] = lambda exc: f"Local browser fixture failed: {type(exc).__name__}: {exc}"
        return original_job_start(kind, label, fn, **kwargs)

    runner.run_goal_in_thread = run_goal_in_thread
    dashboard_api.require_provider_or_400 = require_provider_or_400
    learning_jobs.start = start_job

    @app.post("/__browser_fixture/specialist-draft/seed", include_in_schema=False)
    async def seed_specialist_draft(request: Request):
        """Create one local, unpublished draft for the Chat control journey."""
        from maverick import domain
        from maverick_dashboard import auth, specialist_drafts

        if auth.caller_principal(request) != principal:
            raise HTTPException(403, "Synthetic fixture owner required.")
        source = domain.available_domains().get("cx_triage")
        if source is None:
            raise HTTPException(503, "The synthetic specialist template is unavailable.")
        entered = {
            "extends": "cx_triage",
            "description": "Synthetic draft for the local Chat journey",
            "persona": "Summarize the supplied task and state the next review step.",
            "allow_tools": ["read_file"],
            "deny_tools": ["apply_patch", "browser", "shell", "str_replace_editor", "write_file"],
            "max_risk": "low",
            "workflow": [{"name": "Review task", "instruction": "Summarize the supplied task and state the next review step.", "tools": [], "gate": None}],
            "knowledge_sources": [],
            "mcp_servers": [],
            "models": {},
        }
        prepared = specialist_drafts.prepare(
            request, "browser_chat_draft", "save", entered=entered,
            title="Chat entry test draft",
        )
        outcome = specialist_drafts.apply(request, prepared, secrets.token_hex(16))
        if not outcome.get("confirmed") or outcome.get("action") != "Saved a draft":
            raise HTTPException(503, "The synthetic draft could not be confirmed.")
        state["expected_snapshot"] = {
            "profile": prepared["profile"],
            "required_suites": prepared["required_suites"],
        }
        return {"synthetic": True, "saved": True, "provider_calls": 0,
                "connected_service_calls": 0}

    @app.post("/__browser_fixture/specialist-draft/arm", include_in_schema=False)
    async def arm_specialist_draft_run():
        with lock:
            state.update(armed=True, goal=None, snapshot=None, calls=0)
        return {"synthetic": True, "provider_calls": 0, "connected_service_calls": 0}

    @app.get("/__browser_fixture/specialist-draft", include_in_schema=False)
    async def specialist_draft_run_state():
        from maverick_dashboard import learning_jobs

        with lock:
            goal_id, snapshot, calls = state["goal"], state["snapshot"], state["calls"]
        goal = world.get_goal(goal_id) if goal_id else None
        jobs = [job.view() for job in learning_jobs.listed() if job.kind.startswith("specialist_draft_")]
        return {
            "synthetic": True,
            "provider_calls": 0,
            "connected_service_calls": 0,
            "worker_calls": calls,
            "goal_id": goal_id,
            "goal_status": goal.status if goal else None,
            "owner": goal.owner if goal else None,
            "domain": goal.domain if goal else None,
            "profile": snapshot.get("profile") if snapshot else None,
            "required_suites": snapshot.get("required_suites") if snapshot else [],
            "workspace": tenant,
            "jobs": jobs,
        }

    @app.post("/__browser_fixture/specialist-draft/disarm", include_in_schema=False)
    async def disarm_specialist_draft_run():
        with lock:
            state["armed"] = False
        return {"synthetic": True, "armed": False}
