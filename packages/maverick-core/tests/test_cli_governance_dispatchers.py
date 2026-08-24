"""The cron-able governance dispatchers: red-team, regulatory-feed, inventory.

Each dispatcher must isolate per-item failures (one bad schedule/alert/
connector never hides the rest), exit non-zero when anything failed, and
pass the operator identity through unchanged.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from maverick.cli import main


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_red_team_run_due_reports_and_fails_on_partial_dispatch(
    runner, monkeypatch,
):
    from maverick import red_team_assurance as rta

    calls: dict = {}

    def run_due(*, actor: str, limit: int) -> dict:
        calls["args"] = {"actor": actor, "limit": limit}
        return {
            "campaigns": [
                {"id": "RTC-one", "target_binding": {"asset_id": "MAA-" + "a" * 32}},
            ],
            "failures": [{"schedule_id": "RTS-two", "reason": "target retired"}],
            "count": 1,
            "failure_count": 1,
            "due_count": 2,
            "truncated": False,
            "limit": limit,
        }

    monkeypatch.setattr(rta, "run_due_schedules", run_due)
    result = runner.invoke(
        main, ["red-team", "run-due", "--limit", "5", "--by", "cron:ops"],
    )
    assert result.exit_code != 0
    assert calls["args"] == {"actor": "cron:ops", "limit": 5}
    assert "materialized 1 of 2 due" in result.output
    assert "campaign RTC-one" in result.output
    assert "FAILED RTS-two: target retired" in result.output


def test_red_team_run_due_succeeds_quietly_when_nothing_is_due(
    runner, monkeypatch,
):
    from maverick import red_team_assurance as rta

    monkeypatch.setattr(
        rta,
        "run_due_schedules",
        lambda *, actor, limit: {
            "campaigns": [], "failures": [], "count": 0,
            "failure_count": 0, "due_count": 0, "truncated": False,
            "limit": limit,
        },
    )
    result = runner.invoke(main, ["red-team", "run-due", "--by", "cron:ops"])
    assert result.exit_code == 0, result.output
    assert "materialized 0 of 0 due" in result.output


def test_regulatory_feed_sync_bridges_reviewed_alerts_with_isolation(
    runner, monkeypatch, tmp_path,
):
    from types import SimpleNamespace

    from maverick import ai_regulatory_feed as feed
    from maverick.finance import regulatory_change

    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps([{"regime": "eu_ai_act", "control_ids": ["CTRL-1"]}]),
        encoding="utf-8",
    )
    alerts = [
        SimpleNamespace(alert_id="RA-1"),
        SimpleNamespace(alert_id="RA-2"),
        SimpleNamespace(alert_id="RA-3"),
    ]

    class FakeEngine:
        def __init__(self, path):
            self.path = path

        def list_alerts(self, *, status, limit):
            assert status == "accepted"
            assert limit == 100
            return alerts

    monkeypatch.setattr(regulatory_change, "RegulatoryChangeEngine", FakeEngine)

    seen: list[tuple] = []

    def sync_alert(alert, *, rules, actor, deliver):
        seen.append((alert.alert_id, actor, deliver))
        assert rules == [{"regime": "eu_ai_act", "control_ids": ["CTRL-1"]}]
        if alert.alert_id == "RA-2":
            raise ValueError("no rule matched the alert scope")
        if alert.alert_id == "RA-3":
            return {"impact_query": {"matched": False}, "impact": None,
                    "staleness": [], "notice": None}
        return {
            "impact_query": {"matched": True},
            "impact": {"id": "ARI-1"},
            "staleness": [{"id": "ARS-1"}],
            "notice": {"id": "ARN-1", "status": "pending"},
        }

    monkeypatch.setattr(feed, "sync_alert", sync_alert)
    result = runner.invoke(
        main,
        ["regulatory-feed", "sync", "--rules", str(rules_path), "--by", "cron:grc"],
    )
    assert result.exit_code != 0
    assert [row[0] for row in seen] == ["RA-1", "RA-2", "RA-3"]
    assert all(row[1] == "cron:grc" and row[2] is False for row in seen)
    # One bridged with records, one unmatched (silent), one failed loudly.
    assert "RA-1: impact ARI-1  stale-evidence 1  notice ARN-1 [pending]" in result.output
    assert "bridged 1 of 3 accepted alert(s)" in result.output
    assert "FAILED RA-2: no rule matched" in result.output


def test_regulatory_feed_sync_refuses_a_malformed_rules_file(
    runner, tmp_path,
):
    empty = tmp_path / "rules.json"
    empty.write_text("{}", encoding="utf-8")
    result = runner.invoke(
        main,
        ["regulatory-feed", "sync", "--rules", str(empty), "--by", "cron:grc"],
    )
    assert result.exit_code != 0
    assert "non-empty JSON list" in result.output


def test_ai_inventory_sync_sweeps_configured_connectors_with_isolation(
    runner, monkeypatch,
):
    from maverick import ai_inventory

    monkeypatch.setattr(
        ai_inventory,
        "get_ai_inventory_config",
        lambda: {"enable": True, "connectors": ["bedrock", "vertex_ai"]},
    )
    synced: list[tuple[str, str]] = []

    def sync_connector(name, *, actor):
        synced.append((name, actor))
        if name == "vertex_ai":
            raise ai_inventory.DiscoveryTransportError("credential env is unset")
        return {"status": "complete", "asset_count": 7}

    monkeypatch.setattr(ai_inventory, "sync_connector", sync_connector)
    result = runner.invoke(main, ["ai-inventory", "sync", "--by", "cron:ops"])
    assert result.exit_code != 0
    assert synced == [("bedrock", "cron:ops"), ("vertex_ai", "cron:ops")]
    assert "bedrock: complete  assets 7" in result.output
    assert "synced 1 of 2 connector(s)" in result.output
    assert "FAILED vertex_ai: credential env is unset" in result.output

    # Naming one connector narrows the sweep.
    synced.clear()
    result = runner.invoke(
        main,
        ["ai-inventory", "sync", "--connector", "bedrock", "--by", "cron:ops"],
    )
    assert result.exit_code == 0, result.output
    assert synced == [("bedrock", "cron:ops")]


def test_ai_inventory_sync_requires_a_configured_or_named_connector(
    runner, monkeypatch,
):
    from maverick import ai_inventory

    monkeypatch.setattr(
        ai_inventory,
        "get_ai_inventory_config",
        lambda: {"enable": True, "connectors": []},
    )
    result = runner.invoke(main, ["ai-inventory", "sync", "--by", "cron:ops"])
    assert result.exit_code != 0
    assert "no connectors configured" in result.output
