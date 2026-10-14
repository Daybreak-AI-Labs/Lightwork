"""Intent value settings remain separate from legacy savings assumptions."""
from __future__ import annotations

import pytest
from maverick.config import get_intent_value
from maverick.config_lint import lint_config
from maverick.migrate import KNOWN_SECTIONS


def test_intent_value_defaults_do_not_require_company_setup_or_confirmation():
    assert get_intent_value(config={}) == {"enable": True, "confirmation_required": False}
    assert get_intent_value(config={"value": {"enable": False, "hourly_rate": 999}}) == {
        "enable": True, "confirmation_required": False,
    }


def test_intent_value_reads_effective_config(monkeypatch):
    monkeypatch.setattr("maverick.config.load_config", lambda: {
        "intent_value": {"enable": False, "confirmation_required": True},
    })
    assert get_intent_value() == {"enable": False, "confirmation_required": True}


@pytest.mark.parametrize("bad", ["true", "false", 1, 0, None, [], {}])
def test_intent_value_rejects_ambiguous_boolean_values(bad):
    assert get_intent_value(config={"intent_value": {
        "enable": bad, "confirmation_required": bad,
    }}) == {"enable": False, "confirmation_required": False}


@pytest.mark.parametrize("bad", [None, False, [], "enabled"])
def test_intent_value_malformed_section_does_not_enable_reporting(bad):
    assert get_intent_value(config={"intent_value": bad}) == {
        "enable": False, "confirmation_required": False,
    }


def test_intent_value_lint_and_migrate_recognize_settings():
    assert "intent_value" in KNOWN_SECTIONS
    findings = lint_config({"intent_value": {"enable": True, "confirmation_required": False}})
    assert not findings, [f.message for f in findings]
    findings = lint_config({"intent_value": {"confirmation_required": "false"}})
    assert any("must be true or false" in f.message.lower() for f in findings)
