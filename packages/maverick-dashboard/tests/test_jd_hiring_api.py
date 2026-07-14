"""JD-hiring API: match a job description to the roster, draft a pack from it,
add a hired pack to a fleet, and run a goal AS a specialist (GoalIn.domain).

Mutating /api/v1 requests carry a same-origin Origin (the CSRF contract).
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})

FINANCE_JD = """Senior Accounts Payable Specialist

Own vendor invoices and payments end to end.

- Process vendor invoices and three-way match against purchase orders
- Reconcile supplier statements and resolve payment discrepancies
- Prepare weekly payment runs for approval
"""


@pytest.fixture(autouse=True)
def _fresh_world(tmp_path, monkeypatch):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    yield


# ---- /agents/jd/match ---------------------------------------------------------

def test_jd_match_ranks_roster():
    resp = client.post("/api/v1/agents/jd/match",
                       json={"jd_text": FINANCE_JD, "k": 5})
    assert resp.status_code == 200
    matches = resp.json()["matches"]
    assert matches, "a finance JD must hit the shipped roster"
    assert matches[0]["fit"] == 1.0
    assert any((m["suite"] or "").startswith("finance") for m in matches[:3])
    # Explainability fields present.
    assert {"name", "fit", "description", "suite", "department",
            "max_risk", "matched_terms"} <= set(matches[0])


def test_jd_match_validates_input():
    assert client.post("/api/v1/agents/jd/match", json={"jd_text": ""}).status_code == 422
    assert client.post("/api/v1/agents/jd/match",
                       json={"jd_text": "x", "k": 0}).status_code == 422


def test_jd_match_from_file_txt_upload():
    resp = client.post(
        "/api/v1/agents/jd/match-from-file",
        files={"file": ("jd.txt", io.BytesIO(FINANCE_JD.encode()), "text/plain")},
        data={"k": "3"},
    )
    assert resp.status_code == 200
    assert resp.json()["matches"]


def test_jd_match_disabled_by_config_knob(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[agent_factory]\njd_hiring = false\n", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_CONFIG", str(cfg))
    resp = client.post("/api/v1/agents/jd/match",
                       json={"jd_text": FINANCE_JD})
    assert resp.status_code == 403
    assert "jd_hiring" in resp.json()["detail"]


# ---- /agents/jd/draft -----------------------------------------------------------

def test_jd_draft_keyless_is_clamped(monkeypatch):
    # No provider key -> deterministic draft; never a 500.
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    resp = client.post("/api/v1/agents/jd/draft", json={
        "role_title": "Accounts Payable Specialist",
        "jd_text": FINANCE_JD,
        "industry": "manufacturing",
    })
    assert resp.status_code == 200
    body = resp.json()
    draft = body["draft"]
    assert body["generated_with_llm"] is False
    # The generated deny-floor always applies.
    for tool in ("shell", "write_file", "code_exec"):
        assert tool in draft["deny_tools"]
    assert draft["max_risk"] in ("low", "medium")
    # Editor-prefill shape: workflow rows carry the editor's field names.
    assert draft["workflow"] and {"name", "instruction", "tools", "gate"} <= set(
        draft["workflow"][0])


# ---- /fleets/{name}/agents -------------------------------------------------------

def test_add_pack_to_fleet_and_idempotency():
    resp = client.post("/api/v1/fleets/finance-team/agents",
                       json={"pack": "finance_ap"})
    assert resp.status_code == 201, resp.text
    fleet = resp.json()["fleet"]
    assert fleet["name"] == "finance-team"
    assert any(a.get("domain") == "finance_ap" for a in fleet["agents"])
    # Adding again is idempotent, not a duplicate row.
    again = client.post("/api/v1/fleets/finance-team/agents",
                        json={"pack": "finance_ap"})
    assert again.status_code == 201
    assert sum(1 for a in again.json()["fleet"]["agents"]
               if a.get("domain") == "finance_ap") == 1


def test_add_unknown_pack_400():
    resp = client.post("/api/v1/fleets/team-x/agents",
                       json={"pack": "not_a_real_pack_xyz"})
    assert resp.status_code == 400


# ---- GoalIn.domain ---------------------------------------------------------------

def test_create_goal_with_domain_stamps_goal_row(monkeypatch, tmp_path):
    from maverick import world_model
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    # Don't actually run the swarm: capture the background dispatch.
    import maverick.runner as runner
    monkeypatch.setattr(runner, "run_goal_in_thread",
                        lambda *a, **k: "done")

    resp = client.post("/api/v1/goals", json={
        "title": "Reconcile May supplier statements",
        "description": "AP month-end",
        "domain": "finance_ap",
    })
    assert resp.status_code in (200, 201), resp.text
    goal_id = resp.json()["id"]
    w = world_model.WorldModel(world_model.DEFAULT_DB)
    goal = w.get_goal(goal_id)
    assert getattr(goal, "domain", "") == "finance_ap"


def test_create_goal_with_unknown_domain_400(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    resp = client.post("/api/v1/goals", json={
        "title": "x", "domain": "no_such_pack_xyz",
    })
    assert resp.status_code == 400
    assert "unknown specialist" in resp.json()["detail"]
