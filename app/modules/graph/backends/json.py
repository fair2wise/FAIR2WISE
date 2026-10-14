"""Read-only GraphPort over versioned JSON KG files (T08A).

Each configured file is one independent graph (``multi_kg``: graphs are never
concatenated). Every record carries ``graph_id``; ids are looked up only inside their
graph, never joined by display name.

Capabilities: complete listing (the whole file is in memory) and bounded traversal.
Snapshots are frozen, so every write (``apply_plan``) raises ``CapabilityError``; edits
go through the edit journal and a new JSON version (graph-mutation-semantics ADR).

Caching is explicit: a file is read on first use and kept until :meth:`reload`. A file
that changed on disk is not silently re-read mid-session ("caches reload intentionally").
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

from app.modules.contracts.changes import ApprovalRecord, ChangePlan, ImportReceipt
from app.modules.contracts.context import RequestContext

from ..ports import (
    CapabilityError,
    ContractError,
    ErrorCode,
    GraphCapabilities,
    clamp_bounds,
    edge_record,
    entity_record,
    require_scope,
)
from ..snapshots import GraphSnapshot, graph_id_for_snapshot, load_snapshot

BACKEND = "json"
CAPABILITIES = GraphCapabilities(
    backend=BACKEND,
    complete_listing=True,
    bounded_traversal=True,
    mutable_entity_properties=False,
    append_only_relations=True,
    batch_writes=False,
    compare_and_swap=False,
    owned_deletion=False,
)
DIRECTIONS = ("both", "out", "in")


class JsonGraphBackend:
    """GraphPort over one or more KG JSON files.

    Args:
        graphs: Paths (``graph_id`` derived like ``multi_kg.graph_id_for_path``) or an
            explicit ``{graph_id: path}`` mapping.

    Raises:
        ContractError: ``INVALID_INPUT`` if two paths map to the same ``graph_id``.
    """

    def __init__(self, graphs: Union[Sequence[Union[str, Path]], Mapping[str, Union[str, Path]]]) -> None:
        if isinstance(graphs, Mapping):
            pairs = [(str(g), Path(p)) for g, p in graphs.items()]
        else:
            pairs = [(graph_id_for_snapshot(Path(p)), Path(p)) for p in graphs]
        self._paths: Dict[str, Path] = {}
        for graph_id, path in pairs:
            if graph_id in self._paths and self._paths[graph_id] != path:
                raise ContractError(ErrorCode.INVALID_INPUT,
                                    f"graph_id {graph_id!r} is claimed by two files; graphs are never merged")
            self._paths[graph_id] = path
        self._cache: Dict[str, GraphSnapshot] = {}
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------
    def graph_ids(self) -> List[str]:
        """Configured graph ids, in configuration order."""
        return list(self._paths)

    def reload(self, graph_id: Optional[str] = None) -> None:
        """Drop the cached snapshot(s); the next read loads the file again."""
        with self._lock:
            if graph_id is None:
                self._cache.clear()
            else:
                self._cache.pop(graph_id, None)

    def snapshot(self, ctx: RequestContext, graph_id: str) -> GraphSnapshot:
        """Return the loaded snapshot for ``graph_id`` (scope-checked)."""
        require_scope(ctx, graph_id)
        if graph_id not in self._paths:
            raise ContractError(ErrorCode.NOT_FOUND, f"graph {graph_id!r} is not configured")
        with self._lock:
            snap = self._cache.get(graph_id)
            if snap is None:
                snap = load_snapshot(self._paths[graph_id], graph_id=graph_id)
                self._cache[graph_id] = snap
            return snap

    # -- GraphPort ---------------------------------------------------------
    def capabilities(self) -> GraphCapabilities:
        """Return the JSON backend capabilities."""
        return CAPABILITIES

    def _entity(self, snap: GraphSnapshot, node_id: str) -> Dict[str, Any]:
        return entity_record(snap.graph_id, node_id, snap.nodes[node_id], backend=BACKEND,
                             extra={"snapshot_sha256": snap.sha256})

    def get_entity(self, ctx: RequestContext, graph_id: str, entity_id: str) -> Optional[Dict[str, Any]]:
        """Return one node of ``graph_id`` or ``None`` (the listing is complete, so absence is real)."""
        snap = self.snapshot(ctx, graph_id)
        return self._entity(snap, entity_id) if entity_id in snap.nodes else None

    def iter_entities(self, ctx: RequestContext, graph_id: str) -> Iterator[Dict[str, Any]]:
        """Yield every node record of ``graph_id`` in file order (complete listing)."""
        snap = self.snapshot(ctx, graph_id)
        for node_id in snap.nodes:
            yield self._entity(snap, node_id)

    def iter_edges(self, ctx: RequestContext, graph_id: str) -> Iterator[Dict[str, Any]]:
        """Yield every edge record of ``graph_id`` in file order."""
        snap = self.snapshot(ctx, graph_id)
        for eid, edge in snap.edges:
            yield edge_record(graph_id, eid, edge, backend=BACKEND)

    def find_entities(
        self, ctx: RequestContext, graph_id: str, query: str, *, limit: int
    ) -> Tuple[Sequence[Dict[str, Any]], bool]:
        """Case-insensitive substring match on name/label or id; returns ``(hits, complete)``."""
        if limit < 1:
            raise ContractError(ErrorCode.INVALID_INPUT, "limit must be >= 1")
        needle = (query or "").lower()
        snap = self.snapshot(ctx, graph_id)
        hits: List[Dict[str, Any]] = []
        total = 0
        for node_id, node in snap.nodes.items():
            label = str(node.get("name") or node.get("label") or "")
            if needle in label.lower() or needle in str(node_id).lower():
                total += 1
                if len(hits) < limit:
                    hits.append(self._entity(snap, node_id))
        return hits, total <= limit

    def neighborhood(
        self, ctx: RequestContext, graph_id: str, entity_id: str, *, hops: int, max_nodes: int,
        direction: str = "both",
    ) -> Dict[str, Any]:
        """Breadth-first neighborhood within ``hops`` and ``max_nodes``.

        ``direction="out"`` follows ``subject -> object`` only, which reaches exactly the
        ids ``KnowledgeGraph.weighted_bfs`` reaches. Edge endpoints with no node record are
        listed in ``missing`` (never invented as nodes). ``truncated`` is set when the node
        cap stopped the walk.
        """
        if direction not in DIRECTIONS:
            raise ContractError(ErrorCode.INVALID_INPUT, f"direction must be one of {DIRECTIONS}")
        hops, max_nodes = clamp_bounds(hops, max_nodes)
        snap = self.snapshot(ctx, graph_id)
        if entity_id not in snap.nodes:
            raise ContractError(ErrorCode.NOT_FOUND, f"no entity {entity_id!r} in graph {graph_id!r}")
        seen: Dict[str, int] = {entity_id: 0}
        order: List[str] = [entity_id]
        edge_idx: List[int] = []
        edge_seen: set = set()
        frontier = [entity_id]
        truncated = False
        for depth in range(1, hops + 1):
            nxt: List[str] = []
            for node in frontier:
                for idx, other in self._adjacent(snap, node, direction):
                    if other not in seen:
                        if len(order) >= max_nodes:
                            truncated = True
                            continue
                        seen[other] = depth
                        order.append(other)
                        nxt.append(other)
                    if idx not in edge_seen:
                        edge_seen.add(idx)
                        edge_idx.append(idx)
            frontier = nxt
            if not frontier:
                break
        nodes = [dict(self._entity(snap, n), depth=seen[n]) for n in order if n in snap.nodes]
        missing = [n for n in order if n not in snap.nodes]
        edges = [edge_record(graph_id, snap.edges[i][0], snap.edges[i][1], backend=BACKEND) for i in edge_idx]
        return {"graph_id": graph_id, "root": entity_id, "hops": hops, "direction": direction,
                "nodes": nodes, "edges": edges, "missing": missing, "truncated": truncated,
                "snapshot_sha256": snap.sha256}

    @staticmethod
    def _adjacent(snap: GraphSnapshot, node: str, direction: str) -> Iterable[Tuple[int, str]]:
        if direction in ("both", "out"):
            for idx in snap.out_index.get(node, []):
                yield idx, str(snap.edges[idx][1].get("object"))
        if direction in ("both", "in"):
            for idx in snap.in_index.get(node, []):
                yield idx, str(snap.edges[idx][1].get("subject"))

    def apply_plan(self, ctx: RequestContext, plan: ChangePlan, approval: ApprovalRecord) -> ImportReceipt:
        """Frozen snapshots cannot be written; edits become a new JSON version."""
        raise CapabilityError("apply_plan", BACKEND)
