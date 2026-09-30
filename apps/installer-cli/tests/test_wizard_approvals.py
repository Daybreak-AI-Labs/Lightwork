"""Approvals wizard step: undo window + high-risk confirmation -> [approval]."""
from __future__ import annotations


def test_pick_approvals_defaults(monkeypatch):
    from maverick_installer import wizard

    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **kw: kw["default"])
    monkeypatch.setattr(wizard, "_q_confirm", lambda *_a, **kw: kw["default"])
    assert wizard.pick_approvals() == {"undo_grace_seconds": 30, "require_acknowledgement": True}


def test_pick_approvals_bounds_and_choices(monkeypatch):
    from maverick_installer import wizard

    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **_kw: "99999")
    monkeypatch.setattr(wizard, "_q_confirm", lambda *_a, **_kw: False)
    assert wizard.pick_approvals() == {"undo_grace_seconds": 3600, "require_acknowledgement": False}
    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **_kw: "not a number")
    assert wizard.pick_approvals()["undo_grace_seconds"] == 30


def test_step_and_config_section():
    from maverick_installer import wizard

    assert ("approvals", "Approvals") in wizard.STEPS
    lines = wizard._cfg_approval({"undo_grace_seconds": 45, "require_acknowledgement": True})
    assert lines[1] == "[approval]"
    assert "undo_grace_seconds = 45" in lines
    assert "require_acknowledgement = true" in lines
    assert wizard._cfg_approval(None) == []
