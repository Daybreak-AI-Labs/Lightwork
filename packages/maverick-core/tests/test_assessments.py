"""Governance assessments: auto-draft heuristics, the review lifecycle, drift
detection, scheduling, and audit export."""
from __future__ import annotations

import pytest
from maverick import assessments as A


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    # Point the data home (and thus the register) at a scratch dir.
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


HIGH_RISK = {
    "kind": "agent", "subject": "danger_bot", "description": "does risky things",
    "allow_tools": ["shell", "web_search", "file_write", "email"],
    "deny_tools": [], "max_risk": "high", "allow_paths": ["/etc"],
    "allow_hosts": ["api.example.com"], "mcp_servers": [], "knowledge_sources": ["hr_records"],
    "has_human_gate": False, "steps": 2,
}
SAFE = {
    "kind": "agent", "subject": "calm_bot", "description": "reads and summarizes",
    "allow_tools": ["read_file"], "deny_tools": [], "max_risk": "low",
    "allow_paths": [], "allow_hosts": [], "mcp_servers": [], "knowledge_sources": [],
    "has_human_gate": True, "steps": 1,
}


def _use_surface(monkeypatch, surface):
    monkeypatch.setattr(A, "subject_surface", lambda kind, name: dict(surface))


def test_auto_draft_flags_shell_and_missing_gate(monkeypatch):
    _use_surface(monkeypatch, HIGH_RISK)
    a = A.auto_draft("agent", "danger_bot", now=1000.0)
    assert set(a["lenses"]) == set(A.LENSES)
    sec = a["lenses"]["security"]["findings"]
    assert any(f["control"] == "Arbitrary code / shell execution" and f["severity"] == "high" for f in sec)
    ai = a["lenses"]["ai_risk"]["findings"]
    assert any(f["control"] == "Human oversight" and f["severity"] == "high" for f in ai)
    # reads + egress -> a high privacy transfer finding
    priv = a["lenses"]["privacy"]["findings"]
    assert any(f["severity"] == "high" for f in priv)


def test_safe_subject_has_no_high_findings(monkeypatch):
    _use_surface(monkeypatch, SAFE)
    a = A.auto_draft("agent", "calm_bot", now=1.0)
    highs = [f for lv in a["lenses"].values() for f in lv["findings"] if f["severity"] == "high"]
    assert highs == []


def test_template_limits_lenses(monkeypatch):
    _use_surface(monkeypatch, SAFE)
    a = A.auto_draft("agent", "calm_bot", template="security_only", now=1.0)
    assert list(a["lenses"]) == ["security"]


def test_refresh_creates_then_review_marks_reviewed(monkeypatch):
    _use_surface(monkeypatch, SAFE)
    a = A.refresh("agent", "calm_bot", now=1.0)
    assert a["status"] == "draft"
    for lens in A.LENSES:
        A.record_review("agent", "calm_bot", lens, "accepted", reviewer="ada",
                        cadence_days=30, now=2.0)
    got = A.get_assessment("agent", "calm_bot")
    assert got["status"] == "reviewed"
    assert got["due_at"] == pytest.approx(2.0 + 30 * 86400)
    # every open finding is now accepted
    assert all(f["status"] == "accepted"
               for lv in got["lenses"].values() for f in lv["findings"])


def test_drift_flips_reviewed_back_to_needs_review(monkeypatch):
    _use_surface(monkeypatch, SAFE)
    A.refresh("agent", "calm_bot", now=1.0)
    for lens in A.LENSES:
        A.record_review("agent", "calm_bot", lens, "accepted", now=2.0)
    assert A.get_assessment("agent", "calm_bot")["status"] == "reviewed"
    # The subject gains shell + high risk -> surface hash changes -> drift.
    _use_surface(monkeypatch, {**SAFE, "allow_tools": ["read_file", "shell"], "max_risk": "high"})
    a = A.refresh("agent", "calm_bot", now=3.0)
    assert a["status"] == "needs_review"
    assert a["lenses"]["security"]["status"] == "open"


def test_evidence_and_export(monkeypatch):
    _use_surface(monkeypatch, HIGH_RISK)
    A.refresh("agent", "danger_bot", now=1.0)
    A.add_evidence("agent", "danger_bot", "security",
                   "Arbitrary code / shell execution", "sandbox verified: ticket-42", now=2.0)
    rows = A.export_rows()
    assert any(r["evidence"] == "sandbox verified: ticket-42" for r in rows)
    assert {"kind", "subject", "lens", "control", "severity"} <= set(rows[0])


def test_due_lists_drafts_and_overdue(monkeypatch):
    _use_surface(monkeypatch, SAFE)
    A.refresh("agent", "calm_bot", now=1.0)                       # stays draft
    due = A.due_assessments(now=10.0)
    assert any(d["subject"] == "calm_bot" for d in due)


def test_unknown_subject_returns_none(monkeypatch):
    monkeypatch.setattr(A, "subject_surface", lambda kind, name: None)
    assert A.auto_draft("agent", "nope") is None
    assert A.refresh("agent", "nope") is None


def test_real_agent_surface_resolves():
    # Integration: a real built-in pack yields a surface (name varies, so just
    # assert the shape for any one agent).
    from maverick.domain_edit import list_agents
    agents = list_agents()
    if not agents:
        pytest.skip("no packs discovered in this environment")
    s = A.agent_surface(agents[0]["name"])
    assert s is not None and s["kind"] == "agent" and "allow_tools" in s
