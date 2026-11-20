"""Tracing settings read from the environment (OpenTelemetry to Jaeger).

Tracing is **off** unless ``F2W_OTEL_ENABLED`` (or the hub-style alias ``OTEL_ENABLED``) is truthy.
Every other name is the standard OpenTelemetry one or an ``F2W_OTEL_*`` knob documented in
``mkdocs/docs/observability.md`` (a drift test checks that every name read here is documented).

This module imports nothing from OpenTelemetry, so reading the configuration costs nothing when
tracing is off.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import os
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

ENABLED_VAR = "F2W_OTEL_ENABLED"
ENABLED_ALIAS_VAR = "OTEL_ENABLED"
ENDPOINT_VAR = "OTEL_EXPORTER_OTLP_ENDPOINT"
TRACES_ENDPOINT_VAR = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
HEADERS_VAR = "OTEL_EXPORTER_OTLP_HEADERS"
PROTOCOL_VAR = "OTEL_EXPORTER_OTLP_PROTOCOL"
SERVICE_NAME_VAR = "OTEL_SERVICE_NAME"
RESOURCE_ATTRIBUTES_VAR = "OTEL_RESOURCE_ATTRIBUTES"
SAMPLER_VAR = "OTEL_TRACES_SAMPLER"
SAMPLER_ARG_VAR = "OTEL_TRACES_SAMPLER_ARG"
TRACE_FUNCTIONS_VAR = "F2W_OTEL_TRACE_FUNCTIONS"
TRACE_MODULES_VAR = "F2W_OTEL_TRACE_MODULES"
MIN_SPAN_MS_VAR = "F2W_OTEL_MIN_SPAN_MS"
EXPORT_TIMEOUT_VAR = "F2W_OTEL_EXPORT_TIMEOUT_S"
QUEUE_SIZE_VAR = "F2W_OTEL_QUEUE_SIZE"
TRACE_HTTP_VAR = "F2W_OTEL_TRACE_HTTP"

#: Variables read by the resource builder (``runtime``), listed here so the drift test sees them.
COMMIT_VARS = ("F2W_CODE_COMMIT", "GIT_COMMIT")
PROFILE_VARS = ("F2W_IMAGE_PROFILE", "F2W_PROFILE_FILE", "F2W_DEPLOYMENT_PROFILE")

DEFAULT_ENDPOINT = "http://localhost:4318"
DEFAULT_SERVICE_NAME = "f2w"
DEFAULT_EXPORT_TIMEOUT_S = 3.0
DEFAULT_QUEUE_SIZE = 2048
_TRUE = frozenset({"1", "true", "yes", "on"})

#: Every environment variable this package reads (the documentation drift test uses this tuple).
ENV_VARS = (
    ENABLED_VAR, ENABLED_ALIAS_VAR, ENDPOINT_VAR, TRACES_ENDPOINT_VAR, HEADERS_VAR, PROTOCOL_VAR,
    SERVICE_NAME_VAR, RESOURCE_ATTRIBUTES_VAR, SAMPLER_VAR, SAMPLER_ARG_VAR, TRACE_FUNCTIONS_VAR,
    TRACE_MODULES_VAR, MIN_SPAN_MS_VAR, EXPORT_TIMEOUT_VAR, QUEUE_SIZE_VAR, TRACE_HTTP_VAR,
    *COMMIT_VARS, *PROFILE_VARS,
)


@dataclass(frozen=True)
class TracingConfig:
    """Validated tracing settings (header values are kept out of ``repr``)."""

    enabled: bool = False
    endpoint: str = DEFAULT_ENDPOINT + "/v1/traces"
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    service_name: str = DEFAULT_SERVICE_NAME
    resource_attributes: Mapping[str, str] = field(default_factory=dict)
    sampler: str = "parentbased_always_on"
    sampler_arg: float | None = None
    trace_functions: str | None = None
    trace_modules: tuple[str, ...] = ()
    min_span_ms: float = 0.0
    export_timeout_s: float = DEFAULT_EXPORT_TIMEOUT_S
    queue_size: int = DEFAULT_QUEUE_SIZE
    trace_http: bool = True

    @property
    def endpoint_host(self) -> str:
        """Host (no port) of the traces endpoint, for logs and the NO_PROXY rule."""
        return endpoint_host(self.endpoint)


def truthy(value: str | None) -> bool:
    """``1``/``true``/``yes``/``on`` (any case) are true; everything else, including unset, is false."""
    return (value or "").strip().lower() in _TRUE


def enabled_from(environ: Mapping[str, str]) -> bool:
    """Whether tracing is on: ``F2W_OTEL_ENABLED`` wins over its alias ``OTEL_ENABLED`` when both are set."""
    if (environ.get(ENABLED_VAR) or "").strip():
        return truthy(environ.get(ENABLED_VAR))
    return truthy(environ.get(ENABLED_ALIAS_VAR))


def endpoint_host(endpoint: str) -> str:
    """Hostname of ``endpoint`` (lower case; empty when it cannot be parsed)."""
    try:
        return (urlsplit(endpoint.strip()).hostname or "").lower()
    except ValueError:
        return ""


def traces_endpoint(environ: Mapping[str, str]) -> str:
    """Full OTLP/HTTP traces URL: the signal-specific variable verbatim, else the base plus ``/v1/traces``."""
    specific = (environ.get(TRACES_ENDPOINT_VAR) or "").strip()
    if specific:
        return specific
    base = (environ.get(ENDPOINT_VAR) or "").strip() or DEFAULT_ENDPOINT
    return base.rstrip("/") + "/v1/traces"


def parse_pairs(raw: str | None) -> dict[str, str]:
    """Parse ``k=v,k2=v2`` (the OTEL headers and resource-attribute syntax); values are percent-decoded."""
    out: dict[str, str] = {}
    for item in (raw or "").split(","):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            out[key.strip()] = unquote(value.strip())
    return out


def _float(environ: Mapping[str, str], name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    return value if value >= minimum else default


def _split(raw: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (raw or "").split(",") if part.strip())


def from_env(environ: Mapping[str, str] | None = None) -> TracingConfig:
    """Read the settings from ``environ`` (default ``os.environ``). Bad numbers fall back with a warning."""
    env = os.environ if environ is None else environ
    attrs = parse_pairs(env.get(RESOURCE_ATTRIBUTES_VAR))
    name = (env.get(SERVICE_NAME_VAR) or "").strip() or attrs.get("service.name") or DEFAULT_SERVICE_NAME
    attrs.pop("service.name", None)
    arg_raw = (env.get(SAMPLER_ARG_VAR) or "").strip()
    sampler_arg: float | None = None
    if arg_raw:
        try:
            sampler_arg = float(arg_raw)
        except ValueError:
            logger.warning("%s=%r is not a number; ignored", SAMPLER_ARG_VAR, arg_raw)
    functions = env.get(TRACE_FUNCTIONS_VAR)
    return TracingConfig(
        enabled=enabled_from(env),
        endpoint=traces_endpoint(env),
        headers=parse_pairs(env.get(HEADERS_VAR)),
        service_name=name,
        resource_attributes=attrs,
        sampler=(env.get(SAMPLER_VAR) or "").strip().lower() or "parentbased_always_on",
        sampler_arg=sampler_arg,
        trace_functions=functions,  # None = unset (built-in default list); empty = no function wrapping
        trace_modules=_split(env.get(TRACE_MODULES_VAR)),
        min_span_ms=_float(env, MIN_SPAN_MS_VAR, 0.0),
        export_timeout_s=_float(env, EXPORT_TIMEOUT_VAR, DEFAULT_EXPORT_TIMEOUT_S, minimum=0.1),
        queue_size=max(16, int(_float(env, QUEUE_SIZE_VAR, float(DEFAULT_QUEUE_SIZE)))),
        trace_http=(env.get(TRACE_HTTP_VAR) or "1").strip().lower() not in {"0", "false", "no", "off"},
    )
