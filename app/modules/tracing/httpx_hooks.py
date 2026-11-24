"""Client spans for ``httpx`` (every F2W model, OpenAlex, download and identity call goes through it).

``httpx.Client.send`` and ``httpx.AsyncClient.send`` are wrapped once (when tracing is on and
``F2W_OTEL_TRACE_HTTP`` is not off). Each call is an ``HTTP <METHOD>`` span (a non-standard method token is named ``HTTP other``) with the method, host, port,
scheme, status code and error type. The path, query string, headers and bodies are never recorded, and no
``traceparent`` header is injected (third-party APIs get no trace id from us).
"""

from __future__ import annotations

import functools
import logging
import threading
from typing import Any, cast

import httpx

from .runtime import is_enabled, start

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_ORIGINALS: dict[str, Any] = {}


def _span_name(request: httpx.Request) -> str:
    """``HTTP <METHOD>`` for a standard verb, ``HTTP other`` for any other token (a closed set, as the metrics label)."""
    from ..metrics.labels import http_method

    return f"HTTP {http_method(request.method)}"


def _attrs(request: httpx.Request) -> dict[str, object]:
    url = request.url
    return {"http.request.method": request.method, "server.address": url.host, "server.port": url.port or (
        443 if url.scheme == "https" else 80), "url.scheme": url.scheme}


def _request(args: tuple[object, ...], kwargs: dict[str, object]) -> httpx.Request | None:
    candidate = args[0] if args else kwargs.get("request")
    return candidate if isinstance(candidate, httpx.Request) else None


def _done(handle: Any, response: httpx.Response) -> None:  # noqa: ANN401  # Any: SpanHandle (kept untyped to avoid an import cycle in annotations)
    handle.set_attribute("http.response.status_code", response.status_code)
    if response.status_code >= 500:
        handle.set_error_status(f"HTTP {response.status_code}")


def _wrap_sync(original: Any) -> Any:  # noqa: ANN401  # Any: the original unbound method
    @functools.wraps(original)
    def send(self: httpx.Client, *args: object, **kwargs: object) -> httpx.Response:
        request = _request(args, kwargs)
        if not is_enabled() or request is None:
            return cast(httpx.Response, original(self, *args, **kwargs))
        with start(_span_name(request), _attrs(request)) as handle:
            response = cast(httpx.Response, original(self, *args, **kwargs))
            _done(handle, response)
            return response

    return send


def _wrap_async(original: Any) -> Any:  # noqa: ANN401  # Any: the original unbound coroutine method
    @functools.wraps(original)
    async def send(self: httpx.AsyncClient, *args: object, **kwargs: object) -> httpx.Response:
        request = _request(args, kwargs)
        if not is_enabled() or request is None:
            return cast(httpx.Response, await original(self, *args, **kwargs))
        with start(_span_name(request), _attrs(request)) as handle:
            response = cast(httpx.Response, await original(self, *args, **kwargs))
            _done(handle, response)
            return response

    return send


def install() -> bool:
    """Wrap the two ``send`` methods once; ``False`` when they already were."""
    with _LOCK:
        if _ORIGINALS:
            return False
        _ORIGINALS["sync"] = httpx.Client.send
        _ORIGINALS["async"] = httpx.AsyncClient.send
        httpx.Client.send = _wrap_sync(_ORIGINALS["sync"])  # type: ignore[method-assign]
        httpx.AsyncClient.send = _wrap_async(_ORIGINALS["async"])  # type: ignore[method-assign]
    return True


def uninstall() -> None:
    """Restore the original methods (tests)."""
    with _LOCK:
        if _ORIGINALS:
            httpx.Client.send = _ORIGINALS["sync"]  # type: ignore[method-assign]
            httpx.AsyncClient.send = _ORIGINALS["async"]  # type: ignore[method-assign]
            _ORIGINALS.clear()
