"""OpenTelemetry metrics SDK wiring: resource, views, temporality, quiet exporter and periodic reader.

Imported only by :func:`app.modules.metrics.runtime.init` when metrics are on, so a process with metrics off
never pays for the SDK import. Recording is in memory; a ``PeriodicExportingMetricReader`` exports on its own
thread, so no request or job ever waits for the collector.
"""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry.sdk.metrics import (
    Counter,
    Histogram,
    MeterProvider,
    ObservableCounter,
    ObservableGauge,
    ObservableUpDownCounter,
    UpDownCounter,
)
from opentelemetry.sdk.metrics._internal.instrument import Gauge  # the synchronous gauge is not re-exported
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    MetricExporter,
    MetricExportResult,
    MetricReader,
    MetricsData,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource

from ..tracing._sdk import throttle_library_logs
from .catalog import SPECS
from .config import MetricsConfig

logger = logging.getLogger(__name__)

#: Cumulative for every instrument kind, whatever ``OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`` says.
CUMULATIVE: dict[type, AggregationTemporality] = {
    kind: AggregationTemporality.CUMULATIVE
    for kind in (Counter, UpDownCounter, Histogram, Gauge, ObservableCounter, ObservableUpDownCounter, ObservableGauge)
}


def build_resource(service_name: str, process: str | None, profile: str | None) -> Resource:
    """The small metrics resource: ``service.name``, ``f2w.process`` and ``f2w.profile`` (when set).

    Built with the plain ``Resource`` constructor, not ``Resource.create`` (which would add a random
    ``service.instance.id`` and ``telemetry.sdk.*``). No instance id, pid, host name, commit or anything else that
    changes per restart or release: the collector turns resource attributes into labels, so each would restart every
    series. The commit is exposed by the ``f2w.build.info`` metric instead.

    Single-replica assumption (owner decision): FAIR2WISE runs one replica (SQLite stores, ``Recreate`` rollout),
    so there is deliberately no per-pod attribute. With more than one replica the cumulative series would collide
    in the collector; a per-pod attribute would then be needed.
    """
    attrs: dict[str, str] = {"service.name": service_name}
    for key, value in (("f2w.process", process), ("f2w.profile", profile)):
        if value:
            attrs[key] = value
    return Resource(attrs)


def build_views() -> list[View]:
    """One explicit-bucket view per histogram in the catalog (boundaries in seconds)."""
    return [View(instrument_name=s.name, aggregation=ExplicitBucketHistogramAggregation(boundaries=list(s.bounds)))
            for s in SPECS if s.kind == "histogram"]


class QuietMetricExporter(MetricExporter):
    """Wrap an exporter so a down collector costs one warning and one recovery note, and never raises."""

    def __init__(self, inner: MetricExporter, target: str) -> None:
        super().__init__(preferred_temporality=dict(CUMULATIVE))
        self._inner = inner
        self._target = target
        self._failing = False
        self.failed_exports = 0

    def export(self, metrics_data: MetricsData, timeout_millis: float = 10_000, **kwargs: Any) -> MetricExportResult:  # noqa: ANN401
        """Export one snapshot; failures are counted and reported once until an export succeeds again."""
        try:
            result = self._inner.export(metrics_data, timeout_millis=timeout_millis, **kwargs)
        except Exception as exc:  # the reader thread must survive any exporter bug
            self._failed(type(exc).__name__)
            return MetricExportResult.FAILURE
        if result is MetricExportResult.SUCCESS:
            if self._failing:
                self._failing = False
                logger.info("metric export to %s recovered", self._target)
            return result
        self._failed("export rejected or collector unreachable")
        return result

    def _failed(self, reason: str) -> None:
        self.failed_exports += 1
        if not self._failing:
            self._failing = True
            logger.warning("metric export to %s failed (%s); retrying silently at the next interval", self._target, reason)

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        """Flush the inner exporter."""
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:
            return False

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:  # noqa: ANN401
        """Shut the inner exporter down; never raises."""
        try:
            self._inner.shutdown(timeout_millis=timeout_millis, **kwargs)
        except Exception as exc:
            logger.debug("metric exporter shutdown failed: %s", type(exc).__name__)


def build_exporter(config: MetricsConfig) -> MetricExporter:
    """OTLP/HTTP metric exporter (short timeout, cumulative, quiet failures). Raises ImportError without the package.

    ``requests`` (the exporter's transport) honours ``NO_PROXY``, exactly as the trace exporter does; nothing here
    sets a proxy.
    """
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    inner = OTLPMetricExporter(endpoint=config.endpoint, headers=dict(config.headers) or None,
                               timeout=config.export_timeout_s, preferred_temporality=dict(CUMULATIVE))
    return QuietMetricExporter(inner, config.endpoint.split("?", 1)[0])


def build_provider(config: MetricsConfig, resource: Resource, reader: MetricReader | None = None,
                   exporter: MetricExporter | None = None) -> tuple[MeterProvider, MetricReader]:
    """Assemble the provider: resource, views, and a periodic reader (or the injected test ``reader``)."""
    if reader is None:
        wrapped = exporter if exporter is not None else build_exporter(config)
        reader = PeriodicExportingMetricReader(
            wrapped, export_interval_millis=config.export_interval_s * 1000,
            export_timeout_millis=config.export_timeout_s * 1000 + 500)
        throttle_library_logs()
    provider = MeterProvider(metric_readers=[reader], resource=resource, views=build_views(), shutdown_on_exit=False)
    return provider, reader

