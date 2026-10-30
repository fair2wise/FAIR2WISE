"""``/v1/operations`` routes over ``F2WOperations`` (policies V25-V31; task T19E).

Thin handlers for the five T19C Knowledge Operations: each takes the server-built
context, calls one ``F2WOperations`` method and projects the result. The reads return
the same bounded ``ToolResult`` the agent Tool returns (``status`` ``ok`` or ``partial``,
``data`` the bounded ``OperationReport``). ``POST /v1/operations/jobs`` submits the
``knowledge_operation`` job kind (``ops.run``) for a whole-population scan with no inline
bound; ``GET /v1/operations/jobs/{job_id}`` returns its bounded report once computed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..modules.contracts.context import RequestContext
from .routes_common import Prefixed, context, dump, fresh_context, operations, project


class OperationJobRequest(BaseModel):
    """Body of ``POST /v1/operations/jobs``. The context never comes from the body."""

    model_config = ConfigDict(extra="forbid")

    operation: str = Field(min_length=1, max_length=100)
    args: Dict[str, Any] = Field(default_factory=dict)
    corpus_scope: Optional[List[str]] = Field(default=None, max_length=50)


def install(app: FastAPI) -> FastAPI:
    """Mount the Knowledge Operations routes on ``app``."""
    router = Prefixed(app, "/v1/operations")

    @router.get("/statistics")
    def statistics(request: Request, ctx: RequestContext = Depends(context),
                   graph_id: str = Query(min_length=1, max_length=128)) -> Dict[str, Any]:
        """``kg_compute_statistics``: structural statistics of one graph."""
        return project(request, dump(operations(request).graph_statistics(ctx, graph_id)))

    @router.get("/compare")
    def compare(request: Request, ctx: RequestContext = Depends(context),
                graph_ids: List[str] = Query(min_length=2, max_length=12)) -> Dict[str, Any]:
        """``kg_compare_snapshots``: growth across graph versions in the given order."""
        return project(request, dump(operations(request).compare_graphs(ctx, graph_ids)))

    @router.get("/paper-subgraph")
    def paper_subgraph(request: Request, ctx: RequestContext = Depends(context),
                       graph_id: str = Query(min_length=1, max_length=128),
                       paper: str = Query(min_length=3, max_length=300),
                       max_nodes: Optional[int] = Query(default=None, ge=1, le=200),
                       max_edges: Optional[int] = Query(default=None, ge=1, le=400)) -> Dict[str, Any]:
        """``kg_paper_subgraph``: what one graph holds about one Work."""
        result = operations(request).paper_subgraph(ctx, graph_id, paper, max_nodes=max_nodes, max_edges=max_edges)
        return project(request, dump(result))

    @router.get("/recent")
    def recent(request: Request, ctx: RequestContext = Depends(context),
               graph_id: str = Query(min_length=1, max_length=128),
               since: Optional[str] = Query(default=None, max_length=40),
               limit: Optional[int] = Query(default=None, ge=1, le=50)) -> Dict[str, Any]:
        """``kg_recent_entities``: entities added to a live graph after ``since``."""
        return project(request, dump(operations(request).recent_entities(ctx, graph_id, since=since, limit=limit)))

    @router.get("/corpus-audit")
    def corpus_audit(request: Request, ctx: RequestContext = Depends(context),
                     corpus: str = Query(min_length=1, max_length=128),
                     sections: Optional[List[str]] = Query(default=None, max_length=6)) -> Dict[str, Any]:
        """``curate_audit_corpus``: read-only audit of one configured corpus."""
        return project(request, dump(operations(request).audit_corpus(ctx, corpus, sections=sections)))

    @router.post("/jobs")
    def submit_operation_job(request: Request, body: OperationJobRequest,
                             ctx: RequestContext = Depends(fresh_context)) -> Dict[str, Any]:
        """Submit a ``knowledge_operation`` job (whole population; ``ops.run``)."""
        handle = operations(request).submit_knowledge_operation(ctx, body.operation, body.args,
                                                                corpus_scope=body.corpus_scope)
        return project(request, dump(handle))

    @router.get("/jobs/{job_id}")
    def operation_job_result(request: Request, job_id: str,
                             ctx: RequestContext = Depends(context)) -> Dict[str, Any]:
        """The job handle and, once computed, the bounded report of a ``knowledge_operation`` job."""
        return project(request, operations(request).knowledge_job_result(ctx, job_id))

    return app


__all__ = ("OperationJobRequest", "install")
