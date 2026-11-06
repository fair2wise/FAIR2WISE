"""``GET /v1/graph/sources``: the graph sources the UI graph view may read (policy V80).

Lists the JSON KG files and releases and the Tiled graphs the server itself offers (see
:mod:`app.modules.f2w_agent.graph_sources`); the UI then passes one id as ``source`` to ``GET /graph``,
``POST /graph/nodes/search``, ``GET /graph/neighborhood/{id}`` and ``GET /graph/node/{id}``. The listing never
contains a path, a Tiled URL or a credential. The entry capability is ``chat.read_support`` (as ``GET /graph``);
a Tiled outage is reported in the ``tiled`` object and never fails the listing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, FastAPI, Request

from ..modules.contracts.context import RequestContext
from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.f2w_agent.graph_sources import GraphSources
from .routes_common import Prefixed, context, project_dict


def install(app: FastAPI) -> FastAPI:
    """Mount the graph-source listing on ``app``."""
    router = Prefixed(app, "/v1")

    @router.get("/graph/sources")
    async def graph_sources(request: Request, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """The selectable graph sources and the state of the Tiled side."""
        sources = getattr(request.app.state, "f2w_graph_sources", None)
        if not isinstance(sources, GraphSources):
            raise ContractError(ErrorCode.INTERNAL, "graph sources are not installed")
        listing = await asyncio.to_thread(sources.listing)
        return project_dict(request, listing.as_json())

    return app


__all__ = ("install",)
