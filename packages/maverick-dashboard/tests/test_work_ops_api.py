"""Work operations that used to be command-line only, now in the app.

Covers ``journal list/revert``, ``export``, ``share`` (gist), ``schedule add``,
``template add``, ``onboard``, ``ekko run``, ``plan-reflect``, the debate job,
``tax update --url`` and the Cortex deposit collection choice. Each endpoint is
checked for permission refusal, the kernel call on success (stubbed only at
the model or network boundary), confirmation and bounded-input refusals and
its audit row; each touched page for its new controls and plain wording.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from maverick import config, workspace_journal
from maverick.logging_config import reset_goal_context, set_goal_context
from maverick.paths import tenant_scope
from maverick.sandbox.local import LocalBackend
from maverick.tools.fs import write_text_contained
from maverick.world_model import WorldModel
from maverick_dashboard import rbac

TENANT = "work-ops-workspace"
TEMPLATES = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"
BANNED = re.compile(r"\b(tenant|principal|payload|endpoint|schema|UUID|SHA-256|idempotency|cron|JSON|maverick )\b")


def _wait(client, url, *, tries=200, headers=None):
    for _ in range(tries):
        data = client.get(url, headers=headers or {}).json()
        if data.get("state") != "running":
            return data
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(("MAVERICK_", "LIGHTWORK_")):
            monkeypatch.delenv(key, raising=False)
    home = tmp_path / "private"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[sandbox]\nworkdir = "{work.as_posix()}"\n')
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setenv("MAVERICK_CONFIG", str(cfg))
    monkeypatch.setenv("MAVERICK_SECURE_DEFAULT", "0")
    monkeypatch.setenv("MAVERICK_WORKSPACE_JOURNAL", "1")
    monkeypatch.setenv("MAVERICK_DOMAINS_DIR", str(tmp_path / "domains"))
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:boss")
    config.reset_config_cache()
    workspace_journal._reset_for_tests()
    rbac.set_role("user:owner", "operator")
    rbac.set_role("user:other", "operator")
    rbac.set_role("user:viewer", "viewer")
    rbac.set_role("user:boss", "admin")
    import maverick.audit as audit
    from maverick_dashboard import api as api_module
    from maverick_dashboard import app as dashboard
    from maverick_dashboard import cli_ops_api, viewpoints_api
    from maverick_dashboard.ops_jobs import JOBS
    JOBS.clear()
    dashboard._goal_times.clear()
    dashboard._goal_times_global.clear()
    world = WorldModel(tmp_path / "world.db")
    goal = world.create_goal("Tidy the quarterly notes", owner="user:owner")
    monkeypatch.setattr(dashboard, "_world", lambda: world)
    rows: list[dict] = []
    real = audit.audit_event

    def record(kind, **data):
        rows.append({"kind": kind, **data})
        return real(kind, **data)

    monkeypatch.setattr(audit, "audit_event", record)
    application = FastAPI()

    @application.middleware("http")
    async def identity(request: Request, call_next):
        name = request.headers.get("X-Test-User", "owner")
        request.state.principal = SimpleNamespace(principal="user:" + name, sub=name)
        with tenant_scope(tenant=request.headers.get("X-Test-Tenant", TENANT)):
            return await call_next(request)

    from maverick_dashboard import task_changes_api, task_file_recovery_api
    application.include_router(task_file_recovery_api.router)
    application.include_router(task_changes_api.router)
    application.include_router(api_module.router)
    application.include_router(viewpoints_api.router)
    application.include_router(cli_ops_api.router)
    with TestClient(application, headers={"Origin": "http://testserver"}) as client:
        yield SimpleNamespace(client=client, goal=goal, work=work, world=world, audit=rows, tmp=tmp_path, cfg=cfg)
    JOBS.clear()
    config.reset_config_cache()
    workspace_journal._reset_for_tests()


def _as(user):
    return {"X-Test-User": user}


# ---- journal list / undo ------------------------------------------------------

def _task_writes(env, writes):
    sandbox = LocalBackend(env.work)
    with tenant_scope(tenant=TENANT):
        tokens = set_goal_context(goal_id=env.goal)
        try:
            for name, text in writes:
                write_text_contained(sandbox, sandbox.workdir.resolve() / name, text)
        finally:
            reset_goal_context(tokens)


def _context(env):
    return env.client.get(f"/api/v1/task-files/{env.goal}/journal").json()["context"]


def test_journal_list_and_undo_restore_through_the_kernel(env):
    (env.work / "notes.txt").write_text("before\n")
    _task_writes(env, [("notes.txt", "after\n"), ("new.txt", "made by the task\n")])
    listing = env.client.get(f"/api/v1/task-files/{env.goal}/journal")
    assert listing.status_code == 200, listing.text
    data = listing.json()
    assert data["count"] == 2 and data["incomplete"] is False
    assert {(r["file"], r["change"]) for r in data["entries"]} == {("notes.txt", "existed"), ("new.txt", "created")}
    url = f"/api/v1/task-files/{env.goal}/journal/undo"
    preview = env.client.post(url, json={"context": data["context"], "preview": True}).json()
    assert preview["done"] is False and preview["restored"] == 1 and preview["removed"] == 1
    assert (env.work / "notes.txt").read_text() == "after\n", "a preview changes nothing"
    refused = env.client.post(url, json={"context": data["context"], "preview": False})
    assert refused.status_code == 409 and (env.work / "notes.txt").read_text() == "after\n"
    done = env.client.post(url, json={"context": data["context"], "preview": False, "confirm": True}).json()
    assert done["done"] is True and done["restored"] == 1 and done["removed"] == 1
    assert (env.work / "notes.txt").read_text() == "before\n" and not (env.work / "new.txt").exists()
    assert any(r["kind"] == "workspace_revert" for r in env.audit), "the kernel's signed audit row is written"


def test_journal_undo_refuses_other_people_viewers_and_forged_records(env):
    _task_writes(env, [("notes.txt", "after\n")])
    context = _context(env)
    url = f"/api/v1/task-files/{env.goal}/journal/undo"
    assert env.client.get(f"/api/v1/task-files/{env.goal}/journal", headers=_as("other")).status_code == 404
    assert env.client.post(url, json={"context": context, "preview": False, "confirm": True},
                           headers=_as("viewer")).status_code == 403
    outside = env.tmp / "outside"
    outside.mkdir()
    with tenant_scope(tenant=TENANT):
        folder = workspace_journal.goal_dir(env.goal)
    (folder / "000099.json").write_text(json.dumps({"seq": 99, "path": "evil.txt", "workdir": str(outside),
                                                    "existed": False, "blob": False}))
    refused = env.client.post(url, json={"context": context, "preview": False, "confirm": True}).json()
    assert refused["available"] is False and "outside" in refused["message"]
    assert (env.work / "notes.txt").read_text() == "after\n"


def test_journal_undo_rejects_a_stale_context_and_extra_fields(env):
    _task_writes(env, [("notes.txt", "after\n")])
    url = f"/api/v1/task-files/{env.goal}/journal/undo"
    assert env.client.post(url, json={"context": "0" * 64, "preview": True}).status_code == 409
    assert env.client.post(url, json={"context": _context(env), "preview": True, "extra": 1}).status_code == 422


# ---- export and gist share ----------------------------------------------------

def test_export_downloads_the_same_bundle_the_command_writes(env):
    from maverick.goal_bundle import build_goal_bundle

    response = env.client.get(f"/api/v1/goals/{env.goal}/export.json")
    assert response.status_code == 200
    assert 'filename="task-' in response.headers["content-disposition"]
    assert json.loads(response.text) == json.loads(json.dumps(build_goal_bundle(env.world, env.goal), default=str))
    assert env.client.get(f"/api/v1/goals/{env.goal}/export.json", headers=_as("other")).status_code == 404


def test_gist_share_needs_confirmation_operate_and_audits_first(env, monkeypatch):
    import maverick.run_share as run_share

    calls = []
    monkeypatch.setattr(run_share, "share_run", lambda gid: calls.append(gid) or "https://gist.github.com/x/1")
    url = f"/api/v1/goals/{env.goal}/share/gist"
    assert env.client.post(url, json={"confirm": False}).status_code == 409
    assert env.client.post(url, json={"confirm": True}, headers=_as("viewer")).status_code == 403
    assert env.client.post(url, json={"confirm": True}, headers=_as("other")).status_code == 404
    assert calls == []
    data = env.client.post(url, json={"confirm": True}).json()
    assert data == {"shared": True, "url": "https://gist.github.com/x/1"} and calls == [env.goal]
    assert [r["phase"] for r in env.audit if r["kind"] == "goal_shared_gist"] == ["start", "done"]
    monkeypatch.setattr(run_share, "share_run", lambda gid: (_ for _ in ()).throw(RuntimeError("no GitHub token")))
    assert "isn't connected" in env.client.post(url, json={"confirm": True}).json()["message"]


# ---- schedule add ------------------------------------------------------------

def _timetable(**extra):
    return {"repeat": "weekdays", "at": "08:30", "zone": "America/New_York", **extra}


def test_schedule_preview_builds_the_timetable_on_the_server(env):
    data = env.client.post("/api/v1/schedules/preview", json={"timetable": _timetable()}).json()
    assert data["cron"] == "30 8 * * 1-5" and data["zone"] == "America/New_York" and len(data["next_runs"]) == 3
    assert env.client.post("/api/v1/schedules/preview", json={"timetable": _timetable(zone="Mars/Base")}).status_code == 400
    bad = env.client.post("/api/v1/schedules/preview", json={"timetable": _timetable(advanced="61 * * * *")})
    assert bad.status_code == 400
    custom = env.client.post("/api/v1/schedules/preview", json={"timetable": _timetable(advanced="0  7 * * 0")}).json()
    assert custom["cron"] == "0 7 * * 0"
    assert env.client.post("/api/v1/schedules/preview", json={"timetable": _timetable()},
                           headers=_as("viewer")).status_code == 403


def test_schedule_add_task_and_rerun_carry_zone_owner_and_audit(env):
    from maverick.job_queue import JobQueue

    made = env.client.post("/api/v1/schedules/add", json={"what": "task", "text": "Summarise overnight email",
                                                          "timetable": _timetable()})
    assert made.status_code == 201, made.text
    assert made.json()["zone"] == "America/New_York" and made.json()["cron"] == "30 8 * * 1-5"
    rerun = env.client.post("/api/v1/schedules/add", json={"what": "rerun", "goal_id": env.goal,
                                                           "timetable": {"repeat": "monthly", "day_of_month": 3,
                                                                         "at": "07:00", "zone": "UTC"}})
    assert rerun.status_code == 201 and rerun.json()["kind"] == "run_goal"
    with tenant_scope(tenant=TENANT):
        jobs = {j.kind: j for j in JobQueue().list(status="pending")}
    assert jobs["start_goal"].payload["__tz__"] == "America/New_York" and jobs["start_goal"].payload["owner"] == "user:owner"
    assert jobs["run_goal"].payload["goal_id"] == env.goal and jobs["run_goal"].payload["__cron__"] == "0 7 3 * *"
    assert [r["what"] for r in env.audit if r["kind"] == "schedule_created"] == ["task", "rerun"]
    listed = env.client.get("/api/v1/schedules").json()["schedules"]
    assert {s["zone"] for s in listed} == {"America/New_York", "UTC"}


def test_schedule_add_refuses_foreign_tasks_viewers_and_unbounded_input(env):
    assert env.client.post("/api/v1/schedules/add", json={"what": "rerun", "goal_id": env.goal, "timetable": _timetable()},
                           headers=_as("other")).status_code == 404
    assert env.client.post("/api/v1/schedules/add", json={"what": "task", "text": "x", "timetable": _timetable()},
                           headers=_as("viewer")).status_code == 403
    assert env.client.post("/api/v1/schedules/add", json={"what": "task", "text": "x" * 16_001,
                                                          "timetable": _timetable()}).status_code == 422
    assert env.client.post("/api/v1/schedules/add", json={"what": "task", "text": "x",
                                                          "timetable": _timetable(days=list(range(8)))}).status_code == 422


# ---- community templates -----------------------------------------------------

def test_community_templates_list_and_admin_only_install(env, monkeypatch):
    import maverick.templates as templates

    entry = SimpleNamespace(name="weekly-digest", summary="A weekly summary", rating=4.5, ratings_count=2)
    monkeypatch.setattr(templates, "browse_templates", lambda: [entry, SimpleNamespace(name="../evil", summary="")])
    installed = []
    monkeypatch.setattr(templates, "install_template_from_catalog",
                        lambda name: installed.append(name) or SimpleNamespace(name=name, title="Weekly digest"))
    listing = env.client.get("/api/v1/templates/community").json()
    assert [t["name"] for t in listing["templates"]] == ["weekly-digest"] and listing["can_add"] is False
    url = "/api/v1/templates/community/add"
    assert env.client.post(url, json={"name": "weekly-digest", "confirm": True}).status_code == 403
    assert env.client.post(url, json={"name": "weekly-digest"}, headers=_as("boss")).status_code == 409
    assert env.client.post(url, json={"name": "../evil", "confirm": True}, headers=_as("boss")).status_code == 422
    assert installed == []
    added = env.client.post(url, json={"name": "weekly-digest", "confirm": True}, headers=_as("boss")).json()
    assert added["added"] is True and installed == ["weekly-digest"]
    assert any(r["kind"] == "template_added_from_library" for r in env.audit)
    monkeypatch.setattr(templates, "install_template_from_catalog",
                        lambda name: (_ for _ in ()).throw(ValueError("content hash mismatch")))
    assert "genuine" in env.client.post(url, json={"name": "weekly-digest", "confirm": True},
                                         headers=_as("boss")).json()["message"]


# ---- business specialist setup (onboard) ---------------------------------------

def test_business_setup_drafts_then_saves_only_after_confirmation(env, monkeypatch):
    base = "/api/v1/agents/business-setup"
    body = {"name": "Acme Bakery", "description": "We bake bread for cafes", "use_ai": False}
    assert env.client.post(base + "/draft", json=body, headers=_as("owner")).status_code == 403
    started = env.client.post(base + "/draft", json=body, headers=_as("boss"))
    assert started.status_code == 202, started.text
    job = started.json()["job"]
    draft = _wait(env.client, f"{base}/{job}?step=draft", headers=_as("boss"))
    # the client sees the plain draft; the pack itself stays on the server
    client_view = draft["result"]
    assert draft["state"] == "done" and client_view["ran"] is True and "_profile" not in client_view
    assert client_view["agent"]["name"] and not list((env.tmp / "domains").glob("*.toml"))
    assert env.client.post(f"{base}/{job}/approve", data={"confirm": "false"}, headers=_as("boss")).status_code == 409
    approve = env.client.post(f"{base}/{job}/approve", data={"confirm": "true"}, headers=_as("boss"))
    assert approve.status_code == 202, approve.text
    saved = _wait(env.client, f"{base}/{approve.json()['job']}?step=approve", headers=_as("boss"))["result"]
    assert saved["saved"] is True and list((env.tmp / "domains").glob("*.toml"))
    assert any(r["kind"] == "business_agent_approved" for r in env.audit)
    again = env.client.post(f"{base}/{job}/approve", data={"confirm": "true"}, headers=_as("boss"))
    assert again.status_code == 404, "a draft is approved at most once"


def test_business_setup_bounds_documents(env):
    base = "/api/v1/agents/business-setup"
    job = env.client.post(base + "/draft", json={"name": "Acme", "use_ai": False}, headers=_as("boss")).json()["job"]
    _wait(env.client, f"{base}/{job}?step=draft", headers=_as("boss"))
    files = [("documents", (f"d{i}.txt", b"x", "text/plain")) for i in range(11)]
    assert env.client.post(f"{base}/{job}/approve", data={"confirm": "true"}, files=files,
                           headers=_as("boss")).status_code == 413


# ---- Ekko recording ------------------------------------------------------------

def test_ekko_recording_needs_consent_enrollment_and_guided_capture(env):
    body = {"events": json.dumps({"app": "excel", "action": "open"}) + "\n", "acknowledge_capture_scope": False}
    assert env.client.post("/api/v1/ekko/recording/import", json=body).status_code == 400
    body["acknowledge_capture_scope"] = True
    assert env.client.post("/api/v1/ekko/recording/import", json=body).status_code == 409  # Ekko is off
    assert env.client.post("/api/v1/ekko/recording/import", json=body, headers=_as("viewer")).status_code == 403
    too_big = {**body, "events": "x" * 1_000_001}
    assert env.client.post("/api/v1/ekko/recording/import", json=too_big).status_code == 422
    options = env.client.get("/api/v1/ekko/recording/options").json()
    assert options["activity_file"] is True and options["windows_active_app"] is (os.name == "nt")
    if os.name != "nt":
        watch = env.client.post("/api/v1/ekko/recording/watch", json={"acknowledge_capture_scope": True})
        assert watch.status_code == 409 and "Windows" in watch.json()["detail"]


def test_ekko_recording_runs_the_daemon_over_the_uploaded_file(env, monkeypatch):
    import maverick.ekko_daemon as daemon_module
    from maverick_dashboard import ekko_routes

    policy = SimpleNamespace(capture_level="guided", fingerprint=lambda: "f" * 64)
    ran = {}

    class FakeDaemon:
        def __init__(self, *, store, observer, policy):
            ran["observer"] = observer
            self.accepted_events, self.blocked_events = 0, 0

        def run(self, *, stop_when):
            while ran["observer"].observe() is not None:
                self.accepted_events += 1
            assert stop_when()

        def stop(self):
            ran["stopped"] = True

    monkeypatch.setattr(ekko_routes, "_require_enabled_policy", lambda: {"enable": True})
    monkeypatch.setattr(ekko_routes, "_validated_enrolled_policy", lambda store: policy)
    monkeypatch.setattr(daemon_module, "EkkoDaemon", FakeDaemon)
    lines = "\n".join(json.dumps({"app": "excel", "action": "open"}) for _ in range(3)) + "\n"
    started = env.client.post("/api/v1/ekko/recording/import", json={"events": lines, "acknowledge_capture_scope": True})
    assert started.status_code == 202, started.text
    result = _wait(env.client, "/api/v1/ekko/recording/" + started.json()["job"])["result"]
    assert result == {"recorded": 3, "skipped": 0} and ran["stopped"] is True
    assert isinstance(ran["observer"], daemon_module.GuidedJsonlObserver)
    assert any(r.get("state") == "start_authorized" and r.get("observer") == "guided_jsonl" for r in env.audit)
    other = env.client.get("/api/v1/ekko/recording/" + started.json()["job"], headers=_as("other"))
    assert other.status_code == 404, "a recording's status is private to its owner"


# ---- planning steps (plan-reflect) and the debate job ------------------------------

class FakeLLM:
    def __init__(self, model=None):
        self.model = model

    def complete(self, *, system, messages, budget=None, **_):
        budget.check()
        budget.record_tokens(100, 40)
        if "planner" in system:
            return SimpleNamespace(text='{"steps": ["Read the notes", "Write the summary"]}')
        if "reflector" in system:
            return SimpleNamespace(text='{"status": "done", "notes": "Looks complete"}')
        if "impartial judge" in system:
            return SimpleNamespace(text='{"winner": "Proponent", "reason": "Clearer", "key_argument": "Time"}')
        return SimpleNamespace(text="Did: " + messages[0]["content"][:30])


@pytest.fixture
def ai(env, monkeypatch):
    import maverick.llm as llm
    from maverick_dashboard import _shared, viewpoints_api

    roles = []
    monkeypatch.setattr(llm, "LLM", FakeLLM)
    monkeypatch.setattr(llm, "model_for_role", lambda role: roles.append(role) or "anthropic:configured")
    monkeypatch.setattr(_shared, "require_provider_or_400", lambda role=None: None)
    monkeypatch.setattr(viewpoints_api, "require_provider_or_400", lambda role=None: None)
    return roles


def test_planning_steps_trace_uses_the_orchestrator_role_and_changes_nothing(env, ai):
    url = f"/api/v1/goals/{env.goal}/planning-steps"
    assert env.client.post(url, json={"passes": 1}, headers=_as("viewer")).status_code == 403
    assert env.client.post(url, json={"passes": 1}, headers=_as("other")).status_code == 404
    assert env.client.post(url, json={"passes": 9}).status_code == 422
    started = env.client.post(url, json={"passes": 1})
    assert started.status_code == 202, started.text
    result = _wait(env.client, f"{url}/{started.json()['job']}")["result"]
    assert result["ran"] is True and result["plan"] == ["Read the notes", "Write the summary"]
    assert result["status"] == "done" and len(result["steps"]) == 2 and result["cost"] > 0
    assert ai == ["orchestrator"]
    assert [r["phase"] for r in env.audit if r["kind"] == "planning_steps_run"] == ["start", "end"]
    assert env.world.get_goal(env.goal).status != "running"


def test_viewpoints_job_runs_with_chosen_sides(env, ai):
    started = env.client.post("/api/v1/viewpoints/jobs", json={"question": "Should we open on Sundays?", "rounds": 1,
                                                               "for_side": "Sunday trade is worth it"})
    assert started.status_code == 202, started.text
    result = _wait(env.client, "/api/v1/viewpoints/jobs/" + started.json()["job"])["result"]
    assert result["ran"] is True and result["verdict"] == "for" and ai == ["analyst"]
    assert env.client.get("/api/v1/viewpoints/jobs/" + started.json()["job"], headers=_as("other")).status_code == 404
    assert env.client.post("/api/v1/viewpoints/jobs", json={"question": "Why?", "for_side": "x" * 301}).status_code == 422


# ---- pages ------------------------------------------------------------------------

@pytest.mark.parametrize("template, needles", [
    ("task_file_recovery.html", ["tj-list", "tj-preview", "tj-undo", "lightwork-task-journal.js"]),
    ("trajectory.html", ["export.json", "goal-gist", "lightwork-goal-export.js"]),
    ("automations.html", ["auto-new-sched", "auto-new-zone", "Technical details", "lightwork-schedule-picker.js"]),
    ("templates_market.html", ["tplm-library-load", "lightwork-community-templates.js"]),
    ("agents.html", ["biz-draft", "biz-approve", "lightwork-business-agent.js"]),
    ("ekko.html", ["ekko-rec-import", "ekko-rec-watch", "lightwork-ekko-recording.js"]),
    ("chat_goal.html", ["ps-run", "Show planning steps", "lightwork-planning-steps.js"]),
    ("chat.html", ["vp-for", "vp-against"]),
    ("cortex.html", ["fa-cortex-collection"]),
    ("finance.html", ["tax-tables-url", "tax-tables-check-url"]),
    ("flow_teach.html", ["tr-learn", "rc-learn", "sg-learn", "lightwork-flow-teach.js"]),
])
def test_pages_offer_the_new_controls_in_plain_words(template, needles):
    text = (TEMPLATES / template).read_text()
    for needle in needles:
        assert needle in text
    for added in re.findall(r'<(?:section|details|div)[^>]*id="(?:task-journal|goal-export|auto-new-sched|tplm-library|'
                            r'biz-setup|ekko-recording|planning-steps)".*?</(?:section|details)>', text, re.S):
        visible = re.sub(r"<[^>]+>|\{\{.*?\}\}|\{%.*?%\}", " ", added)
        assert not BANNED.search(visible), BANNED.search(visible)


def test_scripts_are_served(env):
    from maverick_dashboard.cli_ops_api import ASSETS

    for name in ASSETS:
        response = env.client.get("/static/" + name)
        assert response.status_code == 200 and "javascript" in response.headers["content-type"]
