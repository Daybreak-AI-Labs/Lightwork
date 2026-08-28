"""Acceptance and adversarial tests for the governed AI regulatory bridge."""
from __future__ import annotations

import hashlib
import json

import pytest
from maverick import ai_regulatory_feed as arf
from maverick import governed_records
from maverick.governed_records import GovernedRecordStore
from maverick.paths import tenant_scope
from maverick.privacy_ops import RecordConflict


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    governed_records._reset_process_authority_pin_for_testing()
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    (tmp_path / "config.toml").write_text(
        "[finance_operations]\n"
        "enable = true\n"
        "\n[evidence_gateway]\n"
        "enable = true\n"
        "\n[ai_regulatory_feed]\n"
        "enable = true\n",
        encoding="utf-8",
    )
    from maverick import audit, config

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *_args, **_kwargs: True)
    config.reset_config_cache()
    yield
    config.reset_config_cache()
    governed_records._reset_process_authority_pin_for_testing()


def _alert(*, revision: int = 1, title: str = "EU AI rule changed") -> dict:
    return {
        "alert_id": "alert-one",
        "source_key": "eurlex-ai-act",
        "external_id": "oj-l-2024-1689",
        "document_version": revision,
        "content_sha256": _hex(f"content:{revision}"),
        "title": title,
        "jurisdiction": "eu",
        "record_url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
        "official_citation": "Regulation (EU) 2024/1689",
        "change_kind": "updated",
        "diff": {
            "article_73": {
                "before": "previous text",
                "after": "current text",
            }
        },
        "matched_regimes": ["eu-ai-act"],
        "matched_domains": ["ai-governance"],
        "citations": [
            {
                "source_name": "EUR-Lex",
                "feed_url": "https://eur-lex.europa.eu/",
                "retrieval_url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
                "record_url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
                "retrieved_at": "2026-08-28T12:00:00Z",
                "content_sha256": _hex(f"content:{revision}"),
                "official_citation": "Regulation (EU) 2024/1689",
            }
        ],
        "revision": revision,
    }


def _rules() -> list[arf.ImpactRule]:
    return [
        arf.ImpactRule(
            "eu-ai-act-core",
            regimes=("eu-ai-act",),
            policy_ids=("default",),
            control_ids=("CTRL-AI-INCIDENT",),
            asset_ids=("asset-one",),
            evidence_kinds=("evaluation",),
        )
    ]


def _service(*, clock=lambda: 1_777_000_000.0):
    calls: list[dict] = []

    def record(alert_id, **kwargs):
        calls.append({"alert_id": alert_id, **kwargs})
        return {"id": f"AER-{alert_id}", "revision": 1}

    service = arf.RegulatoryFeedBridge(
        staleness_store=GovernedRecordStore(
            "test_ai_regulatory_staleness",
            "ARS",
            "ai_regulatory_staleness",
        ),
        notice_store=GovernedRecordStore(
            "test_ai_regulatory_notices",
            "ARN",
            "ai_regulatory_customer_notice",
        ),
        impact_recorder=record,
        evidence_lister=lambda: [
            {
                "id": "MRE-evaluation-one",
                "asset_id": "asset-one",
                "evidence_kind": "evaluation",
            }
        ],
        clock=clock,
    )
    return service, calls


def test_config_uses_real_evidence_gateway_section_and_is_default_off(tmp_path):
    from maverick import config

    assert arf.get_ai_regulatory_feed_config() == {
        "enable": True,
        "finance_operations_enable": True,
        "ai_evidence_gateway_enable": True,
    }
    (tmp_path / "config.toml").write_text(
        "[finance_operations]\nenable = true\n"
        "[evidence_gateway]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    assert arf.enabled() is False


def test_query_impact_is_explicit_bounded_and_binds_exact_evidence():
    result = arf.query_impact(
        _alert(),
        _rules(),
        evidence_records=[
            {
                "id": "MRE-evaluation-one",
                "asset_id": "asset-one",
                "evidence_kind": "evaluation",
            }
        ],
    )
    assert result["matched_rule_ids"] == ["eu-ai-act-core"]
    assert result["affected_policy_ids"] == ["default"]
    assert result["affected_asset_ids"] == ["asset-one"]
    assert result["control_ids"] == ["CTRL-AI-INCIDENT"]
    assert result["stale_evidence_ids"] == ["MRE-evaluation-one"]
    assert result["query_sha256"]

    with pytest.raises(ValueError, match="credential-free HTTPS"):
        hostile = _alert()
        hostile["record_url"] = "https://user:secret@example.test/change"
        arf.query_impact(hostile, _rules())


def test_sync_delivery_and_resolved_state_are_idempotent():
    service, calls = _service()
    delivered: list[dict] = []
    with tenant_scope(tenant="alpha"):
        first = service.sync_alert(
            _alert(),
            rules=_rules(),
            actor="regulatory-owner",
            deliver=True,
            notification_sink=lambda payload: delivered.append(dict(payload)) or True,
        )
        assert first["notice"]["delivery"]["status"] == "sent"
        assert first["notice"]["delivery"]["attempts"] == 1
        assert len(delivered) == 1
        assert "previous text" in json.dumps(delivered[0])
        resolved = service.resolve_staleness(
            first["staleness"]["id"],
            expected_revision=first["staleness"]["revision"],
            resolution="Controls and exact evidence were reviewed and refreshed.",
            actor="control-owner",
        )
        assert resolved["state"] == "resolved"

        repeated = service.sync_alert(
            _alert(),
            rules=_rules(),
            actor="regulatory-owner",
            deliver=True,
            notification_sink=lambda payload: delivered.append(dict(payload)) or True,
        )
        assert repeated["notice"]["delivery"]["attempts"] == 1
        assert repeated["staleness"]["state"] == "resolved"
        assert len(delivered) == 1
        assert len(calls) == 2


def test_delivery_failure_is_retryable_and_does_not_persist_secret_message():
    service, _calls = _service()

    def fail(_payload):
        raise RuntimeError("provider secret sk-live-do-not-store")

    with tenant_scope(tenant="alpha"):
        first = service.sync_alert(
            _alert(),
            rules=_rules(),
            actor="regulatory-owner",
        )
        failed = service.deliver_notice(
            first["notice"]["id"],
            expected_revision=first["notice"]["revision"],
            actor="regulatory-owner",
            notification_sink=fail,
        )
        assert failed["delivery"] == {
            "status": "failed",
            "attempts": 1,
            "delivered_at": None,
            "last_error_type": "RuntimeError",
        }
        assert "sk-live" not in json.dumps(failed)
        sent = service.deliver_notice(
            first["notice"]["id"],
            expected_revision=failed["revision"],
            actor="regulatory-owner",
            notification_sink=lambda _payload: True,
        )
        assert sent["delivery"]["status"] == "sent"
        assert sent["delivery"]["attempts"] == 2


def test_records_are_tenant_bound_and_cas_resolution_rejects_stale_writer():
    service, _calls = _service()
    with tenant_scope(tenant="alpha"):
        alpha = service.sync_alert(
            _alert(), rules=_rules(), actor="regulatory-owner"
        )
        with pytest.raises(RecordConflict):
            service.resolve_staleness(
                alpha["staleness"]["id"],
                expected_revision=alpha["staleness"]["revision"] + 1,
                resolution="Stale writer must lose.",
                actor="control-owner",
            )
        assert len(service.list_notices()) == 1

    with tenant_scope(tenant="beta"):
        assert service.list_notices() == []
        beta = service.sync_alert(
            _alert(), rules=_rules(), actor="regulatory-owner"
        )
        assert beta["notice"]["id"] != alpha["notice"]["id"]
        assert service.get_notice(alpha["notice"]["id"]) is None


def test_same_bridge_identity_rejects_changed_regulatory_content():
    service, _calls = _service()
    with tenant_scope(tenant="alpha"):
        service.sync_alert(_alert(), rules=_rules(), actor="regulatory-owner")
        with pytest.raises(RecordConflict, match="reused with different content"):
            service.sync_alert(
                _alert(title="Conflicting title"),
                rules=_rules(),
                actor="regulatory-owner",
            )


def test_finance_alert_sync_reads_the_authoritative_record_by_id():
    service, _calls = _service()

    class FakeEngine:
        def __init__(self):
            self.requested: list[str] = []

        def get_alert(self, alert_id):
            self.requested.append(alert_id)
            return _alert() if alert_id == "alert-one" else None

    engine = FakeEngine()
    with tenant_scope(tenant="alpha"):
        result = service.sync_finance_alert(
            "alert-one",
            rules=_rules(),
            actor="regulatory-owner",
            engine=engine,
        )
        assert result["notice"]["alert_id"] == "alert-one"
        assert engine.requested == ["alert-one"]
        with pytest.raises(arf.AIRegulatoryFeedStateError, match="does not exist"):
            service.sync_finance_alert(
                "missing-alert",
                rules=_rules(),
                actor="regulatory-owner",
                engine=engine,
            )
