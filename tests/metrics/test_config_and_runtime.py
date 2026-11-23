"""Opt-in rules, endpoint derivation, temporality, resource key set, views and quiet export failures."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
import sys
import types
from typing import Any

import pytest

from app.modules import metrics
from app.modules.metrics import config as cfg
from app.modules.metrics import runtime

from .conftest import metric, points

BASE = {"F2W_METRICS_ENABLED": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4318"}


def test_off_by_default_and_needs_both_settings() -> None:
    assert not cfg.from_env({}).enabled
    assert not cfg.from_env({"F2W_METRICS_ENABLED": "1"}).enabled
    assert not cfg.from_env({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4318"}).enabled
    assert not cfg.from_env({**BASE, "F2W_METRICS_ENABLED": "0"}).enabled
    assert cfg.from_env(BASE).enabled


def test_disabled_init_imports_no_sdk_and_records_nothing() -> None:
    code = (
        "import sys\n"
        "from app.modules import metrics\n"
        "assert metrics.init(process='api', environ={}) is False\n"
        "metrics.record_http_request(route='/x', method='GET', status=200, seconds=0.1, known_routes=None)\n"
        "assert not metrics.is_enabled()\n"
        "assert not any(n.startswith('opentelemetry.sdk.metrics') for n in sys.modules)\n"
    )
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path.cwd())}
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("base,expected", [
    ("http://c:4318", "http://c:4318/v1/metrics"),
    ("http://c:4318/", "http://c:4318/v1/metrics"),
    ("http://c:4318/v1/traces", "http://c:4318/v1/metrics"),
    ("http://c:4318/otel/v1/traces/", "http://c:4318/otel/v1/metrics"),
])
def test_metrics_url_is_base_plus_v1_metrics(base: str, expected: str) -> None:
    assert cfg.metrics_endpoint({"OTEL_EXPORTER_OTLP_ENDPOINT": base}) == expected


def test_signal_specific_endpoint_variable_is_not_read() -> None:
    env = {**BASE, "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://other:1/v1/metrics"}
    assert cfg.from_env(env).endpoint == "http://c:4318/v1/metrics"
    assert "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT" not in cfg.ENV_VARS


def test_interval_and_timeout_defaults_and_minimum() -> None:
    assert cfg.from_env(BASE).export_interval_s == 15.0 and cfg.from_env(BASE).export_timeout_s == 3.0
    assert cfg.from_env({**BASE, "F2W_METRICS_EXPORT_INTERVAL_S": "0.2"}).export_interval_s == 1.0
    assert cfg.from_env({**BASE, "F2W_METRICS_EXPORT_INTERVAL_S": "30"}).export_interval_s == 30.0
    assert cfg.from_env({**BASE, "F2W_METRICS_EXPORT_INTERVAL_S": "x"}).export_interval_s == 15.0
    assert cfg.from_env({**BASE, "F2W_OTEL_EXPORT_TIMEOUT_S": "7"}).export_timeout_s == 7.0


def test_init_is_idempotent(reader: Any) -> None:  # noqa: ANN401
    provider = runtime._STATE.provider
    assert runtime.init(process="other") is True
    assert runtime._STATE.provider is provider


def test_resource_has_exactly_the_small_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    for k, v in {**BASE, "F2W_IMAGE_PROFILE": "cluster", "F2W_CODE_COMMIT": "abc1234"}.items():
        monkeypatch.setenv(k, v)
    memory = InMemoryMetricReader()
    assert runtime.init(process="worker", reader=memory)
    metrics.record_http_request(route=None, method="GET", status=200, seconds=0.0, known_routes=None)
    attrs = memory.get_metrics_data().resource_metrics[0].resource.attributes
    assert dict(attrs) == {"service.name": "f2w", "f2w.process": "worker", "f2w.profile": "cluster"}
    for banned in ("service.instance.id", "process.pid", "host.name", "f2w.code.commit", "telemetry.sdk.name",
                   "telemetry.sdk.language", "telemetry.sdk.version"):
        assert banned not in attrs


def test_resource_omits_unset_optionals(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.metrics import _sdk

    res = _sdk.build_resource("f2w", "api", None)
    assert dict(res.attributes) == {"service.name": "f2w", "f2w.process": "api"}
    for banned in ("service.instance.id", "process.pid", "host.name"):
        assert banned not in res.attributes


def test_views_set_http_boundaries(reader: Any) -> None:  # noqa: ANN401
    metrics.record_http_request(route="/a", method="GET", status=200, seconds=0.3, known_routes=frozenset({"/a"}))
    (point,) = points(reader, "f2w.http.request.duration")
    assert list(point.explicit_bounds) == [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]


def test_temporality_is_cumulative_for_every_kind_ignoring_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.metrics.export import AggregationTemporality

    from app.modules.metrics import _sdk

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "delta")
    quiet = _sdk.QuietMetricExporter(types.SimpleNamespace(), "http://c")  # type: ignore[arg-type]
    assert quiet._preferred_temporality and len(quiet._preferred_temporality) == 7
    assert set(quiet._preferred_temporality.values()) == {AggregationTemporality.CUMULATIVE}


def test_reader_built_from_exporter_is_cumulative_and_exporter_gets_explicit_mapping(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.metrics import Counter, Histogram, ObservableGauge, UpDownCounter
    from opentelemetry.sdk.metrics.export import AggregationTemporality, MetricExportResult

    seen: dict[str, Any] = {}

    class FakeOtlp:
        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
            seen.update(kwargs)

        def export(self, data: Any, timeout_millis: float = 0, **kw: Any) -> MetricExportResult:  # noqa: ANN401
            seen["exported"] = True
            return MetricExportResult.SUCCESS

        def force_flush(self, timeout_millis: float = 0) -> bool:
            return True

        def shutdown(self, timeout_millis: float = 0, **kw: Any) -> None:  # noqa: ANN401
            return None

    module = types.ModuleType("opentelemetry.exporter.otlp.proto.http.metric_exporter")
    module.OTLPMetricExporter = FakeOtlp  # type: ignore[attr-defined]
    for name in ("opentelemetry.exporter", "opentelemetry.exporter.otlp", "opentelemetry.exporter.otlp.proto",
                 "opentelemetry.exporter.otlp.proto.http"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "delta")
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("F2W_OTEL_EXPORT_TIMEOUT_S", "2")
    assert runtime.init(process="api")
    assert seen["endpoint"] == "http://c:4318/v1/metrics" and seen["timeout"] == 2.0
    mapping = seen["preferred_temporality"]
    for kind in (Counter, Histogram, UpDownCounter, ObservableGauge):
        assert mapping[kind] is AggregationTemporality.CUMULATIVE
    metrics.record_http_request(route=None, method="GET", status=200, seconds=0.1, known_routes=None)
    runtime._STATE.provider.force_flush()  # type: ignore[union-attr]
    assert seen.get("exported")


def test_export_failures_warn_once_and_note_recovery(caplog: pytest.LogCaptureFixture) -> None:
    from opentelemetry.sdk.metrics.export import MetricExportResult

    from app.modules.metrics import _sdk

    outcomes = [RuntimeError("boom"), MetricExportResult.FAILURE, MetricExportResult.SUCCESS,
                MetricExportResult.FAILURE]

    class Inner:
        def export(self, data: Any, timeout_millis: float = 0, **kw: Any) -> MetricExportResult:  # noqa: ANN401
            item = outcomes.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    quiet = _sdk.QuietMetricExporter(Inner(), "http://c:4318/v1/metrics")  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO, logger="app.modules.metrics._sdk"):
        results = [quiet.export(None) for _ in range(4)]  # type: ignore[arg-type]
    assert results == [MetricExportResult.FAILURE, MetricExportResult.FAILURE, MetricExportResult.SUCCESS,
                       MetricExportResult.FAILURE]
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(warns) == 2 and len(infos) == 1  # one warning per failure streak, one recovery note


def test_missing_exporter_package_leaves_metrics_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.http.metric_exporter", None)
    assert runtime.init(process="api", environ=BASE) is False
    assert not metrics.is_enabled()
    metrics.record_http_request(route=None, method="GET", status=200, seconds=0.1, known_routes=None)


def test_counter_is_cumulative_in_the_reader(reader: Any) -> None:  # noqa: ANN401
    known = frozenset({"/a"})
    for _ in range(3):
        metrics.record_http_request(route="/a", method="GET", status=200, seconds=0.01, known_routes=known)
        assert metric(reader, "f2w.http.requests") is not None
    assert [p.value for p in points(reader, "f2w.http.requests")] == [3]


def test_fastapi_route_template_and_unmatched(reader: Any) -> None:  # noqa: ANN401
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app.modules.metrics.asgi import install

    app = FastAPI()

    @app.get("/v1/papers/{doi:path}")
    def paper(doi: str) -> dict[str, str]:
        return {"ok": "1"}

    assert install(app)
    with TestClient(app) as client:
        client.get("/v1/papers/10.1000/xyz?token=s3cret")
        client.get("/missing/10.1000/xyz")
    seen = {(p.attributes["http_route"], p.attributes["status_class"]) for p in points(reader, "f2w.http.requests")}
    assert seen == {("/v1/papers/{doi:path}", "2xx"), ("unmatched", "4xx")}


def _build_info_points(monkeypatch: pytest.MonkeyPatch, process: str) -> list[Any]:  # noqa: ANN401
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    for k, v in {**BASE, "F2W_IMAGE_PROFILE": "cluster", "F2W_CODE_COMMIT": "abc1234def"}.items():
        monkeypatch.setenv(k, v)
    memory = InMemoryMetricReader()
    assert runtime.init(process=process, reader=memory)
    return points(memory, "f2w.build.info")


def test_build_info_is_emitted_by_the_api_only(monkeypatch: pytest.MonkeyPatch) -> None:
    (point,) = _build_info_points(monkeypatch, "api")
    assert point.value == 1 and dict(point.attributes) == {"commit": "abc1234def", "profile": "cluster"}


def test_worker_does_not_emit_build_info(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _build_info_points(monkeypatch, "worker") == []


def test_build_info_labels_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.metrics import labels

    assert labels.build_commit("abc1234") == "abc1234" and labels.build_commit("v1.2; drop") == "unknown"
    assert labels.build_profile("als-prod") == "als-prod" and labels.build_profile("A Title: x") == "unknown"
