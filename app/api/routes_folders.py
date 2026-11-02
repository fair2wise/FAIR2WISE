"""``/v1/folders`` routes: browse and index over fixed roots (policies V39-V41; task T32, owner decision B4).

Mounted by ``create_api_app`` on the ``:local`` profile only; an ALS profile never registers
them, so they answer a plain 404 there (``app.api.folders``). A request names a configured
root *label* and a relative path; every path check and every refusal (a fixed 404 that names
no path) is in :class:`~app.api.folders.FolderRoots`.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import Depends, FastAPI, Query, Request

from ..modules.contracts.context import RequestContext
from .folders import MAX_INDEX_DOCUMENTS, MAX_LIST_ENTRIES
from .routes_common import Prefixed, context, operations, project


def install(app: FastAPI) -> FastAPI:
    """Mount the folder routes on ``app`` (the factory calls this on ``:local`` only)."""
    router = Prefixed(app, "/v1/folders")

    @router.get("/roots")
    def roots(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """The configured root labels (never their paths)."""
        return cast(dict[str, Any], project(request, operations(request).folder_roots(ctx)))

    @router.get("/list")
    def list_folder(request: Request, ctx: RequestContext = Depends(context),
                    root: str = Query(min_length=1, max_length=64),
                    path: str = Query(default="", max_length=4096),
                    limit: int = Query(default=MAX_LIST_ENTRIES, ge=1, le=MAX_LIST_ENTRIES)) -> dict[str, Any]:
        """Entries of one folder under ``root`` (links named, never followed)."""
        return cast(dict[str, Any], project(request, operations(request).list_folder(ctx, root, path, limit=limit)))

    @router.get("/index")
    def index_folder(request: Request, ctx: RequestContext = Depends(context),
                     root: str = Query(min_length=1, max_length=64),
                     path: str = Query(default="", max_length=4096),
                     limit: int = Query(default=500, ge=1, le=MAX_INDEX_DOCUMENTS)) -> dict[str, Any]:
        """Bounded inventory of the documents under one folder (what an ingest job would index)."""
        return cast(dict[str, Any], project(request, operations(request).folder_index(ctx, root, path, limit=limit)))

    return app


__all__ = ("install",)
