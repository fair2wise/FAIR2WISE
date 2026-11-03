"""Minimal ``multipart/form-data`` reader for single-file uploads (T35; no ``python-multipart`` dependency).

FastAPI's ``UploadFile``/``Form`` need the ``python-multipart`` package, which the pinned environment
(``pixi.toml``) does not carry. The library upload takes exactly one file field, so this reads the
request body (already bounded: :class:`~app.api.upload_limits.MaxBodySizeMiddleware` plus the explicit cap
below) and extracts that field. Strictly: a boundary is required, parts need ``Content-Disposition:
form-data``, the part must carry a ``filename``, and nothing is decoded or interpreted beyond that.
"""

from __future__ import annotations

from email.message import Message
from email.parser import BytesParser
from email import policy

from fastapi import HTTPException, Request

#: Bytes allowed for multipart framing (boundaries and headers) on top of the file cap.
FRAMING_BYTES = 64 * 1024
_MAX_HEADER_BYTES = 8 * 1024


class PartFile:
    """An ``UploadFile``-shaped view of bytes already read (``size`` and ``read``), for ``read_upload_limited``."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self.size = len(data)

    async def read(self, n: int = -1) -> bytes:
        """Return the next ``n`` bytes (all the rest when negative)."""
        end = len(self._data) if n < 0 else self._pos + n
        chunk = self._data[self._pos:end]
        self._pos += len(chunk)
        return chunk


def _bad(detail: str = "malformed multipart/form-data body") -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def boundary_of(content_type: str | None) -> bytes:
    """The multipart boundary of a ``Content-Type`` value.

    Raises:
        HTTPException: 415 when it is not ``multipart/form-data``, 422 when the boundary is missing or unusable.
    """
    msg = Message()
    msg["content-type"] = content_type or ""
    if msg.get_content_type() != "multipart/form-data":
        raise HTTPException(status_code=415, detail="multipart/form-data is required")
    boundary = msg.get_param("boundary")
    if not isinstance(boundary, str) or not boundary or len(boundary) > 200 or "\r" in boundary or "\n" in boundary:
        raise _bad("multipart boundary is missing")
    return boundary.encode("latin-1", "ignore")


def extract_file(body: bytes, boundary: bytes, field: str) -> tuple[str | None, bytes]:
    """The ``(filename, bytes)`` of the part named ``field``.

    Raises:
        HTTPException: 422 when the body is malformed or has no such file part.
    """
    delimiter = b"--" + boundary
    pos = body.find(delimiter)
    if pos < 0:
        raise _bad()
    while True:
        pos += len(delimiter)
        if body[pos:pos + 2] == b"--":
            break  # closing delimiter
        if body[pos:pos + 2] != b"\r\n":
            raise _bad()
        start = pos + 2
        split = body.find(b"\r\n\r\n", start)
        if split < 0 or split - start > _MAX_HEADER_BYTES:
            raise _bad()
        nxt = body.find(b"\r\n" + delimiter, split + 4)
        if nxt < 0:
            raise _bad()
        headers = BytesParser(policy=policy.HTTP).parsebytes(body[start:split] + b"\r\n\r\n", headersonly=True)
        disposition = headers.get("content-disposition")
        if disposition is not None and headers.get_content_disposition() == "form-data" \
                and headers.get_param("name", header="content-disposition") == field:
            filename = headers.get_filename()
            if filename is None:
                raise _bad(f"field {field!r} must be a file")
            return str(filename), bytes(body[split + 4:nxt])
        pos = nxt + 2
    raise _bad(f"missing file field {field!r}")


async def read_single_file(request: Request, field: str, max_bytes: int) -> tuple[str | None, bytes]:
    """Read the request body (at most ``max_bytes`` of file plus framing) and return the ``field`` file part.

    Raises:
        HTTPException: 413 when the body exceeds the cap, 415 for a non-multipart type, 422 when malformed.
    """
    boundary = boundary_of(request.headers.get("content-type"))
    limit = max_bytes + FRAMING_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=f"request body exceeds the {max_bytes} byte limit")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail=f"request body exceeds the {max_bytes} byte limit")
        chunks.append(chunk)
    return extract_file(b"".join(chunks), boundary, field)


__all__ = ("FRAMING_BYTES", "PartFile", "boundary_of", "extract_file", "read_single_file")
