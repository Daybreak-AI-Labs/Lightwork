"""Approvals wizard step: undo window, high-risk confirmation and who approves
a risky action the requester asked for -> [approval]."""
from __future__ import annotations


def test_pick_approvals_defaults(monkeypatch):
    from maverick_installer import wizard

    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **kw: kw["default"])
    monkeypatch.setattr(wizard, "_q_confirm", lambda *_a, **kw: kw["default"])
    monkeypatch.setattr(wizard, "_q_select", lambda *_a, **kw: kw["default"])
    assert wizard.pick_approvals() == {"undo_grace_seconds": 30, "require_acknowledgement": True,
                                       "separate_approver": "when_available",
                                       "record_checks": True, "check_retention_days": 90}


def test_pick_approvals_bounds_and_choices(monkeypatch):
    from maverick_installer import wizard

    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **_kw: "99999")
    monkeypatch.setattr(wizard, "_q_confirm", lambda *_a, **_kw: False)
    monkeypatch.setattr(wizard, "_q_select", lambda _message, choices, **_kw: choices[1])
    assert wizard.pick_approvals() == {"undo_grace_seconds": 3600, "require_acknowledgement": False,
                                       "separate_approver": "always",
                                       "record_checks": False, "check_retention_days": 90}
    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **_kw: "not a number")
    assert wizard.pick_approvals()["undo_grace_seconds"] == 30


def test_every_separate_approver_choice_is_a_mode_the_kernel_knows(monkeypatch):
    from maverick.safety.dual_control import SEPARATE_APPROVER_MODES
    from maverick_installer import wizard

    offered = []

    def remember(_message, choices, **_kw):
        offered.extend(choices)
        return choices[-1]

    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **kw: kw["default"])
    monkeypatch.setattr(wizard, "_q_confirm", lambda *_a, **kw: kw["default"])
    monkeypatch.setattr(wizard, "_q_select", remember)
    assert wizard.pick_approvals()["separate_approver"] == "never"
    assert tuple(choice.split()[0] for choice in offered) == SEPARATE_APPROVER_MODES


def test_the_step_says_how_long_a_risky_step_waits(monkeypatch):
    from maverick_installer import wizard

    printed = []
    monkeypatch.setattr(wizard.console, "print", lambda *a, **_k: printed.append(" ".join(map(str, a))))
    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **kw: kw["default"])
    monkeypatch.setattr(wizard, "_q_confirm", lambda *_a, **kw: kw["default"])
    monkeypatch.setattr(wizard, "_q_select", lambda *_a, **kw: kw["default"])
    wizard.pick_approvals()
    text = " ".join(printed)
    assert "30 minutes" in text and "10 minutes" in text


def test_step_and_config_section():
    from maverick_installer import wizard

    assert ("approvals", "Approvals") in wizard.STEPS
    lines = wizard._cfg_approval({"undo_grace_seconds": 45, "require_acknowledgement": True,
                                  "separate_approver": "always"})
    assert lines[1] == "[approval]"
    assert "undo_grace_seconds = 45" in lines
    assert "require_acknowledgement = true" in lines
    assert 'separate_approver = "always"' in lines
    assert wizard._cfg_approval(None) == []


def test_the_record_of_approval_checks_is_asked_and_bounded(monkeypatch):
    from maverick import approval_checks, config
    from maverick_installer import wizard

    monkeypatch.setattr(wizard, "_q_confirm", lambda *_a, **_kw: True)
    monkeypatch.setattr(wizard, "_q_select", lambda *_a, **kw: kw["default"])
    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **_kw: "99999")
    answers = wizard.pick_approvals()
    assert answers["record_checks"] is True and answers["check_retention_days"] == 3650
    lines = wizard._cfg_approval({"record_checks": False, "check_retention_days": 30})
    assert "record_checks = false" in lines and "check_retention_days = 30" in lines
    assert approval_checks.validate_retention_days(30) == 30
    config.reset_config_cache()
