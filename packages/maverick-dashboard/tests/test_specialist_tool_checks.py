"""Saved answer checks that say which tools a specialist calls, run against recording stubs only."""
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


@pytest.fixture
def no_real_tools(monkeypatch):
    """Any touch of the real tool registry, its sandbox or a subprocess fails the test."""
    import subprocess

    import maverick.tools as tools

    def boom(*_args, **_kwargs):
        raise AssertionError("a real tool or process was reached during a tool check")

    monkeypatch.setattr(tools.ToolRegistry, "run", boom)
    monkeypatch.setattr(tools.ToolRegistry, "get", boom)
    monkeypatch.setattr(tools, "base_registry", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    return boom


def _case(specialist="finance_ap", **extra):
    row = {"specialist": specialist,
           "task": "Two invoices have the same purchase order. What would you do?",
           "includes": ["duplicate"], "excludes": ["payment sent"],
           "refusal": False, "citation": False}
    row.update(extra)
    return row


def _tool_case(**extra):
    row = _case(with_tools=True, tools_called=[{"name": "send_email", "contains": "Acme"}],
                tools_not_called=["delete_record"], tool_results=[])
    row.update(extra)
    return row


def _knob(tmp, text):
    from maverick import config

    (tmp / "config.toml").write_text(text, encoding="utf-8")
    config.reset_config_cache()


def _wait(client, response):
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    deadline = time.time() + 15
    while job["state"] in {"admitted", "running", "stopping"}:
        assert time.time() < deadline, job
        time.sleep(.05)
        job = client.get("/api/v1/learning-ops/jobs/" + job["id"]).json()["job"]
    return job


def _revisions(app, specialist="finance_ap"):
    rows = app.get(API + "/customer", params={"specialist": specialist}).json()["cases"]
    return {row["id"]: row["revision"] for row in rows}


def _scripted_llm(monkeypatch, script):
    """Replay ``script`` (LLMResponse objects) and record every provider call."""
    from maverick import llm

    calls = []

    def answer(self, system, messages, **kwargs):
        calls.append({"system": system, "messages": [dict(m) for m in messages], "kwargs": kwargs})
        return script.pop(0) if script else llm.LLMResponse(
            text="FINAL: (script exhausted)", thinking=None, tool_calls=[], stop_reason="end_turn")

    monkeypatch.setattr(llm.LLM, "complete", answer)
    return calls


def _use(name, args, call_id="call-1"):
    from maverick import llm

    return llm.LLMResponse(text="", thinking=None, stop_reason="tool_use",
                           tool_calls=[llm.ToolCall(id=call_id, name=name, input=args)])


def _say(text):
    from maverick import llm

    return llm.LLMResponse(text=text, thinking=None, tool_calls=[], stop_reason="end_turn")


# ---- pure checks ----------------------------------------------------------------

def test_a_tool_check_cannot_be_created_while_the_switch_is_off():
    from maverick_dashboard import specialist_tool_checks as tools

    with pytest.raises(ValueError, match="turned off"):
        tools.normalized(enabled=False, with_tools=True, called=[{"name": "send_email", "contains": ""}],
                         not_called=[], results=[])
    # A check without tools keeps today's shape whatever the switch says.
    plain = tools.normalized(enabled=False, with_tools=False, called=[], not_called=[], results=[])
    assert plain == {"with_tools": False, "tools_called": [], "tools_not_called": [], "tool_results": []}


def test_tool_fields_reject_empty_contradictory_and_malformed_entries():
    from maverick_dashboard import specialist_tool_checks as tools

    def build(**kwargs):
        base = {"enabled": True, "with_tools": True, "called": [], "not_called": [], "results": []}
        base.update(kwargs)
        return tools.normalized(**base)

    with pytest.raises(ValueError, match="at least one tool"):
        build()
    with pytest.raises(ValueError, match="both required and forbidden"):
        build(called=[{"name": "send_email", "contains": ""}], not_called=["send_email"])
    with pytest.raises(ValueError, match="tool name"):
        build(not_called=["rm -rf /"])
    with pytest.raises(ValueError, match="canned result"):
        build(not_called=["send_email"], results=[{"name": "send_email", "text": "   "}])
    with pytest.raises(ValueError, match="tool lines|Turn on"):
        tools.normalized(enabled=True, with_tools=False, called=[{"name": "x", "contains": ""}],
                         not_called=[], results=[])
    clean = build(called=[{"name": " send_email ", "contains": " Acme "}], not_called=["delete_record"],
                  results=[{"name": "send_email", "text": "Sent."}])
    assert clean["tools_called"] == [{"name": "send_email", "contains": "Acme"}]
    assert clean["tools_not_called"] == ["delete_record"]
    assert clean["tool_results"] == [{"name": "send_email", "text": "Sent."}]


def test_each_expectation_passes_only_for_the_right_call_with_the_text():
    from maverick_dashboard import specialist_tool_checks as tools

    case = _tool_case()
    right = [{"name": "send_email", "arguments": '{"to": "Acme"}', "answer": "canned"}]
    results = tools.tool_check_results(case, right)
    assert [r["passed"] for r in results] == [True, True]
    assert "Called send_email" in results[0]["result"] and "Did not call delete_record" in results[1]["result"]

    wrong_text = [{"name": "send_email", "arguments": '{"to": "Other"}', "answer": "canned"}]
    results = tools.tool_check_results(case, wrong_text)
    assert results[0]["passed"] is False and "no call included" in results[0]["result"]

    forbidden = right + [{"name": "delete_record", "arguments": "{}", "answer": "not_available"}]
    results = tools.tool_check_results(case, forbidden)
    assert results[1]["passed"] is False and "must not call" in results[1]["result"]

    missing = tools.tool_check_results(case, [])
    assert missing[0]["passed"] is False and "Did not call send_email" in missing[0]["result"]


def test_the_stubs_record_each_call_and_answer_with_the_canned_text_or_a_fixed_refusal():
    from maverick_dashboard import specialist_tool_checks as tools

    stubs = tools.ToolStubs(["lookup_invoice", "send_email"], {"lookup_invoice": "Invoice 77 is a duplicate."})
    assert stubs.answer("lookup_invoice", {"id": 77}) == "Invoice 77 is a duplicate."
    assert stubs.answer("send_email", {"to": "Acme"}) == tools._NOT_AVAILABLE
    assert stubs.answer("format_disk", {"path": "/"}) == tools._NOT_AVAILABLE
    assert [c["answer"] for c in stubs.calls] == ["canned", "not_available", "not_offered"]
    assert stubs.calls[0]["arguments"] == '{"id": 77}'
    stubs.answer("send_email", {"blob": "a" * 5000})
    assert len(stubs.calls[-1]["arguments"]) <= tools._RECORD_LIMIT
    assert len(tools._bounded("y" * 5000, tools._RECORD_LIMIT)) <= tools._RECORD_LIMIT


def test_offered_tools_are_the_named_ones_plus_the_allowed_ones_minus_the_denied():
    from maverick_dashboard import specialist_tool_checks as tools

    case = _tool_case(tool_results=[{"name": "lookup_invoice", "text": "Found."}])
    offered = tools.offered_tools(case, allow=["summarize", "delete_record"], deny=["delete_record", "shell"])
    assert offered[:3] == ["send_email", "delete_record", "lookup_invoice"]  # named checks always stay offered
    assert "summarize" in offered and "shell" not in offered
    assert len(offered) <= tools.MAX_TOOLS_OFFERED


def test_grading_combines_the_answer_with_the_tool_checks():
    from maverick_dashboard import specialist_test_sets as saved

    case = _tool_case(id="c" * 32, revision=1, task="Two invoices are duplicates. What now?",
                      includes=["duplicate"], excludes=["payment sent"], refusal=False, citation=False)
    good_calls = [{"name": "send_email", "arguments": '{"to": "Acme"}', "answer": "canned"}]
    passed = saved.grade(case, "This is a duplicate; I emailed Acme.", tool_calls=good_calls)
    assert passed["passed"] is True and [c["passed"] for c in passed["tool_checks"]] == [True, True]
    assert passed["tool_calls"] == good_calls
    # A good answer with a missing call still fails.
    failed = saved.grade(case, "This is a duplicate; I emailed Acme.", tool_calls=[])
    assert failed["passed"] is False and failed["checks"]["includes"] == 1
    # Cases without tools grade exactly as before.
    plain = saved.grade({**case, "with_tools": False}, "This is a duplicate.")
    assert "tool_checks" not in plain and plain["passed"] is True


# ---- the answer run ----------------------------------------------------------------

def test_the_check_calls_the_expected_tool_with_the_text_and_no_real_tool_runs(
        client, monkeypatch, audit_rows, no_real_tools):
    app, tmp = client
    _knob(tmp, "[models]\ncapability_tests = true\n[agent_tests]\ntool_checks = true\n")
    created = app.post(API + "/customer", json={**_tool_case(), "with_tools": True,
                                              "tools_called": [{"name": "send_email", "contains": "Acme"}],
                                              "tools_not_called": ["delete_record"],
                                              "tool_results": [{"name": "lookup_invoice",
                                                                "text": "Invoice 77 is a duplicate."}]})
    assert created.status_code == 200, created.text
    assert created.json()["case"]["with_tools"] is True
    calls = _scripted_llm(monkeypatch, [
        _use("lookup_invoice", {"id": 77}, "call-a"),
        _use("send_email", {"to": "Acme", "body": "Duplicate invoice, no payment sent."}, "call-b"),
        _say("This is a duplicate; nothing was paid. I emailed Acme."),
    ])
    job = _wait(app, app.post(API + "/customer/run", json={"specialist": "finance_ap",
                                                        "operation_id": "e" * 32,
                                                        "case_revisions": _revisions(app)}))
    assert job["state"] == "done", job
    run = job["result"]["run"]
    assert run["state"] == "done" and run["passed"] == 1 and run["total"] == 1
    result = run["results"][0]
    assert result["passed"] is True
    assert [c["passed"] for c in result["tool_checks"]] == [True, True]
    assert [c["answer"] for c in result["tool_calls"]] == ["canned", "not_available"]
    # The model was offered stubs only: the named tools plus the pack's allowed tools, never the denied ones.
    offered = {tool["name"] for tool in calls[0]["kwargs"]["tools"]}
    assert {"send_email", "delete_record", "lookup_invoice"} <= offered
    assert "release_payment" not in offered and "shell" not in offered  # the pack's denied tools
    assert all(tool["input_schema"] == {"type": "object", "additionalProperties": True}
               for tool in calls[0]["kwargs"]["tools"])
    # The canned answer went back to the model as the tool result.
    tool_results = calls[1]["messages"][-1]["content"]
    assert tool_results[0]["content"] == "Invoice 77 is a duplicate."
    assert calls[0]["kwargs"]["_no_failover"] is True
    run_audit = [row for row in audit_rows if row.get("verb") == "specialist_test_run"]
    assert run_audit and run_audit[-1]["tool_checks"] == 1 and "task" not in run_audit[-1]


def test_a_forbidden_call_or_a_missing_expected_call_fails_the_check(client, monkeypatch, no_real_tools):
    app, tmp = client
    _knob(tmp, "[models]\ncapability_tests = true\n[agent_tests]\ntool_checks = true\n")
    assert app.post(API + "/customer", json={**_tool_case(), "with_tools": True,
                                            "tools_called": [{"name": "send_email", "contains": ""}],
                                            "tools_not_called": ["delete_record"]}).status_code == 200
    _scripted_llm(monkeypatch, [_use("delete_record", {"id": 1}), _say("This is a duplicate; nothing was paid.")])
    job = _wait(app, app.post(API + "/customer/run", json={"specialist": "finance_ap",
                                                        "operation_id": "f" * 32,
                                                        "case_revisions": _revisions(app)}))
    result = job["result"]["run"]["results"][0]
    assert result["passed"] is False
    assert [c["passed"] for c in result["tool_checks"]] == [False, False]
    assert "must not call" in result["tool_checks"][1]["result"]
    assert "Did not call send_email" in result["tool_checks"][0]["result"]
    assert job["result"]["run"]["passed"] == 0


def test_a_budget_refusal_stops_the_check_cleanly(client, monkeypatch, no_real_tools):
    from maverick import budget as budget_module

    app, tmp = client
    _knob(tmp, "[models]\ncapability_tests = true\n[agent_tests]\ntool_checks = true\n")
    assert app.post(API + "/customer", json={**_tool_case(), "with_tools": True,
                                            "tools_called": [{"name": "send_email", "contains": ""}]}).status_code == 200

    def refuse(self):
        raise budget_module.BudgetExceeded("tool calls over the limit")

    monkeypatch.setattr(budget_module.Budget, "record_tool_call", refuse)
    calls = _scripted_llm(monkeypatch, [_use("send_email", {"to": "Acme"})])
    job = _wait(app, app.post(API + "/customer/run", json={"specialist": "finance_ap",
                                                        "operation_id": "1" * 32,
                                                        "case_revisions": _revisions(app)}))
    assert job["state"] == "done", job
    run = job["result"]["run"]
    assert run["state"] == "stopped" and run["passed"] == 0 and run["results"] == []
    assert "allowance" in run["message"] and len(calls) == 1


def test_the_knob_off_skips_existing_tool_checks_without_any_provider_call(client, monkeypatch, no_real_tools):
    app, tmp = client
    _knob(tmp, "[models]\ncapability_tests = true\n[agent_tests]\ntool_checks = true\n")
    assert app.post(API + "/customer", json={**_tool_case(), "with_tools": True,
                                            "tools_called": [{"name": "send_email", "contains": ""}]}).status_code == 200
    _knob(tmp, "[models]\ncapability_tests = true\n")
    calls = _scripted_llm(monkeypatch, [_say("This is a duplicate; nothing was paid.")])
    job = _wait(app, app.post(API + "/customer/run", json={"specialist": "finance_ap",
                                                        "operation_id": "2" * 32,
                                                        "case_revisions": _revisions(app)}))
    run = job["result"]["run"]
    assert calls == []
    assert run["state"] == "done" and run["passed"] == 0 and run["total"] == 1
    assert "turned off" in run["results"][0]["note"] and run["results"][0]["passed"] is False


def test_creating_a_tool_check_is_refused_while_the_switch_is_off(client, audit_rows):
    app, tmp = client
    _knob(tmp, "[models]\ncapability_tests = true\n")
    refused = app.post(API + "/customer", json={**_tool_case(), "with_tools": True,
                                              "tools_called": [{"name": "send_email", "contains": ""}]})
    assert refused.status_code == 422 and "turned off" in refused.text
    assert app.get(API + "/customer", params={"specialist": "finance_ap"}).json()["cases"] == []
    # Plain checks still save while the switch is off.
    assert app.post(API + "/customer", json=_case()).status_code == 200


def test_the_publish_summary_counts_a_failed_tool_check_as_not_passing():
    from maverick_dashboard import agent_publish_gate as gate
    from maverick_dashboard import specialist_test_sets as saved

    case = _tool_case(id="a" * 32, revision=1, task="Two invoices are duplicates. What now?",
                      includes=["duplicate"], excludes=[], refusal=False, citation=False)
    # A correct answer with no tool call: the answer check passes, the tool check fails.
    result = saved.grade(case, "This is a duplicate.", tool_calls=[])
    run = {"id": "r" * 32, "state": "done", "case_revisions": [{"id": "a" * 32, "revision": 1}],
           "results": [result]}
    summary = gate._summary(run, "current", {"a" * 32: 1})
    assert result["checks"]["includes"] == 1 and summary["passed"] == 0 and summary["passing"] is False


def test_the_workforce_page_says_whether_tool_checks_are_on(client):
    app, tmp = client
    _knob(tmp, "[models]\ncapability_tests = true\n")
    assert app.get(API + "/customer", params={"specialist": "finance_ap"}).json()["tool_checks_enabled"] is False
    _knob(tmp, "[models]\ncapability_tests = true\n[agent_tests]\ntool_checks = true\n")
    assert app.get(API + "/customer", params={"specialist": "finance_ap"}).json()["tool_checks_enabled"] is True
