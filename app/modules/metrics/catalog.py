"""The instruments (OTel name, kind, unit) and the histogram bucket boundaries, as plain data.

Imports nothing from OpenTelemetry. The Prometheus names a collector produces from these are listed in
``mkdocs/docs/observability.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

METER_NAME = "app.modules.metrics"

HTTP_BOUNDS_S = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


@dataclass(frozen=True)
class Spec:
    """One instrument: ``kind`` is ``counter``, ``histogram`` or ``gauge`` (observable, built where it is registered); ``bounds`` (seconds) apply to histograms."""

    key: str
    name: str
    kind: str
    unit: str
    description: str
    bounds: tuple[float, ...] = ()


SPECS = (
    Spec("build_info", "f2w.build.info", "gauge", "{info}", "Build identity (value 1); API process only"),
    Spec("http_requests", "f2w.http.requests", "counter", "{request}", "HTTP requests served"),
    Spec("http_duration", "f2w.http.request.duration", "histogram", "s", "HTTP request duration", HTTP_BOUNDS_S),
)
