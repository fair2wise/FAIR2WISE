"""``GET /v1/approvals/pending`` (policy V82; owner instruction 2026-11-09, sixteenth set).

The Pending Approvals page of the UI reads this one aggregate. The handler only validates, resolves the
context and projects :meth:`app.api.operations.F2WOperations.pending_approvals`; approving or rejecting an
item goes through the existing routes each item names (V18, V08, V09, V13, V81), never through a route added
here. ``graph.publish`` is an admin capability, so on ALS the dependency re-runs the role check before the
handler and the service refuses a non-admin explicitly.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Request

from ..modules.contracts.context import RequestContext
from .routes_common import Prefixed, context, operations, project_dict


def install(app: FastAPI) -> FastAPI:
    """Mount the pending-approvals read on ``app``."""
    router = Prefixed(app, "/v1/approvals")

    @router.get("/pending")
    def pending(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Every item awaiting a human decision, each with the existing route and body that decides it."""
        return project_dict(request, operations(request).pending_approvals(ctx))

    return app


__all__ = ("install",)
