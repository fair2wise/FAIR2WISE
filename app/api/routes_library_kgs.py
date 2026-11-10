"""``/v1/library`` KG bundle routes (policies V70-V75; task T38, :local only).

``GET kgs|schemas|models``, ``POST estimate``, ``POST kgs/{name}/export`` and ``POST kgs/continue``.
Mounted by ``create_api_app`` on the ``:local`` profile only, so an ALS profile answers 404. The model
list is read-only over the live catalog: nothing here calls or changes ``PUT /settings``. All logic is in
:class:`app.modules.library.kg_service.KgLibrary`.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..modules.contracts.context import RequestContext
from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.library.kg_service import MAX_PAPER_IDS, KgLibrary
from ..modules.library.publish import LibraryPublisher
from ..modules.security.authorization import require
from .routes_common import Prefixed, context, project


class EstimateBody(BaseModel):
    """Body of ``POST /v1/library/estimate`` (``EstimateRequest`` of ``extractionApi.ts``)."""

    model_config = ConfigDict(extra="forbid")

    paper_ids: list[str] = Field(min_length=1, max_length=MAX_PAPER_IDS)
    model: str = Field(min_length=1, max_length=200)
    schema_: str = Field(alias="schema", min_length=1, max_length=120)


class ExportBody(BaseModel):
    """Body of ``POST /v1/library/kgs/{name}/export``: the explicit confirmation of a write to the release directory."""

    model_config = ConfigDict(extra="forbid")

    confirm: bool = False


class PublishBody(BaseModel):
    """Body of ``POST /v1/library/kgs/{name}/publish``: ``confirm`` false is a preview, ``again`` forces a new version."""

    model_config = ConfigDict(extra="forbid")

    confirm: bool = False
    again: bool = False


class ContinueBody(BaseModel):
    """Body of ``POST /v1/library/kgs/continue``: give ``source`` (a KG file) or ``parent`` (a bundle)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    source: str | None = Field(default=None, max_length=256)
    parent: str | None = Field(default=None, max_length=64)
    schema_: str | None = Field(default=None, alias="schema", max_length=120)
    topic: str | None = Field(default=None, max_length=200)


def kg_library(request: Request) -> KgLibrary:
    """The app's KG library service, or fail closed."""
    service = getattr(request.app.state, "f2w_kg_library", None)
    if not isinstance(service, KgLibrary):
        raise ContractError(ErrorCode.INTERNAL, "the KG library is not installed")
    return service


def install(app: FastAPI, service: KgLibrary) -> FastAPI:
    """Mount the routes on ``app`` (``:local`` only) and keep ``service`` in ``app.state.f2w_kg_library``."""
    app.state.f2w_kg_library = service
    router = Prefixed(app, "/v1/library")

    @router.get("/kgs")
    def list_kgs(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """KG bundles plus the plain KG files that can be continued."""
        return project(request, kg_library(request).kgs())  # type: ignore[no-any-return]

    @router.get("/schemas")
    def list_schemas(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Extraction schema choices (registry v0.3.x)."""
        return project(request, kg_library(request).schemas())  # type: ignore[no-any-return]

    @router.get("/models")
    def list_models(request: Request, refresh: bool = Query(default=False),
                    ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Live model list for extraction; ``available=false`` when no backend answers."""
        return project(request, kg_library(request).models(refresh=refresh))  # type: ignore[no-any-return]

    @router.post("/estimate")
    def estimate(request: Request, body: EstimateBody, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Cost estimate for the chosen papers, model and schema, with a confirmation token."""
        return project(request, kg_library(request).estimate(  # type: ignore[no-any-return]
            body.paper_ids, body.model, body.schema_))

    @router.post("/kgs/continue")
    def continue_kg(request: Request, body: ContinueBody, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Create a bundle by continuing a bundle or a plain KG file (reverse build)."""
        return project(request, kg_library(request).continue_kg(  # type: ignore[no-any-return]
            body.name, source=body.source, parent=body.parent, schema=body.schema_, topic=body.topic))

    @router.post("/kgs/{name}/export")
    def export_kg(name: str, body: ExportBody, request: Request,
                  ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Export a bundle to the configured release directory as ``matkg_<name>.json`` (needs ``confirm: true``)."""
        if body.confirm is not True:
            raise ContractError(ErrorCode.INVALID_INPUT, "exporting writes to the release directory; confirm is required")
        return project(request, kg_library(request).export(name))  # type: ignore[no-any-return]

    @router.post("/kgs/{name}/publish")
    def publish_kg(name: str, body: PublishBody, request: Request,
                   ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Publish to Tiled: preview (``confirm`` false) or submit the ``library_publish`` job; never approves anything."""
        publisher = getattr(request.app.state, "f2w_library_publisher", None)
        if not isinstance(publisher, LibraryPublisher):
            raise ContractError(ErrorCode.INTERNAL, "the publisher is not installed")
        require(ctx, "library.extract")
        if body.confirm is not True:
            detail = publisher.preview(name, again=body.again)
            detail.pop("_problems", None)
            detail.pop("_inputs", None)
            return project(request, detail)  # type: ignore[no-any-return]
        return project(request, publisher.submit(ctx, name, again=body.again))  # type: ignore[no-any-return]

    return app


__all__ = ("ContinueBody", "EstimateBody", "ExportBody", "PublishBody", "install", "kg_library")
