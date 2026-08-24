"""The G12 regulatory-change bridge: matching, records, notices, tenancy.

The bridge is deliberately dumb about law: an alert only produces impact when
an operator-reviewed :class:`ImpactRule` matches its scope, and everything it
writes is a bounded projection with the upstream authority carried by digest.
These tests drive it entirely through its injected seams — no live finance
feed, gateway, or notifier is represented as accepted.
"""
from __future__ import annotations

import hashlib
import time

import pytest
from maverick import ai_regulatory_feed as feed
from maverick import config
from maverick.privacy_ops import RecordConflict


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    from maverick import audit

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *_args, **_kwargs: True)
    (tmp_path / "config.toml").write_text(
        "[ai_regulatory_feed]\nenable = true\n"
        "\n[finance_operations]\nenable = true\n"
        "\n[ai_evidence_gateway]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _citation() -> dict:
    return {
        "source_name": "EUR-Lex",
        "feed_url": "https://eur-lex.europa.eu/feed",
        "retrieval_url": "https://eur-lex.europa.eu/retrieve",
        "record_url": "https://eur-lex.europa.eu/eli/reg/2024/1689",
        "retrieved_at": "2026-08-23T12:00:00+00:00",
        "content_sha256": _hex("citation"),
    }


def _alert(**overrides) -> dict:
    base = {
        "alert_id": "ALRT-omnibus-1",
        "source_key": "eu-oj",
        "external_id": "reg-2024-1689",
        "document_version": 2,
        "revision": 3,
        "content_sha256": _hex("consolidated-2026-07-27"),
        "title": "Digital Omnibus defers Annex III high-risk obligations",
        "jurisdiction": "EU",
        "record_url": "https://eur-lex.europa.eu/eli/reg/2024/1689/2026-07-27",
        "official_citation": "Regulation (EU) 2024/1689",
        "change_kind": "amended",
        "diff": {
            "high_risk_applicability": {
                "before": "2026-08-02",
                "after": "2027-12-02",
            },
        },
        "matched_regimes": ["eu-ai-act"],
        "matched_domains": [],
        "citations": [_citation()],
    }
    base.update(overrides)
    return base


def _rule(**overrides) -> dict:
    base = {
        "rule_id": "rule-eu-ai-act",
        "regimes": ("eu-ai-act",),
        "policy_ids": ("policy-ai-governance",),
        "control_ids": ("ctrl-annex-iii-readiness",),
        "asset_ids": ("MAA-credit",),
        "evidence_kinds": ("evaluation",),
    }
    base.update(overrides)
    return base


def _evidence() -> list[dict]:
    return [
        {"id": "MRE-eval-1", "asset_id": "MAA-credit", "evidence_kind": "evaluation"},
        {"id": "MRE-other", "asset_id": "MAA-other", "evidence_kind": "training_run"},
    ]


def _bridge(recorded: list | None = None, clock=None) -> feed.RegulatoryFeedBridge:
    impacts = recorded if recorded is not None else []

    def recorder(alert_id, **kwargs):
        # The real gateway recorder is idempotent per alert; the fake must be
        # too, or the bridge's stable staleness/notice ids cannot recover.
        impacts.append({"alert_id": alert_id, **kwargs})
        stable = _hex(f"impact:{alert_id}")[:12]
        return {"id": f"ARI-{stable}", "alert_id": alert_id, **kwargs}

    return feed.RegulatoryFeedBridge(
        impact_recorder=recorder,
        evidence_lister=_evidence,
        clock=clock or time.time,
    )


# ---- matching --------------------------------------------------------------


def test_query_impact_matches_only_operator_reviewed_scope():
    matched = feed.query_impact(
        _alert(), [_rule()], evidence_records=_evidence()
    )
    assert matched["matched_rule_ids"] == ["rule-eu-ai-act"]
    assert matched["affected_policy_ids"] == ["policy-ai-governance"]
    assert matched["control_ids"] == ["ctrl-annex-iii-readiness"]
    # Stale evidence resolves from BOTH the rule's asset and evidence-kind
    # selectors, and never sweeps in unrelated records.
    assert matched["stale_evidence_ids"] == ["MRE-eval-1"]
    assert matched["query_sha256"]

    unmatched = feed.query_impact(
        _alert(matched_regimes=["us-colorado"]),
        [_rule()],
        evidence_records=_evidence(),
    )
    assert unmatched["matched_rule_ids"] == []
    assert unmatched["stale_evidence_ids"] == []

    with pytest.raises(feed.AIRegulatoryFeedStateError, match="unknown evidence"):
        feed.query_impact(
            _alert(),
            [_rule(evidence_ids=("MRE-ghost",))],
            evidence_records=_evidence(),
        )


def test_alert_validation_fails_closed_on_hostile_input():
    with pytest.raises(ValueError, match="record_url"):
        feed.query_impact(
            _alert(record_url="http://insecure.example"), [_rule()]
        )
    with pytest.raises(ValueError, match="control characters"):
        feed.query_impact(
            _alert(diff={"field": {"before": "a\x00b", "after": "c"}}), [_rule()]
        )
    with pytest.raises(ValueError, match="matched tenant scope"):
        feed.query_impact(
            _alert(matched_regimes=[], matched_domains=[]), [_rule()]
        )
    with pytest.raises(ValueError, match="regime or domain"):
        feed.ImpactRule(rule_id="r", policy_ids=("p",))
    # Oversized nested diff values are digested and redacted, never copied.
    safe = feed.query_impact(
        _alert(diff={"annex": {"before": {"huge": "x" * 5000}, "after": "ok"}}),
        [_rule()],
    )
    assert safe["matched_rule_ids"] == ["rule-eu-ai-act"]


# ---- sync: impact + staleness + notice, idempotently ------------------------


def test_sync_alert_creates_bound_records_and_recovers_idempotently():
    impacts: list = []
    bridge = _bridge(impacts)
    first = bridge.sync_alert(_alert(), rules=[_rule()], actor="governance-lead")

    assert impacts[0]["alert_id"] == "ALRT-omnibus-1"
    staleness = first["staleness"]
    assert staleness["state"] == "active"
    assert staleness["stale_evidence_ids"] == ["MRE-eval-1"]
    assert staleness["content_sha256"] == _hex("consolidated-2026-07-27")
    notice = first["notice"]
    assert notice["delivery"]["status"] == "pending"
    assert notice["notice"] == feed.NON_LEGAL_NOTICE
    assert notice["title"].startswith("Digital Omnibus")

    # Re-syncing the same alert recovers the same records — no duplicates and
    # exactly one additional upstream impact write.
    again = bridge.sync_alert(_alert(), rules=[_rule()], actor="governance-lead")
    assert again["staleness"]["id"] == staleness["id"]
    assert again["notice"]["id"] == notice["id"]
    assert bridge.list_notices() == [again["notice"]]

    with pytest.raises(feed.AIRegulatoryFeedStateError, match="operator-reviewed"):
        bridge.sync_alert(
            _alert(matched_regimes=["us-colorado"]),
            rules=[_rule()],
            actor="governance-lead",
        )


def test_notice_delivery_is_cas_gated_bounded_and_terminal():
    bridge = _bridge(clock=lambda: 1_756_000_000.0)
    seen: list[dict] = []
    result = bridge.sync_alert(
        _alert(),
        rules=[_rule()],
        actor="governance-lead",
        deliver=True,
        notification_sink=lambda payload: not seen.append(dict(payload)),
    )
    notice = result["notice"]
    assert notice["delivery"] == {
        "status": "sent",
        "attempts": 1,
        "delivered_at": 1_756_000_000.0,
        "last_error_type": None,
    }
    # The delivered projection is the bounded notice — no tenant hash, no
    # credentials, no upstream document body.
    assert len(seen) == 1
    payload = seen[0]
    assert payload["idempotency_key"] == notice["id"]
    assert "tenant_sha256" not in payload
    assert payload["notice"] == feed.NON_LEGAL_NOTICE

    # A sent notice is terminal: re-delivery returns it without invoking any
    # sink again, and a stale revision cannot resurrect delivery.
    again = bridge.deliver_notice(
        notice["id"],
        expected_revision=int(notice["revision"]),
        actor="governance-lead",
        notification_sink=lambda payload: seen.append(dict(payload)),
    )
    assert again["delivery"]["status"] == "sent"
    assert len(seen) == 1


def test_failed_delivery_records_error_type_and_allows_retry():
    bridge = _bridge()
    result = bridge.sync_alert(_alert(), rules=[_rule()], actor="lead")
    notice = result["notice"]

    def explode(_payload):
        raise TimeoutError("provider unreachable")

    failed = bridge.deliver_notice(
        notice["id"],
        expected_revision=int(notice["revision"]),
        actor="lead",
        notification_sink=explode,
    )
    assert failed["delivery"]["status"] == "failed"
    assert failed["delivery"]["attempts"] == 1
    assert failed["delivery"]["last_error_type"] == "TimeoutError"
    assert failed["delivery"]["delivered_at"] is None

    with pytest.raises(RecordConflict):
        bridge.deliver_notice(
            notice["id"],
            expected_revision=int(notice["revision"]),
            actor="lead",
            notification_sink=lambda payload: True,
        )
    recovered = bridge.deliver_notice(
        notice["id"],
        expected_revision=int(failed["revision"]),
        actor="lead",
        notification_sink=lambda payload: True,
    )
    assert recovered["delivery"]["status"] == "sent"
    assert recovered["delivery"]["attempts"] == 2


def test_staleness_resolution_is_a_human_cas_assertion():
    bridge = _bridge()
    result = bridge.sync_alert(_alert(), rules=[_rule()], actor="lead")
    staleness = result["staleness"]
    assert bridge.list_active_staleness(evidence_id="MRE-eval-1")

    resolved = bridge.resolve_staleness(
        staleness["id"],
        expected_revision=int(staleness["revision"]),
        resolution="Evaluation evidence re-run against the deferred deadline.",
        actor="risk-reviewer",
    )
    assert resolved["state"] == "resolved"
    assert resolved["resolution"]["actor"] == "risk-reviewer"
    assert bridge.list_active_staleness() == []
    with pytest.raises(RecordConflict):
        bridge.resolve_staleness(
            staleness["id"],
            expected_revision=int(resolved["revision"]),
            resolution="Already resolved records stay resolved.",
            actor="risk-reviewer",
        )


# ---- tenancy and gating -----------------------------------------------------


def test_records_are_tenant_bound(monkeypatch):
    bridge = _bridge()
    result = bridge.sync_alert(_alert(), rules=[_rule()], actor="lead")
    notice_id = result["notice"]["id"]

    # Storage is tenant-scoped, so another tenant cannot even see the record
    # — stronger than the tenant_sha256 binding check, which remains as
    # defense-in-depth for a shared multi-replica backend.
    monkeypatch.setenv("MAVERICK_TENANT", "globex")
    assert bridge.get_notice(notice_id) is None
    with pytest.raises(feed.AIRegulatoryFeedStateError, match="does not exist"):
        bridge.deliver_notice(
            notice_id,
            expected_revision=1,
            actor="lead",
            notification_sink=lambda payload: True,
        )
    assert bridge.list_notices() == []
    assert bridge.list_active_staleness() == []

    # And the binding check itself fires when a foreign-tenant row appears in
    # the store (a shared backend scenario), never returning the record.
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    foreign = dict(bridge._notices.get(notice_id))  # noqa: SLF001 - test rig
    foreign["tenant_sha256"] = "0" * 64
    monkeypatch.setattr(bridge._notices, "get", lambda _id: foreign)  # noqa: SLF001
    with pytest.raises(feed.AIRegulatoryFeedStateError, match="tenant binding"):
        bridge.get_notice(notice_id)


def test_bridge_is_off_unless_all_three_authorities_are_enabled(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[ai_regulatory_feed]\nenable = true\n"
        "\n[finance_operations]\nenable = false\n"
        "\n[ai_evidence_gateway]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    assert feed.enabled() is False
    with pytest.raises(feed.AIRegulatoryFeedConfigError, match="requires"):
        _bridge().sync_alert(_alert(), rules=[_rule()], actor="lead")
