"""``/v1/audit/status`` and ``/v1/ops/status`` (policies V21, V22; O-07 monitor contract)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Request

from ..modules.contracts.context import RequestContext
from .routes_common import Prefixed, context, operations, project


def install(app: FastAPI) -> FastAPI:
    """Mount the status routes on ``app``."""
    router = Prefixed(app, "/v1")

    @router.get("/audit/status")
    def audit_status(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Audit-path health. ``degraded: true`` means critical mutations are refused right now."""
        return project(request, operations(request).audit_status(ctx))

    @router.get("/ops/status")
    def ops_status(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Monitoring DTO: jobs by state, review queue, audit and publication configuration."""
        return project(request, operations(request).ops_status(ctx))

    return app


__all__ = ("install",)
