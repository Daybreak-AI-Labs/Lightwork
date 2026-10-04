"""Customer-owned answer checks use current specialist instructions and real run receipts."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/workforce-ops/test-suite"


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app
    from maverick_dashboard import learning_jobs

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(audit_writer, "_default", None)
    audit_writer._defaults.clear()
    config.reset_config_cache()
    dash_app._world_cache.clear()
    learning_jobs.clear_for_tests()
    yield TestClient(dash_app.app, headers={"Origin": "http://testserver"}), tmp_path
    learning_jobs.clear_for_tests()
    config.reset_config_cache()
    dash_app._world_cache.clear()
    audit_writer._defaults.clear()


@pytest.fixture
def audit_rows(monkeypatch):
    import maverick.audit as audit

    rows = []
    monkeypatch.setattr(audit, "audit_event", lambda kind, **payload: rows.append(payload) or True)
    return rows


def _case(specialist="finance_ap", **extra):
    row = {"specialist": specialist,
           "task": "Two invoices have the same purchase order. What would you do?",
           "includes": ["duplicate"], "excludes": ["payment sent"],
           "refusal": False, "citation": False}
    row.update(extra)
    return row


def _wait(client, response):
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    deadline = time.time() + 15
    while job["state"] in {"running", "stopping"}:
        assert time.time() < deadline, job
        time.sleep(.05)
        job = client.get("/api/v1/learning-ops/jobs/" + job["id"]).json()["job"]
    return job


def _revisions(app, specialist="finance_ap"):
    rows = app.get(API + "/customer", params={"specialist": specialist}).json()["cases"]
    return {row["id"]: row["revision"] for row in rows}


def test_cases_are_saved_scoped_editable_and_revision_guarded(client, monkeypatch, audit_rows):
    app, _tmp = client
    from maverick_dashboard import auth, rbac

    who = "user:worker"
    monkeypatch.setattr(auth, "caller_principal", lambda request: who)
    rbac.set_role(who, "operator")
    assert app.post(API + "/customer", json=_case()).status_code == 403
    rbac.set_role(who, "admin")
    assert app.post(API + "/customer", json=_case(includes=[], excludes=[])).status_code == 422
    created = app.post(API + "/customer", json=_case())
    assert created.status_code == 200, created.text
    row = created.json()["case"]
    listed = app.get(API + "/customer", params={"specialist": "finance_ap"}).json()
    assert listed["cases"] == [row] and listed["live_enabled"] is False
    assert app.get(API + "/customer", params={"specialist": "not_real"}).status_code == 404
    update = _case(identity=row["id"], expected_revision=1)
    update["task"] = "Two invoices are identical. Should either be paid?"
    changed = app.post(API + "/customer", json=update).json()["case"]
    assert changed["revision"] == 2
    assert app.post(API + "/customer", json=update).status_code == 409
    assert app.post(API + "/customer/remove", json={"specialist": "finance_ap", "identity": row["id"],
                                                    "expected_revision": 1}).status_code == 409
    removed = app.post(API + "/customer/remove", json={"specialist": "finance_ap", "identity": row["id"],
                                                      "expected_revision": 2})
    assert removed.json() == {"removed": True}
    assert app.get(API + "/customer", params={"specialist": "finance_ap"}).json()["cases"] == []
    assert [x["verb"] for x in audit_rows if x.get("verb", "").startswith("specialist_test_")] == [
        "specialist_test_save", "specialist_test_save", "specialist_test_save",
        "specialist_test_remove", "specialist_test_remove"]


def test_live_batch_uses_current_instructions_no_tools_and_durable_results(client, monkeypatch, audit_rows):
    app, tmp = client
    from maverick import config, llm

    (tmp / "config.toml").write_text("[models]\ncapability_tests = true\n", encoding="utf-8")
    config.reset_config_cache()
    one = app.post(API + "/customer", json=_case()).json()["case"]
    two = app.post(API + "/customer", json=_case(task="Another invoice could be duplicate. What next?"))
    assert two.status_code == 200
    calls = []

    def answer(self, system, messages, **kwargs):
        calls.append((system, messages, kwargs))
        assert kwargs["tools"] is None and kwargs["max_physical_attempts"] == 1
        assert kwargs["_no_failover"] is True and kwargs["max_tokens"] == 128
        assert kwargs["budget"].max_dollars <= .25
        kwargs["dispatch_guard"]()
        return llm.LLMResponse(text="This is a duplicate; no payment sent.", thinking=None,
                               tool_calls=[], stop_reason="end_turn")

    monkeypatch.setattr(llm.LLM, "complete", answer)
    request_id = "d" * 32
    job = _wait(app, app.post(API + "/customer/run", json={"specialist": "finance_ap",
                                                        "operation_id": request_id,
                                                        "case_revisions": _revisions(app)}))
    assert job["state"] == "done", job
    run = job["result"]["run"]
    assert len(calls) == 2 and all("No tools, files" in system for system, _, _ in calls)
    assert run["total"] == 2 and run["passed"] == 0
    assert all(r["checks"]["includes"] == 1 and r["checks"]["excludes"] == 0 for r in run["results"])
    assert run["case_revisions"][0] == {"id": one["id"], "revision": 1}
    history = app.get(API + "/customer", params={"specialist": "finance_ap"}).json()["runs"]
    assert history[0]["id"] == run["id"] and history[0]["results"] == run["results"]
    original = app.get(API + "/customer/run/" + request_id)
    assert original.status_code == 200 and original.json()["run"]["id"] == run["id"]
    replay = app.post(API + "/customer/run", json={"specialist": "finance_ap",
                                                 "operation_id": request_id,
                                                 "case_revisions": _revisions(app)})
    assert replay.json()["replayed"] is True and len(calls) == 2
    assert audit_rows[-1]["verb"] == "specialist_test_run"
    assert "task" not in audit_rows[-1]


def test_failed_provider_call_is_unknown_and_never_replayed(client, monkeypatch):
    app, tmp = client
    from maverick import config, llm

    (tmp / "config.toml").write_text("[models]\ncapability_tests = true\n", encoding="utf-8")
    config.reset_config_cache()
    app.post(API + "/customer", json=_case())
    attempts = []

    def fail(self, *args, **kwargs):
        attempts.append(True)
        raise TimeoutError("provider response uncertain")

    monkeypatch.setattr(llm.LLM, "complete", fail)
    job = _wait(app, app.post(API + "/customer/run", json={"specialist": "finance_ap",
                                                        "operation_id": "e" * 32,
                                                        "case_revisions": _revisions(app)}))
    run = job["result"]["run"]
    assert attempts == [True]
    assert run["state"] == "unknown" and run["passed"] == 0
    assert "may have been billed" in run["message"]
    readback = app.get(API + "/customer/run/" + "e" * 32).json()["run"]
    assert readback["state"] == "unknown"
    replay = app.post(API + "/customer/run", json={"specialist": "finance_ap", "operation_id": "e" * 32,
                                                 "case_revisions": _revisions(app)})
    assert replay.json()["replayed"] and attempts == [True]


def test_large_saved_set_runs_only_the_selected_cases(client, monkeypatch):
    app, tmp = client
    from maverick import config, llm

    (tmp / "config.toml").write_text("[models]\ncapability_tests = true\n", encoding="utf-8")
    config.reset_config_cache()
    ids = []
    for n in range(6):
        row = app.post(API + "/customer", json=_case(task=f"Invoice example {n}: what should be checked first?"))
        assert row.status_code == 200, row.text
        ids.append(row.json()["case"]["id"])
    assert app.post(API + "/customer/run", json={"specialist": "finance_ap",
                                                    "operation_id": "f" * 32,
                                                    "case_revisions": dict(list(_revisions(app).items())[:5])}).status_code == 409
    calls = []

    def answer(self, system, messages, **kwargs):
        calls.append(messages[0]["content"])
        return llm.LLMResponse(text="Check for duplicate before approval.", thinking=None,
                               tool_calls=[], stop_reason="end_turn")

    monkeypatch.setattr(llm.LLM, "complete", answer)
    stale = app.post(API + "/customer/run", json={"specialist": "finance_ap",
                                                   "operation_id": "a" * 32, "case_ids": ids[1:3],
                                                   "case_revisions": {ids[1]: 1, ids[2]: 2}})
    assert stale.status_code == 409 and calls == []
    job = _wait(app, app.post(API + "/customer/run", json={"specialist": "finance_ap",
                                                        "operation_id": "a" * 32, "case_ids": ids[1:3],
                                                        "case_revisions": {ids[1]: 1, ids[2]: 1}}))
    assert job["result"]["run"]["total"] == 2
    assert calls == ["Invoice example 1: what should be checked first?",
                     "Invoice example 2: what should be checked first?"]


def test_copilot_reads_creates_and_starts_only_one_reviewed_run(client, monkeypatch, audit_rows):
    _app, tmp = client
    from maverick import config
    from maverick_dashboard import auth
    from maverick_dashboard import product_operations as ops
    from maverick_dashboard import product_operations_specialist_tests as actions
    from maverick_dashboard import specialist_test_sets as saved
    from starlette.requests import Request

    (tmp / "config.toml").write_text("[models]\ncapability_tests = true\n", encoding="utf-8")
    config.reset_config_cache()
    monkeypatch.setattr(actions, "context_for", lambda request: "current-workspace")
    monkeypatch.setattr(auth, "require_permission", lambda request, permission: None)
    request = Request({"type": "http", "method": "POST", "path": "/workforce",
                       "headers": [(b"host", b"testserver")]})
    assert {"specialists.tests.read", "specialists.tests.create", "specialists.tests.run"} <= set(ops.adapters())
    args = {"specialist": "finance_ap", "task": "Two invoices have the same amount. What should happen?",
            "must_mention": "duplicate", "must_never_say": "payment sent",
            "must_refuse": "no", "must_cite": "no"}
    review = actions.create_prepare(request, {}, args)
    assert "Nothing is sent" in review.summary
    created = actions.create_execute(request, {}, args, review.native, "b" * 32)
    replay = actions.create_recover(request, {}, args, review.native, "b" * 32)
    assert created.status == "confirmed" and replay.status == "replayed"
    assert len(saved.cases_for("finance_ap")) == 1
    read = actions.read_prepare(request, {}, {"specialist": "finance_ap"})
    assert len(read.native["cases"]) == 1 and "duplicate" in read.native["cases"][0]["must_mention"]
    run_review = actions.run_prepare(request, {}, {"specialist": "finance_ap"})
    calls = []

    def started(_request, specialist, *, operation_id):
        calls.append((specialist, operation_id))
        saved.begin_run(specialist, run_review.native["model"], run_review.native["instructions"],
                        saved.snapshot(specialist), "user:reviewer", identity=operation_id)
        return {"job": {"id": "job-1", "state": "running"}}

    monkeypatch.setattr(actions.native, "_start_customer_test_job", started)
    first = actions.run_execute(request, {}, {"specialist": "finance_ap"}, run_review.native, "c" * 32)
    found = actions.run_recover(request, {}, {"specialist": "finance_ap"}, run_review.native, "c" * 32)
    assert first.status == "queued" and found.status == "replayed"
    assert calls == [("finance_ap", "c" * 32)]
    assert [row["verb"] for row in audit_rows if row.get("verb") == "specialist_test_save"] == ["specialist_test_save"]
