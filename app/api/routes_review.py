"""``/v1/curation`` routes over ``review_case`` and cost approvals (policies V10-V15).

Case lists carry no evidence text; a single case carries a bounded evidence excerpt
(``EVIDENCE_EXCERPT_CHARS``), never the stored chunk in full (D19: no reconstructable
source-text export). A decision binds to the case ``revision`` the reviewer was shown.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..modules.contracts.context import RequestContext
from .routes_common import Prefixed, context, dump, operations, project

#: Characters of stored evidence shown with one case (reviewers see the cited quote and context).
EVIDENCE_EXCERPT_CHARS = 600
#: Case fields a list shows (no evidence, payload or model responses).
CASE_SUMMARY_FIELDS = ("id", "corpus", "publication_id", "case_type", "reason", "state", "route", "priority",
                       "revision", "attempts", "needs_validation", "resolved_by", "created", "updated")


class ReviewRequest(BaseModel):
    """Body of ``POST /v1/curation/cases/{case_id}/review``."""

    model_config = ConfigDict(extra="forbid")

    bound_revision: str = Field(min_length=1, max_length=200)
    accept: bool
    resolution: dict[str, Any] | None = None
    reason: str = Field(default="", max_length=2000)


class CostApprovalRequest(BaseModel):
    """Body of ``POST /v1/curation/cost-approvals``."""

    model_config = ConfigDict(extra="forbid")

    scope: list[str] = Field(min_length=1, max_length=100)
    models: list[str] = Field(min_length=1, max_length=50)
    cap_usd: float = Field(gt=0)
    expires_at: datetime


def _summary(case: dict[str, Any]) -> dict[str, Any]:
    return {k: case.get(k) for k in CASE_SUMMARY_FIELDS}


def _excerpt(case: dict[str, Any]) -> dict[str, Any]:
    out = dict(case)
    evidence = str(out.get("evidence") or "")
    out["evidence"] = evidence[:EVIDENCE_EXCERPT_CHARS]
    out["evidence_truncated"] = len(evidence) > EVIDENCE_EXCERPT_CHARS
    for key in ("lease_token", "owner"):
        out.pop(key, None)
    return out


def install(app: FastAPI) -> FastAPI:
    """Mount the curation review routes on ``app``."""
    router = Prefixed(app, "/v1/curation")

    @router.get("/summary")
    def summary(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Review queue counts by state and route, and the remote-cost ledger summary."""
        return cast(dict[str, Any], project(request, operations(request).curation_summary(ctx)))

    @router.get("/cases")
    def list_cases(request: Request, state: str | None = Query(default=None, max_length=40),
                   route: str | None = Query(default=None, max_length=40),
                   limit: int = Query(default=100, ge=1, le=1000),
                   ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Case summaries within the caller's corpus scope."""
        cases = operations(request).list_cases(ctx, state=state, route=route, limit=limit)
        return cast(dict[str, Any], project(request, {"cases": [_summary(c) for c in cases]}))

    @router.get("/cases/{case_id}")
    def get_case(request: Request, case_id: str, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """One case, its attempts and history."""
        found = operations(request).get_case(ctx, case_id)
        return cast(dict[str, Any], project(request, {**found, "case": _excerpt(found["case"])}))

    @router.post("/cases/{case_id}/review")
    def review(request: Request, case_id: str, body: ReviewRequest,
               ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Accept or reject the case at the revision the reviewer saw (stale revision: 409)."""
        result = operations(request).review_case(ctx, case_id, bound_revision=body.bound_revision,
                                                 accept=body.accept, resolution=body.resolution,
                                                 reason=body.reason)
        return cast(dict[str, Any], project(request, {"case": _excerpt(result)}))

    @router.post("/cost-approvals")
    def create_cost_approval(request: Request, body: CostApprovalRequest,
                             ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Record a human's remote-cost approval."""
        approval = operations(request).create_cost_approval(ctx, scope=body.scope, models=body.models,
                                                            cap_usd=body.cap_usd, expires_at=body.expires_at)
        return cast(dict[str, Any], project(request, dump(approval)))

    @router.post("/cost-approvals/{approval_id}/revoke")
    def revoke_cost_approval(request: Request, approval_id: str,
                             ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Revoke a cost approval; later reservations under it are refused."""
        return cast(dict[str, Any], project(request, operations(request).revoke_cost_approval(ctx, approval_id)))

    return app


__all__ = ("CASE_SUMMARY_FIELDS", "CostApprovalRequest", "EVIDENCE_EXCERPT_CHARS", "ReviewRequest", "install")
