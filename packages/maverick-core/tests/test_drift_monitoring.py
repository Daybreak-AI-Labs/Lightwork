"""Governed data, prediction, embedding, and performance drift monitoring."""
from __future__ import annotations

import hashlib
import json
import time

import pytest
from maverick import config
from maverick import drift_monitoring as drift
from maverick import model_risk_assurance as mra
from maverick.privacy_ops import RecordConflict


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


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
        "[evidence_graph]\n"
        "enable = true\n"
        "\n[model_risk_assurance]\n"
        "enable = true\n"
        "gate_promotions = true\n"
        "\n[drift_monitoring]\n"
        "enable = true\n"
        "bins = 5\n"
        "min_samples = 10\n"
        "psi_threshold = 0.15\n"
        "kl_threshold = 0.08\n"
        "embedding_cosine_threshold = 0.1\n"
        "performance_decay_threshold = 0.05\n"
        "evidence_validity_seconds = 3600\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _observed_and_declared(officer: mra.ModelRiskAssuranceOfficer):
    now = time.time()
    observation = officer.observe_asset(
        asset_type="model",
        source="drift_test",
        source_id="credit-model",
        display_name="Credit model",
        version_digest=_hex("credit-model-v1"),
        actor="inventory-operator",
        observed_at=now,
        metadata={"environment": "test"},
        dependencies=[],
    )
    proposed = officer.declare_asset(
        observation["asset_id"],
        owner="model-owner",
        purpose="Human-reviewed credit decision support.",
        intended_use="Rank applications for trained underwriters.",
        risk_tier="high",
        risk_context={
            "human_oversight": True,
            "high_risk_domains": ["essential_services"],
        },
        eu_ai_act={
            "category": "high_risk",
            "asserted_by": "qualified-reviewer",
            "asserted_at": now,
            "as_of": "2026-08-23",
            "source_ref": mra.EU_AI_ACT_METADATA["source"],
            "rationale": "Reviewed credit-access deployment context.",
        },
        actor="model-owner",
    )
    officer.review_declaration(
        observation["asset_id"],
        decision="approved",
        rationale="Ownership, use, and monitoring obligations reviewed.",
        reviewer="independent-risk-reviewer",
        expected_revision=proposed["revision"],
    )
    return observation


def test_distribution_metrics_are_deterministic_and_reject_misalignment():
    assert drift.population_stability_index([50, 50], [50, 50]) == 0.0
    assert drift.kl_divergence([50, 50], [50, 50]) == 0.0
    assert drift.population_stability_index([90, 10], [10, 90]) > 1.0
    assert drift.kl_divergence([90, 10], [10, 90]) > 1.0
    with pytest.raises(ValueError, match="align"):
        drift.population_stability_index([1, 2], [1])
    with pytest.raises(ValueError, match="positive mass"):
        drift.kl_divergence([0, 0], [1, 1])


def test_embedding_centroid_drift_covers_distance_and_zero_vectors():
    assert drift.embedding_centroid_drift([1, 0], [0, 1]) == {
        "centroid_displacement": pytest.approx(2**0.5),
        "cosine_distance": 1.0,
    }
    assert drift.embedding_centroid_drift([0, 0], [0, 0]) == {
        "centroid_displacement": 0.0,
        "cosine_distance": 0.0,
    }


def test_shifted_distribution_emits_evidence_alert_and_assurance_finding():
    officer = mra.ModelRiskAssuranceOfficer()
    observation = _observed_and_declared(officer)
    alerts = []
    service = drift.DriftMonitoringService(
        officer=officer,
        alert_sink=lambda alert: not alerts.append(dict(alert)),
    )
    baseline = service.capture_baseline(
        observation["asset_id"],
        source_id="production-window",
        actor="monitor-operator",
        feature_samples={"income": ([0.0] * 50) + ([1.0] * 50)},
        predictions=(["approve"] * 50) + (["decline"] * 50),
        embeddings=([[1.0, 0.0]] * 100),
        ground_truth=(["approve"] * 50) + (["decline"] * 50),
    )

    report = service.evaluate(
        baseline["id"],
        run_id="2026-08-23T1200Z",
        actor="monitor-operator",
        feature_samples={"income": [10.0] * 100},
        predictions=["decline"] * 100,
        embeddings=([[0.0, 1.0]] * 100),
        ground_truth=["approve"] * 100,
    )

    assert report["result"] == "failed"
    assert report["alert"]["recorded"] is True
    assert report["metrics"]["features"]["income"]["psi"] >= 0.15
    assert report["metrics"]["prediction"]["psi"] >= 0.15
    assert report["metrics"]["embedding"]["cosine_distance"] == 1.0
    assert report["metrics"]["performance"]["decay"] == 1.0
    assert alerts and alerts[0]["asset_id"] == observation["asset_id"]

    evidence = officer.get_evidence(report["evidence_id"])
    assert evidence["evidence_kind"] == "drift_monitoring"
    assert evidence["artifact_digest"] == observation["version_digest"]
    assert evidence["status"] == "pending_review"
    assert any(
        finding["finding_type"] == "drift_detected"
        and finding["details"]["evidence_id"] == evidence["id"]
        for finding in officer.findings()
    )

    serialised = json.dumps({"baseline": baseline, "evidence": evidence})
    assert "feature_samples" not in serialised
    assert "ground_truth" not in serialised
    assert "embeddings" not in serialised


def test_healthy_window_passes_without_alert_and_retry_is_idempotent():
    officer = mra.ModelRiskAssuranceOfficer()
    observation = _observed_and_declared(officer)
    service = drift.DriftMonitoringService(officer=officer)
    values = [float(index % 10) for index in range(100)]
    baseline = service.capture_baseline(
        observation["asset_id"],
        source_id="stable-window",
        actor="monitor-operator",
        feature_samples={"score": values},
    )
    retry = service.capture_baseline(
        observation["asset_id"],
        source_id="stable-window",
        actor="monitor-operator",
        feature_samples={"score": values},
        expected_revision=baseline["revision"],
    )
    assert retry["revision"] == baseline["revision"]
    report = service.evaluate(
        baseline["id"],
        run_id="stable-run",
        actor="monitor-operator",
        feature_samples={"score": values},
    )
    assert report["result"] == "passed"
    assert report["alert"] is None
    assert report["metrics"]["exceeded"] == []


def test_baseline_cas_and_artifact_rotation_fail_closed():
    officer = mra.ModelRiskAssuranceOfficer()
    observation = _observed_and_declared(officer)
    service = drift.DriftMonitoringService(officer=officer)
    baseline = service.capture_baseline(
        observation["asset_id"],
        source_id="rotation-window",
        actor="monitor-operator",
        feature_samples={"score": [0.0, 1.0] * 10},
    )
    with pytest.raises(RecordConflict, match="expected revision 0"):
        service.capture_baseline(
            observation["asset_id"],
            source_id="rotation-window",
            actor="monitor-operator",
            feature_samples={"score": [10.0, 11.0] * 10},
        )

    officer.observe_asset(
        asset_type="model",
        source="drift_test",
        source_id="credit-model",
        display_name="Credit model",
        version_digest=_hex("credit-model-v2"),
        actor="inventory-operator",
        expected_revision=observation["revision"],
        observed_at=time.time(),
        metadata={"environment": "test"},
        dependencies=[],
    )
    with pytest.raises(drift.DriftMonitoringStateError, match="new drift baseline"):
        service.evaluate(
            baseline["id"],
            run_id="stale-baseline-run",
            actor="monitor-operator",
            feature_samples={"score": [0.0, 1.0] * 10},
        )


def test_feature_gate_and_config_are_strict(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[evidence_graph]\nenable = true\n"
        "[model_risk_assurance]\nenable = true\n"
        "[drift_monitoring]\nenable = false\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    assert drift.enabled() is False
    with pytest.raises(drift.DriftMonitoringConfigError, match="requires"):
        drift.DriftMonitoringService().capture_baseline(
            "MAA-" + ("0" * 32),
            source_id="disabled",
            actor="operator",
            feature_samples={"score": [0.0] * 30},
        )

    path.write_text(
        "[evidence_graph]\nenable = true\n"
        "[model_risk_assurance]\nenable = true\n"
        "[drift_monitoring]\nenable = true\nunknown = 1\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    with pytest.raises(drift.DriftMonitoringConfigError, match="unknown"):
        drift.get_drift_config()
