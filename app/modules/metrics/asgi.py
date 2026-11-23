"""ASGI middleware recording ``f2w.http.requests`` and ``f2w.http.request.duration``.

Labels are the matched route *template* (``/v1/jobs/{job_id}``, never the raw path), the method and the status
class. A request that matched no route (a 404) is ``unmatched``. Health probes (``/livez``, ``/readyz``,
``/health``, ``/healthz``) are skipped, as in tracing: they are answered before routing and would only add noise.
The duration covers the whole response, so long streams (job events) land in the top buckets.

Add it with :func:`install`, which does nothing when metrics are off.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import runtime
from ..tracing.asgi import excluded


def declared_routes(app: Any) -> frozenset[str]:  # noqa: ANN401  # Any: a Starlette/FastAPI app or router (only ``routes`` is read)
    """Every route template the application declares (mounts contribute their prefix and their children's paths)."""
    found: set[str] = set()

    def walk(routes: Any, prefix: str) -> None:  # noqa: ANN401
        for route in routes or ():
            path = getattr(route, "path", None)
            if isinstance(path, str):
                found.add(path)
                found.add(prefix + path)
            children = getattr(route, "routes", None)
            if children:
                walk(children, prefix + (path if isinstance(path, str) else ""))

    walk(getattr(app, "routes", None), "")
    return frozenset(found)


def matched_template(scope: Scope) -> str | None:
    """The matched route's path template: FastAPI's ``scope["route"]``, else the first fully matching route."""
    template = getattr(scope.get("route"), "path", None)
    if isinstance(template, str):
        return template
    from starlette.routing import Match

    for route in getattr(scope.get("app"), "routes", None) or ():
        try:
            if route.matches(scope)[0] is Match.FULL and isinstance(getattr(route, "path", None), str):
                return str(route.path)
        except Exception:  # a custom route type: treat as unmatched
            continue
    return None


class MetricsMiddleware:
    """Pure ASGI middleware: one count and one duration per HTTP request (websockets and lifespan pass through)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._known: frozenset[str] | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Record the request unless it is not HTTP, metrics are off, or the path is a health probe."""
        if scope["type"] != "http" or not runtime.is_enabled() or excluded(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status: list[int] = []

        async def counting_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status.append(int(message["status"]))
            await send(message)

        try:
            await self.app(scope, receive, counting_send)
        finally:
            self._finish(scope, status, started)

    def _finish(self, scope: Scope, status: list[int], started: float) -> None:
        try:
            if self._known is None:
                self._known = declared_routes(scope.get("app"))
            runtime.record_http_request(route=matched_template(scope), method=scope.get("method"), status=status[0] if status else 500,
                                        seconds=time.perf_counter() - started, known_routes=self._known)
        except Exception:  # recording must never fail the request
            return


def install(app: Any) -> bool:  # noqa: ANN401  # Any: a Starlette/FastAPI application (only ``add_middleware`` is used)
    """Add :class:`MetricsMiddleware` as the outermost middleware when metrics are on; return whether it was added."""
    if not runtime.is_enabled():
        return False
    app.add_middleware(MetricsMiddleware)
    return True
