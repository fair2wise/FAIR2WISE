"""Configuration parsing, the disabled no-op, idempotent init and a collector that is down."""

from __future__ import annotations

import logging
import time

import httpx
import pytest

from app.modules.tracing import config as cfg
from app.modules.tracing import httpx_hooks, instrument, runtime


def test_off_by_default_and_truthy_spellings() -> None:
    assert not cfg.from_env({}).enabled
    for value in ("1", "true", "YES", "On"):
        assert cfg.from_env({"F2W_OTEL_ENABLED": value}).enabled
    for value in ("0", "false", "", "no", "maybe"):
        assert not cfg.from_env({"F2W_OTEL_ENABLED": value}).enabled


def test_hub_alias_and_precedence() -> None:
    assert cfg.from_env({"OTEL_ENABLED": "true"}).enabled
    assert not cfg.from_env({"OTEL_ENABLED": "true", "F2W_OTEL_ENABLED": "0"}).enabled


def test_endpoint_headers_service_resource_sampler() -> None:
    c = cfg.from_env({
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318/",
        "OTEL_EXPORTER_OTLP_HEADERS": "authorization=Bearer%20abc,x-team=f2w",
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=pilot,service.name=ignored",
        "OTEL_TRACES_SAMPLER": "ParentBased_TraceIdRatio", "OTEL_TRACES_SAMPLER_ARG": "0.25",
    })
    assert c.endpoint == "http://otel-collector:4318/v1/traces"
    assert c.endpoint_host == "otel-collector"
    assert c.headers == {"authorization": "Bearer abc", "x-team": "f2w"}
    assert "Bearer" not in repr(c)  # header values stay out of logs
    assert c.service_name == "ignored" and c.resource_attributes == {"deployment.environment": "pilot"}
    assert (c.sampler, c.sampler_arg) == ("parentbased_traceidratio", 0.25)
    assert cfg.from_env({}).service_name == "f2w"
    assert cfg.from_env({"OTEL_SERVICE_NAME": "x", "OTEL_RESOURCE_ATTRIBUTES": "service.name=y"}).service_name == "x"


def test_traces_endpoint_variable_is_used_verbatim() -> None:
    env = {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://c.example/custom", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:1"}
    assert cfg.traces_endpoint(env) == "https://c.example/custom"
    assert cfg.traces_endpoint({}) == "http://localhost:4318/v1/traces"


def test_bad_numbers_fall_back(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        c = cfg.from_env({"F2W_OTEL_MIN_SPAN_MS": "fast", "F2W_OTEL_EXPORT_TIMEOUT_S": "-1"})
    assert c.min_span_ms == 0.0 and c.export_timeout_s == cfg.DEFAULT_EXPORT_TIMEOUT_S


def test_trace_functions_unset_vs_empty() -> None:
    assert cfg.from_env({}).trace_functions is None
    assert cfg.from_env({"F2W_OTEL_TRACE_FUNCTIONS": ""}).trace_functions == ""


def test_disabled_is_a_no_op_with_no_global_side_effects() -> None:
    from opentelemetry import trace

    provider_before = trace.get_tracer_provider()
    send_before = httpx.Client.send
    assert runtime.init(process="api") is False
    assert not runtime.is_enabled()
    with runtime.span("anything", f2w_x=1) as handle:
        handle.set_attribute("f2w.paper_count", 3)
        handle.set_error(RuntimeError("boom"))
        assert handle.trace_id is None
    assert runtime.current_trace_id() is None and runtime.current_traceparent() is None
    assert trace.get_tracer_provider() is provider_before
    assert httpx.Client.send is send_before
    assert instrument.applied() == frozenset()
    assert runtime.span("a") is runtime.span("b")  # one shared object: no allocation per call


def test_init_is_idempotent(exporter) -> None:  # noqa: ANN001
    provider = runtime._STATE.provider
    assert runtime.init(process="again") is True
    assert runtime._STATE.provider is provider
    assert httpx_hooks.install() is False  # already installed by the first init


def test_missing_exporter_package_leaves_tracing_off(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import app.modules.tracing._sdk as sdk

    def boom(config: object) -> object:
        raise ImportError("no exporter")

    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setattr(sdk, "build_exporter", boom)
    with caplog.at_level(logging.WARNING):
        assert runtime.init() is False
    assert "not started" in caplog.text


def test_resource_attributes(monkeypatch: pytest.MonkeyPatch, exporter) -> None:  # noqa: ANN001
    monkeypatch.setenv("F2W_CODE_COMMIT", "a" * 40)
    attrs = runtime.resource_attributes({"F2W_IMAGE_PROFILE": "cluster"}, "api")
    assert attrs["f2w.code_commit"] == "a" * 40 and attrs["f2w.profile"] == "cluster" and attrs["f2w.process"] == "api"
    assert "service.version" in attrs
    with runtime.span("x"):
        pass
    resource = exporter.get_finished_spans()[0].resource.attributes
    assert resource["service.name"] == "f2w" and resource["f2w.process"] == "test"
    assert "host.name" in resource and "process.pid" in resource


def test_image_profile_falls_back_to_the_baked_profile_file(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "profile.json"
    path.write_text('{"profile": "cluster"}')
    assert runtime.image_profile({"F2W_PROFILE_FILE": str(path)}) == "cluster"
    assert runtime.image_profile({"F2W_PROFILE_FILE": str(tmp_path / "none"), "F2W_DEPLOYMENT_PROFILE": "local"}) == "local"


def test_service_version_matches_pyproject() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{runtime.FALLBACK_VERSION}"' in text


def test_collector_down_never_raises_or_blocks_and_warns_once(monkeypatch: pytest.MonkeyPatch,
                                                              caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setenv("F2W_OTEL_TRACE_FUNCTIONS", "")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")  # discard port: connection refused
    monkeypatch.setenv("F2W_OTEL_EXPORT_TIMEOUT_S", "1")
    with caplog.at_level(logging.INFO):
        assert runtime.init(process="test")
        started = time.perf_counter()
        for _ in range(50):
            with runtime.span("request"):
                with runtime.span("child"):
                    pass
        assert time.perf_counter() - started < 1.0  # spans are queued, never exported inline
        runtime.flush(2.0)
        runtime.flush(2.0)
        t0 = time.perf_counter()
        runtime.shutdown()
        assert time.perf_counter() - t0 < 8.0
    warnings = [r for r in caplog.records if r.name == "app.modules.tracing._sdk" and r.levelno == logging.WARNING]
    assert len(warnings) <= 1
    assert all("127.0.0.1" in r.getMessage() for r in warnings)
