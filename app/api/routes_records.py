"""``/v1/vocabulary/quality`` and ``/v1/figures/scales`` (policies V35-V38; task T31, owner decision B5).

Thin handlers over ``F2WOperations``: the vocabulary-quality snapshot history (counts only)
and the human-read figure-scale records (identifiers and numbers only: no image, crop, path
or source text). Reads need ``chat.read_support``; recording a scale is a human action under
``curation.approve`` with a fresh ALS role check.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..modules.contracts.context import RequestContext
from ..modules.curation.scale_records import MAX_NOTE_CHARS
from .routes_common import Prefixed, context, fresh_context, operations, project


class ScaleRecordRequest(BaseModel):
    """Body of ``POST /v1/figures/scales``: one panel's human-read scale."""

    model_config = ConfigDict(extra="forbid")

    corpus: str = Field(min_length=1, max_length=128)
    work_id: str = Field(min_length=1, max_length=300)
    figure_id: str = Field(min_length=1, max_length=128)
    panel_index: int = Field(ge=0, le=1000)
    nm_per_px: float = Field(gt=0, allow_inf_nan=False)
    bar_length_px: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    bar_length_nm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    length_source: str = Field(default="human", max_length=32)
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)


def install(app: FastAPI) -> FastAPI:
    """Mount the vocabulary-quality and figure-scale routes on ``app``."""
    router = Prefixed(app, "/v1")

    @router.get("/vocabulary/quality")
    def vocabulary_quality(request: Request, ctx: RequestContext = Depends(context),
                           limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
        """Newest-first vocabulary-quality snapshots (candidate and canonical counts kept apart)."""
        return cast(dict[str, Any], project(request, operations(request).vocabulary_quality(ctx, limit=limit)))

    @router.get("/figures/scales")
    def list_scales(request: Request, ctx: RequestContext = Depends(context),
                    corpus: str | None = Query(default=None, min_length=1, max_length=128),
                    work_id: str | None = Query(default=None, min_length=1, max_length=300),
                    figure_id: str | None = Query(default=None, min_length=1, max_length=128),
                    limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
        """Human-read scale records within the caller's corpus scope."""
        records = operations(request).list_figure_scales(ctx, corpus=corpus, work_id=work_id, figure_id=figure_id,
                                                         limit=limit)
        return cast(dict[str, Any], project(request, {"records": records}))

    @router.post("/figures/scales")
    def record_scale(request: Request, body: ScaleRecordRequest,
                     ctx: RequestContext = Depends(fresh_context)) -> dict[str, Any]:
        """Record one human-read scale (human only; corpus in scope; audited)."""
        record = operations(request).record_figure_scale(
            ctx, corpus=body.corpus, work_id=body.work_id, figure_id=body.figure_id, panel_index=body.panel_index,
            nm_per_px=body.nm_per_px, bar_length_px=body.bar_length_px, bar_length_nm=body.bar_length_nm,
            length_source=body.length_source, note=body.note)
        return cast(dict[str, Any], project(request, {"record": record}))

    @router.get("/figures/scales/{record_id}")
    def get_scale(request: Request, record_id: str, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """One record; a record outside the caller's scope is 404."""
        return cast(dict[str, Any], project(request, {"record": operations(request).get_figure_scale(ctx, record_id)}))

    return app


__all__ = ("ScaleRecordRequest", "install")
