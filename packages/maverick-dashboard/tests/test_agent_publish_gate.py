"""Builders run a whole saved test set, and changed instructions publish only after it passes."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/workforce-ops/test-suite"
PACK = "proc_po_chaser"


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app
    from maverick_dashboard import learning_jobs

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_DOMAINS_DIR", str(tmp_path / "domains"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr("maverick.config.dashboard_overrides_path", lambda: tmp_path / "dashboard.toml")
    monkeypatch.setattr(audit_writer, "_default", None)
    audit_writer._defaults.clear()
    (tmp_path / "config.toml").write_text("[models]\ncapability_tests = true\n", encoding="utf-8")
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


def _wait(app, response):
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    deadline = time.time() + 20
    while job["state"] in {"admitted", "queued", "running", "stopping"}:
        assert time.time() < deadline, job
        time.sleep(.05)
        job = app.get("/api/v1/learning-ops/jobs/" + job["id"]).json()["job"]
    return job


def _save_tests(app, count):
    for index in range(count):
        row = app.post(API + "/customer", json={
            "specialist": PACK, "task": f"Purchase order example {index}: which supplier should we chase?",
            "includes": ["chase"], "excludes": [], "refusal": False, "citation": False})
        assert row.status_code == 200, row.text
    cases = app.get(API + "/customer", params={"specialist": PACK}).json()["cases"]
    return {row["id"]: row["revision"] for row in cases}


def _answers(monkeypatch, text="We should chase the supplier today."):
    from maverick import llm

    calls = []

    def answer(self, system, messages, **kwargs):
        calls.append(kwargs["budget"].max_dollars)
        kwargs["dispatch_guard"]()
        return llm.LLMResponse(text=text, thinking=None, tool_calls=[], stop_reason="end_turn")

    monkeypatch.setattr(llm.LLM, "complete", answer)
    return calls


def _current(app):
    return app.get("/api/v1/agents/" + PACK).json()


def _edit(app, persona, **extra):
    view = _current(app)
    body = {"expected_generation": view["generation"], "persona": persona}
    body.update(extra)
    return body


def test_a_whole_twenty_test_set_runs_in_one_bounded_run(client, monkeypatch):
    app, _tmp = client
    revisions = _save_tests(app, 20)
    calls = _answers(monkeypatch)
    job = _wait(app, app.post(API + "/customer/run", json={
        "specialist": PACK, "operation_id": "a" * 32, "case_revisions": revisions}))
    assert job["state"] == "done", job
    run = job["result"]["run"]
    assert run["total"] == 20 and run["passed"] == 20 and len(calls) == 20
    # Twenty questions are bounded at $0.05 each, never more than the configured caps.
    assert max(calls) <= 1.0
    assert run["instructions_key"]


def test_changed_instructions_need_a_passing_run_or_a_recorded_reason(client, monkeypatch, audit_rows):
    app, _tmp = client
    _save_tests(app, 2)
    new = "You chase overdue purchase orders and always name the supplier to chase."
    check = app.post("/api/v1/agents/" + PACK + "/publish-check", json=_edit(app, new))
    assert check.status_code == 200, check.text
    verdict = check.json()
    assert verdict["required"] and verdict["blocked"] and verdict["gate_on"]
    assert verdict["tests"] == 2 and verdict["latest"] is None
    assert verdict["tests_url"].startswith("/workforce?specialist=" + PACK)

    refused = app.post("/api/v1/agents/" + PACK + "/override", json=_edit(app, new))
    assert refused.status_code == 409 and "no passing test run" in refused.json()["detail"]
    assert _current(app)["persona"] != new
    short = app.post("/api/v1/agents/" + PACK + "/override",
                     json=_edit(app, new, publish_without_passing_tests=True, override_reason="ok"))
    assert short.status_code == 422

    published = app.post("/api/v1/agents/" + PACK + "/override", json=_edit(
        app, new, publish_without_passing_tests=True, override_reason="Urgent supplier change approved by finance"))
    assert published.status_code == 200, published.text
    assert _current(app)["persona"] == new
    logged = [row for row in audit_rows if row.get("verb") == "agent_published_without_passing_tests"]
    assert logged and logged[0]["reason"] == "Urgent supplier change approved by finance"
    assert logged[0]["specialist"] == PACK


def test_a_passing_comparison_of_the_exact_new_instructions_unlocks_publishing(client, monkeypatch, audit_rows):
    app, _tmp = client
    revisions = _save_tests(app, 2)
    _answers(monkeypatch)
    new = "You chase overdue purchase orders and always say which supplier to chase first."
    listed = app.get(API + "/customer", params={"specialist": PACK}).json()
    job = _wait(app, app.post(API + "/customer/compare", json={
        "specialist": PACK, "operation_id": "b" * 32, "case_ids": list(revisions),
        "case_revisions": revisions, "proposed_instructions": new,
        "baseline_revision": listed["baseline_revision"], "expected_context": listed["context"]}))
    assert job["state"] == "done", job
    verdict = app.post("/api/v1/agents/" + PACK + "/publish-check", json=_edit(app, new)).json()
    assert verdict["required"] and verdict["passing"] and not verdict["blocked"]
    assert verdict["latest"]["passed"] == 2 and verdict["latest"]["covers_all_tests"]
    saved = app.post("/api/v1/agents/" + PACK + "/override", json=_edit(app, new))
    assert saved.status_code == 200, saved.text
    assert not [row for row in audit_rows if row.get("verb") == "agent_published_without_passing_tests"]

    # A different wording is a different set of instructions: it needs its own run.
    other = app.post("/api/v1/agents/" + PACK + "/publish-check", json=_edit(app, new + " Be brief."))
    assert other.json()["blocked"]


def test_a_failing_or_partial_run_does_not_count(client, monkeypatch):
    app, _tmp = client
    revisions = _save_tests(app, 3)
    _answers(monkeypatch, text="I would wait and see.")
    new = "You chase overdue purchase orders politely."
    listed = app.get(API + "/customer", params={"specialist": PACK}).json()
    _wait(app, app.post(API + "/customer/compare", json={
        "specialist": PACK, "operation_id": "c" * 32, "case_ids": list(revisions),
        "case_revisions": revisions, "proposed_instructions": new,
        "baseline_revision": listed["baseline_revision"], "expected_context": listed["context"]}))
    verdict = app.post("/api/v1/agents/" + PACK + "/publish-check", json=_edit(app, new)).json()
    assert verdict["blocked"] and verdict["latest"]["passed"] == 0
    assert "0 of 3" in verdict["message"]

    _answers(monkeypatch)
    first_two = dict(list(revisions.items())[:2])
    _wait(app, app.post(API + "/customer/compare", json={
        "specialist": PACK, "operation_id": "d" * 32, "case_ids": list(first_two),
        "case_revisions": first_two, "proposed_instructions": new,
        "baseline_revision": listed["baseline_revision"], "expected_context": listed["context"]}))
    partial = app.post("/api/v1/agents/" + PACK + "/publish-check", json=_edit(app, new)).json()
    assert partial["blocked"] and not partial["latest"]["covers_all_tests"]


def test_unchanged_instructions_new_agents_and_the_switched_off_gate_are_not_blocked(client, monkeypatch):
    app, tmp = client
    from maverick import config

    view = _current(app)
    same = app.post("/api/v1/agents/" + PACK + "/override", json={
        "expected_generation": view["generation"], "persona": view["persona"], "description": "Chases late POs"})
    assert same.status_code == 200, same.text
    created = app.post("/api/v1/agents/acme_po_helper/override", json={
        "expected_generation": "new", "description": "PO helper", "persona": "You help with purchase orders.",
        "allow_tools": ["read_file"], "max_risk": "low"})
    assert created.status_code == 200, created.text

    (tmp / "config.toml").write_text("[models]\ncapability_tests = true\n"
                                    "[agent_factory]\nrequire_passing_tests = false\n", encoding="utf-8")
    config.reset_config_cache()
    off = app.post("/api/v1/agents/" + PACK + "/override", json=_edit(app, "You chase every late purchase order."))
    assert off.status_code == 200, off.text


def test_the_knob_defaults_on_and_stays_on_when_settings_are_unreadable(monkeypatch, tmp_path):
    from maverick import agent_publish_policy, config

    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    try:
        assert agent_publish_policy.require_passing_tests() is True
        (tmp_path / "config.toml").write_text("[agent_factory]\nrequire_passing_tests = \"no\"\n", encoding="utf-8")
        config.reset_config_cache()
        assert agent_publish_policy.require_passing_tests() is True
        (tmp_path / "config.toml").write_text("[agent_factory]\nrequire_passing_tests = false\n", encoding="utf-8")
        config.reset_config_cache()
        assert agent_publish_policy.require_passing_tests() is False
    finally:
        config.reset_config_cache()
