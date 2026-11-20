"""OpenTelemetry tracing for FAIR2WISE (OTLP/HTTP to Jaeger), off by default.

Operator page: ``mkdocs/docs/observability.md``. Typical use::

    from app.modules import tracing

    tracing.init(process="api")                 # once per process; no-op unless F2W_OTEL_ENABLED
    with tracing.span("library.estimate", f2w_corpus="x") as s:
        s.set_attribute("f2w.paper_count", 12)  # ids, names, counts, sizes only (see safe.py)

    @tracing.traced("kg.load")                  # sync or async, signature preserved
    def load(path: str) -> dict[str, object]: ...

Functions can also be traced without editing them: ``F2W_OTEL_TRACE_FUNCTIONS`` (see
:mod:`app.modules.tracing.instrument`; the built-in list is :mod:`app.modules.tracing.defaults`).

Nothing is recorded that could leave the protected store (``docs/adr/content-disclosure.md``): no prompt
text, passages, document text, keys, tokens, file contents or e-mail addresses.
"""

from __future__ import annotations

from .config import ENV_VARS, TracingConfig, from_env
from .decorators import traced
from .runtime import (
    SpanHandle,
    current_trace_id,
    current_traceparent,
    flush,
    init,
    is_enabled,
    record_model_call,
    record_span,
    shutdown,
    span,
    span_from_traceparent,
    start,
)

__all__ = (
    "ENV_VARS", "SpanHandle", "TracingConfig", "current_trace_id", "current_traceparent", "flush", "from_env", "init",
    "is_enabled", "record_model_call", "record_span", "shutdown", "span", "span_from_traceparent", "start", "traced",
)
