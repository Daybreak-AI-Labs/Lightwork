"""Governed drift monitoring for observed AI assets.

The monitor keeps only bounded aggregate baselines and evaluation metrics. Raw
feature rows, predictions, labels, and embeddings are evaluated in memory and
are never written to the governed stores. A completed evaluation is recorded as
``drift_monitoring`` evidence in the Model Risk & AI Assurance Officer; a failed
or inconclusive result therefore appears through the officer's ordinary finding
path.

This module provides operational drift signals, not a statistical, legal, or
regulatory conclusion. Thresholds must be calibrated for the deployed model.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from . import model_risk_assurance as mra
from .governed_records import GovernedRecordStore
from .privacy_ops import RecordConflict

BASELINE_SCHEMA = "lightwork.model-drift-baseline.v1"
REPORT_SCHEMA = "lightwork.model-drift-report.v1"
FORMULA_VERSION = "model-drift-formula-v1"

_BASELINES = GovernedRecordStore(
    "model_drift_baselines",
    "MDB",
    "model_drift_baseline",
)
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}\Z")
_ASSET_RE = re.compile(r"MAA-[0-9a-f]{32}\Z")
_MAX_BASELINES = 2_000
_MAX_FEATURES = 64
_MAX_SAMPLES = 50_000
_MAX_EMBEDDING_SAMPLES = 10_000
_MAX_EMBEDDING_DIMENSIONS = 2_048
_EPSILON = 1e-12

_DEFAULTS: dict[str, object] = {
    "enable": False,
    "bins": 10,
    "min_samples": 30,
    "psi_threshold": 0.2,
    "kl_threshold": 0.1,
    "embedding_cosine_threshold": 0.1,
    "performance_decay_threshold": 0.05,
    "evidence_validity_seconds": 30 * 24 * 60 * 60,
}


class DriftMonitoringError(RuntimeError):
    """Base failure for governed drift monitoring."""


class DriftMonitoringConfigError(DriftMonitoringError):
    """The default-off drift-monitoring plane is unavailable or malformed."""


class DriftMonitoringStateError(DriftMonitoringError):
    """Persisted baseline or model-risk authority is malformed or stale."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _bounded_number(
    value: object,
    label: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    number = _finite_number(value, label)
    if number < minimum or number > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return number


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def _identity(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"{label} is invalid")
    return text


def _asset_id(value: object) -> str:
    text = str(value or "").strip()
    if not _ASSET_RE.fullmatch(text):
        raise ValueError("asset_id is invalid")
    return text


def _finite_series(
    values: object,
    label: str,
    *,
    minimum: int,
    maximum: int = _MAX_SAMPLES,
) -> list[float]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError(f"{label} must be a bounded list of numbers")
    if len(values) < minimum or len(values) > maximum:
        raise ValueError(f"{label} must contain between {minimum} and {maximum} values")
    return [_finite_number(value, f"{label}[{index}]") for index, value in enumerate(values)]


def _normalise_distribution(values: Sequence[object], label: str) -> list[float]:
    if not values:
        raise ValueError(f"{label} must not be empty")
    numbers = [_bounded_number(value, label, minimum=0.0, maximum=1e18) for value in values]
    total = sum(numbers)
    if total <= 0:
        raise ValueError(f"{label} must have positive mass")
    return [max(number / total, _EPSILON) for number in numbers]


def population_stability_index(
    reference: Sequence[object],
    current: Sequence[object],
) -> float:
    """Return PSI for two aligned count/probability vectors."""

    if len(reference) != len(current):
        raise ValueError("reference and current distributions must align")
    expected = _normalise_distribution(reference, "reference distribution")
    actual = _normalise_distribution(current, "current distribution")
    value = sum((a - e) * math.log(a / e) for e, a in zip(expected, actual, strict=True))
    return round(max(0.0, value), 12)


def kl_divergence(reference: Sequence[object], current: Sequence[object]) -> float:
    """Return smoothed KL(reference || current) for aligned distributions."""

    if len(reference) != len(current):
        raise ValueError("reference and current distributions must align")
    expected = _normalise_distribution(reference, "reference distribution")
    actual = _normalise_distribution(current, "current distribution")
    value = sum(e * math.log(e / a) for e, a in zip(expected, actual, strict=True))
    return round(max(0.0, value), 12)


def _edges(values: Sequence[float], bins: int) -> list[float]:
    low = min(values)
    high = max(values)
    if low == high:
        span = max(abs(low) * 0.01, 0.5)
        low -= span
        high += span
    width = (high - low) / bins
    return [low + (width * index) for index in range(bins + 1)]


def _histogram(values: Sequence[float], edges: Sequence[float]) -> list[int]:
    if len(edges) < 2:
        raise ValueError("histogram edges are invalid")
    bins = len(edges) - 1
    counts = [0 for _ in range(bins)]
    low = edges[0]
    high = edges[-1]
    width = (high - low) / bins
    if width <= 0 or not math.isfinite(width):
        raise ValueError("histogram edges are invalid")
    for value in values:
        if value <= low:
            index = 0
        elif value >= high:
            index = bins - 1
        else:
            index = min(bins - 1, int((value - low) / width))
        counts[index] += 1
    return counts


def _numeric_profile(values: Sequence[float], *, bins: int) -> dict[str, Any]:
    edges = _edges(values, bins)
    counts = _histogram(values, edges)
    return {
        "kind": "numeric",
        "edges": [round(value, 12) for value in edges],
        "counts": counts,
        "sample_count": len(values),
    }


def _token(value: object, label: str) -> str:
    text = str(value).strip()
    if not text or len(text) > 128 or any(ord(char) < 32 for char in text):
        raise ValueError(f"{label} is invalid")
    return text


def _prediction_profile(values: Sequence[object], *, bins: int) -> dict[str, Any]:
    if not values or len(values) > _MAX_SAMPLES:
        raise ValueError("predictions must be a non-empty bounded list")
    numeric: list[float] = []
    numeric_only = True
    for index, value in enumerate(values):
        try:
            numeric.append(_finite_number(value, f"predictions[{index}]"))
        except ValueError:
            numeric_only = False
            break
    if numeric_only:
        return _numeric_profile(numeric, bins=bins)
    labels = [_token(value, f"predictions[{index}]") for index, value in enumerate(values)]
    categories = sorted(set(labels))
    if len(categories) > 64:
        raise ValueError("predictions contain too many categories")
    counts = {category: labels.count(category) for category in categories}
    return {
        "kind": "categorical",
        "categories": categories,
        "counts": [counts[category] for category in categories] + [0],
        "other_bucket": True,
        "sample_count": len(labels),
    }


def _current_prediction_counts(
    values: Sequence[object],
    baseline: Mapping[str, Any],
) -> list[int]:
    kind = baseline.get("kind")
    if kind == "numeric":
        numeric = _finite_series(values, "predictions", minimum=1)
        return _histogram(numeric, list(baseline.get("edges") or []))
    if kind != "categorical":
        raise DriftMonitoringStateError("prediction baseline is malformed")
    categories = list(baseline.get("categories") or [])
    if not categories or len(categories) > 64:
        raise DriftMonitoringStateError("prediction categories are malformed")
    index = {str(category): position for position, category in enumerate(categories)}
    counts = [0 for _ in range(len(categories) + 1)]
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError("predictions must be a bounded list")
    if not values or len(values) > _MAX_SAMPLES:
        raise ValueError("predictions must be a non-empty bounded list")
    for position, value in enumerate(values):
        label = _token(value, f"predictions[{position}]")
        counts[index.get(label, len(categories))] += 1
    return counts


def _embedding_profile(embeddings: object, *, minimum: int) -> dict[str, Any]:
    if not isinstance(embeddings, Sequence) or isinstance(embeddings, (str, bytes, bytearray)):
        raise ValueError("embeddings must be a bounded matrix")
    if len(embeddings) < minimum or len(embeddings) > _MAX_EMBEDDING_SAMPLES:
        raise ValueError(
            f"embeddings must contain between {minimum} and {_MAX_EMBEDDING_SAMPLES} rows"
        )
    rows: list[list[float]] = []
    dimension: int | None = None
    for row_index, row in enumerate(embeddings):
        values = _finite_series(
            row,
            f"embeddings[{row_index}]",
            minimum=1,
            maximum=_MAX_EMBEDDING_DIMENSIONS,
        )
        if dimension is None:
            dimension = len(values)
        elif len(values) != dimension:
            raise ValueError("embedding rows must have one fixed dimension")
        rows.append(values)
    assert dimension is not None
    centroid = [sum(row[index] for row in rows) / len(rows) for index in range(dimension)]
    return {
        "centroid": [round(value, 12) for value in centroid],
        "dimension": dimension,
        "sample_count": len(rows),
    }


def embedding_centroid_drift(
    reference_centroid: Sequence[object],
    current_centroid: Sequence[object],
) -> dict[str, float]:
    """Return centroid displacement and cosine distance."""

    if not reference_centroid or len(reference_centroid) != len(current_centroid):
        raise ValueError("embedding centroids must have one aligned dimension")
    reference = [_finite_number(value, "reference centroid") for value in reference_centroid]
    current = [_finite_number(value, "current centroid") for value in current_centroid]
    displacement = math.sqrt(
        sum((actual - expected) ** 2 for expected, actual in zip(reference, current, strict=True))
    )
    reference_norm = math.sqrt(sum(value * value for value in reference))
    current_norm = math.sqrt(sum(value * value for value in current))
    if reference_norm == 0 and current_norm == 0:
        cosine_distance = 0.0
    elif reference_norm == 0 or current_norm == 0:
        cosine_distance = 1.0
    else:
        similarity = sum(
            expected * actual for expected, actual in zip(reference, current, strict=True)
        ) / (reference_norm * current_norm)
        cosine_distance = 1.0 - max(-1.0, min(1.0, similarity))
    return {
        "centroid_displacement": round(displacement, 12),
        "cosine_distance": round(max(0.0, cosine_distance), 12),
    }


def _accuracy(predictions: Sequence[object], labels: Sequence[object]) -> dict[str, Any]:
    if len(predictions) != len(labels) or not predictions:
        raise ValueError("predictions and ground_truth must have the same non-zero length")
    if len(predictions) > _MAX_SAMPLES:
        raise ValueError("performance sample count exceeds the operational limit")
    matches = sum(
        _token(prediction, "prediction") == _token(label, "ground truth")
        for prediction, label in zip(predictions, labels, strict=True)
    )
    return {
        "metric": "accuracy",
        "value": round(matches / len(predictions), 12),
        "sample_count": len(predictions),
    }


def get_drift_config() -> dict[str, Any]:
    """Read and validate the closed, default-off global configuration."""

    try:
        from .config import config_source_errors, load_global_config

        config = load_global_config()
        if config_source_errors(include_tenant=False) or not isinstance(config, dict):
            raise DriftMonitoringConfigError("drift-monitoring configuration is unreadable")
    except DriftMonitoringConfigError:
        raise
    except Exception as exc:
        raise DriftMonitoringConfigError("drift-monitoring configuration is unavailable") from exc
    section = config.get("drift_monitoring") or {}
    if not isinstance(section, dict):
        raise DriftMonitoringConfigError("drift_monitoring must be a table")
    unknown = set(section) - set(_DEFAULTS)
    if unknown:
        raise DriftMonitoringConfigError(
            f"unknown drift_monitoring keys: {', '.join(sorted(unknown))}"
        )
    values = {**_DEFAULTS, **section}
    if not isinstance(values["enable"], bool):
        raise DriftMonitoringConfigError("drift_monitoring.enable must be a boolean")
    try:
        values["bins"] = _integer(values["bins"], "bins", 2, 64)
        values["min_samples"] = _integer(
            values["min_samples"], "min_samples", 2, _MAX_SAMPLES
        )
        values["psi_threshold"] = _bounded_number(
            values["psi_threshold"], "psi_threshold", minimum=0.0, maximum=100.0
        )
        values["kl_threshold"] = _bounded_number(
            values["kl_threshold"], "kl_threshold", minimum=0.0, maximum=100.0
        )
        values["embedding_cosine_threshold"] = _bounded_number(
            values["embedding_cosine_threshold"],
            "embedding_cosine_threshold",
            minimum=0.0,
            maximum=2.0,
        )
        values["performance_decay_threshold"] = _bounded_number(
            values["performance_decay_threshold"],
            "performance_decay_threshold",
            minimum=0.0,
            maximum=1.0,
        )
        values["evidence_validity_seconds"] = _integer(
            values["evidence_validity_seconds"],
            "evidence_validity_seconds",
            60,
            366 * 24 * 60 * 60,
        )
    except ValueError as exc:
        raise DriftMonitoringConfigError(str(exc)) from exc
    return values


def enabled() -> bool:
    try:
        return bool(get_drift_config()["enable"] is True and mra.enabled())
    except Exception:
        return False


def _baseline_id(asset_id: str, source_id: str) -> str:
    return f"MDB-{_sha256({'asset_id': asset_id, 'source_id': source_id})[:24]}"


def _live_alert_sink(alert: Mapping[str, Any]) -> bool:  # pragma: no cover - live audit
    from .audit import EventKind, record

    return bool(
        record(
            EventKind.MODEL_DRIFT_ALERT,
            agent="drift_monitoring",
            asset_id=alert["asset_id"],
            baseline_id=alert["baseline_id"],
            evidence_id=alert["evidence_id"],
            reasons=alert["reasons"],
        )
    )


class DriftMonitoringService:
    """Capture aggregate baselines and emit exact-artifact drift evidence."""

    def __init__(
        self,
        *,
        officer: mra.ModelRiskAssuranceOfficer | None = None,
        baselines: GovernedRecordStore | None = None,
        clock: Callable[[], float] | None = None,
        alert_sink: Callable[[Mapping[str, Any]], bool] | None = None,
        enforce_feature: bool = True,
    ) -> None:
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")
        if alert_sink is not None and not callable(alert_sink):
            raise ValueError("alert_sink must be callable")
        self._officer = officer or mra.ModelRiskAssuranceOfficer()
        self._baselines = baselines or _BASELINES
        self._clock = clock
        self._alert_sink = alert_sink
        self._enforce_feature = enforce_feature

    def _settings(self) -> dict[str, Any]:
        settings = get_drift_config()
        if self._enforce_feature and not enabled():
            raise DriftMonitoringConfigError(
                "drift monitoring requires [drift_monitoring] enable = true and "
                "enabled model-risk assurance dependencies"
            )
        if self._officer.backend_kind != self._baselines.backend_kind:
            raise DriftMonitoringStateError(
                "drift baselines and model-risk authority use different backends"
            )
        return settings

    def _now(self) -> float:
        source = self._clock or self._baselines.authoritative_time
        now = _finite_number(source(), "authority clock")
        if now <= 0:
            raise DriftMonitoringStateError("authority clock is invalid")
        return now

    def capture_baseline(
        self,
        asset_id: str,
        *,
        source_id: str,
        actor: str,
        feature_samples: Mapping[str, Sequence[object]] | None = None,
        predictions: Sequence[object] | None = None,
        embeddings: Sequence[Sequence[object]] | None = None,
        ground_truth: Sequence[object] | None = None,
        expected_revision: int = 0,
    ) -> dict[str, Any]:
        settings = self._settings()
        asset = _asset_id(asset_id)
        source = _identity(source_id, "source_id")
        operator = _identity(actor, "actor")
        observation = self._officer.get_observation(asset)
        if observation is None:
            raise DriftMonitoringStateError("baseline requires an observed AI asset")
        minimum = int(settings["min_samples"])
        bins = int(settings["bins"])
        features: dict[str, Any] = {}
        if feature_samples is not None:
            if not isinstance(feature_samples, Mapping) or len(feature_samples) > _MAX_FEATURES:
                raise ValueError("feature_samples must be a bounded mapping")
            for raw_name in sorted(feature_samples, key=str):
                name = _identity(raw_name, "feature name")
                values = _finite_series(
                    feature_samples[raw_name], f"feature {name}", minimum=minimum
                )
                features[name] = _numeric_profile(values, bins=bins)
        prediction_profile = None
        if predictions is not None:
            if len(predictions) < minimum:
                raise ValueError("predictions are below min_samples")
            prediction_profile = _prediction_profile(predictions, bins=bins)
        embedding_profile = None
        if embeddings is not None:
            embedding_profile = _embedding_profile(embeddings, minimum=minimum)
        performance = None
        if ground_truth is not None:
            if predictions is None:
                raise ValueError("ground_truth requires predictions")
            performance = _accuracy(predictions, ground_truth)
        if not features and prediction_profile is None and embedding_profile is None and performance is None:
            raise ValueError("baseline requires at least one monitored signal")

        immutable = {
            "asset_id": asset,
            "artifact_digest": observation["version_digest"],
            "source_id": source,
            "formula_version": FORMULA_VERSION,
            "features": features,
            "prediction": prediction_profile,
            "embedding": embedding_profile,
            "performance": performance,
            "legal_certification": False,
        }
        payload_sha = _sha256(immutable)
        record_id = _baseline_id(asset, source)
        record = {
            "id": record_id,
            "schema": BASELINE_SCHEMA,
            **immutable,
            "payload_sha256": payload_sha,
            "status": "active",
        }
        current = self._baselines.get(record_id)
        revision = int((current or {}).get("revision") or 0)
        if revision != expected_revision:
            raise RecordConflict(
                f"drift baseline changed (expected revision {expected_revision}, found {revision})"
            )
        if current is not None and current.get("payload_sha256") == payload_sha:
            return current
        if current is None:
            return self._baselines.create(
                record,
                action="capture_model_drift_baseline",
                actor=operator,
                max_records=_MAX_BASELINES,
            )

        def mutate(row: dict[str, Any]) -> None:
            if row.get("asset_id") != asset or row.get("source_id") != source:
                raise DriftMonitoringStateError("drift baseline identity changed")
            for key, value in record.items():
                if key != "id":
                    row[key] = value

        saved = self._baselines.update(
            record_id,
            mutate,
            expected_revision=expected_revision,
            action="capture_model_drift_baseline",
            actor=operator,
        )
        if saved is None:
            raise DriftMonitoringStateError("drift baseline disappeared during update")
        return saved

    def get_baseline(self, baseline_id: str) -> dict[str, Any] | None:
        identifier = _identity(baseline_id, "baseline_id")
        row = self._baselines.get(identifier)
        if row is not None and row.get("schema") != BASELINE_SCHEMA:
            raise DriftMonitoringStateError("drift baseline schema is invalid")
        return row

    def evaluate(  # noqa: C901 - one explicit, auditable multi-signal decision boundary
        self,
        baseline_id: str,
        *,
        run_id: str,
        actor: str,
        feature_samples: Mapping[str, Sequence[object]] | None = None,
        predictions: Sequence[object] | None = None,
        embeddings: Sequence[Sequence[object]] | None = None,
        ground_truth: Sequence[object] | None = None,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        settings = self._settings()
        baseline = self.get_baseline(baseline_id)
        if baseline is None:
            raise DriftMonitoringStateError("drift baseline does not exist")
        run = _identity(run_id, "run_id")
        operator = _identity(actor, "actor")
        asset = _asset_id(baseline.get("asset_id"))
        observation = self._officer.get_observation(asset)
        if observation is None:
            raise DriftMonitoringStateError("monitored asset is no longer observed")
        if observation.get("version_digest") != baseline.get("artifact_digest"):
            raise DriftMonitoringStateError(
                "asset artifact changed; capture a new drift baseline"
            )
        minimum = int(settings["min_samples"])
        feature_metrics: dict[str, Any] = {}
        baseline_features = baseline.get("features") or {}
        if feature_samples is not None:
            if not isinstance(feature_samples, Mapping) or len(feature_samples) > _MAX_FEATURES:
                raise ValueError("feature_samples must be a bounded mapping")
            unknown = set(map(str, feature_samples)) - set(map(str, baseline_features))
            if unknown:
                raise ValueError(f"features have no baseline: {', '.join(sorted(unknown))}")
            for raw_name in sorted(feature_samples, key=str):
                name = _identity(raw_name, "feature name")
                reference = baseline_features.get(name)
                if not isinstance(reference, Mapping):
                    raise DriftMonitoringStateError("feature baseline is malformed")
                values = _finite_series(
                    feature_samples[raw_name], f"feature {name}", minimum=minimum
                )
                current_counts = _histogram(values, list(reference.get("edges") or []))
                reference_counts = list(reference.get("counts") or [])
                feature_metrics[name] = {
                    "psi": population_stability_index(reference_counts, current_counts),
                    "kl_divergence": kl_divergence(reference_counts, current_counts),
                    "sample_count": len(values),
                }
        prediction_metrics = None
        baseline_prediction = baseline.get("prediction")
        if predictions is not None:
            if not isinstance(baseline_prediction, Mapping):
                raise ValueError("predictions were not part of the baseline")
            if len(predictions) < minimum:
                raise ValueError("predictions are below min_samples")
            current_counts = _current_prediction_counts(predictions, baseline_prediction)
            reference_counts = list(baseline_prediction.get("counts") or [])
            prediction_metrics = {
                "psi": population_stability_index(reference_counts, current_counts),
                "kl_divergence": kl_divergence(reference_counts, current_counts),
                "sample_count": len(predictions),
            }
        embedding_metrics = None
        baseline_embedding = baseline.get("embedding")
        if embeddings is not None:
            if not isinstance(baseline_embedding, Mapping):
                raise ValueError("embeddings were not part of the baseline")
            current_embedding = _embedding_profile(embeddings, minimum=minimum)
            if current_embedding["dimension"] != baseline_embedding.get("dimension"):
                raise ValueError("embedding dimension differs from its baseline")
            embedding_metrics = {
                **embedding_centroid_drift(
                    list(baseline_embedding.get("centroid") or []),
                    current_embedding["centroid"],
                ),
                "sample_count": current_embedding["sample_count"],
            }
        performance_metrics = None
        baseline_performance = baseline.get("performance")
        if ground_truth is not None:
            if predictions is None or not isinstance(baseline_performance, Mapping):
                raise ValueError("performance evaluation requires a performance baseline")
            current_performance = _accuracy(predictions, ground_truth)
            baseline_value = _finite_number(
                baseline_performance.get("value"), "baseline performance"
            )
            performance_metrics = {
                "metric": "accuracy",
                "baseline": baseline_value,
                "current": current_performance["value"],
                "decay": round(max(0.0, baseline_value - current_performance["value"]), 12),
                "sample_count": current_performance["sample_count"],
            }
        if (
            not feature_metrics
            and prediction_metrics is None
            and embedding_metrics is None
            and performance_metrics is None
        ):
            raise ValueError("evaluation requires at least one monitored signal")

        exceeded: list[str] = []
        for name, metrics in feature_metrics.items():
            if metrics["psi"] >= settings["psi_threshold"]:
                exceeded.append(f"feature:{name}:psi")
            if metrics["kl_divergence"] >= settings["kl_threshold"]:
                exceeded.append(f"feature:{name}:kl")
        if prediction_metrics is not None:
            if prediction_metrics["psi"] >= settings["psi_threshold"]:
                exceeded.append("prediction:psi")
            if prediction_metrics["kl_divergence"] >= settings["kl_threshold"]:
                exceeded.append("prediction:kl")
        if (
            embedding_metrics is not None
            and embedding_metrics["cosine_distance"]
            >= settings["embedding_cosine_threshold"]
        ):
            exceeded.append("embedding:cosine")
        if (
            performance_metrics is not None
            and performance_metrics["decay"] >= settings["performance_decay_threshold"]
        ):
            exceeded.append("performance:decay")

        metrics = {
            "formula_version": FORMULA_VERSION,
            "baseline_id": baseline["id"],
            "baseline_revision": baseline["revision"],
            "baseline_payload_sha256": baseline["payload_sha256"],
            "features": feature_metrics,
            "prediction": prediction_metrics,
            "embedding": embedding_metrics,
            "performance": performance_metrics,
            "thresholds": {
                key: settings[key]
                for key in (
                    "psi_threshold",
                    "kl_threshold",
                    "embedding_cosine_threshold",
                    "performance_decay_threshold",
                )
            },
            "exceeded": sorted(exceeded),
        }
        result = "failed" if exceeded else "passed"
        now = self._now() if observed_at is None else _finite_number(observed_at, "observed_at")
        if now <= 0:
            raise ValueError("observed_at must be positive")
        scope_digest = _sha256(
            {
                "baseline_id": baseline["id"],
                "baseline_revision": baseline["revision"],
                "baseline_payload_sha256": baseline["payload_sha256"],
                "run_id": run,
                "metrics": metrics,
            }
        )
        evidence = self._officer.record_evidence(
            asset,
            source_id=f"drift:{baseline['id']}:{run}",
            evidence_kind="drift_monitoring",
            result=result,
            scope_digest=scope_digest,
            artifact_digest=observation["version_digest"],
            actor=operator,
            observed_at=now,
            valid_until=now + int(settings["evidence_validity_seconds"]),
            summary=(
                f"Governed drift evaluation {'crossed' if exceeded else 'did not cross'} "
                "the configured aggregate thresholds."
            ),
            metrics=metrics,
        )
        alert = None
        if exceeded:
            alert = {
                "asset_id": asset,
                "baseline_id": baseline["id"],
                "evidence_id": evidence["id"],
                "reasons": sorted(exceeded),
                "recorded": False,
            }
            if self._alert_sink is not None:
                alert["recorded"] = bool(self._alert_sink(alert))
        return {
            "schema": REPORT_SCHEMA,
            "asset_id": asset,
            "artifact_digest": observation["version_digest"],
            "baseline_id": baseline["id"],
            "run_id": run,
            "result": result,
            "metrics": metrics,
            "evidence_id": evidence["id"],
            "evidence_revision": evidence["revision"],
            "alert": alert,
            "legal_certification": False,
        }


_DEFAULT = DriftMonitoringService()


def capture_baseline(asset_id: str, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT.capture_baseline(asset_id, **kwargs)


def evaluate(baseline_id: str, **kwargs: Any) -> dict[str, Any]:
    return _DEFAULT.evaluate(baseline_id, **kwargs)


def live_service() -> DriftMonitoringService:  # pragma: no cover - live wiring
    return DriftMonitoringService(alert_sink=_live_alert_sink)


__all__ = [
    "BASELINE_SCHEMA",
    "FORMULA_VERSION",
    "REPORT_SCHEMA",
    "DriftMonitoringConfigError",
    "DriftMonitoringError",
    "DriftMonitoringService",
    "DriftMonitoringStateError",
    "capture_baseline",
    "embedding_centroid_drift",
    "enabled",
    "evaluate",
    "get_drift_config",
    "kl_divergence",
    "live_service",
    "population_stability_index",
]
