"""Process-wide metrics state: one-time init, the instruments and the ``record_*`` entry points.

Everything here is a cheap no-op until :func:`init` has run with metrics enabled: each ``record_*`` function
returns after one attribute read, and the OpenTelemetry packages are not imported. :func:`init` is idempotent
and safe in the API process and the worker process; each builds its own provider and flushes it at exit.

The provider is private to this module (not installed as the global provider), so enabling metrics changes
nothing for other libraries and tests can build and discard providers freely. Recording is in memory only
and never raises into the application.
"""

from __future__ import annotations

import atexit
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import logging
import os
import threading
from typing import TYPE_CHECKING, Any

from . import config as cfg
from . import labels
from ..tracing import runtime as tracing_runtime
from .catalog import METER_NAME, SPECS

if TYPE_CHECKING:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import MetricExporter, MetricReader

logger = logging.getLogger(__name__)


@dataclass
class _State:
    """Mutable per-process state (writes guarded by :data:`_LOCK`)."""

    enabled: bool = False
    initialised: bool = False
    config: cfg.MetricsConfig = field(default_factory=cfg.MetricsConfig)
    provider: MeterProvider | None = None
    instruments: dict[str, Any] = field(default_factory=dict)
    exit_hook: bool = False
    process: str = ""
    queue_source: Callable[[], Iterable[tuple[str, str, int]]] | None = None


_STATE = _State()
_LOCK = threading.RLock()


def is_enabled() -> bool:
    """Whether :func:`init` ran with metrics on in this process."""
    return _STATE.enabled


def init(process: str | None = None, *, environ: Mapping[str, str] | None = None,
         reader: MetricReader | None = None, exporter: MetricExporter | None = None) -> bool:
    """Start metrics once per process when enabled; return whether instruments are recording.

    Disabled (the default): returns ``False`` without importing OpenTelemetry or changing any global. Enabled
    needs ``F2W_METRICS_ENABLED`` truthy and ``OTEL_EXPORTER_OTLP_ENDPOINT`` set. Later calls return the first
    result. Any failure (for example a missing exporter package) is logged and leaves metrics off.

    Args:
        process: Role of this process (``api`` or ``worker``), recorded as ``f2w.process``.
        environ: Settings source (default ``os.environ``).
        reader: Test hook: collect into this reader instead of exporting over OTLP/HTTP.
        exporter: Test hook: export through this exporter on the periodic reader.
    """
    if _STATE.initialised:
        return _STATE.enabled
    env = os.environ if environ is None else environ
    config = cfg.from_env(env)
    if not config.enabled:
        return False
    with _LOCK:
        if _STATE.initialised:
            return _STATE.enabled
        try:
            _start(config, env, process, reader, exporter)
        except Exception as exc:
            logger.warning("metrics requested but not started (%s: %s)", type(exc).__name__, exc)
            _STATE.initialised, _STATE.enabled = True, False
            _STATE.provider, _STATE.instruments = None, {}
            return False
    return _STATE.enabled


def _start(config: cfg.MetricsConfig, environ: Mapping[str, str], process: str | None,
           reader: MetricReader | None, exporter: MetricExporter | None) -> None:
    from . import _sdk

    profile = tracing_runtime.image_profile(environ)
    resource = _sdk.build_resource(config.service_name, process, profile)
    provider, _ = _sdk.build_provider(config, resource, reader, exporter)
    meter = provider.get_meter(METER_NAME)
    instruments: dict[str, Any] = {}
    for spec in SPECS:
        if spec.kind == "gauge":
            continue  # observable gauges are registered below
        if spec.kind == "counter":
            instruments[spec.key] = meter.create_counter(spec.name, unit=spec.unit, description=spec.description)
        else:
            instruments[spec.key] = meter.create_histogram(spec.name, unit=spec.unit, description=spec.description)
    _register_queue_depth(meter)
    if process == "api":  # one build-info series per release; the worker would only duplicate it
        _register_build_info(meter, labels.build_commit(tracing_runtime.code_commit()), labels.build_profile(profile))
    _STATE.config, _STATE.process = config, process or ""
    _STATE.provider, _STATE.instruments = provider, instruments
    _STATE.enabled = _STATE.initialised = True
    if not _STATE.exit_hook:
        atexit.register(shutdown)
        _STATE.exit_hook = True
    logger.info("metrics on: process=%s endpoint=%s interval=%ss", process, config.endpoint.split("?", 1)[0],
                config.export_interval_s)


def _register_build_info(meter: Any, commit: str, profile: str) -> None:  # noqa: ANN401  # Any: an SDK Meter (imported lazily)
    from opentelemetry.metrics import Observation

    spec = next(s for s in SPECS if s.key == "build_info")
    attrs = {"commit": commit, "profile": profile}

    def callback(_options: object) -> list[Any]:
        return [Observation(1, attrs)]

    meter.create_observable_gauge(spec.name, callbacks=[callback], unit=spec.unit, description=spec.description)


def _register_queue_depth(meter: Any) -> None:  # noqa: ANN401  # Any: an SDK Meter (imported lazily)
    from opentelemetry.metrics import Observation

    spec = next(s for s in SPECS if s.key == "queue_depth")

    def callback(_options: object) -> list[Any]:
        source = _STATE.queue_source
        if source is None:  # the API process owns no queue: it reports nothing
            return []
        try:
            return [Observation(int(count), {"kind": str(kind), "state": labels.enum(state, labels.QUEUE_STATES)})
                    for kind, state, count in source()]
        except Exception as exc:  # a locked or closed database must not break the export
            logger.debug("queue depth not observed: %s", type(exc).__name__)
            return []

    meter.create_observable_gauge(spec.name, callbacks=[callback], unit=spec.unit, description=spec.description)


def set_queue_depth_source(source: Callable[[], Iterable[tuple[str, str, int]]] | None) -> None:
    """Report job queue depth from ``source``: ``(kind, state, count)`` triples, ``kind`` already a closed-set value.

    Called by the worker process, which owns the queue. The source runs on the export thread once per interval and
    must be cheap; any exception is swallowed.
    """
    _STATE.queue_source = source


def shutdown() -> None:
    """Export what is pending and stop the provider (registered with ``atexit``); never raises."""
    provider = _STATE.provider
    if provider is None:
        return
    try:
        provider.shutdown()
    except Exception as exc:
        logger.debug("metrics shutdown failed: %s", type(exc).__name__)


def reset() -> None:
    """Undo :func:`init` (tests): shut the provider down and forget the state."""
    with _LOCK:
        shutdown()
        _STATE.enabled = _STATE.initialised = False
        _STATE.provider = None
        _STATE.instruments = {}
        _STATE.config = cfg.MetricsConfig()
        _STATE.process = ""
        _STATE.queue_source = None


def _add(key: str, attrs: Mapping[str, str], amount: int = 1) -> None:
    instrument = _STATE.instruments.get(key)
    if instrument is not None:
        try:
            instrument.add(amount, attrs)
        except Exception as exc:  # recording must never fail the caller
            logger.debug("metric %s not recorded: %s", key, type(exc).__name__)


def _observe(key: str, attrs: Mapping[str, str], value: float) -> None:
    instrument = _STATE.instruments.get(key)
    if instrument is not None:
        try:
            instrument.record(max(0.0, float(value)), attrs)
        except Exception as exc:
            logger.debug("metric %s not recorded: %s", key, type(exc).__name__)


def record_http_request(*, route: object, method: object, status: object, seconds: float,
                        known_routes: frozenset[str] | None) -> None:
    """Count one HTTP request and record its duration.

    Args:
        route: The matched route template, or ``None`` when no route matched.
        method: HTTP method.
        status: Response status code (a failed request without one is passed as 500).
        seconds: Server-side duration.
        known_routes: The templates the application declared; a ``route`` outside them is ``unmatched``.
    """
    if not _STATE.enabled:
        return
    attrs = {"http_route": labels.http_route(route, known_routes), "http_method": labels.http_method(method),
             "status_class": labels.status_class(status)}
    _add("http_requests", attrs)
    _observe("http_duration", attrs, seconds)


#: Job states that end a run for metrics purposes (a ``queued`` result is a requeue, not a finish).
_JOB_OUTCOME = {"succeeded": "succeeded", "failed": "failed", "cancelled": "cancelled", "paused_budget": "paused",
                "paused_provider": "paused", "awaiting_review": "paused"}


def record_job(*, kind: object, state: object, seconds: float, kinds: Iterable[str]) -> None:
    """Count one finished job run and record its duration.

    Args:
        kind: The job kind; one of ``kinds`` (the kinds the job service registered) or it is ``other``.
        state: The state the run ended in (``JobState`` or its value; ``failed`` for a run that raised).
        seconds: Wall-clock duration of the run.
        kinds: The registered job kinds (code-defined, a closed set).
    """
    if not _STATE.enabled:
        return
    outcome = _JOB_OUTCOME.get(str(getattr(state, "value", state)))
    if outcome is None:  # requeued or lease lost: the job did not finish here
        return
    attrs = {"kind": labels.enum(kind, kinds), "outcome": outcome}
    _add("jobs", attrs)
    _observe("job_duration", attrs, seconds)
