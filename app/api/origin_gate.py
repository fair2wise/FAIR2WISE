"""Browser-origin protection for state-changing requests (I-07; access contract section 9).

CORS controls which responses a browser lets a page *read*; it does not stop the server
from *processing* a cross-site request. A page on another site can still send a simple
(non-preflighted) ``POST`` that carries the user's cookie, so an effect would happen
before the browser discards the response. This module refuses such requests before any
route code runs:

* :class:`OriginGateMiddleware` guards ``POST``/``PUT``/``PATCH``/``DELETE`` on the given
  path prefixes (the T14 app uses ``/v1/`` and ``/auth/``).
* :func:`check_browser_origin` is the same decision for one request; the ALS sign-in and
  sign-out handlers call it themselves, so they are protected in every app that mounts
  them, including the legacy ``create_app``.

Decision for one state-changing request:

1. ``Sec-Fetch-Site: cross-site`` (sent by every current browser) is refused.
2. A present ``Origin`` is allowed only when it is in the allowlist (the app's CORS
   origins) or names the request's own host (``Host``, or ``X-Forwarded-Host`` set by the
   gateway). Browsers attach ``Origin`` to every ``POST``; a page cannot suppress it.
3. No ``Origin`` and no ``Sec-Fetch-Site``: a non-browser client (curl, SDK, tests). It
   is not a CSRF vector, and it still needs a valid session or the local profile.

This supplements authentication and authorization; it is not a replacement for either.

Adapted from als-computing/forge_rsoxs_analysis src/forge/api/origin_gate.py
(``OriginGateMiddleware``) (BSD-3-Clause, Copyright (c) 2026, The Regents of the University
of California, through Lawrence Berkeley National Laboratory), adding same-origin matching,
``Sec-Fetch-Site`` and several path prefixes.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

#: Methods that can change state. GET/HEAD/OPTIONS are never gated.
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Stable error code of a refused cross-site request (403).
CROSS_SITE_CODE = "cross_site_request"


def _default_port(scheme: str) -> Optional[int]:
    return {"http": 80, "https": 443}.get(scheme)


def _origin_host(origin: str) -> Optional[Tuple[str, int]]:
    """Return ``(host, port)`` of an ``Origin`` value, or ``None`` when it is not an http(s) origin."""
    try:
        parts = urlsplit(origin.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts.hostname.lower(), port or _default_port(scheme) or 0


def _host_header(value: str, scheme: str) -> Optional[Tuple[str, int]]:
    """Return ``(host, port)`` of a ``Host``-style header value (first entry only)."""
    first = value.split(",", 1)[0].strip()
    if not first:
        return None
    try:
        parts = urlsplit(f"//{first}")
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname.lower(), port or _default_port(scheme) or 0


def origin_allowed(origin: str, headers: Mapping[str, str], *, scheme: str = "http",
                   allowed_origins: Iterable[str] = (), allowed_origin_regex: Optional[str] = None) -> bool:
    """Return whether a present ``Origin`` may make a state-changing request.

    Args:
        origin: The ``Origin`` header value. ``null`` (sandboxed frames, some redirects) is refused.
        headers: Lower-cased request headers (``host``, ``x-forwarded-host``, ``x-forwarded-proto``).
        scheme: Scheme the server received the request on.
        allowed_origins: Explicitly allowed origins (the app's CORS allowlist).
        allowed_origin_regex: Additional allowed origins, matched in full.
    """
    if origin in set(allowed_origins):
        return True
    if allowed_origin_regex and re.fullmatch(allowed_origin_regex, origin):
        return True
    claimed = _origin_host(origin)
    if claimed is None:
        return False
    proto = (headers.get("x-forwarded-proto") or scheme or "http").split(",", 1)[0].strip().lower()
    for name in ("x-forwarded-host", "host"):
        value = headers.get(name)
        if value:
            own = _host_header(value, proto)
            if own is not None and own == claimed:
                return True
    return False


def browser_origin_refusal(method: str, headers: Mapping[str, str], *, scheme: str = "http",
                           allowed_origins: Iterable[str] = (),
                           allowed_origin_regex: Optional[str] = None) -> Optional[str]:
    """Return why a request is refused as cross-site, or ``None`` when it may proceed."""
    if method.upper() not in STATE_CHANGING_METHODS:
        return None
    fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site == "cross-site":
        return "cross-site browser request"
    origin = headers.get("origin")
    if origin is None:
        return None
    if not origin_allowed(origin, headers, scheme=scheme, allowed_origins=allowed_origins,
                          allowed_origin_regex=allowed_origin_regex):
        return f"origin {origin!r} is not allowed"
    return None


def refusal_response(reason: str) -> JSONResponse:
    """The 403 body of a refused cross-site request (no route code ran)."""
    return JSONResponse({"code": CROSS_SITE_CODE, "message": f"cross-site request rejected: {reason}"},
                        status_code=403)


def check_browser_origin(request: Any, *, allowed_origins: Iterable[str] = (),
                         allowed_origin_regex: Optional[str] = None) -> Optional[JSONResponse]:
    """Return a 403 response for a cross-site state-changing ``request``, else ``None``."""
    headers = {k.lower(): v for k, v in request.headers.items()}
    reason = browser_origin_refusal(request.method, headers, scheme=request.url.scheme,
                                    allowed_origins=allowed_origins, allowed_origin_regex=allowed_origin_regex)
    if reason is None:
        return None
    logger.warning("refused %s %s: %s", request.method, request.url.path, reason)
    return refusal_response(reason)


class OriginGateMiddleware:
    """Refuse cross-site state-changing requests under ``path_prefixes`` before any route runs.

    Args:
        app: The wrapped ASGI app.
        allowed_origins: Origins allowed besides the request's own host (the CORS allowlist).
        path_prefixes: Guarded path prefixes. A gate whose prefixes do not match its routes
            protects nothing, so they are explicit.
        allowed_origin_regex: Additional allowed origins, matched in full.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str] = (),
                 path_prefixes: Sequence[str] = ("/v1/",), allowed_origin_regex: Optional[str] = None) -> None:
        """Bind the app and freeze the allowlist and guarded prefixes."""
        self.app = app
        self._allowed = frozenset(allowed_origins)
        self._prefixes = tuple(path_prefixes)
        self._regex = allowed_origin_regex

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Refuse a state-changing HTTP request to a guarded path whose browser origin is not allowed.

        Other requests, and allowed ones, go to the wrapped app unchanged.
        """
        if (scope["type"] != "http" or scope["method"] not in STATE_CHANGING_METHODS
                or not scope["path"].startswith(self._prefixes)):
            await self.app(scope, receive, send)
            return
        headers = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope["headers"]}
        reason = browser_origin_refusal(scope["method"], headers, scheme=scope.get("scheme", "http"),
                                        allowed_origins=self._allowed, allowed_origin_regex=self._regex)
        if reason is not None:
            logger.warning("origin gate refused %s %s: %s", scope["method"], scope["path"], reason)
            await refusal_response(reason)(scope, receive, send)
            return
        await self.app(scope, receive, send)


__all__ = ("CROSS_SITE_CODE", "OriginGateMiddleware", "STATE_CHANGING_METHODS", "browser_origin_refusal",
           "check_browser_origin", "origin_allowed", "refusal_response")
