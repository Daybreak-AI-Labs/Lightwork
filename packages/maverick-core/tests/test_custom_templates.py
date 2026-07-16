"""Custom / edited assessment templates: authoring, overriding a built-in,
scoring parity, and department routing."""
from __future__ import annotations

import pytest
from maverick.assessment import (
    AssessmentSession,
    delete_custom_template,
    get_template,
    list_templates,
    save_custom_template,
    save_session,
    template_department,
)


@pytest.fixture(autouse=True)
def _fresh_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))


def _payload(**over):
    base = {
        "type": "dpia_lite", "title": "DPIA Lite",
        "framework": "GDPR Art. 35 (lightweight)", "department": "privacy",
        "questions": [
            {"text": "Is the processing high risk to data subjects?",
             "risk_answer": "yes", "severity": "high", "section": "Scope"},
            {"text": "Is a defined retention schedule in place?",
             "risk_answer": "no", "severity": "medium",
             "section": "Retention"},
        ],
    }
    base.update(over)
    return base


def test_author_new_template_and_score_with_it():
    tpl = save_custom_template(_payload())
    assert tpl.type == "dpia_lite"
    assert get_template("dpia_lite").title == "DPIA Lite"
    assert "dpia_lite" in {t.type for t in list_templates()}
    assert template_department("dpia_lite") == "privacy"
    # The engine scores a custom questionnaire exactly like a built-in.
    s = AssessmentSession(type="dpia_lite", subject="New analytics tool")
    s.record("dpia_lite_q1", "yes")
    s.record("dpia_lite_q2", "no")
    save_session(s)
    r = s.evaluate()
    assert r.residual_risk == "high"
    assert r.risks_in_scope == 2


def test_override_builtin_and_restore():
    save_custom_template(_payload(
        type="pia", title="PIA (company edition)",
        questions=[{"text": "Company-specific question?",
                    "risk_answer": "no", "severity": "low"}]))
    assert get_template("pia").title == "PIA (company edition)"
    assert len(get_template("pia").questions) == 1
    # One catalog entry per type: the override wins, no duplicate.
    assert [t.type for t in list_templates()].count("pia") == 1
    assert delete_custom_template("pia") is True
    assert get_template("pia").title == "Privacy Impact Assessment"
    assert delete_custom_template("pia") is False  # nothing left to delete


def test_validation_rejects_bad_input():
    for bad in (
        _payload(type="Bad Type!"),
        _payload(title=""),
        _payload(questions=[]),
        _payload(questions=[{"text": "ok?", "severity": "urgent"}]),
        _payload(questions=[{"text": "ok?", "risk_answer": "maybe"}]),
        _payload(department="engineering"),
    ):
        with pytest.raises(ValueError):
            save_custom_template(bad)


def test_finance_department_routing():
    save_custom_template(_payload(
        type="tax_readiness", title="Tax Readiness Check",
        framework="internal tax policy", department="finance"))
    assert template_department("tax_readiness") == "finance"
    assert template_department("sox_control") == "finance"
    assert template_department("pia") == "privacy"
