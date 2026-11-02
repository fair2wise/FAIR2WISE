"""Request-body and upload size limits, PDF type check and safe storage paths (I-05).

Starlette's multipart parser drains the whole body into a spooled temporary file before a
route runs, so a size check inside the route is too late to bound that buffering.
:class:`MaxBodySizeMiddleware` counts bytes on the ASGI ``receive`` channel and answers 413
while they are still arriving, whether or not the client sent ``Content-Length``.
:func:`read_upload_limited` is the per-route backstop; :func:`require_pdf` and
:func:`safe_child` validate an upload before it is stored.

Unlike the donor, nothing here reads the environment: the cap is a constructor argument
chosen by trusted startup wiring (:func:`app.api.main.create_api_app`).

Adapted from als-computing/forge_rsoxs_analysis src/forge/api/upload_limits.py
(``read_upload_limited``, ``MaxBodySizeMiddleware``) (BSD-3-Clause, Copyright (c) 2026, The
Regents of the University of California, through Lawrence Berkeley National Laboratory),
adding path prefixes, the PDF check and the storage-path check.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Default cap for one request body, in bytes (100 MiB).
DEFAULT_MAX_BODY_BYTES = 100 * 1024 * 1024
#: Cap on the legacy UI routes served by ``create_app`` (AUD-04): 128 MiB, the gateway's
#: ``client_max_body_size 128m`` (``docker/nginx.conf``), so a directly exposed API refuses the
#: same bodies nginx would.
LEGACY_MAX_BODY_BYTES = 128 * 1024 * 1024
_CHUNK_BYTES = 1 << 20
#: Stable error code of an oversized body (413).
TOO_LARGE_CODE = "payload_too_large"


def _too_large(limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail=f"request body exceeds the {limit} byte limit")


async def read_upload_limited(file: Any, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> bytes:
    """Read an ``UploadFile`` completely, refusing once it exceeds ``max_bytes``.

    The multipart-reported ``file.size`` is client-controlled, so the chunked read enforces
    the same cap.

    Raises:
        fastapi.HTTPException: 413 when the upload is larger than ``max_bytes``.
    """
    size = getattr(file, "size", None)
    if size is not None and size > max_bytes:
        raise _too_large(max_bytes)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise _too_large(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def require_pdf(data: bytes) -> bytes:
    """Return ``data`` if it starts like a PDF file (``%PDF-``), else raise 415."""
    if not data[:1024].lstrip(b"\x00\t\r\n ").startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="upload is not a PDF file")
    return data


def safe_child(root: Path, name: str) -> Path:
    """Return ``root/name`` for a plain file name, refusing separators, ``..`` and escapes.

    Raises:
        fastapi.HTTPException: 422 for an unsafe name.
    """
    if not name or name in (".", "..") or any(c in name for c in "/\\\x00") or name.startswith("."):
        raise HTTPException(status_code=422, detail="unsafe file name")
    base = Path(root).resolve()
    target = (base / name).resolve()
    if target.parent != base:
        raise HTTPException(status_code=422, detail="unsafe file name")
    return target


class MaxBodySizeMiddleware:
    """Cap the size of request bodies under ``path_prefixes`` (all paths when empty).

    Args:
        app: The wrapped ASGI app.
        max_bytes: Largest accepted body.
        path_prefixes: Paths to cap; an empty sequence caps every path.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_BODY_BYTES,
                 path_prefixes: Sequence[str] = ()) -> None:
        """Bind the app and the limit.

        Raises:
            ValueError: If ``max_bytes`` is not positive.
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes
        self._prefixes = tuple(path_prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer 413 when the declared or streamed body exceeds the limit; otherwise call the wrapped app.

        Only HTTP requests under ``path_prefixes`` are checked.
        """
        if scope["type"] != "http" or (self._prefixes and not scope["path"].startswith(self._prefixes)):
            await self.app(scope, receive, send)
            return
        limit = self.max_bytes
        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    declared: int | None = int(value)
                except ValueError:
                    declared = None
                if declared is not None and declared > limit:
                    await self._refuse(scope, receive, send, limit)
                    return
        total = 0

        async def limited_receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise _too_large(limit)
            return message

        try:
            await self.app(scope, limited_receive, send)
        except HTTPException as exc:
            if exc.status_code != 413:
                raise
            await self._refuse(scope, receive, send, limit)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        body = {"code": TOO_LARGE_CODE, "message": f"request body exceeds the {limit} byte limit"}
        response = JSONResponse(body, status_code=413)
        await response(scope, receive, send)


__all__ = ("DEFAULT_MAX_BODY_BYTES", "LEGACY_MAX_BODY_BYTES", "MaxBodySizeMiddleware", "TOO_LARGE_CODE",
           "read_upload_limited",
           "require_pdf", "safe_child")
