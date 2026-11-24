"""The instruments (OTel name, kind, unit) and the histogram bucket boundaries, as plain data.

Imports nothing from OpenTelemetry. The Prometheus names a collector produces from these are listed in
``mkdocs/docs/observability.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

METER_NAME = "app.modules.metrics"

LLM_BOUNDS_S = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 180.0, 300.0)
JOB_BOUNDS_S = (1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1200.0, 1800.0, 3600.0)
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
    Spec("jobs", "f2w.jobs", "counter", "{job}", "Jobs that finished a run"),
    Spec("job_duration", "f2w.job.duration", "histogram", "s", "Duration of one job run", JOB_BOUNDS_S),
    Spec("queue_depth", "f2w.jobs.queue.depth", "gauge", "{job}", "Jobs queued or running (worker only)"),
    Spec("llm_calls", "f2w.llm.calls", "counter", "{call}", "Model calls"),
    Spec("llm_duration", "f2w.llm.call.duration", "histogram", "s", "Model call duration", LLM_BOUNDS_S),
    Spec("harvest_fetches", "f2w.harvest.fetches", "counter", "{fetch}", "Harvest searches and PDF downloads"),
    Spec("tiled_operations", "f2w.tiled.operations", "counter", "{operation}", "Tiled graph requests"),
    Spec("library_operations", "f2w.library.operations", "counter", "{operation}", "Library service operations"),
)
