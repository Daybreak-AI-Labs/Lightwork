"""Intent reporting setup preserves explicit choices in every setup mode."""
from __future__ import annotations

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- Python 3.10
    import tomli as tomllib

from maverick.config import get_intent_value
from maverick_installer import wizard


def test_intent_picker_defaults_to_reporting_without_confirmation(monkeypatch):
    prompts = []

    def confirm(message, *, default):
        prompts.append((message, default))
        return default

    monkeypatch.setattr(wizard, "_q_confirm", confirm)
    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **_kw: pytest.fail("mandatory baseline setup"))
    assert wizard.pick_intent_value() == {"enable": True, "confirmation_required": False}
    assert [default for _, default in prompts] == [True, False]
    assert "execution evidence" in prompts[1][0]


def test_disabled_reporting_skips_completion_prompt(monkeypatch):
    prompts = []

    def decline(message, *, default):
        prompts.append(message)
        return False

    monkeypatch.setattr(wizard, "_q_confirm", decline)
    assert wizard.pick_intent_value() == {"enable": False, "confirmation_required": False}
    assert len(prompts) == 1


def test_confirmation_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setattr(wizard, "_q_confirm", lambda *_a, **_kw: True)
    assert wizard.pick_intent_value() == {"enable": True, "confirmation_required": True}


@pytest.mark.parametrize("choice", [None, {}, {"enable": False}, {"confirmation_required": True}])
def test_installer_settings_round_trip_to_runtime(choice):
    parsed = tomllib.loads("\n".join(wizard._cfg_intent_value(choice)))
    expected = {"enable": True, "confirmation_required": False, **(choice or {})}
    assert parsed == {"intent_value": expected}
    assert get_intent_value(config=parsed) == expected


@pytest.mark.parametrize("key", ["enable", "confirmation_required"])
def test_installer_rejects_ambiguous_boolean_settings(key):
    with pytest.raises(ValueError, match="must be boolean"):
        wizard._cfg_intent_value({key: "false"})


def _isolate_config(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wizard, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(wizard, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(wizard, "_clear_partial", lambda: None)
    monkeypatch.setattr(wizard, "_docker_available", lambda: False)
    monkeypatch.setattr(wizard, "_first_setup_guard", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_full_writer_preserves_explicit_disable_and_separate_savings(tmp_path, monkeypatch):
    _isolate_config(monkeypatch, tmp_path)
    wizard.write_config(
        providers=["anthropic"], role_models={}, channels={}, safety={}, budget={},
        sandbox={"backend": "local", "workdir": str(tmp_path / "work")}, keys={},
        intent_value={"enable": False, "confirmation_required": True},
        value={"hourly_rate": 90.0, "hours_per_task": 0.5},
    )
    parsed = tomllib.loads(wizard.CONFIG_FILE.read_text(encoding="utf-8"))
    assert get_intent_value(config=parsed) == {"enable": False, "confirmation_required": True}
    assert parsed["value"] == {"hourly_rate": 90.0, "hours_per_task": 0.5}


@pytest.mark.parametrize("mode", ["run_fast", "run_consumer", "run_express"])
def test_short_setup_modes_persist_same_nonintrusive_defaults(tmp_path, monkeypatch, mode):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(wizard, "preflight", lambda: True)
    monkeypatch.setattr(wizard, "smoke_test", lambda: True)
    monkeypatch.setattr(wizard, "welcome", lambda: None)
    monkeypatch.setattr(wizard, "_consumer_api_key", dict)
    monkeypatch.setattr(wizard, "_consumer_budget", dict)
    monkeypatch.setattr(wizard, "pick_governance_profile", lambda: "essentials")
    monkeypatch.setattr(wizard, "pick_first_outcome", lambda: "finance")
    monkeypatch.setattr(wizard, "_q_text", lambda *_a, **_kw: str(tmp_path / "work"))
    monkeypatch.setattr(wizard, "_q_confirm", lambda *_a, **kw: kw.get("default", False))
    monkeypatch.setattr(wizard, "pick_intent_value", lambda: pytest.fail("short setup asked extra questions"))
    getattr(wizard, mode)()
    parsed = tomllib.loads(wizard.CONFIG_FILE.read_text(encoding="utf-8"))
    assert parsed["intent_value"] == {"enable": True, "confirmation_required": False}


def test_advanced_resume_preserves_answer_without_reasking(monkeypatch):
    state = {}
    # Fill unrelated picks so the test exercises the real contiguous resume block.
    for key, _ in wizard.STEPS:
        state[key] = {"configured": True}
    state.update(approval={"configured": True}, workspace={"configured": True},
                 oauth={"configured": True}, advanced={"api_keys": False},
                 intent_value={"enable": False, "confirmation_required": False})
    monkeypatch.setattr(wizard, "_save_partial", lambda _state: None)
    monkeypatch.setattr(wizard, "pick_intent_value", lambda: pytest.fail("reasked saved decision"))
    result = wizard._run_simple_picks(state, lambda: None)
    assert result["intent_value"] == {"enable": False, "confirmation_required": False}
