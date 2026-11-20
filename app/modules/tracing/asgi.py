"""ASGI request spans for the F2W API (a small stand-in for ``opentelemetry-instrumentation-asgi``).

The instrumentation packages are not in ``pixi.lock``; adding them would pull more packages than the
exporter does, so this middleware records the same semantic-convention attributes itself:
``http.request.method``, ``http.route`` (the matched route template once routing ran), ``url.path`` (the
template when known, never the query string), ``http.response.status_code`` and the server duration
(``f2w.http.duration_ms``). Health and probe paths (``/livez``, ``/readyz``, ``/health``, ``/healthz``) are
not traced. When tracing is on the response also carries ``X-F2W-Trace-Id`` (the 32-hex trace id, no body
change) so a browser or curl user can look the request up in Jaeger.

Add it with :func:`install`, which does nothing when tracing is off, so a disabled deployment keeps the
exact middleware stack it had.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import runtime

EXCLUDED_PATHS = ("/livez", "/readyz", "/health", "/healthz")
TRACE_ID_HEADER = b"x-f2w-trace-id"


def excluded(path: str) -> bool:
    """Whether ``path`` (with or without a gateway prefix such as ``/fair2wise/api``) is a health probe."""
    trimmed = path.rstrip("/") or "/"
    return any(trimmed.endswith(p) for p in EXCLUDED_PATHS)  # each entry starts with "/", so this is a segment match


def route_template(scope: Scope) -> str | None:
    """The matched route's path template (``/v1/jobs/{job_id}``), or ``None`` before/without routing."""
    route = scope.get("route")  # set by FastAPI's routes
    template = getattr(route, "path", None)
    if isinstance(template, str):
        return template
    params = scope.get("path_params") or {}  # plain Starlette: rebuild the template from the matched parameters
    if not params:
        return None
    by_value = {str(value): name for name, value in params.items()}
    segments = [f"{{{by_value[seg]}}}" if seg in by_value else seg for seg in str(scope.get("path", "")).split("/")]
    return "/".join(segments)


def _incoming_traceparent(scope: Scope) -> str | None:
    for key, value in scope.get("headers") or ():
        if key == b"traceparent":
            return str(value.decode("latin-1"))
    return None


class TracingMiddleware:
    """Pure ASGI middleware: one ``SERVER`` span per HTTP request (websockets and lifespan pass through)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Trace the request unless it is not HTTP, tracing is off, or the path is a health probe."""
        if scope["type"] != "http" or not runtime.is_enabled() or excluded(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "GET"))
        started = time.perf_counter()
        status: list[int] = []
        parent = _incoming_traceparent(scope)
        with runtime.start(method, {"http.request.method": method, "url.path": scope.get("path", ""),
                                    "url.scheme": scope.get("scheme", "http")},
                           traceparent=parent if runtime.valid_traceparent(parent) else None,
                           kind="server") as handle:
            trace_id = handle.trace_id

            async def traced_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    status.append(int(message["status"]))
                    if trace_id:
                        message = {**message, "headers": [*message.get("headers", ()),
                                                          (TRACE_ID_HEADER, trace_id.encode("ascii"))]}
                await send(message)

            try:
                await self.app(scope, receive, traced_send)
            finally:
                self._finish(handle, scope, method, status, started)

    @staticmethod
    def _finish(handle: runtime.SpanHandle, scope: Scope, method: str, status: list[int], started: float) -> None:
        template = route_template(scope)
        if template:
            handle.update_name(f"{method} {template}")
            handle.set_attributes({"http.route": template, "url.path": template})
        handle.set_attribute("f2w.http.duration_ms", round((time.perf_counter() - started) * 1000, 3))
        if status:
            handle.set_attribute("http.response.status_code", status[0])
            if status[0] >= 500:
                handle.set_error_status(f"HTTP {status[0]}")


def install(app: Any) -> bool:  # noqa: ANN401  # Any: a Starlette/FastAPI application (only ``add_middleware`` is used)
    """Add :class:`TracingMiddleware` as the outermost middleware when tracing is on; return whether it was added."""
    if not runtime.is_enabled():
        return False
    app.add_middleware(TracingMiddleware)
    return True
