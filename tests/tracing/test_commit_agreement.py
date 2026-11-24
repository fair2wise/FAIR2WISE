"""One commit source for traces and metrics (``F2W_CODE_COMMIT``/``GIT_COMMIT``, else the checkout's git HEAD)."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import pytest

from app.modules import tracing
from app.modules.f2w_agent import trace as turn_trace
from app.modules.metrics import labels
from app.modules.metrics import runtime as metrics_runtime
from app.modules.tracing import runtime

COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_metrics(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    import os

    for name in list(os.environ):
        if name.startswith("F2W_METRICS_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("F2W_CODE_COMMIT", raising=False)
    metrics_runtime.reset()
    yield
    metrics_runtime.reset()


def _both(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:  # noqa: ANN401
    """Start tracing and metrics (process ``api``) with in-memory sinks; return (span exporter, metric reader)."""
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setenv("F2W_OTEL_TRACE_FUNCTIONS", "")
    monkeypatch.setenv("F2W_METRICS_ENABLED", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.example:4318")
    spans, reader = InMemorySpanExporter(), InMemoryMetricReader()
    assert runtime.init(process="api", exporter=spans) and metrics_runtime.init(process="api", reader=reader)
    with tracing.span("probe"):
        pass
    return spans, reader


def _build_info(reader: Any) -> dict[str, Any]:  # noqa: ANN401
    data = reader.get_metrics_data()
    found = [dict(p.attributes) for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
             if m.name == "f2w.build.info" for p in m.data.data_points]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize("variable", ["F2W_CODE_COMMIT", "GIT_COMMIT"])
def test_metric_and_trace_report_the_same_commit(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    monkeypatch.setenv(variable, COMMIT)
    spans, reader = _both(monkeypatch)
    (probe,) = spans.get_finished_spans()
    assert probe.resource.attributes["f2w.code_commit"] == COMMIT
    assert _build_info(reader)["commit"] == COMMIT == runtime.code_commit()


def test_no_commit_variable_and_no_git_directory_is_unknown_and_omitted(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An image has no ``.git``: ``git rev-parse HEAD`` fails, the metric says ``unknown``, the trace omits the key."""
    root = tmp_path / "image" / "app"
    fake_module = root / "modules" / "f2w_agent" / "trace.py"  # git_sha() runs git in ``parents[3]`` = ``root``
    fake_module.parent.mkdir(parents=True)
    monkeypatch.setattr(turn_trace, "__file__", str(fake_module))
    monkeypatch.setattr(turn_trace, "_GIT_SHA", None)
    monkeypatch.setattr(turn_trace, "_GIT_SHA_READY", False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))  # never find a repository above the fake image
    assert runtime.code_commit() is None
    spans, reader = _both(monkeypatch)
    (probe,) = spans.get_finished_spans()
    assert "f2w.code_commit" not in probe.resource.attributes
    assert _build_info(reader)["commit"] == "unknown"


def test_a_checkout_falls_back_to_git_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(turn_trace, "git_sha", lambda: COMMIT)
    assert runtime.code_commit() == COMMIT and labels.build_commit(runtime.code_commit()) == COMMIT
