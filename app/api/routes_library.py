"""``/v1/library`` routes: catalog, upload and verify of the mounted papers folder (policies V56-V59; task T35, A8).

Mounted by ``create_api_app`` on the ``:local`` profile only (an ALS profile never registers them: a plain
404). The mount is ``F2W_LIBRARY_DIR`` (:class:`~app.modules.library.service.LibraryService`); a request
names a paper by an opaque id and never a path. No route returns PDF bytes, and no path matches
``SOURCE_SERVING_PATH_RE``. Verify-all and index are jobs (``POST /v1/jobs``, kind ``library_verify`` /
``library_index``), not routes.
"""

from __future__ import annotations

import re
from typing import Any, cast

from fastapi import Depends, FastAPI, Request
from starlette.concurrency import run_in_threadpool

from ..modules.contracts.context import RequestContext
from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.library.service import LibraryService
from .multipart_upload import PartFile, read_single_file
from .routes_common import Prefixed, context, project
from .upload_limits import read_upload_limited

_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def library_service(request: Request) -> LibraryService:
    """The app's library service, or fail closed."""
    service = getattr(request.app.state, "f2w_library", None)
    if not isinstance(service, LibraryService):
        raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, "the library is not installed")
    return service


def install(app: FastAPI) -> FastAPI:
    """Mount the library routes on ``app`` (the factory calls this on ``:local`` only)."""
    router = Prefixed(app, "/v1/library")

    @router.get("")
    def status(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Whether the papers folder is mounted, with headline counts and the OpenAlex quota state."""
        return cast(dict[str, Any], project(request, library_service(request).status()))

    @router.get("/papers")
    def papers(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """The papers of the mounted folder: name, title, DOI, verification and index status."""
        return cast(dict[str, Any], project(request, library_service(request).list_papers()))

    @router.post("/papers")
    async def upload(request: Request, folder: str | None = None,
                     ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Store one PDF (multipart field ``file``) in the mounted folder; a sha256 duplicate writes nothing.

        With the query parameter ``folder`` the paper is filed under that category (created when new) in a
        sub-folder of its publication year, and the category's ``manifest.json`` is updated.

        The multipart body is read by :mod:`app.api.multipart_upload` (the pinned environment has no
        ``python-multipart``); the size cap is enforced twice (body and :func:`read_upload_limited`).
        """
        service = library_service(request)
        filename, raw = await read_single_file(request, "file", service.max_upload_bytes)
        data = await read_upload_limited(PartFile(raw), service.max_upload_bytes)
        result = await run_in_threadpool(service.upload, filename, data, folder)
        return cast(dict[str, Any], project(request, result))

    @router.post("/papers/{paper_id}/verify")
    def verify(paper_id: str, request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Verify one paper's title and DOI against OpenAlex (cached by sha256; reports an open quota breaker)."""
        if not _ID_RE.match(paper_id):
            raise ContractError(ErrorCode.NOT_FOUND, "paper not found")
        return cast(dict[str, Any], project(request, library_service(request).verify_paper(paper_id, ctx)))

    return app


__all__ = ("install", "library_service")
