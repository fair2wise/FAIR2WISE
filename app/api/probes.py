"""Kubernetes liveness and readiness probes: ``GET /livez`` and ``GET /readyz``.

Both are answered by a pure ASGI middleware that sits outermost in :func:`app.api.main.create_api_app`,
before CORS, the origin gate, authentication and routing. They are therefore unauthenticated by design
(a kubelet carries no credentials), cannot be turned off by an access policy, and do not depend on any
application state. They show no path, setting, version, user or secret: only fixed check names and
``ok``/``fail``.

``/livez``
    The API process is up and its event loop answers. No dependency is called.

``/readyz``
    ``200`` only when every selected check passes, else ``503`` listing the failing names:

    ``state``
        the session/state directory (``cfg.workdir``) exists and is writable by this uid
        (``access(2)``: an ``EROFS`` mount or a wrong owner fails it).
    ``worker``
        the supervisor status file (``F2W_SUPERVISOR_STATUS``) lists the worker as ``running`` and its
        process exists. Outside the image supervisor (no status file) the check is skipped, not failed.
    ``tiled``
        the bundled, managed Tiled answers ``GET /healthz`` on loopback (``F2W_TILED_MODE=managed``;
        skipped otherwise: external Tiled is not this pod's dependency).

``F2W_READYZ_CHECKS`` (comma list, default ``state,worker,tiled``) selects the checks, for example
``state,worker`` to keep a pod ready while a failed Tiled leaves the JSON workflows serving.

Behind the nginx gateway or the built-in front the public paths are ``/fair2wise/api/livez`` and
``/fair2wise/api/readyz``; on the loopback API port (``127.0.0.1:8090``) they are ``/livez`` and ``/readyz``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

from starlette.types import ASGIApp, Receive, Scope, Send

LIVE_PATH = "/livez"
READY_PATH = "/readyz"
CHECKS_ENV = "F2W_READYZ_CHECKS"
STATUS_ENV = "F2W_SUPERVISOR_STATUS"
DEFAULT_CHECKS = ("state", "worker", "tiled")
_HEADERS = [(b"content-type", b"application/json"), (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff")]


def selected_checks(environ: Mapping[str, str]) -> tuple[str, ...]:
    """The checks named by :data:`CHECKS_ENV` (unknown names are kept and fail, never ignored)."""
    raw = (environ.get(CHECKS_ENV) or "").strip()
    return tuple(c.strip().lower() for c in raw.split(",") if c.strip()) if raw else DEFAULT_CHECKS


def check_state(workdir: Path) -> bool:
    """Whether ``workdir`` is a directory this process can create files in."""
    return workdir.is_dir() and os.access(workdir, os.W_OK | os.X_OK)


def check_worker(environ: Mapping[str, str]) -> bool | None:
    """Worker state from the supervisor status file; ``None`` (skipped) when there is no supervisor."""
    path = Path(environ.get(STATUS_ENV) or "/run/fair2wise/supervisor.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return False
    worker = (data.get("services") or {}).get("worker") if isinstance(data, dict) else None
    if not isinstance(worker, dict) or worker.get("state") != "running":
        return False
    pid = worker.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by another uid: still alive
        return True
    return True


def check_tiled(environ: Mapping[str, str], timeout: float = 1.5) -> bool | None:
    """Bundled Tiled ``/healthz`` on loopback (never through a proxy); ``None`` unless Tiled is managed here."""
    if (environ.get("F2W_TILED_MODE") or "").strip().lower() != "managed":
        return None
    port = (environ.get("F2W_TILED_PORT") or "8765").strip()
    if not port.isdigit():
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as response:
            return bool(200 <= response.status < 300)
    except (urllib.error.URLError, OSError, ValueError):
        return False


def readiness(environ: Mapping[str, str], workdir: Path | None) -> dict[str, str]:
    """Run the selected checks; ``{name: "ok" | "fail" | "skipped"}``."""
    out: dict[str, str] = {}
    for name in selected_checks(environ):
        result: bool | None
        if name == "state":
            result = True if workdir is None else check_state(workdir)
        elif name == "worker":
            result = check_worker(environ)
        elif name == "tiled":
            result = check_tiled(environ)
        else:
            result = False
        out[name] = "skipped" if result is None else ("ok" if result else "fail")
    return out


class ProbeMiddleware:
    """Answer ``/livez`` and ``/readyz`` before anything else (see the module docstring)."""

    def __init__(self, app: ASGIApp, *, workdir: Path | str | None = None,
                 environ: Mapping[str, str] | None = None,
                 check: Callable[[Mapping[str, str], Path | None], dict[str, str]] = readiness) -> None:
        """Wrap ``app``; ``workdir`` is the state directory the ``state`` check tests."""
        self.app = app
        self.workdir = Path(workdir) if workdir else None
        self.environ = environ
        self.check = check

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle a probe request, or pass the connection on untouched."""
        if scope["type"] != "http" or scope.get("method") not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        root = str(scope.get("root_path", ""))
        if root and path.startswith(root + "/"):
            path = path[len(root):]
        if path not in (LIVE_PATH, READY_PATH):
            await self.app(scope, receive, send)
            return
        status = 200
        body: dict[str, Any] = {"status": "live"}
        if path == READY_PATH:
            environ = os.environ if self.environ is None else self.environ
            checks: dict[str, str] = await asyncio.to_thread(self.check, environ, self.workdir)
            ready = all(v != "fail" for v in checks.values())
            status, body = (200 if ready else 503), {"status": "ready" if ready else "not_ready", "checks": checks}
        await self._send(scope, send, status, body)

    @staticmethod
    async def _send(scope: Scope, send: Send, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = [*_HEADERS, (b"content-length", str(len(payload)).encode("ascii"))]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else payload})


__all__ = ("CHECKS_ENV", "LIVE_PATH", "ProbeMiddleware", "READY_PATH", "readiness", "selected_checks")
