"""The All settings catalog only names settings the kernel really reads.

Each entry records the kernel module that reads it. These tests fail if an
entry names a config table the kernel does not know (``KNOWN_SECTIONS``), a
key its reader never mentions, an environment override its reader does not
consult, or a per-tool limit for a tool that does not exist -- so the page can
never offer a control that silently does nothing.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from maverick.migrate import KNOWN_SECTIONS
from maverick_dashboard import settings_catalog as catalog


def _source(module: str) -> str:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin, f"reader module {module} is missing"
    return Path(spec.origin).read_text(encoding="utf-8")


def _quoted(text: str, name: str) -> bool:
    return f'"{name}"' in text or f"'{name}'" in text


_CONFIG = _source("maverick.config")
_TOOLS_DIR = Path(importlib.util.find_spec("maverick.tools").origin).parent


@pytest.mark.parametrize("setting", catalog.SETTINGS, ids=lambda s: s.id)
def test_every_catalog_key_is_read_by_the_kernel(setting):
    assert setting.top in KNOWN_SECTIONS, f"{setting.top} is not a known config table"
    reader = _source(setting.reader)
    for part in setting.path[:-1]:
        # The table may be fetched through a config getter (get_budget_overrides,
        # get_billing_stripe) that the reader calls.
        assert _quoted(reader, part) or _quoted(_CONFIG, part), (
            f"{setting.id}: table {part!r} is not read by {setting.reader}"
        )
    if setting.key_kind == "tool":
        registered = any(
            f'name="{setting.key}"' in path.read_text(encoding="utf-8")
            for path in _TOOLS_DIR.glob("*.py")
        )
        assert registered, f"{setting.id}: {setting.key!r} is not a registered tool"
        assert _quoted(reader, setting.top)
    else:
        assert _quoted(reader, setting.key), (
            f"{setting.id}: key {setting.key!r} is not read by {setting.reader}"
        )
    for name in setting.env:
        assert name in reader, f"{setting.id}: {setting.reader} never reads {name}"


def test_catalog_is_well_formed_and_plain():
    ids = [s.id for s in catalog.SETTINGS]
    assert len(ids) == len(set(ids)), "duplicate catalog entries"
    for s in catalog.SETTINGS:
        assert s.group in catalog.GROUPS
        assert s.label and s.help.endswith(".")
        assert "_" not in s.label and "_" not in s.help, s.id
        assert "[" not in s.help and "MAVERICK" not in s.help, s.id
        for _value, label in s.choices:
            assert "_" not in label, s.id
        if s.warning:
            assert s.weaker is not None, s.id


def test_catalog_does_not_change_kernel_defaults():
    from maverick import config

    defaults = {
        "safety.profile": config.get_safety()["profile"],
        "safety.scan_input": config.get_safety()["scan_input"],
        "ifc.mode": config.get_ifc()["mode"],
        "swarm.max_total_spawns": config.get_swarm()["max_total_spawns"],
        "repl.max_seconds": config.get_repl()["max_seconds"],
        "harness_refine.require_approval": config.get_harness_refine()["require_approval"],
        "earned_autonomy.max_auto_risk": config.get_earned_autonomy()["max_auto_risk"],
        "commerce.max_session_spend": config.get_commerce()["max_session_spend"],
        "durable.keep_last": config.get_durable()["keep_last"],
        "flows.max_node_retries": config.get_flows()["max_node_retries"],
    }
    for setting_id, kernel_default in defaults.items():
        assert catalog.BY_ID[setting_id].default == kernel_default, setting_id


def test_coerce_validates_types_and_bounds():
    budget = catalog.BY_ID["budget.max_tool_calls"]
    assert catalog.coerce(budget, 25) == 25
    assert catalog.coerce(budget, None) is None  # no limit
    for bad in (0, 2.5, "lots", True):
        with pytest.raises(catalog.SettingError):
            catalog.coerce(budget, bad)
    profile = catalog.BY_ID["safety.profile"]
    assert catalog.coerce(profile, "strict") == "strict"
    with pytest.raises(catalog.SettingError):
        catalog.coerce(profile, "paranoid")
    toggle = catalog.BY_ID["audit.sign"]
    with pytest.raises(catalog.SettingError):
        catalog.coerce(toggle, "false")
    hooks = catalog.BY_ID["webhooks.outbound"]
    assert catalog.coerce(hooks, ["https://a.example/x", " ", "https://a.example/x"]) == [
        "https://a.example/x"
    ]
    with pytest.raises(catalog.SettingError):
        catalog.coerce(hooks, ["http://insecure.example"])
    rate = catalog.BY_ID["rate_limits.shell"]
    assert catalog.coerce(rate, {"count": 5, "seconds": 60}) == "5/60"
    with pytest.raises(catalog.SettingError):
        catalog.coerce(rate, {"count": 0, "seconds": 60})
    providers = catalog.BY_ID["routing.allowed_providers"]
    assert catalog.coerce(providers, []) is None  # empty never means "allow none"


def test_weakening_directions():
    w = catalog.weakens
    assert w(catalog.BY_ID["audit.sign"], True, False)
    assert not w(catalog.BY_ID["audit.sign"], False, True)
    assert w(catalog.BY_ID["safety.profile"], "balanced", "off")
    assert not w(catalog.BY_ID["safety.profile"], "balanced", "strict")
    assert w(catalog.BY_ID["security.approvals_required"], 2, 1)
    assert w(catalog.BY_ID["retention.audit_days"], None, 30)
    assert w(catalog.BY_ID["retention.audit_days"], 90, 30)
    assert not w(catalog.BY_ID["retention.audit_days"], 30, 90)
    assert w(catalog.BY_ID["a2a.enabled"], False, True)
    assert not w(catalog.BY_ID["persona.name"], "", "Ada")


def test_cross_checks_refuse_configurations_the_kernel_rejects():
    with pytest.raises(catalog.SettingError):
        catalog.cross_checks({"ai_cloud_agent_governance.enable": True,
                              "ai_cloud_agent_governance.enabled_providers": []})
    catalog.cross_checks({"ai_cloud_agent_governance.enable": True,
                          "ai_cloud_agent_governance.enabled_providers": ["aws_bedrock"]})
    with pytest.raises(catalog.SettingError):
        catalog.cross_checks({"residency.strict": True, "residency.region": ""})
