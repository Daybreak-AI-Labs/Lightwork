"""Reviewer decisions + review cadence: the record comes due instead of
dying in a folder, and the follow-up cycle re-opens it."""
from __future__ import annotations

import time

import pytest
from maverick.assessment import (
    AssessmentSession,
    add_followups,
    answer_followup,
    decide_assessment,
    list_saved,
    load_saved,
    save_session,
)


@pytest.fixture(autouse=True)
def _fresh_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))


def _saved() -> AssessmentSession:
    s = AssessmentSession(type="pia", subject="Acme CRM")
    s.record("pia_necessity", "yes")
    save_session(s)
    return s


def test_approve_schedules_the_next_review():
    s = _saved()
    rec = decide_assessment(s.id, "approved", decided_by="dpo",
                            cadence_days=365)
    assert rec["status"] == "approved"
    assert rec["decided_by"] == "dpo"
    assert rec["cadence_days"] == 365
    expected = rec["decided_at"] + 365 * 86400
    assert abs(rec["next_review_at"] - expected) < 1
    row = list_saved()[0]
    assert row["status"] == "approved"
    assert row["review_due"] is False


def test_no_cadence_means_no_re_review_date():
    s = _saved()
    rec = decide_assessment(s.id, "approved", cadence_days=0)
    assert rec["next_review_at"] is None
    rec2 = decide_assessment(s.id, "rejected", cadence_days=365)
    assert rec2["status"] == "rejected"
    assert rec2["next_review_at"] is None  # rejections never come due


def test_review_due_when_the_cadence_elapses(monkeypatch):
    s = _saved()
    decide_assessment(s.id, "approved", cadence_days=30)
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 31 * 86400)
    row = list_saved()[0]
    assert row["review_due"] is True


def test_followup_cycle_reopens_a_decided_record():
    s = _saved()
    decide_assessment(s.id, "approved")
    rec = add_followups(s.id, ["Annual re-attestation: anything changed?"],
                        asked_by="dpo")
    assert rec["status"] == "needs_more"
    fid = rec["followups"][-1]["id"]
    rec = answer_followup(s.id, fid, "No changes to processing.",
                          answered_by="requester")
    assert rec["status"] == "pending_review"
    rec = decide_assessment(s.id, "approved", cadence_days=180)
    assert rec["status"] == "approved"
    assert rec["cadence_days"] == 180


def test_unknown_id_and_bad_decision():
    assert decide_assessment("nope", "approved") is None
    s = _saved()
    with pytest.raises(ValueError):
        decide_assessment(s.id, "maybe")
    # The failed decision left no partial write behind.
    assert load_saved(s.id).get("status", "pending_review") == "pending_review"
