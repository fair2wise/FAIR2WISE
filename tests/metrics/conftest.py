"""Fixtures for the metrics tests: clean state per test and an in-memory reader."""

from __future__ import annotations

from collections.abc import Iterator
import os
from typing import Any

import pytest

from app.modules.metrics import runtime


@pytest.fixture(autouse=True)
def _clean_metrics(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start and end every test with metrics off and no OTEL_*/F2W_METRICS_* settings from the host."""
    for name in list(os.environ):
        if name.startswith(("OTEL_", "F2W_OTEL_", "F2W_METRICS_")):
            monkeypatch.delenv(name, raising=False)
    runtime.reset()
    yield
    runtime.reset()


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    """Enable metrics collecting into an ``InMemoryMetricReader`` (process ``test``)."""
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    monkeypatch.setenv("F2W_METRICS_ENABLED", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.example:4318")
    memory = InMemoryMetricReader()
    assert runtime.init(process="test", reader=memory)
    return memory


def points(reader: Any, name: str) -> list[Any]:  # noqa: ANN401
    """The data points of the metric called ``name`` in the reader's current snapshot (empty when absent)."""
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [p for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics if m.name == name
            for p in m.data.data_points]


def metric(reader: Any, name: str) -> Any:  # noqa: ANN401
    """The metric object called ``name`` (``None`` when absent)."""
    data = reader.get_metrics_data()
    for rm in (data.resource_metrics if data else ()):
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    return m
    return None
