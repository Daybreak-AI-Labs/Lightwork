"""Focused contracts for consent-gated product red-team campaigns."""
from __future__ import annotations

import hashlib
import json

import pytest
from maverick import red_team_campaigns as campaigns
from maverick.governed_records import GovernedRecordStore


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    from maverick import audit, config

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *_args, **_kwargs: True)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


class _ScriptedTarget:
    def __init__(self) -> None:
        self.attack_blocked = True
        self.retry_once = False
        self.fail_with_raw_output = False
        self.invocations: list[campaigns.RedTeamInvocation] = []

    def invoke(self, invocation: campaigns.RedTeamInvocation) -> campaigns.RedTeamTargetResult:
        self.invocations.append(invocation)
        if self.retry_once and invocation.case_id == "attack-01":
            self.retry_once = False
            raise campaigns.RedTeamCampaignRetryableError("network_reset")
        if self.fail_with_raw_output:
            raise RuntimeError("PRIVATE TARGET RESPONSE MUST NEVER BE PERSISTED")
        if invocation.case_id == "attack-01" and self.attack_blocked:
            return campaigns.RedTeamTargetResult("blocked", ("target_blocked",))
        return campaigns.RedTeamTargetResult("allowed", ("target_allowed",))


def _corpus() -> campaigns.RedTeamCorpus:
    return campaigns.RedTeamCorpus(
        "synthetic-shield-corpus",
        "test-v1",
        (
            campaigns.RedTeamCorpusCase(
                "attack-01",
                "SYNTHETIC ATTACK PROMPT MUST NEVER ENTER A CAMPAIGN REPORT",
                "block",
                "prompt_injection",
            ),
            campaigns.RedTeamCorpusCase(
                "allow-01",
                "SYNTHETIC SAFE PROMPT MUST NEVER ENTER A CAMPAIGN REPORT",
                "allow",
                "false_positive_floor",
            ),
        ),
    )


def _service(*, now, target=None, max_attempts=3):
    target = target or _ScriptedTarget()
    adapter = campaigns.RedTeamTargetAdapter(
        adapter_id="target-one",
        target_kind="mcp_upstream",
        environment="consenting_test",
        configuration_sha256=_digest("target-config-v1"),
        consent_reference_sha256=_digest("consent-record-v1"),
        consent_confirmed=True,
        invoke=target.invoke,
    )
    stores = {
        "specs": GovernedRecordStore("rt_product_specs_test", "RTCS", "red_team_campaign_spec"),
        "runs": GovernedRecordStore("rt_product_runs_test", "RTCR", "red_team_campaign_run"),
        "reports": GovernedRecordStore("rt_product_reports_test", "RTCP", "red_team_campaign_report"),
        "drifts": GovernedRecordStore("rt_product_drifts_test", "RTCD", "red_team_campaign_drift"),
    }
    service = campaigns.RedTeamCampaignService(
        target_adapters=(adapter,),
        corpus_loader=_corpus,
        specs=stores["specs"],
        runs=stores["runs"],
        reports=stores["reports"],
        drifts=stores["drifts"],
        clock=lambda: now[0],
        lease_seconds=60,
        max_attempts=max_attempts,
    )
    return service, target, adapter, stores


def _spec(service, *, key="campaign-one", cadence=60):
    return service.create_campaign_spec(
        target_adapter_id="target-one",
        idempotency_key=key,
        actor="system:test",
        cadence_seconds=cadence,
    )


def test_multiline_synthetic_corpus_case_is_ephemeral_and_digest_bound():
    case = campaigns.RedTeamCorpusCase(
        "multiline-case",
        "synthetic first line\nsynthetic second line",
        "block",
        "delimiter_test",
    )
    corpus = campaigns.RedTeamCorpus("synthetic-corpus", "test-v1", (case,))

    assert case.text == "synthetic first line\nsynthetic second line"
    assert len(corpus.sha256) == 64


def test_optional_shield_corpus_adapter_returns_only_closed_status_metadata():
    pytest.importorskip("maverick_shield")
    corpus = campaigns.load_shield_corpus()
    adapter = campaigns.make_shield_pack_adapter(
        adapter_id="local-shield",
        environment="consenting_test",
        configuration_sha256=_digest("shield-config"),
        consent_reference_sha256=_digest("shield-consent"),
        consent_confirmed=True,
    )
    case = next(item for item in corpus.cases if item.expected == "block")

    result = adapter.invoke(
        campaigns.RedTeamInvocation(
            "spec",
            "run",
            "invocation",
            case.case_id,
            case.text,
            case.expected,
            case.category,
        )
    )

    assert len(corpus.cases) > 0
    assert result.decision in {"blocked", "allowed", "inconclusive"}
    assert set(result.signal_codes).issubset(
        {"shield_allowed", "shield_blocked"}
    )


def test_only_explicitly_configured_and_consented_targets_can_be_scheduled():
    now = [1000.0]
    service, _target, adapter, _stores = _service(now=now)

    with pytest.raises(ValueError, match="fixed non-content"):
        campaigns.RedTeamTargetResult("blocked", ("private_target_response",))

    with pytest.raises(campaigns.RedTeamCampaignConsentError, match="configured"):
        service.create_campaign_spec(
            target_adapter_id="not-configured",
            idempotency_key="missing-target",
            actor="system:test",
            cadence_seconds=60,
        )

    revoked = campaigns.RedTeamTargetAdapter(
        adapter_id=adapter.adapter_id,
        target_kind=adapter.target_kind,
        environment=adapter.environment,
        configuration_sha256=adapter.configuration_sha256,
        consent_reference_sha256=adapter.consent_reference_sha256,
        consent_confirmed=False,
        invoke=adapter.invoke,
    )
    service.register_target_adapter(revoked)
    with pytest.raises(campaigns.RedTeamCampaignConsentError, match="consent"):
        _spec(service, key="unconsented-target")


def test_campaign_run_report_and_drift_are_durable_but_content_free():
    now = [1_000.0]
    service, _target, _adapter, stores = _service(now=now)
    spec = _spec(service)

    dispatched = service.run_due_campaigns(actor="system:runner", limit=5)

    assert dispatched["count"] == 1
    run = dispatched["runs"][0]
    report = service.get_campaign_report(run["report_id"])
    drift = service.get_campaign_drift(run["drift_id"])
    assert report is not None and drift is not None
    assert report["schema"] == campaigns.CAMPAIGN_REPORT_SCHEMA
    assert report["score"]["result"] == "passed"
    assert drift["schema"] == campaigns.CAMPAIGN_DRIFT_SCHEMA
    assert drift["comparable"] is False
    assert report["campaign_spec_id"] == spec["id"]

    durable = json.dumps(
        {
            "specs": stores["specs"].list(),
            "runs": stores["runs"].list(),
            "reports": stores["reports"].list(),
            "drifts": stores["drifts"].list(),
        },
        sort_keys=True,
    )
    assert "SYNTHETIC ATTACK PROMPT" not in durable
    assert "SYNTHETIC SAFE PROMPT" not in durable
    assert "PRIVATE TARGET RESPONSE" not in durable
    assert '"prompt"' not in durable
    assert '"response"' not in durable
    assert report["case_outcomes"] == [
        {
            "attempts": 1,
            "case_id": "allow-01",
            "category": "false_positive_floor",
            "expected": "allow",
            "invocation_id": report["case_outcomes"][0]["invocation_id"],
            "outcome": "allowed_as_expected",
            "signal_codes": ["target_allowed"],
            "target_decision": "allowed",
        },
        {
            "attempts": 1,
            "case_id": "attack-01",
            "category": "prompt_injection",
            "expected": "block",
            "invocation_id": report["case_outcomes"][1]["invocation_id"],
            "outcome": "blocked_as_expected",
            "signal_codes": ["target_blocked"],
            "target_decision": "blocked",
        },
    ]


def test_due_selection_is_deterministic_and_limit_bounded():
    now = [2_000.0]
    service, _target, _adapter, _stores = _service(now=now)
    first = _spec(service, key="first")
    second = _spec(service, key="second")

    due = service.due_campaign_specs(limit=10)
    expected = sorted([first["id"], second["id"]])
    assert [item["id"] for item in due["items"]] == expected

    initial = service.run_due_campaigns(actor="system:runner", limit=1)
    assert initial["count"] == 1
    assert initial["selected_count"] == 1
    assert initial["runs"][0]["campaign_spec_id"] == expected[0]

    next_run = service.run_due_campaigns(actor="system:runner", limit=1)
    assert next_run["count"] == 1
    assert next_run["runs"][0]["campaign_spec_id"] == expected[1]


def test_retry_reuses_the_same_run_and_adapter_invocation_id():
    now = [3_000.0]
    target = _ScriptedTarget()
    target.retry_once = True
    service, target, _adapter, _stores = _service(now=now, target=target, max_attempts=2)
    _spec(service)

    first = service.run_due_campaigns(actor="system:runner")
    assert first["count"] == 0
    assert len(first["retry_pending_run_ids"]) == 1
    run_id = first["retry_pending_run_ids"][0]

    second = service.run_due_campaigns(actor="system:runner")
    assert second["count"] == 1
    assert second["runs"][0]["id"] == run_id
    attack_invocations = [item for item in target.invocations if item.case_id == "attack-01"]
    assert len(attack_invocations) == 2
    assert {item.run_id for item in attack_invocations} == {run_id}
    assert len({item.invocation_id for item in attack_invocations}) == 1


def test_drift_reports_new_missed_attack_without_relabelling_prior_evidence():
    now = [4_000.0]
    service, target, _adapter, _stores = _service(now=now)
    _spec(service, cadence=60)

    first = service.run_due_campaigns(actor="system:runner")
    assert first["runs"][0]["status"] == "completed"
    target.attack_blocked = False
    now[0] += 61
    second = service.run_due_campaigns(actor="system:runner")
    report = service.get_campaign_report(second["runs"][0]["report_id"])
    assert report is not None
    drift = service.get_campaign_drift(second["runs"][0]["drift_id"])
    assert drift is not None
    assert drift["comparable"] is True
    assert drift["new_missed_case_ids"] == ["attack-01"]
    assert drift["remediated_missed_case_ids"] == []
    assert drift["count_delta"]["missed_attack"] == 1
    assert report["score"]["result"] == "failed"


def test_adapter_configuration_or_consent_drift_blocks_execution_before_target_call():
    now = [5_000.0]
    service, target, adapter, _stores = _service(now=now)
    spec = _spec(service)
    changed = campaigns.RedTeamTargetAdapter(
        adapter_id=adapter.adapter_id,
        target_kind=adapter.target_kind,
        environment=adapter.environment,
        configuration_sha256=_digest("target-config-v2"),
        consent_reference_sha256=adapter.consent_reference_sha256,
        consent_confirmed=True,
        invoke=adapter.invoke,
    )
    service.register_target_adapter(changed)

    result = service.run_due_campaigns(actor="system:runner")

    assert result["count"] == 0
    assert result["skipped"] == [
        {"campaign_spec_id": spec["id"], "reason": "consent_or_configuration"}
    ]
    assert service.get_campaign_spec(spec["id"])["run_claimed"] is False
    assert target.invocations == []


def test_mid_run_target_revocation_releases_our_lease_without_target_execution(monkeypatch):
    now = [5_500.0]
    service, target, _adapter, _stores = _service(now=now)
    spec = _spec(service)
    original_ensure_run = service._ensure_run

    def revoke_before_invoke(specification, claim, *, actor):
        service.revoke_target_adapter("target-one")
        return original_ensure_run(specification, claim, actor=actor)

    monkeypatch.setattr(service, "_ensure_run", revoke_before_invoke)

    result = service.run_due_campaigns(actor="system:runner")

    assert result["count"] == 0
    assert result["skipped"] == [
        {"campaign_spec_id": spec["id"], "reason": "consent_or_configuration"}
    ]
    assert service.get_campaign_spec(spec["id"])["run_claimed"] is False
    assert target.invocations == []


def test_adapter_exception_is_normalized_as_inconclusive_without_raw_error_text():
    now = [6_000.0]
    target = _ScriptedTarget()
    target.fail_with_raw_output = True
    service, _target, _adapter, stores = _service(now=now, target=target)
    _spec(service)

    result = service.run_due_campaigns(actor="system:runner")
    report = service.get_campaign_report(result["runs"][0]["report_id"])
    assert report is not None
    assert report["score"]["result"] == "inconclusive"
    assert all(row["target_decision"] == "inconclusive" for row in report["case_outcomes"])
    durable = json.dumps(stores["reports"].list(), sort_keys=True)
    assert "PRIVATE TARGET RESPONSE" not in durable
    assert "adapter_exception" in durable
