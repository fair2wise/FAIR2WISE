"""The API process as its own front door: ``F2W_GATEWAY=off`` of the ``cluster`` image.

The nginx gateway (``docker/nginx.conf``) does four things: it serves the built UI and the MkDocs site,
removes the ``/fair2wise/api`` prefix once, forwards ``/fair2wise/model-api`` as ``/model-api`` and
answers ``/healthz``. Behind a Kubernetes ingress (Traefik, oauth2-proxy) that already terminates TLS and
authentication, a second proxy in the pod only adds a process, temp paths and a hop. :class:`FrontApp`
reproduces the same routing table in front of the API application, so one uvicorn process can be the
container's only listener:

=================================  ==========================================================
``/``                              302 to ``<prefix>/``
``<prefix>``                       301 to ``<prefix>/`` (query kept)
``/healthz``, ``<prefix>/healthz`` ``200 ok`` (process answers; the same text as the gateway)
``<prefix>/api/<path>``            the API with the prefix removed once and ``root_path=<prefix>/api``
``<prefix>/model-api/<path>``      the API at ``/model-api/<path>`` (``root_path`` as above)
``<prefix>/docs/...``              the MkDocs site (``index.html`` for a directory)
``<prefix>/assets/...``            built assets, long cache; a missing one is 404, never HTML
``<prefix>/<file.ext>``            a built file, else 404
other ``<prefix>/...``             the UI entry document (SPA fallback), never cached
anything else                      404
=================================  ==========================================================

Differences from nginx that matter (documented in ``docs/cluster-profile.md``): no request buffering
or ``client_max_body_size`` (the API's own 128 MiB cap applies), no HTTP/2, and uvicorn believes
``X-Forwarded-*`` only from ``FORWARDED_ALLOW_IPS`` (the ingress must be listed there). As the gateway
does, this front blanks ``X-Forwarded-Host`` and ``X-Forwarded-Prefix`` and passes ``X-Forwarded-Proto``
only when it is literally ``http`` or ``https`` (else the connection's scheme), because the origin gate
reads that header (``app/api/origin_gate.py``). The edge must still overwrite ``X-Forwarded-Proto``.

Importing this module has no side effects.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .gateway import PublicLayout

#: Where the images keep the built UI (``<root>/fair2wise/``) and docs (``<root>/fair2wise/docs/``).
DEFAULT_UI_ROOT = Path("/usr/share/nginx/html")
#: Set to ``1`` by ``docker/entrypoint/api_service.py --front``: ``run_api`` wraps the app in :class:`FrontApp`.
FRONT_ENV = "F2W_API_FRONT"
#: Optional override of :data:`DEFAULT_UI_ROOT` (tests, other images).
UI_ROOT_ENV = "F2W_UI_ROOT"

_NO_CACHE = "no-cache"
_IMMUTABLE = "public, max-age=31536000, immutable"
_STRIPPED_HEADERS = frozenset({b"x-forwarded-host", b"x-forwarded-prefix", b"x-forwarded-proto"})


def front_enabled(environ: Mapping[str, str]) -> bool:
    """Whether the process is asked to serve its own front (``F2W_API_FRONT=1``)."""
    return str(environ.get(FRONT_ENV, "")).strip() == "1"


def ui_root_from_env(environ: Mapping[str, str]) -> Path:
    """The directory holding ``<prefix>/`` (default :data:`DEFAULT_UI_ROOT`)."""
    return Path(environ.get(UI_ROOT_ENV) or DEFAULT_UI_ROOT)


def _has_extension(path: str) -> bool:
    """Whether the last segment looks like a file name (an extension of 1 to 8 letters or digits, as in nginx.conf)."""
    name = path.rsplit("/", 1)[-1]
    stem, dot, ext = name.rpartition(".")
    return bool(dot and stem and 1 <= len(ext) <= 8 and ext.isalnum() and ext.isascii())


class FrontApp:
    """ASGI front door in front of the API application (see the module docstring).

    Attributes:
        api: The wrapped API application (receives lifespan events and the API routes).
        layout: The public layout (prefix) the UI bundle was built for.
        ui_root: Directory that holds ``<prefix>/`` (UI build) and ``<prefix>/docs/``.
    """

    def __init__(self, api: ASGIApp, layout: PublicLayout, ui_root: Path) -> None:
        """Wrap ``api`` for ``layout``.

        Raises:
            ValueError: When ``layout`` has no prefix (the front always serves under one, like the gateway).
        """
        if not layout.prefixed:
            raise ValueError("the front serves under a prefix; set F2W_PUBLIC_PREFIX")
        self.api = api
        self.layout = layout
        self.ui_root = Path(ui_root)
        self._real_root = os.path.realpath(self.ui_root)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Route one connection: API forwarding, static files, redirects or 404."""
        if scope["type"] not in ("http", "websocket"):
            await self.api(scope, receive, send)  # lifespan
            return
        path: str = scope["path"]
        prefix = self.layout.prefix
        target = self._api_target(path)
        if target is not None:
            await self.api(self._api_scope(scope, target), receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = self._static_or_redirect(scope, path, prefix)
        await response(scope, receive, send)

    # -- API forwarding --------------------------------------------------------------------------
    def _api_target(self, path: str) -> str | None:
        """The path the API sees for ``path``, or ``None`` when the front answers itself."""
        prefix = self.layout.prefix
        api, model = f"{prefix}/api/", f"{prefix}/model-api/"
        if path.startswith(f"{prefix}/api/model-api"):
            return None  # one public path for the model API (B7): 404 below
        if path.startswith(api):
            return path[len(prefix) + len("/api"):]
        if path.startswith(model):
            return path[len(prefix):]
        return None

    def _api_scope(self, scope: Scope, target: str) -> Scope:
        """Copy of ``scope`` with the prefix removed once and the gateway's header hygiene applied."""
        removed = scope["path"][:len(scope["path"]) - len(target)].encode("utf-8")
        headers = [(k, v) for k, v in scope.get("headers", []) if k.lower() not in _STRIPPED_HEADERS]
        original = {k.lower(): v for k, v in scope.get("headers", [])}.get(b"x-forwarded-proto", b"").strip().lower()
        proto = original if original in (b"http", b"https") else str(scope.get("scheme", "http")).encode("ascii")
        headers.append((b"x-forwarded-proto", proto))
        new = dict(scope, path=target, root_path=self.layout.api_root_path, headers=headers)
        raw = scope.get("raw_path")
        if raw is not None:  # the prefix is plain ASCII; an encoded spelling of it falls back to the decoded target
            new["raw_path"] = raw[len(removed):] if raw.startswith(removed) else quote(target).encode("ascii")
        return new

    # -- everything the front answers itself --------------------------------------------------------
    def _static_or_redirect(self, scope: Scope, path: str, prefix: str) -> Response:
        method = scope["method"]
        query = scope.get("query_string", b"").decode("latin-1")
        suffix = f"?{query}" if query else ""
        if path in ("/healthz", f"{prefix}/healthz"):
            return self._plain(PlainTextResponse("ok\n"))
        if path == "/":
            return self._plain(RedirectResponse(f"{prefix}/", status_code=302))
        if path == prefix:
            return self._plain(RedirectResponse(f"{prefix}/{suffix}", status_code=301))
        if path in (f"{prefix}/api", f"{prefix}/model-api") or path.startswith(f"{prefix}/api/model-api"):
            return self._plain(PlainTextResponse("not found\n", status_code=404))
        if path == f"{prefix}/docs":
            return self._plain(RedirectResponse(f"{prefix}/docs/{suffix}", status_code=301))
        if not path.startswith(f"{prefix}/"):
            return self._plain(PlainTextResponse("not found\n", status_code=404))
        if method not in ("GET", "HEAD"):
            return self._plain(PlainTextResponse("method not allowed\n", status_code=405,
                                                 headers={"Allow": "GET, HEAD"}))
        if path.startswith(f"{prefix}/docs/"):
            return self._docs(path, suffix, method)
        found = self._file(path)
        if path.startswith(f"{prefix}/assets/"):
            return self._served(found, method, _IMMUTABLE)
        if path == f"{prefix}/index.html":
            return self._served(found, method, _NO_CACHE)
        if _has_extension(path):
            return self._served(found, method, None)
        if found is not None:
            return self._served(found, method, None)
        return self._served(self._file(f"{prefix}/index.html"), method, _NO_CACHE)  # SPA deep link

    def _docs(self, path: str, suffix: str, method: str) -> Response:
        found = self._file(path, allow_dir=True)
        if found is not None and found.is_dir():
            if not path.endswith("/"):
                return self._plain(RedirectResponse(f"{quote(path)}/{suffix}", status_code=301))
            found = self._file(path + "index.html")
        return self._served(found, method, None)

    def _file(self, path: str, *, allow_dir: bool = False) -> Path | None:
        """The file below :attr:`ui_root` for a URL path, or ``None`` (traversal, links out, missing)."""
        if "\0" in path or "\\" in path or any(part == ".." for part in path.split("/")):
            return None
        candidate = self.ui_root / path.lstrip("/")
        resolved = os.path.realpath(candidate)
        if resolved != self._real_root and not resolved.startswith(self._real_root + os.sep):
            return None
        target = Path(resolved)
        if target.is_file() or (allow_dir and target.is_dir()):
            return target
        return None

    def _served(self, found: Path | None, method: str, cache: str | None) -> Response:
        if found is None:
            return self._plain(PlainTextResponse("not found\n", status_code=404))
        headers: dict[str, Any] = {"Cache-Control": cache} if cache else {}
        return self._plain(FileResponse(found, headers=headers))

    @staticmethod
    def _plain(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


__all__ = ("DEFAULT_UI_ROOT", "FRONT_ENV", "FrontApp", "UI_ROOT_ENV", "front_enabled", "ui_root_from_env")
