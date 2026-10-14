"""OTLP endpoint/headers config + OTel GenAI semconv for tool spans.

Extends the existing opt-in OTel layer (test_genai_semconv.py covers the LLM
helpers). Here we assert:

  - ``MAVERICK_OTEL_HEADERS`` is parsed from the env into the exporter, and the
    no-header default path is unchanged;
  - enabling stays opt-in / no-op by default (otel off);
  - tool spans get the standard ``execute_tool`` gen_ai.* attributes.

Hermetic: no network, no live collector. Tests that need a real tracer skip
gracefully when opentelemetry isn't importable, like the repo's other
otel-dependent tests.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest
from maverick import observability as obs


@pytest.fixture(autouse=True)
def _reset_obs_state(monkeypatch):
    """Each test starts from a clean, otel-off module state."""
    monkeypatch.delenv("MAVERICK_OTEL_EXPORTER", raising=False)
    monkeypatch.delenv("MAVERICK_OTEL_ENDPOINT", raising=False)
    monkeypatch.delenv("MAVERICK_OTEL_HEADERS", raising=False)
    monkeypatch.delenv("MAVERICK_OTEL_METRICS", raising=False)
    monkeypatch.delenv("MAVERICK_PROMETHEUS_PORT", raising=False)
    monkeypatch.setattr(obs, "_initialized", False, raising=False)
    monkeypatch.setattr(obs, "_tracer", None, raising=False)
    monkeypatch.setattr(obs, "_metrics", {}, raising=False)
    monkeypatch.setattr(obs, "_otel_instruments", {}, raising=False)


# ---------- MAVERICK_OTEL_HEADERS parsing ----------

def test_headers_unset_is_empty(monkeypatch):
    assert obs._otlp_headers() == {}


def test_headers_blank_is_empty(monkeypatch):
    monkeypatch.setenv("MAVERICK_OTEL_HEADERS", "   ")
    assert obs._otlp_headers() == {}


def test_headers_parsed_from_env(monkeypatch):
    monkeypatch.setenv(
        "MAVERICK_OTEL_HEADERS", "x-honeycomb-team=abc123, dd-api-key=xyz"
    )
    assert obs._otlp_headers() == {
        "x-honeycomb-team": "abc123",
        "dd-api-key": "xyz",
    }


def test_headers_skip_malformed_pairs(monkeypatch):
    # A bare token with no '=' is skipped, not crashed on.
    monkeypatch.setenv("MAVERICK_OTEL_HEADERS", "garbage,authorization=Bearer t")
    assert obs._otlp_headers() == {"authorization": "Bearer t"}


# ---------- opt-in / no-op by default ----------

def test_disabled_by_default(monkeypatch):
    assert obs._otel_enabled() is False
    assert obs.is_enabled() is False


def test_trace_span_is_noop_when_disabled(monkeypatch):
    # No tracer is created, span yields None, attributes don't raise.
    with obs.trace_span("chat test-model", attributes={"gen_ai.system": "x"}) as s:
        assert s is None
    assert obs._tracer is None


def test_headers_not_read_when_disabled(monkeypatch):
    # Setting headers without the exporter flag must not enable anything.
    monkeypatch.setenv("MAVERICK_OTEL_HEADERS", "authorization=Bearer t")
    obs._initialize()
    assert obs._tracer is None
    assert obs.is_enabled() is False


# ---------- headers reach the exporter (otel required) ----------

def test_headers_passed_to_exporter_when_enabled(monkeypatch):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    from opentelemetry.exporter.otlp.proto.http import trace_exporter as te

    captured: dict[str, object] = {}

    class _SpyExporter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def export(self, spans):  # pragma: no cover - never called in test
            return None

        def shutdown(self):  # pragma: no cover
            return None

    monkeypatch.setattr(te, "OTLPSpanExporter", _SpyExporter)
    monkeypatch.setenv("MAVERICK_OTEL_EXPORTER", "otlp")
    monkeypatch.setenv("MAVERICK_OTEL_ENDPOINT", "https://collector.example/v1/traces")
    monkeypatch.setenv("MAVERICK_OTEL_HEADERS", "x-api-key=secret")

    obs._initialize()

    assert captured.get("endpoint") == "https://collector.example/v1/traces"
    assert captured.get("headers") == {"x-api-key": "secret"}
    assert obs._tracer is not None


def test_no_headers_kwarg_omitted_when_unset(monkeypatch):
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    from opentelemetry.exporter.otlp.proto.http import trace_exporter as te

    captured: dict[str, object] = {}

    class _SpyExporter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def export(self, spans):  # pragma: no cover
            return None

        def shutdown(self):  # pragma: no cover
            return None

    monkeypatch.setattr(te, "OTLPSpanExporter", _SpyExporter)
    monkeypatch.setenv("MAVERICK_OTEL_EXPORTER", "otlp")

    obs._initialize()

    # default path: no headers kwarg, so the exporter keeps its own default.
    assert "headers" not in captured


# ---------- tool span gen_ai.* attributes ----------

def test_tool_attributes_minimal():
    a = obs.gen_ai_tool_attributes("read_file")
    assert a["gen_ai.operation.name"] == "execute_tool"
    assert a["gen_ai.tool.name"] == "read_file"
    assert a["gen_ai.tool.type"] == "function"
    # optional fields omitted when unknown
    assert "gen_ai.tool.call.id" not in a
    assert "gen_ai.tool.description" not in a


def test_tool_attributes_full():
    a = obs.gen_ai_tool_attributes(
        "bash", call_id="call_42", description="run a shell command",
        tool_type="function",
    )
    assert a["gen_ai.tool.name"] == "bash"
    assert a["gen_ai.tool.call.id"] == "call_42"
    assert a["gen_ai.tool.description"] == "run a shell command"


def test_tool_helper_exported():
    assert "gen_ai_tool_attributes" in obs.__all__


# ---------- import-safe without otel ----------

def test_module_imports_without_otel(monkeypatch):
    # Re-importing the module must never require opentelemetry at import time.
    mod = importlib.reload(obs)
    assert hasattr(mod, "trace_span")
    assert hasattr(mod, "gen_ai_tool_attributes")


# ---------- OTLP metrics (MAVERICK_OTEL_METRICS) ----------

class _FakeInstrument:
    """Counter stand-in: ``add`` records the way the OTel counter does."""

    def __init__(self, name, description):
        self.name = name
        self.description = description
        self.points = []

    def add(self, amount, attributes=None, context=None):
        self.points.append((amount, dict(attributes or {})))


class _FakeHistogram(_FakeInstrument):
    """Histogram stand-in: ``record`` is the histogram's entry point."""

    def record(self, amount, attributes=None, context=None):
        self.points.append((amount, dict(attributes or {})))


@pytest.fixture
def fake_otlp_metrics(monkeypatch):
    """Stand-ins for the OTLP metric exporter and the metrics SDK.

    The venvs do not install opentelemetry-sdk, so these modules are injected
    and the real SDK path is covered by the importorskip tests below.
    """
    built: dict = {}

    class _Meter:
        def create_counter(self, name, unit="", description=""):
            instrument = _FakeInstrument(name, description)
            built[name] = instrument
            return instrument

        def create_histogram(self, name, unit="", description="", **_kwargs):
            instrument = _FakeHistogram(name, description)
            built[name] = instrument
            return instrument

    class _MeterProvider:
        def __init__(self, resource=None, metric_readers=()):
            built["readers"] = list(metric_readers)

        def get_meter(self, name, *_args, **_kwargs):
            built["meter"] = name
            return _Meter()

    class _Exporter:
        def __init__(self, **kwargs):
            built["exporter_kwargs"] = kwargs

    exporter_mod = types.ModuleType("opentelemetry.exporter.otlp.proto.http.metric_exporter")
    exporter_mod.OTLPMetricExporter = _Exporter
    sdk_mod = types.ModuleType("opentelemetry.sdk.metrics")
    sdk_mod.MeterProvider = _MeterProvider
    export_mod = types.ModuleType("opentelemetry.sdk.metrics.export")
    export_mod.PeriodicExportingMetricReader = lambda exporter, **_kw: ("reader", exporter)
    for name, module in (
        ("opentelemetry.exporter.otlp.proto.http.metric_exporter", exporter_mod),
        ("opentelemetry.sdk.metrics", sdk_mod),
        ("opentelemetry.sdk.metrics.export", export_mod),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return built


def test_metric_names_and_kinds_are_pinned():
    """Dashboards and alerts key on these names; OTel mirrors them exactly."""
    assert [(kind, name, labels) for kind, _key, name, _doc, labels in obs._METRIC_SPECS] == [
        ("counter", "maverick_llm_calls_total", ("provider", "model")),
        ("histogram", "maverick_llm_latency_seconds", ("provider", "model")),
        ("counter", "maverick_llm_tokens_total", ("provider", "model", "direction")),
        ("counter", "maverick_llm_cache_tokens_total", ("provider", "model", "kind")),
        ("counter", "maverick_tool_calls_total", ("tool", "status")),
        ("counter", "maverick_budget_dollars_spent", ()),
        ("counter", "maverick_tool_latency_budget_exceeded_total", ("tool",)),
    ]
    keys = [key for _kind, key, _name, _doc, _labels in obs._METRIC_SPECS]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize(
    ("setting", "expected"),
    [(None, False), ("", False), ("0", False), ("false", False), ("off", False),
     ("1", True), ("true", True), ("Yes", True), ("on", True)],
)
def test_otlp_metrics_knob_is_on_only_for_a_true_value(monkeypatch, setting, expected):
    # [observability] otlp_metrics = false reaches the module as "0", which a
    # plain bool() would read as on.
    monkeypatch.setattr(obs, "_setting", lambda env_name, key: setting)
    assert obs._otlp_metrics_enabled() is expected


@pytest.mark.parametrize(
    ("traces", "metrics"),
    [
        ("http://localhost:4318/v1/traces", "http://localhost:4318/v1/metrics"),
        ("https://collector.example/base/v1/traces", "https://collector.example/base/v1/metrics"),
        # No derivable metrics path: stay off rather than post to a guessed URL.
        ("https://collector.example/custom", None),
        ("https://collector.example/v1/traces/", None),
    ],
)
def test_metrics_url_replaces_only_the_signal_path(traces, metrics):
    assert obs._otlp_metrics_endpoint(traces) == metrics


def test_metrics_exporter_uses_the_span_endpoint_and_headers(fake_otlp_metrics):
    obs._initialize_otlp_metrics(
        "https://collector.example/v1/traces", {"x-api-key": "secret"}, None)
    assert fake_otlp_metrics["exporter_kwargs"] == {
        "endpoint": "https://collector.example/v1/metrics",
        "headers": {"x-api-key": "secret"},
    }


def test_metrics_exporter_omits_headers_kwarg_when_unset(fake_otlp_metrics):
    obs._initialize_otlp_metrics("http://localhost:4318/v1/traces", {}, None)
    assert fake_otlp_metrics["exporter_kwargs"] == {
        "endpoint": "http://localhost:4318/v1/metrics"}


def test_every_recorded_metric_gets_an_instrument_of_the_same_name_and_kind(
        fake_otlp_metrics):
    obs._initialize_otlp_metrics("http://localhost:4318/v1/traces", {}, None)
    assert set(obs._otel_instruments) == {key for _kind, key, *_ in obs._METRIC_SPECS}
    for kind, key, name, doc, _labels in obs._METRIC_SPECS:
        recorded_kind, instrument = obs._otel_instruments[key]
        assert recorded_kind == kind
        assert instrument.name == name
        assert instrument.description == doc
        assert isinstance(instrument, _FakeHistogram) == (kind == "histogram")


def test_record_metric_mirrors_to_otel_with_the_same_labels(fake_otlp_metrics, monkeypatch):
    obs._initialize_otlp_metrics("http://localhost:4318/v1/traces", {}, None)
    monkeypatch.setattr(obs, "_metrics", {})  # Prometheus off: OTel alone records
    obs.record_metric("tool_calls", labels={"tool": "search", "status": "ok"})
    obs.record_metric("llm_latency", 0.25, labels={"provider": "anthropic", "model": "m"})
    obs.record_metric("budget_dollars", 0.5)
    assert obs._otel_instruments["tool_calls"][1].points == [
        (1.0, {"tool": "search", "status": "ok"})]
    assert obs._otel_instruments["llm_latency"][1].points == [
        (0.25, {"provider": "anthropic", "model": "m"})]
    assert obs._otel_instruments["budget_dollars"][1].points == [(0.5, {})]


def test_otel_points_carry_the_redacted_label_not_the_raw_value(fake_otlp_metrics, monkeypatch):
    obs._initialize_otlp_metrics("http://localhost:4318/v1/traces", {}, None)
    monkeypatch.setattr(obs, "_metrics", {})
    raw = "model-controlled-" + "x" * 200
    obs.record_metric("tool_calls", labels={"tool": raw, "status": "ok"})
    (_value, attributes), = obs._otel_instruments["tool_calls"][1].points
    assert attributes["tool"] == obs._safe_metric_label(raw)
    assert raw not in attributes.values()


def test_otel_series_obey_the_same_cardinality_budget(fake_otlp_metrics, monkeypatch):
    monkeypatch.setattr(obs, "_MAX_LABEL_SERIES_PER_METRIC", 3)
    monkeypatch.setattr(obs, "_metric_label_series", {})
    monkeypatch.setattr(obs, "_metrics", {})
    obs._initialize_otlp_metrics("http://localhost:4318/v1/traces", {}, None)
    for tool in ("a", "b", "c", "d"):
        obs.record_metric("tool_calls", labels={"tool": tool, "status": "ok"})
    attributes = [attrs for _value, attrs in obs._otel_instruments["tool_calls"][1].points]
    assert attributes[:2] == [{"tool": "a", "status": "ok"}, {"tool": "b", "status": "ok"}]
    assert attributes[2:] == [{"tool": "__other__", "status": "__other__"}] * 2


def test_prometheus_and_otel_receive_the_same_labels(fake_otlp_metrics, monkeypatch):
    seen = []

    class _Child:
        def inc(self, amount=1.0):
            seen.append(("prometheus", amount))

    class _PrometheusCounter:
        def labels(self, **labels):
            seen.append(("labels", labels))
            return _Child()

    obs._initialize_otlp_metrics("http://localhost:4318/v1/traces", {}, None)
    monkeypatch.setattr(obs, "_metrics", {"tool_calls": _PrometheusCounter()})
    obs.record_metric("tool_calls", labels={"tool": "search", "status": "ok"})
    assert seen == [("labels", {"tool": "search", "status": "ok"}), ("prometheus", 1.0)]
    (_value, attributes), = obs._otel_instruments["tool_calls"][1].points
    assert attributes == {"tool": "search", "status": "ok"}


def test_metrics_knob_without_the_exporter_stays_off_and_says_why(monkeypatch, caplog):
    monkeypatch.setenv("MAVERICK_OTEL_METRICS", "1")
    obs._initialize()
    assert obs._otel_instruments == {}
    assert "MAVERICK_OTEL_METRICS" in caplog.text


def test_metrics_knob_off_loads_no_metrics_sdk(monkeypatch, caplog):
    """The default path never imports the OTel metrics SDK."""
    pytest.importorskip("opentelemetry.sdk.trace")
    for name in ("opentelemetry.sdk.metrics",
                 "opentelemetry.exporter.otlp.proto.http.metric_exporter"):
        monkeypatch.setitem(sys.modules, name, None)  # any import now raises ImportError
    monkeypatch.setenv("MAVERICK_OTEL_EXPORTER", "otlp")
    monkeypatch.setenv("MAVERICK_OTEL_ENDPOINT", "http://127.0.0.1:9/v1/traces")
    obs._initialize()
    assert obs._tracer is not None
    assert obs._otel_instruments == {}
    assert "OTLP metrics" not in caplog.text


def test_real_sdk_metrics_carry_the_prometheus_names(monkeypatch):
    """Real SDK path: the in-memory reader sees the same names and labels."""
    pytest.importorskip("opentelemetry.sdk.metrics")
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.metric_exporter")
    from opentelemetry.sdk.metrics import export as metric_export

    readers = []

    def _reader(exporter, **_kwargs):
        reader = metric_export.InMemoryMetricReader()
        readers.append(reader)
        return reader

    monkeypatch.setattr(metric_export, "PeriodicExportingMetricReader", _reader)
    obs._initialize_otlp_metrics("http://localhost:4318/v1/traces", {}, None)
    monkeypatch.setattr(obs, "_metrics", {})
    obs.record_metric("tool_calls", labels={"tool": "search", "status": "ok"})
    obs.record_metric("llm_latency", 0.25, labels={"provider": "anthropic", "model": "m"})

    data = readers[0].get_metrics_data()
    metrics = {
        metric.name: metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    calls = metrics["maverick_tool_calls_total"].data.data_points
    assert [(dict(p.attributes), p.value) for p in calls] == [
        ({"tool": "search", "status": "ok"}, 1)]
    latency = metrics["maverick_llm_latency_seconds"].data.data_points
    assert [dict(p.attributes) for p in latency] == [{"provider": "anthropic", "model": "m"}]
