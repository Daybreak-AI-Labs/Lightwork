"""The DSAR estate worksheet: where a human looks beyond the platform.

The assertions that matter: the worksheet reflects the platform's own
holdings, surfaces only identifying data-map assets, lists active vendors
as processors, and degrades every absent module to ``enabled: false``
instead of failing.
"""
from __future__ import annotations

import pytest
from maverick import config, privacy_ops


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
        "[privacy_ops]\nenable = true\n\n"
        "[data_map]\nenable = true\n\n"
        "[vendor_management]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    yield tmp_path
    config.reset_config_cache()


def test_worksheet_builds_the_estate_search_plan():
    from maverick import data_map, vendor_management

    request = privacy_ops.open_dsar(
        "alice@example.com", kind="access", opened_by="ops",
    )
    data_map.register_data_asset(
        "crm.contacts", system="snowflake", kind="table", owner="data",
        actor="ops", columns=["id", "email", "phone"],
    )
    # A flagged-but-non-identifying asset stays off the worksheet.
    data_map.register_data_asset(
        "vault.secrets", system="snowflake", kind="table", owner="sec",
        actor="ops", columns=["api_key"],
    )
    vendor_management.register_vendor(
        "Acme Analytics", category="analytics", criticality="high",
        owner="procurement", actor="ops",
    )

    plan = privacy_ops.dsar_estate_worksheet(request["id"])
    assert plan["request_id"] == request["id"]
    assert plan["kind"] == "access"
    assert plan["platform_holdings"]["enabled"] is True
    assert isinstance(plan["platform_holdings"]["sections"], dict)
    assert [asset["name"] for asset in plan["data_map"]["assets"]] == [
        "crm.contacts"
    ]
    assert plan["data_map"]["assets"][0]["categories"] == ["email", "phone"]
    assert plan["processors"]["vendors"][0]["name"] == "Acme Analytics"
    # Document discovery has no configured sources here: no hits, and
    # discover() contributes nothing rather than erroring.
    assert plan["documents"].get("hits", []) == []


def test_worksheet_degrades_when_the_estate_modules_are_off(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[privacy_ops]\nenable = true\n", encoding="utf-8",
    )
    config.reset_config_cache()
    request = privacy_ops.open_dsar(
        "bob@example.com", kind="erasure", opened_by="ops",
    )
    plan = privacy_ops.dsar_estate_worksheet(request["id"])
    assert plan["data_map"]["enabled"] is False
    assert plan["processors"]["enabled"] is False
    assert plan["platform_holdings"]["enabled"] is True
    with pytest.raises(KeyError):
        privacy_ops.dsar_estate_worksheet("DSAR-nope")
