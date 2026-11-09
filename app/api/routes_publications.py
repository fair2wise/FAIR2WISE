"""``/v1/publications`` routes over ``Publication`` (policies V16-V20, V53-V55). Real targets stay H5-gated.

The approval route takes the ``plan_sha256`` the reviewer was shown; the approval is created
server-side by ``security.authorization.create_approval`` with the caller's own credential
from the session store (a fresh role check on ALS), so no approval record is ever accepted
from a request body.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..modules.contracts.context import RequestContext
from .auth import session_credential
from .routes_common import Prefixed, context, dump, operations, project_dict

_SHA256 = r"^[0-9a-f]{64}$"


class PublicationRequest(BaseModel):
    """Body of ``POST /v1/publications``: a frozen snapshot and a target."""

    model_config = ConfigDict(extra="forbid")

    snapshot_ref: str = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=1000)


class ApprovalRequest(BaseModel):
    """Body of ``POST /v1/publications/{job_id}/approval``."""

    model_config = ConfigDict(extra="forbid")

    plan_sha256: str = Field(pattern=_SHA256)
    expires_at: datetime | None = None


class RollbackRequest(BaseModel):
    """Body of ``POST /v1/publications/rollback``."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=300)
    #: Model C (owner decision 2026-11-09): roll back this corpus to *its* previous published version.
    corpus: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[^/]+$")


class CorpusRetireRequest(BaseModel):
    """Body of ``POST /v1/publications/corpus-retire``: the corpus name typed in, plus reviewed resolutions."""

    model_config = ConfigDict(extra="forbid")

    scope: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=1000)
    confirm_scope: str = Field(max_length=200)
    resolutions: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)


class CorpusPurgeRequest(BaseModel):
    """Body of ``POST /v1/publications/corpus-purge``."""

    model_config = ConfigDict(extra="forbid")

    scope: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=1000)
    include_previous: bool = False


def install(app: FastAPI) -> FastAPI:
    """Mount the publication routes on ``app``."""
    router = Prefixed(app, "/v1/publications")

    @router.post("")
    def submit(request: Request, body: PublicationRequest,
               ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Submit a publication job (plans, then waits for review; nothing is written before approval)."""
        return project_dict(request, dump(operations(request).submit_publication(ctx, body.snapshot_ref, body.target)))

    @router.get("/pointer")
    def pointer(request: Request, target: str = Query(min_length=1, max_length=1000),
                ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """The active publication pointer of ``target`` (``pointer: null`` when nothing is active).

        ``corpora`` maps each corpus live on the target to ``{snapshot_ref, version_of_pointer_that_activated_it,
        receipt_id}``; the pointer itself stays the last activated version of any corpus.
        """
        ops = operations(request)
        return project_dict(request, {"pointer": ops.publication_pointer(ctx, target),
                                      "corpora": ops.publication_corpora(ctx, target)})

    @router.post("/rollback")
    def rollback(request: Request, body: RollbackRequest,
                 ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Restore the previous active pointer (of ``corpus`` when named); candidates and history are kept."""
        restored = operations(request).rollback_publication(ctx, body.target, reason=body.reason,
                                                          corpus=body.corpus)
        return project_dict(request, {"pointer": restored})

    @router.post("/rollback-replacement")
    def rollback_replacement(request: Request, body: RollbackRequest,
                             ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Roll back (or forward) as a replacement plan to the previous version (of ``corpus`` when named); approve next."""
        return project_dict(request, dump(operations(request).rollback_publication_replacement(
            ctx, body.target, reason=body.reason, corpus=body.corpus)))

    @router.post("/corpus-retire")
    def corpus_retire(request: Request, body: CorpusRetireRequest,
                      ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Plan a corpus removal (the corpus name typed in); approve it through the approval route."""
        return project_dict(request, dump(operations(request).submit_corpus_retire(
            ctx, body.scope, body.target, confirm_scope=body.confirm_scope, resolutions=body.resolutions)))

    @router.post("/corpus-purge")
    def corpus_purge(request: Request, body: CorpusPurgeRequest,
                     ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Plan a purge of the corpus's retired records (separate approval; foreign links are never touched)."""
        return project_dict(request, dump(operations(request).submit_corpus_purge(
            ctx, body.scope, body.target, include_previous=body.include_previous)))

    @router.get("/{job_id}/plan")
    def current_plan(request: Request, job_id: str, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """The plan a reviewer is asked to approve."""
        return project_dict(request, dump(operations(request).current_plan(ctx, job_id)))

    @router.post("/{job_id}/approval")
    def approve(request: Request, job_id: str, body: ApprovalRequest,
                ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Approve exactly the plan with ``plan_sha256`` (another current plan: 409 ``stale_approval``)."""
        result = operations(request).approve_publication(ctx, job_id, body.plan_sha256,
                                                         approver_credential=session_credential(request),
                                                         expires_at=body.expires_at)
        return project_dict(request, result)

    return app


__all__ = ("ApprovalRequest", "CorpusPurgeRequest", "CorpusRetireRequest", "PublicationRequest", "RollbackRequest",
           "install")
