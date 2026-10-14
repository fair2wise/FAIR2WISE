"""Graph port surface for literature graphs (T08A, catalog K-02 interface-reference, K-05 adapt).

The protocol and capability model live in :mod:`app.modules.contracts.ports`; this module
re-exports them and adds what every graph backend shares:

* :class:`TruncatedGraphRead` - a bounded read could not prove its answer (K-05: "a
  truncation warning is not complete pagination"). Raised instead of returning ``None`` or
  an empty list when absence cannot be established.
* :func:`entity_record` / :func:`edge_record` - the one record shape both backends return.
  The original node/edge payload is kept verbatim under ``data``; nothing is merged into
  it, so old fields (including a node's own ``graph_id`` key in ``nodes``/``edges`` files)
  survive round trips.
* :func:`require_scope` - the ``corpus_scope`` rule shared with ``InMemoryGraphPort``.
* :func:`clamp_bounds` - shared traversal-bound validation.

Adapted in part from als-computing/forge_rsoxs_analysis src/forge/kg/paging.py (BSD-3-Clause).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from app.modules.contracts.context import RequestContext
from app.modules.contracts.errors import CapabilityError, ContractError, ErrorCode
from app.modules.contracts.ports import GraphCapabilities, GraphPort
from app.modules.f2w_agent.multi_kg import MAX_KG_QUERY_HOPS

__all__ = [
    "CapabilityError",
    "ContractError",
    "ErrorCode",
    "GraphCapabilities",
    "GraphPort",
    "MAX_HOPS",
    "TruncatedGraphRead",
    "clamp_bounds",
    "edge_record",
    "entity_record",
    "require_scope",
]

#: Hop ceiling shared with the existing UI/agent presets (``multi_kg.MAX_KG_QUERY_HOPS``).
MAX_HOPS = MAX_KG_QUERY_HOPS


class TruncatedGraphRead(ContractError):
    """A bounded scan ended before it could prove the answer.

    Args:
        reader: Name of the reader whose bound was reached.
        bound: The bound (rows scanned).
    """

    def __init__(self, reader: str, bound: int) -> None:
        super().__init__(
            ErrorCode.UNAVAILABLE,
            f"{reader} reached its scan bound of {bound} rows before finishing; the result is unknown, not empty",
            retryable=False,
        )
        self.reader = reader
        self.bound = bound


def require_scope(ctx: RequestContext, graph_id: str) -> None:
    """Refuse a graph outside a non-empty ``ctx.corpus_scope`` (same rule as ``InMemoryGraphPort``)."""
    if ctx.corpus_scope and graph_id not in ctx.corpus_scope:
        raise ContractError(ErrorCode.DENIED, f"graph {graph_id!r} is outside the request scope")


def clamp_bounds(hops: int, max_nodes: int) -> tuple:
    """Validate traversal bounds; raise ``INVALID_INPUT`` for non-positive values."""
    try:
        hops_i, nodes_i = int(hops), int(max_nodes)
    except (TypeError, ValueError):
        raise ContractError(ErrorCode.INVALID_INPUT, "hops and max_nodes must be integers") from None
    if hops_i < 0 or nodes_i < 1:
        raise ContractError(ErrorCode.INVALID_INPUT, "hops must be >= 0 and max_nodes >= 1")
    return min(hops_i, MAX_HOPS), nodes_i


def _citations(data: Mapping[str, Any]) -> List[str]:
    for key in ("source_papers", "papers"):
        value = data.get(key)
        if isinstance(value, list):
            return [str(v) for v in value if v not in (None, "")]
        if isinstance(value, str) and value:
            return [value]
    return []


def entity_record(graph_id: str, entity_id: str, data: Mapping[str, Any], *, backend: str,
                  name: Optional[str] = None, category: Optional[str] = None,
                  extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Build the graph-scoped entity record returned by every backend.

    Args:
        graph_id: Owning graph (always set; B27).
        entity_id: F2W node id, verbatim.
        data: Original node payload (copied, never mutated).
        backend: ``"json"`` or ``"tiled"``.
        name: Display name; defaults to ``name`` then ``label``.
        category: Category; defaults to ``category`` then ``entityType``.
        extra: Backend-specific keys (for example ``tiled_id``).
    """
    record = {
        "graph_id": graph_id,
        "id": entity_id,
        "name": name if name is not None else str(data.get("name") or data.get("label") or ""),
        "category": (category if category is not None
                     else str(data.get("category") or data.get("entityType") or "")),
        "citations": _citations(data),
        "backend": backend,
        "data": dict(data),
    }
    if extra:
        record.update(extra)
    return record


def edge_record(graph_id: str, edge_id: str, data: Mapping[str, Any], *, backend: str,
                extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Build the graph-scoped edge record (``subject``/``predicate``/``object`` plus verbatim ``data``)."""
    record = {
        "graph_id": graph_id,
        "id": edge_id,
        "subject": data.get("subject"),
        "predicate": data.get("predicate"),
        "object": data.get("object"),
        "backend": backend,
        "data": dict(data),
    }
    if extra:
        record.update(extra)
    return record
