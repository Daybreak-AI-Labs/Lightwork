"""An answer says whether the extra check reviewed it, exactly as the record shows."""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard import answer_checks
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})
ROOT = "orchestrator-0-72eb85"


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", "", html).split())


def _events(*rows):
    return [SimpleNamespace(id=index + 1, agent=agent, kind=kind, content=content)
            for index, (agent, kind, content) in enumerate(rows)]


@pytest.mark.parametrize(("rows", "state", "reason"), [
    ([(ROOT, "verify", "verifier accepted (conf=0.91)")], "checked", answer_checks.CHECKED),
    ([(ROOT, "verify", "verification skipped (conversational answer: no tools, no code)")], "not_checked",
     "Short conversational answers that use no tools or files are not sent to the extra check."),
    ([(ROOT, "verify", "verification skipped (risk-proportional: low-risk answer, no tools/code)")], "not_checked",
     "Your workspace skips the extra check for low-risk answers that use no tools or code."),
    ([(ROOT, "verify", "verifier rejected (conf=0.40): misses the email")], "not_checked",
     "The extra check asked for changes, and the revised answer was not checked again."),
    ([(ROOT, "verify", "verifier did not complete (budget)")], "not_checked",
     "The extra check started but could not finish within the spending limit. Raise the limit or check fewer items, then try again."),
    ([(ROOT, "error", "verifier failed: timeout")], "not_checked", "The extra check could not run. Try again."),
    ([(ROOT, "verify", "test-driven verifier: FAIL_TO_PASS: 2/2 pass; PASS_TO_PASS: 5/5 pass")], "checked",
     "The required tests passed."),
    ([(ROOT, "verify", "test-driven verifier: FAIL_TO_PASS: 1/2 pass; PASS_TO_PASS: 5/5 pass")], "concerns",
     "The required tests did not all pass."),
    ([(ROOT, "plan", "role=orchestrator depth=0 brief=x"), (ROOT, "finding", "answer")], "not_checked",
     answer_checks.NOT_SENT),
    ([], "not_checked", answer_checks.NOT_SENT),
])
def test_the_badge_reflects_the_recorded_check_outcome_exactly(rows, state, reason):
    found = answer_checks.verification(_events(*rows))
    assert found == {"state": state, "label": answer_checks.LABELS[state], "reason": reason}


def test_a_helper_or_coding_step_check_is_never_mistaken_for_the_answer_check():
    rows = _events(("researcher-1-a1b2c3", "verify", "verifier accepted (conf=0.99)"),
                   (ROOT, "verify", "patch rejected: corrupt hunk"))
    assert answer_checks.verification(rows)["state"] == "not_checked"


def test_only_the_latest_run_of_the_main_agent_counts():
    rows = _events(("orchestrator-0-aaaaaa", "verify", "verifier accepted (conf=0.9)"),
                   ("orchestrator-0-bbbbbb", "plan", "resumed"),
                   ("orchestrator-0-bbbbbb", "verify", "verification skipped (conversational answer: no tools, no code)"))
    assert answer_checks.verification(rows)["state"] == "not_checked"


def test_a_withheld_check_is_shown_as_hidden_not_as_checked_or_unchecked():
    marker = "[AI-generated run-event content withheld]"
    rows = _events((ROOT, "verify", marker))
    found = answer_checks.verification(rows, frozenset({marker}))
    assert found["state"] == "hidden" and found["reason"] == answer_checks.HIDDEN


def test_backup_model_use_is_reported_for_the_latest_run_only():
    old = _events(("orchestrator-0-aaaaaa", "model_fallback", "primary=a backup=b"),
                  ("orchestrator-0-bbbbbb", "plan", "x"))
    assert answer_checks.used_backup_model(old) is False
    current = _events(("orchestrator-0-bbbbbb", "plan", "x"),
                      ("researcher-1-cccccc", "model_fallback", "primary=a backup=b"))
    assert answer_checks.used_backup_model(current) is True


@pytest.fixture
def world(tmp_path, monkeypatch):
    from maverick import world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr("maverick.config.dashboard_overrides_path", lambda: tmp_path / "dashboard.toml")
    w = world_model.WorldModel(tmp_path / "world.db")
    yield w
    w.close()


def test_the_answer_shows_not_checked_with_its_reason_and_the_backup_notice(world):
    gid = world.create_goal("Summarize vendor spend", "")
    world.append_event(gid, ROOT, "plan", "role=orchestrator depth=0 brief=x")
    world.append_event(gid, ROOT, "model_fallback", "primary=anthropic:claude-opus-4-8 backup=openai:gpt-5.4")
    world.append_event(gid, ROOT, "verify", "verification skipped (conversational answer: no tools, no code)")
    world.set_goal_status(gid, "done", result="Here is the summary.")
    body = client.get(f"/chat/goal/{gid}").text
    check = body.split('id="answer-check"', 1)[1].split("</p>", 1)[0]
    assert 'data-state="not_checked"' in check
    assert "Not checked" in check and "Short conversational answers" in check
    assert "Checked<" not in check.replace("Not checked<", "")
    assert "The backup AI model was used for this answer because the main one was unavailable." in body
    agent = _text(body.split('id="answer-agent"', 1)[1].split("</p>", 1)[0])
    assert "Agent: Lightwork · no recorded changes" in agent


def test_the_live_projection_carries_the_same_check(world):
    gid = world.create_goal("Summarize vendor spend", "")
    world.append_event(gid, ROOT, "verify", "verifier accepted (conf=0.93)")
    world.set_goal_status(gid, "done", result="Here is the summary.")
    body = client.get(f"/chat/goal/{gid}/answer-panel").text
    assert 'data-state="checked"' in body
    assert answer_checks.CHECKED in body


def test_no_badge_while_the_task_is_still_working(world):
    gid = world.create_goal("Summarize vendor spend", "")
    world.append_event(gid, ROOT, "verify", "verifier accepted (conf=0.93)")
    world.set_goal_status(gid, "active")
    assert 'id="answer-check"' not in client.get(f"/chat/goal/{gid}").text


def test_a_pack_answer_names_its_specialist_and_the_last_change(world):
    from maverick_dashboard import agent_changelog

    agent_changelog.record("proc_po_chaser", ["Changed its instructions"], by="user:alice")
    gid = world.create_goal("Which POs are overdue?", "", domain="proc_po_chaser")
    world.append_event(gid, "proc_po_chaser-0-a1b2c3", "verify", "verifier accepted (conf=0.93)")
    world.set_goal_status(gid, "done", result="Three are overdue.")
    body = client.get(f"/chat/goal/{gid}").text
    agent = _text(body.split('id="answer-agent"', 1)[1].split("</p>", 1)[0])
    assert re.search(r"Specialist: PO Confirmation Chaser · updated \d{1,2} \w+ \d{4}", agent), agent
    assert "What changed in this specialist" in body and "Changed its instructions" in body
    # Who made the change is for the agent editor, not every reader of the answer.
    assert "alice" not in body.split('id="answer-facts"', 1)[1].split("</div>", 1)[0]
