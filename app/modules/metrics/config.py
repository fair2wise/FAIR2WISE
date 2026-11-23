"""Metrics settings read from the environment (OpenTelemetry metrics over OTLP/HTTP).

Metrics are **off** unless ``F2W_METRICS_ENABLED`` is truthy **and** ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set;
there is no default endpoint and no separate metrics-endpoint variable (``OTEL_EXPORTER_OTLP_METRICS_ENDPOINT``
is deliberately not read). The export timeout reuses ``F2W_OTEL_EXPORT_TIMEOUT_S`` from tracing.

This module imports nothing from OpenTelemetry, so reading the configuration costs nothing when metrics are off.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import os

from ..tracing import config as tracing_cfg

logger = logging.getLogger(__name__)

ENABLED_VAR = "F2W_METRICS_ENABLED"
INTERVAL_VAR = "F2W_METRICS_EXPORT_INTERVAL_S"
#: Variables shared with tracing (documented in the tracing table, listed here so the drift test sees them).
SHARED_VARS = (
    tracing_cfg.ENDPOINT_VAR, tracing_cfg.HEADERS_VAR, tracing_cfg.SERVICE_NAME_VAR, tracing_cfg.EXPORT_TIMEOUT_VAR,
    *tracing_cfg.COMMIT_VARS, *tracing_cfg.PROFILE_VARS,
)
ENV_VARS = (ENABLED_VAR, INTERVAL_VAR, *SHARED_VARS)

DEFAULT_INTERVAL_S = 15.0
MIN_INTERVAL_S = 1.0
_SUFFIXES = ("/v1/traces", "/v1/metrics")


@dataclass(frozen=True)
class MetricsConfig:
    """Validated metrics settings (header values are kept out of ``repr``)."""

    enabled: bool = False
    endpoint: str = ""
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    service_name: str = tracing_cfg.DEFAULT_SERVICE_NAME
    export_interval_s: float = DEFAULT_INTERVAL_S
    export_timeout_s: float = tracing_cfg.DEFAULT_EXPORT_TIMEOUT_S


def metrics_endpoint(environ: Mapping[str, str]) -> str:
    """OTLP/HTTP metrics URL: the base endpoint (any ``/v1/traces`` suffix removed) plus ``/v1/metrics``; ``""`` if unset."""
    base = (environ.get(tracing_cfg.ENDPOINT_VAR) or "").strip().rstrip("/")
    for suffix in _SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
    return f"{base}/v1/metrics" if base else ""


def _seconds(environ: Mapping[str, str], name: str, default: float, minimum: float) -> float:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    if value != value or value < minimum:  # NaN or below the floor
        logger.warning("%s=%r is below the minimum %s; using %s", name, raw, minimum, minimum)
        return minimum
    return value


def from_env(environ: Mapping[str, str] | None = None) -> MetricsConfig:
    """Read the settings from ``environ`` (default ``os.environ``)."""
    env = os.environ if environ is None else environ
    endpoint = metrics_endpoint(env)
    return MetricsConfig(
        enabled=tracing_cfg.truthy(env.get(ENABLED_VAR)) and bool(endpoint),
        endpoint=endpoint,
        headers=tracing_cfg.parse_pairs(env.get(tracing_cfg.HEADERS_VAR)),
        service_name=(env.get(tracing_cfg.SERVICE_NAME_VAR) or "").strip() or tracing_cfg.DEFAULT_SERVICE_NAME,
        export_interval_s=_seconds(env, INTERVAL_VAR, DEFAULT_INTERVAL_S, MIN_INTERVAL_S),
        export_timeout_s=_seconds(env, tracing_cfg.EXPORT_TIMEOUT_VAR, tracing_cfg.DEFAULT_EXPORT_TIMEOUT_S, 0.1),
    )
