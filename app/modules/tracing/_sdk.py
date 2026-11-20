"""OpenTelemetry SDK wiring: provider, sampler, exporter and span processors.

Imported only by :func:`app.modules.tracing.runtime.init` when tracing is on, so a process with
tracing off never pays for the SDK import.

The export path never blocks or fails a request. Spans are handed to a ``BatchSpanProcessor`` (a
bounded queue drained by its own thread); the OTLP/HTTP exporter has a short timeout; export failures
are counted and reported by :class:`QuietExporter` with one warning (and one recovery note), and the
exporter library's own per-attempt log lines are throttled to one per interval.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import logging
import os
import platform
import socket
import threading
import time
from typing import TYPE_CHECKING

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
)
from opentelemetry.trace import StatusCode

from .config import TracingConfig

if TYPE_CHECKING:
    from opentelemetry.context import Context

logger = logging.getLogger(__name__)

#: Seconds between two copies of the same library log line that are let through.
LOG_THROTTLE_S = 300.0
#: Spans per export request and the delay between exports.
MAX_EXPORT_BATCH = 256
SCHEDULE_DELAY_MS = 2000
_EXPORTER_LOGGER_PREFIXES = ("opentelemetry.exporter", "opentelemetry.sdk.trace.export", "opentelemetry.sdk._shared_internal")


def build_sampler(name: str, arg: float | None) -> Sampler:
    """Map ``OTEL_TRACES_SAMPLER`` (+ ``_ARG``) to a sampler; unknown names fall back to ``parentbased_always_on``."""
    ratio = 1.0 if arg is None else min(1.0, max(0.0, arg))
    table: dict[str, Callable[[], Sampler]] = {
        "always_on": lambda: ALWAYS_ON,
        "always_off": lambda: ALWAYS_OFF,
        "traceidratio": lambda: TraceIdRatioBased(ratio),
        "parentbased_always_on": lambda: ParentBased(ALWAYS_ON),
        "parentbased_always_off": lambda: ParentBased(ALWAYS_OFF),
        "parentbased_traceidratio": lambda: ParentBased(TraceIdRatioBased(ratio)),
    }
    if name not in table:
        logger.warning("OTEL_TRACES_SAMPLER=%r is not supported; using parentbased_always_on", name)
        name = "parentbased_always_on"
    return table[name]()


def build_resource(config: TracingConfig, attributes: Mapping[str, str | int]) -> Resource:
    """Resource with ``service.name`` and the F2W attributes, merged over ``OTEL_RESOURCE_ATTRIBUTES``."""
    merged: dict[str, str | int] = {
        "service.name": config.service_name,
        "host.name": socket.gethostname(),
        "process.pid": os.getpid(),
        "process.runtime.name": platform.python_implementation().lower(),
        "process.runtime.version": platform.python_version(),
    }
    merged.update(config.resource_attributes)
    merged.update(attributes)
    return Resource.create(merged)


class _Throttle(logging.Filter):
    """Let one record per logger and level through every ``interval`` seconds (the rest are dropped)."""

    def __init__(self, interval: float = LOG_THROTTLE_S) -> None:
        super().__init__()
        self.interval = interval
        self._seen: dict[tuple[str, int], float] = {}
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno)
        now = time.monotonic()
        with self._lock:
            last = self._seen.get(key)
            if last is not None and now - last < self.interval:
                return False
            self._seen[key] = now
        return True


_THROTTLE = _Throttle()


def throttle_library_logs() -> None:
    """Attach the throttle to the exporter libraries' loggers (idempotent)."""
    for name in list(logging.root.manager.loggerDict):
        if name.startswith(_EXPORTER_LOGGER_PREFIXES):
            lib = logging.getLogger(name)
            if _THROTTLE not in lib.filters:
                lib.addFilter(_THROTTLE)


class QuietExporter(SpanExporter):
    """Wrap an exporter so a down collector costs one warning, not one per batch, and never raises."""

    def __init__(self, inner: SpanExporter, target: str) -> None:
        self._inner = inner
        self._target = target
        self._failing = False
        self.failed_batches = 0
        self.exported_spans = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export a batch; failures are counted and reported once until an export succeeds again."""
        try:
            result = self._inner.export(spans)
        except Exception as exc:  # the batch thread must survive any exporter bug
            self._failed(type(exc).__name__, len(spans))
            return SpanExportResult.FAILURE
        if result is SpanExportResult.SUCCESS:
            self.exported_spans += len(spans)
            if self._failing:
                self._failing = False
                logger.info("trace export to %s recovered", self._target)
            return result
        self._failed("export rejected or collector unreachable", len(spans))
        return result

    def _failed(self, reason: str, spans: int) -> None:
        self.failed_batches += 1
        if not self._failing:
            self._failing = True
            logger.warning("trace export to %s failed (%s); %d spans dropped, retrying silently with later batches",
                           self._target, reason, spans)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush the inner exporter."""
        return self._inner.force_flush(timeout_millis)

    def shutdown(self) -> None:
        """Shut the inner exporter down (retry sleeps are interrupted by the library)."""
        try:
            self._inner.shutdown()
        except Exception as exc:
            logger.debug("exporter shutdown failed: %s", type(exc).__name__)


class MinDurationProcessor(SpanProcessor):
    """Drop finished spans shorter than the floor registered for their name (noise filter)."""

    def __init__(self, delegate: SpanProcessor, floors: Mapping[str, float]) -> None:
        self._delegate = delegate
        self._floors = floors

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        """Forward the start event."""
        self._delegate.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        """Forward the span unless it ended faster than its registered floor and did not fail."""
        floor = self._floors.get(span.name, 0.0)
        if floor > 0 and span.start_time is not None and span.end_time is not None:
            fast = (span.end_time - span.start_time) / 1e6 < floor
            if fast and span.status.status_code is not StatusCode.ERROR:
                return
        self._delegate.on_end(span)

    def shutdown(self) -> None:
        """Shut the delegate down."""
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush the delegate."""
        return self._delegate.force_flush(timeout_millis)


def build_exporter(config: TracingConfig) -> SpanExporter:
    """OTLP/HTTP exporter for ``config`` (short timeout, quiet failures). Raises ImportError without the package."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    protocol = (os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL") or "http/protobuf").strip().lower()
    if protocol not in {"http/protobuf", ""}:
        logger.warning("OTEL_EXPORTER_OTLP_PROTOCOL=%r is not supported here; using http/protobuf", protocol)
    inner = OTLPSpanExporter(endpoint=config.endpoint, headers=dict(config.headers) or None,
                             timeout=config.export_timeout_s)
    return QuietExporter(inner, config.endpoint.split("?", 1)[0])


def build_provider(
    config: TracingConfig,
    resource_attributes: Mapping[str, str | int],
    floors: Mapping[str, float],
    exporter: SpanExporter | None = None,
) -> TracerProvider:
    """Assemble the provider: resource, sampler, then filter -> batch (or simple, for a test exporter) -> exporter."""
    provider = TracerProvider(resource=build_resource(config, resource_attributes),
                              sampler=build_sampler(config.sampler, config.sampler_arg))
    processor: SpanProcessor
    if exporter is not None:
        processor = SimpleSpanProcessor(exporter)
    else:
        processor = BatchSpanProcessor(
            build_exporter(config), max_queue_size=config.queue_size, max_export_batch_size=MAX_EXPORT_BATCH,
            schedule_delay_millis=SCHEDULE_DELAY_MS, export_timeout_millis=int(config.export_timeout_s * 1000) + 500)
        throttle_library_logs()
    provider.add_span_processor(MinDurationProcessor(processor, floors))
    return provider
