"""Process-wide tracing state: one-time init, spans, trace ids and ``traceparent`` propagation.

Everything here is a cheap no-op until :func:`init` has run with tracing enabled: :func:`span` returns a
shared do-nothing context manager, :func:`current_trace_id` returns ``None``, and the OpenTelemetry
packages are not imported. :func:`init` is idempotent and safe in the API process, the worker process
and the supervisor's children; each process builds its own provider and flushes it at exit.

The provider is private to this module (it is not installed as the global provider), so enabling
tracing changes nothing for other libraries and tests can build and discard providers freely.
"""

from __future__ import annotations

import atexit
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import TYPE_CHECKING, Any

from . import config as cfg
from .safe import error_type, sanitize

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter
    from opentelemetry.trace import Span, Tracer

logger = logging.getLogger(__name__)

#: Used only when the project is not installed as a distribution (the images set ``PYTHONPATH=/app``).
FALLBACK_VERSION = "0.1.0.dev0"
TRACER_NAME = "app.modules.tracing"
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")
_ZERO_TRACE = "0" * 32


@dataclass
class _State:
    """Mutable per-process state (guarded by :data:`_LOCK` for writes)."""

    enabled: bool = False
    initialised: bool = False
    config: cfg.TracingConfig = field(default_factory=cfg.TracingConfig)
    provider: TracerProvider | None = None
    tracer: Tracer | None = None
    floors: dict[str, float] = field(default_factory=dict)
    exit_hook: bool = False
    process: str = ""


_STATE = _State()
_LOCK = threading.RLock()


class SpanHandle:
    """What a ``with span(...) as s`` block receives; every method is a no-op for a disabled/unsampled span."""

    __slots__ = ("_errored", "_span")

    def __init__(self, span: Span | None) -> None:
        self._span = span
        self._errored = False

    def set_attribute(self, key: str, value: object) -> None:
        """Set one attribute if it passes the content-disclosure filter (:mod:`.safe`)."""
        if self._span is not None:
            for k, v in sanitize({key: value}).items():
                self._span.set_attribute(k, v)

    def set_attributes(self, attributes: Mapping[str, object]) -> None:
        """Set several attributes through the same filter."""
        if self._span is not None:
            for k, v in sanitize(attributes).items():
                self._span.set_attribute(k, v)

    def update_name(self, name: str) -> None:
        """Rename the span (for example once the route template of a request is known)."""
        if self._span is not None:
            self._span.update_name(name)

    def set_error(self, exc: BaseException) -> None:
        """Mark the span failed with the exception *type* only (messages can quote content); the first call wins."""
        if self._span is None or self._errored:
            return
        self._errored = True
        from opentelemetry.trace import Status, StatusCode

        name = error_type(exc)
        self._span.set_status(Status(StatusCode.ERROR, name))
        self._span.set_attribute("error.type", name)
        self._span.add_event("exception", {"exception.type": name})

    def set_error_status(self, description: str) -> None:
        """Mark the span failed without an exception (for example an HTTP 5xx); ``description`` is a fixed word."""
        if self._span is not None:
            from opentelemetry.trace import Status, StatusCode

            self._span.set_status(Status(StatusCode.ERROR, description))

    @property
    def trace_id(self) -> str | None:
        """Hex trace id of this span, or ``None`` when it records nothing."""
        if self._span is None:
            return None
        ctx = self._span.get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None


class _NoopContext:
    """Shared, reusable context manager returned by :func:`span` when tracing is off."""

    __slots__ = ()
    _HANDLE = SpanHandle(None)

    def __enter__(self) -> SpanHandle:
        return self._HANDLE

    def __exit__(self, *exc_info: object) -> None:
        return None


_NOOP = _NoopContext()


def is_enabled() -> bool:
    """Whether :func:`init` ran with tracing on in this process."""
    return _STATE.enabled


def current_config() -> cfg.TracingConfig:
    """The configuration :func:`init` used (defaults when tracing never started)."""
    return _STATE.config


# --------------------------------------------------------------------------- init / shutdown


def code_commit() -> str | None:
    """The code commit: ``F2W_CODE_COMMIT``/``GIT_COMMIT`` (images), else the checkout's ``git_sha`` helper.

    Normalised like the ``commit`` label of ``f2w.build.info`` (:func:`..metrics.labels.valid_commit`: 7 to 40
    lower-case hex digits): anything else yields ``None``, so the trace omits ``f2w.code_commit`` exactly where the
    metric says ``unknown``. A non-empty variable is authoritative (an invalid one does not fall back to git).
    """
    from ..metrics.labels import valid_commit

    for name in cfg.COMMIT_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return valid_commit(value)
    try:
        from ..f2w_agent.trace import git_sha

        return valid_commit(git_sha())
    except Exception:
        return None


def image_profile(environ: Mapping[str, str]) -> str | None:
    """Image profile name: ``F2W_IMAGE_PROFILE``, else the baked profile file's ``profile``, else the deployment profile."""
    explicit = (environ.get("F2W_IMAGE_PROFILE") or "").strip()
    if explicit:
        return explicit
    path = Path(environ.get("F2W_PROFILE_FILE") or "/etc/fair2wise/profile.json")
    try:
        name = json.loads(path.read_text(encoding="utf-8")).get("profile")
        if isinstance(name, str) and name:
            return name
    except (OSError, ValueError, AttributeError):
        pass
    return (environ.get("F2W_DEPLOYMENT_PROFILE") or "").strip() or None


def service_version() -> str:
    """Application version (distribution metadata when installed, else :data:`FALLBACK_VERSION`)."""
    for dist in ("fair2wise", "f2w"):
        try:
            return importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            continue
    return FALLBACK_VERSION


def resource_attributes(environ: Mapping[str, str], process: str | None) -> dict[str, str | int]:
    """The F2W resource attributes (all ids/names; no secrets)."""
    attrs: dict[str, str | int] = {"service.version": service_version()}
    for key, value in (("f2w.code_commit", code_commit()), ("f2w.profile", image_profile(environ)),
                       ("f2w.process", process)):
        if value:
            attrs[key] = value
    return attrs


def init(process: str | None = None, *, environ: Mapping[str, str] | None = None,
         exporter: SpanExporter | None = None) -> bool:
    """Start tracing once per process when enabled; return whether spans are being recorded.

    Disabled (the default): returns ``False`` without importing OpenTelemetry or changing any global.
    Enabled: builds the provider and exporter, wraps the configured functions, instruments ``httpx``, and
    registers a flush at exit. Later calls return the first result. Any failure (for example a missing
    exporter package) is logged and leaves tracing off; it never raises into the application.

    Args:
        process: Role of this process (``api``, ``worker``...), recorded as ``f2w.process``.
        environ: Settings source (default ``os.environ``).
        exporter: Test hook: export to this exporter synchronously instead of OTLP/HTTP.
    """
    if _STATE.initialised:
        return _STATE.enabled
    config = cfg.from_env(environ)
    if not config.enabled:
        return False
    with _LOCK:
        if _STATE.initialised:
            return _STATE.enabled
        try:
            _start(config, environ if environ is not None else os.environ, process, exporter)
        except Exception as exc:
            logger.warning("tracing requested but not started (%s: %s)", type(exc).__name__, exc)
            _STATE.initialised, _STATE.enabled = True, False
            return False
    return _STATE.enabled


def _start(config: cfg.TracingConfig, environ: Mapping[str, str], process: str | None,
           exporter: SpanExporter | None) -> None:
    from . import _sdk, instrument

    _STATE.config = config
    _STATE.process = process or ""
    provider = _sdk.build_provider(config, resource_attributes(environ, process), _STATE.floors, exporter)
    _STATE.provider = provider
    _STATE.tracer = provider.get_tracer(TRACER_NAME)
    _STATE.enabled = _STATE.initialised = True
    if not _STATE.exit_hook:
        atexit.register(shutdown)
        _STATE.exit_hook = True
    wrapped = instrument.apply(config)
    if config.trace_http:
        from . import httpx_hooks

        httpx_hooks.install()
    logger.info("tracing on: service=%s endpoint=%s sampler=%s functions_wrapped=%d", config.service_name,
                config.endpoint.split("?", 1)[0], config.sampler, len(wrapped))


def flush(timeout_s: float = 5.0) -> bool:
    """Export queued spans now (bounded by ``timeout_s``); ``True`` when nothing was pending or it was flushed."""
    provider = _STATE.provider
    if provider is None:
        return True
    return bool(provider.force_flush(int(timeout_s * 1000)))


def shutdown() -> None:
    """Flush and stop the provider (registered with ``atexit``); never raises, bounded by the export timeout."""
    provider = _STATE.provider
    if provider is None:
        return
    try:
        provider.shutdown()
    except Exception as exc:
        logger.debug("tracing shutdown failed: %s", type(exc).__name__)


def reset() -> None:
    """Undo :func:`init` (tests): shut the provider down, unwrap nothing, forget the state."""
    from . import instrument

    with _LOCK:
        shutdown()
        try:
            from . import httpx_hooks

            httpx_hooks.uninstall()
        except Exception:
            logger.debug("httpx hooks not installed")
        instrument.forget()
        _STATE.enabled = _STATE.initialised = False
        _STATE.provider = _STATE.tracer = None
        _STATE.config = cfg.TracingConfig()
        _STATE.floors.clear()


def register_min_duration(span_name: str, milliseconds: float) -> None:
    """Drop finished spans named ``span_name`` that ran for less than ``milliseconds`` (errors are always kept)."""
    if milliseconds > 0:
        _STATE.floors[span_name] = milliseconds


# --------------------------------------------------------------------------- spans


@contextmanager
def _live(name: str, attrs: Mapping[str, object], parent: str | None, kind: str,
          benign: tuple[type[BaseException], ...]) -> Iterator[SpanHandle]:
    from opentelemetry.trace import SpanKind

    tracer = _STATE.tracer
    assert tracer is not None
    context = _context_from(parent) if parent else None
    span_kind = SpanKind.SERVER if kind == "server" else SpanKind.INTERNAL
    with tracer.start_as_current_span(name, context=context, kind=span_kind, attributes=sanitize(attrs),
                                      record_exception=False, set_status_on_exception=False) as sp:
        handle = SpanHandle(sp)
        try:
            yield handle
        except BaseException as exc:
            if isinstance(exc, benign):  # control flow (a pause, a stop): recorded, not an error
                handle.set_attribute("f2w.status", type(exc).__name__)
            else:
                handle.set_error(exc)
            raise


def start(name: str, attrs: Mapping[str, object] | None = None, *, traceparent: str | None = None,
          kind: str = "internal", benign: tuple[type[BaseException], ...] = ()) -> AbstractContextManager[SpanHandle]:
    """Open a span named ``name`` (a no-op context manager when tracing is off).

    Args:
        name: Span name: a fixed, low-cardinality word (never user text).
        attrs: Attributes; anything not on the safe list is dropped (see :mod:`.safe`).
        traceparent: W3C header of the parent (for example from a job record); ``None`` uses the current span.
        kind: ``internal`` or ``server``.
        benign: Exception types that end the span without an error status (``f2w.status`` records the type).
    """
    if not _STATE.enabled:
        return _NOOP
    return _live(name, attrs or {}, traceparent, kind, benign)


def span(name: str, /, **attrs: object) -> AbstractContextManager[SpanHandle]:
    """Open a child span of the current one and yield its :class:`SpanHandle`.

    Keyword names with dots cannot be written as keywords; pass them with ``span(name, **{"f2w.stage": "x"})``
    or call :meth:`SpanHandle.set_attribute` inside the block. Disabled tracing returns a shared no-op.
    """
    return start(name, attrs)


def span_from_traceparent(name: str, traceparent: str | None, /, **attrs: object) -> AbstractContextManager[SpanHandle]:
    """Like :func:`span` but a child of the remote span named by ``traceparent`` (a new root when invalid)."""
    return start(name, attrs, traceparent=traceparent if valid_traceparent(traceparent) else None)


def record_span(name: str, attrs: Mapping[str, object], *, duration_ms: float, error: BaseException | None = None) -> None:
    """Record an already-finished operation as a span ending now and starting ``duration_ms`` ago."""
    tracer = _STATE.tracer
    if not _STATE.enabled or tracer is None:
        return
    end = time.time_ns()
    sp = tracer.start_span(name, start_time=end - int(max(duration_ms, 0.0) * 1e6), attributes=sanitize(attrs))
    handle = SpanHandle(sp)
    if error is not None:
        handle.set_error(error)
    sp.end(end_time=end)


#: The closed set of model-call labels a span may carry: every literal passed as ``label``/``trace_label`` in code,
#: ``chat`` (the default) and ``other``. Anything else (a label built from user or model text) becomes ``other``,
#: so the ``llm.call <label>`` span name has bounded cardinality and never holds free text.
LLM_LABELS = frozenset({
    "baseline", "KG-RAG", "KG-RAG-judge", "KG-RAG-leeway", "chat", "evidence-debate", "session-memory-compress",
    "legacy-route", "direct-response", "domain-knowledge-fallback", "rewrite-question", "general-answer",
    "json-graph-leeway", "pending-download-intent", "pdf-validate", "paper-evidence", "vision.orientation",
    "vision.classify", "orchestrator-classify", "supervisor-plan", "schema-generate", "other",
})


def llm_label(label: object) -> str:
    """``label`` when it is in :data:`LLM_LABELS`, else ``"other"``."""
    return label if isinstance(label, str) and label in LLM_LABELS else "other"


def record_model_call(*, label: str, model: str | None, latency_ms: float, prompt_chars: int, response_chars: int | None,
                      error: BaseException | None) -> None:
    """Span for one model call: model name, call-site label, latency, sizes and status. Never the text."""
    if not _STATE.enabled:
        return
    label = llm_label(label)
    record_span(f"llm.call {label}", {
        "gen_ai.request.model": model, "f2w.llm.label": label, "f2w.llm.latency_ms": round(latency_ms, 3),
        "f2w.llm.prompt_chars": prompt_chars, "f2w.llm.response_chars": response_chars,
        "f2w.llm.status": "error" if error else "ok"}, duration_ms=latency_ms, error=error)


# --------------------------------------------------------------------------- ids and propagation


def current_trace_id() -> str | None:
    """Hex trace id of the active sampled-or-not span in this context, else ``None`` (always ``None`` when off)."""
    if not _STATE.enabled:
        return None
    from opentelemetry import trace

    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def current_traceparent() -> str | None:
    """W3C ``traceparent`` of the active span (to store with a job), or ``None`` when there is none."""
    if not _STATE.enabled:
        return None
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


def valid_traceparent(value: str | None) -> bool:
    """Whether ``value`` has the W3C ``traceparent`` shape and a non-zero trace id."""
    return bool(value) and bool(_TRACEPARENT.match(value or "")) and (value or "")[3:35] != _ZERO_TRACE


def _context_from(traceparent: str) -> Any:  # noqa: ANN401  # Any: an opentelemetry Context (imported lazily)
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    return TraceContextTextMapPropagator().extract({"traceparent": traceparent})
