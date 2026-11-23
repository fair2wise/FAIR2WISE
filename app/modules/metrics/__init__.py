"""Application metrics for FAIR2WISE (OpenTelemetry over OTLP/HTTP), off by default.

Operator page: ``mkdocs/docs/observability.md``. Typical use::

    from app.modules import metrics

    metrics.init(process="api")     # once per process; no-op unless F2W_METRICS_ENABLED and an OTLP endpoint
    metrics.record_http_request(...) # every record_* call is a cheap no-op while metrics are off

Labels are closed vocabularies (:mod:`app.modules.metrics.labels`); nothing a person typed ever becomes a label.
"""

from __future__ import annotations

from .config import ENV_VARS, MetricsConfig, from_env
from .runtime import (
    init,
    is_enabled,
    record_http_request,
    record_job,
    reset,
    set_queue_depth_source,
    shutdown,
)

__all__ = ("ENV_VARS", "MetricsConfig", "from_env", "init", "is_enabled", "record_http_request", "record_job", "reset",
    "set_queue_depth_source", "shutdown")
