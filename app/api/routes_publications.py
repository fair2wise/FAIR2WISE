"""``/v1/publications`` routes over ``Publication`` (policies V16-V20). Real targets stay H5-gated.

The approval route takes the ``plan_sha256`` the reviewer was shown; the approval is created
server-side by ``security.authorization.create_approval`` with the caller's own credential
from the session store (a fresh role check on ALS), so no approval record is ever accepted
from a request body.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..modules.contracts.context import RequestContext
from .auth import session_credential
from .routes_common import Prefixed, context, dump, operations, project

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
    expires_at: Optional[datetime] = None


class RollbackRequest(BaseModel):
    """Body of ``POST /v1/publications/rollback``."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=300)


def install(app: FastAPI) -> FastAPI:
    """Mount the publication routes on ``app``."""
    router = Prefixed(app, "/v1/publications")

    @router.post("")
    def submit(request: Request, body: PublicationRequest,
               ctx: RequestContext = Depends(context)) -> Dict[str, Any]:
        """Submit a publication job (plans, then waits for review; nothing is written before approval)."""
        return project(request, dump(operations(request).submit_publication(ctx, body.snapshot_ref, body.target)))

    @router.get("/pointer")
    def pointer(request: Request, target: str = Query(min_length=1, max_length=1000),
                ctx: RequestContext = Depends(context)) -> Dict[str, Any]:
        """The active publication pointer of ``target`` (``pointer: null`` when nothing is active)."""
        return project(request, {"pointer": operations(request).publication_pointer(ctx, target)})

    @router.post("/rollback")
    def rollback(request: Request, body: RollbackRequest,
                 ctx: RequestContext = Depends(context)) -> Dict[str, Any]:
        """Restore the previous active pointer; candidates and history are kept."""
        restored = operations(request).rollback_publication(ctx, body.target, reason=body.reason)
        return project(request, {"pointer": restored})

    @router.get("/{job_id}/plan")
    def current_plan(request: Request, job_id: str, ctx: RequestContext = Depends(context)) -> Dict[str, Any]:
        """The plan a reviewer is asked to approve."""
        return project(request, dump(operations(request).current_plan(ctx, job_id)))

    @router.post("/{job_id}/approval")
    def approve(request: Request, job_id: str, body: ApprovalRequest,
                ctx: RequestContext = Depends(context)) -> Dict[str, Any]:
        """Approve exactly the plan with ``plan_sha256`` (another current plan: 409 ``stale_approval``)."""
        result = operations(request).approve_publication(ctx, job_id, body.plan_sha256,
                                                         approver_credential=session_credential(request),
                                                         expires_at=body.expires_at)
        return project(request, result)

    return app


__all__ = ("ApprovalRequest", "PublicationRequest", "RollbackRequest", "install")
