"""``GET /v1/capabilities`` (policy V60; owner decision Q13 / O-U3).

The frozen UI reads this to hide the Edit and Save controls from callers who could not use them (an ALS
non-admin). Every field is computed from the very checks that answer 403 on the routes themselves, so the two
cannot drift apart; the server's 403 stays the backstop and a forged request is still refused with no journal
entry (T18D, TIL-22).

* ``can_edit``: the caller holds ``graph.edit`` (the guard of ``PATCH /graph/node/{id}``, R10).
* ``can_save``: ``can_edit`` and the active graph source is editable (not the read-only JSON mode, where the
  same PATCH answers 400) and a Save target is reachable now (on ``:local`` before the first publication the
  same PATCH answers the typed 501; owner decision 2026-11-04, tenth set).
* ``can_upload``: ``graph.upload`` (R11).
* ``role``: ``local_operator`` (``:local``, no sign-in), ``admin`` (verified Tiled admin) or ``user``.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import Depends, FastAPI, Request

from ..modules.contracts.context import RequestContext
from .routes_common import Prefixed, context, project


def capabilities_for_caller(ctx: RequestContext, *, editable_source: bool = True) -> dict[str, Any]:
    """The capability DTO of one caller (pure; the route and the tests share it)."""
    can_edit = "graph.edit" in ctx.capabilities
    if ctx.principal.is_local_operator():
        role = "local_operator"
    else:
        role = "admin" if ctx.principal.is_admin else "user"
    return {"role": role, "profile": ctx.profile.value, "can_edit": can_edit,
            "can_save": can_edit and editable_source, "can_upload": "graph.upload" in ctx.capabilities}


def install(app: FastAPI) -> FastAPI:
    """Mount the capability read on ``app``."""
    router = Prefixed(app, "/v1")

    @router.get("/capabilities")
    def capabilities(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """The caller's editing capabilities, for the UI to hide controls (the 403 on the routes stays)."""
        service = getattr(request.app.state, "f2w_agent_service", None)
        runtime = getattr(service, "deployment_runtime", None)
        editable = getattr(runtime, "graph_source", "splash") != "json"
        available = getattr(service, "save_available", None)
        if editable and callable(available):
            editable = bool(available())
        return cast(dict[str, Any], project(request, capabilities_for_caller(ctx, editable_source=editable)))

    return app


__all__ = ("capabilities_for_caller", "install")
