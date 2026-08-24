"""Art. 4 AI-literacy register: requirements, completions, gaps, evidence.

The invariants: requirements and assignments are named-human CAS assertions,
a completion refresh keeps bounded history, expiry derives only from the
requirement's validity window, and the register never certifies competence.
"""
from __future__ import annotations

import json
import time

import pytest
from maverick import ai_literacy as lit
from maverick import config
from maverick.paths import tenant_scope
from maverick.privacy_ops import RecordConflict

_DAY = 86_400


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    from maverick import audit

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *_args, **_kwargs: True)
    (tmp_path / "config.toml").write_text(
        "[ai_literacy]\nenable = true\n", encoding="utf-8",
    )
    config.reset_config_cache()
    yield tmp_path
    config.reset_config_cache()


def _require_underwriting():
    return lit.set_role_requirement(
        "underwriting_operator",
        [
            {"key": "ai-fundamentals", "title": "AI fundamentals", "validity_days": 365},
            {"key": "model-limits", "title": "Model limitations briefing",
             "validity_days": None},
        ],
        actor="compliance-lead",
    )


def test_register_is_off_by_default_and_rejects_hostile_input(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert lit.enabled() is False
    with pytest.raises(lit.AILiteracyConfigError, match="enable"):
        lit.literacy_gaps()

    (tmp_path / "config.toml").write_text(
        "[ai_literacy]\nenable = true\nextra = 1\n", encoding="utf-8",
    )
    config.reset_config_cache()
    with pytest.raises(lit.AILiteracyConfigError, match="unknown"):
        lit.get_ai_literacy_config()

    (tmp_path / "config.toml").write_text(
        "[ai_literacy]\nenable = true\n", encoding="utf-8",
    )
    config.reset_config_cache()
    with pytest.raises(ValueError, match="identifier"):
        lit.set_role_requirement("bad role!", [], actor="ops")
    with pytest.raises(ValueError, match="1-64"):
        lit.set_role_requirement("role", [], actor="ops")
    with pytest.raises(ValueError, match="unknown training keys"):
        lit.set_role_requirement(
            "role", [{"key": "k", "title": "T", "url": "https://x"}], actor="ops",
        )
    with pytest.raises(ValueError, match="unique"):
        lit.set_role_requirement(
            "role",
            [{"key": "k", "title": "T"}, {"key": "k", "title": "T2"}],
            actor="ops",
        )
    with pytest.raises(ValueError, match="between 1 and 3650"):
        lit.set_role_requirement(
            "role", [{"key": "k", "title": "T", "validity_days": 0}], actor="ops",
        )


def test_requirements_and_assignments_are_named_cas_assertions():
    requirement = _require_underwriting()
    assert requirement["set_by"] == "compliance-lead"
    assert [row["key"] for row in requirement["trainings"]] == [
        "ai-fundamentals", "model-limits",
    ]

    # A second blind create conflicts; the update path needs the revision.
    with pytest.raises(RecordConflict):
        _require_underwriting()
    updated = lit.set_role_requirement(
        "underwriting_operator",
        [{"key": "ai-fundamentals", "title": "AI fundamentals", "validity_days": 180}],
        actor="compliance-lead",
        expected_revision=requirement["revision"],
    )
    assert updated["revision"] > requirement["revision"]

    with pytest.raises(ValueError, match="no recorded requirement"):
        lit.assign_roles("analyst@example.com", ["ghost_role"], actor="hr-ops")
    assignment = lit.assign_roles(
        "analyst@example.com", ["underwriting_operator"], actor="hr-ops",
    )
    assert assignment["assigned_by"] == "hr-ops"
    assert assignment["roles"] == ["underwriting_operator"]


def test_gaps_derive_missing_expired_and_current_states():
    _require_underwriting()
    lit.assign_roles("analyst@example.com", ["underwriting_operator"], actor="hr")
    now = time.time()

    gaps = lit.literacy_gaps(now=now)
    assert gaps["counts"] == {"current": 0, "expired": 0, "missing": 2}
    person = gaps["persons"][0]
    assert person["gap_count"] == 2

    lit.record_completion(
        "analyst@example.com", "ai-fundamentals",
        completed_at=now - 400 * _DAY,
        evidence_ref="lms:course/ai-fundamentals/attempt-1",
        actor="lms-feed",
    )
    lit.record_completion(
        "analyst@example.com", "model-limits",
        completed_at=now - 400 * _DAY,
        evidence_ref="lms:course/model-limits/attempt-1",
        actor="lms-feed",
    )
    gaps = lit.literacy_gaps(now=now)
    states = {row["training"]: row for row in gaps["persons"][0]["trainings"]}
    # 365-day validity lapsed; the no-validity training never expires.
    assert states["ai-fundamentals"]["status"] == "expired"
    assert states["model-limits"]["status"] == "current"
    assert gaps["counts"] == {"current": 1, "expired": 1, "missing": 0}
    assert gaps["regulation"]["article"] == "Art. 4"

    refreshed = lit.record_completion(
        "analyst@example.com", "ai-fundamentals",
        completed_at=now - _DAY,
        evidence_ref="lms:course/ai-fundamentals/attempt-2",
        actor="lms-feed",
    )
    assert len(refreshed["history"]) == 1
    assert refreshed["history"][0]["evidence_ref"].endswith("attempt-1")
    gaps = lit.literacy_gaps(now=now)
    assert gaps["counts"] == {"current": 2, "expired": 0, "missing": 0}


def test_completions_refuse_future_dates_and_keep_bounded_history():
    _require_underwriting()
    with pytest.raises(ValueError, match="future-dated"):
        lit.record_completion(
            "analyst@example.com", "ai-fundamentals",
            completed_at=time.time() + 3_600,
            evidence_ref="lms:x",
            actor="lms-feed",
        )
    now = time.time()
    for attempt in range(25):
        lit.record_completion(
            "analyst@example.com", "ai-fundamentals",
            completed_at=now - (30 - attempt) * _DAY,
            evidence_ref=f"lms:attempt-{attempt}",
            actor="lms-feed",
        )
    row = lit.record_completion(
        "analyst@example.com", "ai-fundamentals",
        completed_at=now,
        evidence_ref="lms:final",
        actor="lms-feed",
    )
    assert len(row["history"]) == 20


def test_evidence_export_is_bounded_json_with_citation_and_notice():
    _require_underwriting()
    lit.assign_roles("analyst@example.com", ["underwriting_operator"], actor="hr")
    exported = json.loads(lit.export_evidence(now=time.time()))
    assert exported["regulation"]["regulation"] == "Regulation (EU) 2024/1689"
    assert exported["regulation"]["applicable_since"] == "2025-02-02"
    assert exported["notice"] == lit.NON_CERTIFICATION_NOTICE
    assert exported["roles"][0]["role"] == "underwriting_operator"
    assert exported["counts"]["missing"] == 2
    assert exported["persons"][0]["person"] == "analyst@example.com"


def test_register_is_tenant_scoped():
    with tenant_scope(tenant="alpha"):
        _require_underwriting()
        lit.assign_roles("analyst@example.com", ["underwriting_operator"], actor="hr")
        assert lit.literacy_gaps()["person_count"] == 1
    with tenant_scope(tenant="beta"):
        assert lit.literacy_gaps()["person_count"] == 0
        assert lit.list_requirements() == []
