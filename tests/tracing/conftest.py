"""Fixtures for the tracing tests: a clean tracing state per test and an in-memory span exporter."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
import os

import pytest

from app.modules.tracing import runtime
from app.modules.tracing.safe import reset_secret_cache

SECRET = "sk-test-FAKEKEY-0123456789abcdef"
SENTINEL = "SENTINEL-prompt-text-7f3a"


@pytest.fixture(autouse=True)
def _clean_tracing(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start and end every test with tracing off and no OTEL_* settings from the host."""
    for name in list(os.environ):
        if name.startswith(("OTEL_", "F2W_OTEL_")):
            monkeypatch.delenv(name, raising=False)
    runtime.reset()
    reset_secret_cache()
    yield
    runtime.reset()
    reset_secret_cache()


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch):
    """Enable tracing with an ``InMemorySpanExporter`` (synchronous, so spans are visible at once)."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setenv("F2W_OTEL_ENABLED", "1")
    monkeypatch.setenv("F2W_OTEL_TRACE_FUNCTIONS", "")  # no default wrapping unless a test asks for it
    memory = InMemorySpanExporter()
    assert runtime.init(process="test", exporter=memory)
    return memory


def every_string(spans: Sequence[object]) -> list[str]:
    """Every name, attribute key/value, event and resource value of ``spans`` as strings."""
    seen: list[str] = []
    for span in spans:
        seen.append(span.name)  # type: ignore[attr-defined]
        seen += [f"{k}={v}" for k, v in (span.attributes or {}).items()]  # type: ignore[attr-defined]
        seen += [f"{k}={v}" for k, v in span.resource.attributes.items()]  # type: ignore[attr-defined]
        for event in span.events:  # type: ignore[attr-defined]
            seen.append(event.name)
            seen += [f"{k}={v}" for k, v in (event.attributes or {}).items()]
        seen.append(str(span.status.description or ""))  # type: ignore[attr-defined]
    return seen
