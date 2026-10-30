"""Read/query GraphPort over Tiled Graph 0.2.18 (T08A; catalog K-03 required adapter, K-05/K-11 adapted).

Built on the **observed** 0.2.18 contract (``docs/adr/evidence/tiled/capabilities_0.2.18.md``):

* Queries: ``entity(id)``, ``entities(entityType, limit, offset)``, nested
  ``outgoingLinks``/``incomingLinks``. There is no search, URI filter or neighborhood
  query, and operation depth is capped at 10. Lookup and search are therefore bounded
  paged scans filtered client-side; a neighborhood is one batched one-hop query per hop.
* Ownership: only records that pass ``identity_adapter.literature_entity_key`` /
  ``literature_link_key`` (owner marker + graph id + URI agreement) are visible in a
  literature scope. A FORGE entity, an unmarked record or a record of another graph is
  dropped even when linked to an in-scope entity.
* Without an access policy anonymous listings expose everything (item 18); this adapter
  always sends the configured service credential and relies on its own scope filter, not
  on server-side ACLs, for corpus isolation.

Capabilities (honest for 0.2.18 as used here):

=========================  =====  ==========================================================
complete_listing           False  offset paging with no revision/snapshot; concurrent writes
                                  or an access policy can hide rows, so completeness cannot
                                  be proven (repair/dedupe planning stays blocked)
bounded_traversal          True   per-hop batched nested-link queries, node/link caps
mutable_entity_properties  False  properties are create-only (item 11)
append_only_relations      True
batch_writes               False  read adapter; publication is T08B
compare_and_swap           False  no revisions or conditional writes in 0.2.18
owned_deletion             False  ``deleteEntity`` has no owner check without a policy
=========================  =====  ==========================================================

Every write raises ``CapabilityError``. A bound that stops a read before it can prove an
answer raises :class:`~..ports.TruncatedGraphRead` (lookups) or sets ``complete=False`` /
``truncated=True`` (search, traversal); it never reports absence.

Adapted from als-computing/forge_rsoxs_analysis src/forge/kg/views.py and
src/forge/kg/paging.py (BSD-3-Clause).
"""

from __future__ import annotations

from typing import Any, Iterator, Sequence, cast

from app.modules.contracts.changes import ApprovalRecord, ChangePlan, ImportReceipt
from app.modules.contracts.context import RequestContext

from ..identity_adapter import (
    IdentityLedger,
    literature_entity_key,
    literature_link_key,
    strip_reserved,
)
from ..ports import (
    CapabilityError,
    ContractError,
    ErrorCode,
    GraphCapabilities,
    TruncatedGraphRead,
    clamp_bounds,
    edge_record,
    entity_record,
    require_scope,
)
from .tiled_client import TiledGraphClient

BACKEND = "tiled"
CAPABILITIES = GraphCapabilities(
    backend=BACKEND,
    complete_listing=False,
    bounded_traversal=True,
    mutable_entity_properties=False,
    append_only_relations=True,
    batch_writes=False,
    compare_and_swap=False,
    owned_deletion=False,
)
#: GraphQL depth used by one hop: entity > links > endpoint > scalar fields (limit is 10).
ENTITY_FIELDS = "id name entityType uri properties"
LINK_FIELDS = "id predicate subjectId objectId properties"
DIRECTIONS = ("both", "out", "in")

_ENTITY_Q = "query($i: ID!) { entity(id: $i) { " + ENTITY_FIELDS + " } }"
_PAGE_Q = "query($l: Int!, $o: Int!) { entities(limit: $l, offset: $o) { " + ENTITY_FIELDS + " } }"


class TiledGraphBackend:
    """GraphPort over a Tiled 0.2.18 graph (read/query only).

    Args:
        client: Validated read client.
        ledger: Known ``(graph_id, node_id) -> UUID`` bindings (for example from a T08B
            receipt); used for direct lookups and checked against what the server returns.
        page_size: Rows per ``entities`` page.
        max_scan: Upper bound on rows scanned by one lookup/search.
        link_limit: Per-entity, per-direction link page in traversal; hitting it sets ``truncated``.
        batch: Entities per batched traversal request.
    """

    def __init__(self, client: TiledGraphClient, *, ledger: IdentityLedger | None = None,
                 page_size: int = 500, max_scan: int = 100_000, link_limit: int = 500, batch: int = 25) -> None:
        if min(page_size, max_scan, link_limit, batch) < 1:
            raise ContractError(ErrorCode.INVALID_INPUT, "page_size, max_scan, link_limit and batch must be >= 1")
        self.client = client
        self.ledger = ledger if ledger is not None else IdentityLedger()
        self.page_size = page_size
        self.max_scan = max_scan
        self.link_limit = link_limit
        self.batch = batch

    def capabilities(self) -> GraphCapabilities:
        """Return the Tiled 0.2.18 read-adapter capabilities."""
        return CAPABILITIES

    # -- records -----------------------------------------------------------
    @staticmethod
    def _record(row: dict[str, Any], graph_id: str, node_id: str) -> dict[str, Any]:
        return entity_record(graph_id, node_id, strip_reserved(row.get("properties")), backend=BACKEND,
                             name=str(row.get("name") or ""), category=str(row.get("entityType") or ""),
                             extra={"tiled_id": row.get("id")})

    def _scan(self) -> Iterator[dict[str, Any]]:
        """Yield raw entity rows page by page; stop at the end or raise at ``max_scan``."""
        offset = 0
        while True:
            limit = min(self.page_size, self.max_scan - offset)
            if limit <= 0:
                raise TruncatedGraphRead("tiled.entities scan", self.max_scan)
            rows = self.client.query(_PAGE_Q, {"l": limit, "o": offset}).get("entities") or []
            yield from rows
            if len(rows) < limit:
                return
            offset += len(rows)

    def _scoped_rows(self, graph_id: str) -> Iterator[tuple[dict[str, Any], str]]:
        for row in self._scan():
            key = literature_entity_key(row)
            if key is not None and key[0] == graph_id:
                yield row, key[1]

    # -- GraphPort ---------------------------------------------------------
    def _resolve(self, graph_id: str, entity_id: str) -> dict[str, Any] | None:
        known = self.ledger.tiled_id(graph_id, entity_id)
        if known:
            row: dict[str, Any] | None = self.client.query(_ENTITY_Q, {"i": known}).get("entity")
            if row is not None:
                if literature_entity_key(row) != (graph_id, entity_id):
                    raise ContractError(ErrorCode.CONFLICT,
                                        f"Tiled entity {known} no longer carries identity "
                                        f"({graph_id}, {entity_id})")
                return row
        matches = [row for row, node_id in self._scoped_rows(graph_id) if node_id == entity_id]
        if len(matches) > 1:
            raise ContractError(ErrorCode.CONFLICT,
                                f"{len(matches)} Tiled entities claim ({graph_id}, {entity_id}); "
                                "no upsert in 0.2.18")
        if matches:
            self.ledger.bind(graph_id, entity_id, matches[0]["id"])
            return matches[0]
        return None

    def get_entity(self, ctx: RequestContext, graph_id: str, entity_id: str) -> dict[str, Any] | None:
        """Return one in-scope entity, ``None`` only after a complete scan found nothing."""
        require_scope(ctx, graph_id)
        row = self._resolve(graph_id, entity_id)
        return self._record(row, graph_id, entity_id) if row is not None else None

    def find_entities(
        self, ctx: RequestContext, graph_id: str, query: str, *, limit: int
    ) -> tuple[Sequence[dict[str, Any]], bool]:
        """Substring match on name or F2W id; ``complete`` is False if capped or the scan bound hit."""
        require_scope(ctx, graph_id)
        if limit < 1:
            raise ContractError(ErrorCode.INVALID_INPUT, "limit must be >= 1")
        needle = (query or "").lower()
        hits: list[dict[str, Any]] = []
        total = 0
        try:
            for row, node_id in self._scoped_rows(graph_id):
                if needle in str(row.get("name") or "").lower() or needle in node_id.lower():
                    total += 1
                    if len(hits) < limit:
                        hits.append(self._record(row, graph_id, node_id))
        except TruncatedGraphRead:
            return hits, False
        return hits, total <= limit

    def neighborhood(
        self, ctx: RequestContext, graph_id: str, entity_id: str, *, hops: int, max_nodes: int,
        direction: str = "both",
    ) -> dict[str, Any]:
        """Bounded neighborhood via nested links, one batched query per hop (depth cap 10)."""
        require_scope(ctx, graph_id)
        if direction not in DIRECTIONS:
            raise ContractError(ErrorCode.INVALID_INPUT, f"direction must be one of {DIRECTIONS}")
        hops, max_nodes = clamp_bounds(hops, max_nodes)
        root = self._resolve(graph_id, entity_id)
        if root is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"no entity {entity_id!r} in graph {graph_id!r}")
        nodes: dict[str, dict[str, Any]] = {root["id"]: dict(self._record(root, graph_id, entity_id), depth=0)}
        edges: dict[str, dict[str, Any]] = {}
        frontier = [root["id"]]
        truncated = False
        for depth in range(1, hops + 1):
            nxt: list[str] = []
            for start in range(0, len(frontier), self.batch):
                chunk = frontier[start:start + self.batch]
                for link, other, capped in self._one_hop(chunk, direction):
                    truncated = truncated or capped
                    if link is None:
                        continue
                    link_key = literature_link_key(link)
                    other_key = literature_entity_key(other) if other else None
                    if link_key is None or link_key[0] != graph_id or other_key is None \
                            or other_key[0] != graph_id:
                        continue  # foreign link or endpoint: never visible in this scope
                    if other["id"] not in nodes:
                        if len(nodes) >= max_nodes:
                            truncated = True
                            continue
                        self.ledger.bind(graph_id, other_key[1], other["id"])
                        nodes[other["id"]] = dict(self._record(other, graph_id, other_key[1]), depth=depth)
                        nxt.append(other["id"])
                    if link["id"] not in edges:
                        edge = self._edge(link, graph_id, link_key[1], nodes)
                        if edge is not None:
                            edges[link["id"]] = edge
            frontier = nxt
            if not frontier:
                break
        return {"graph_id": graph_id, "root": entity_id, "hops": hops, "direction": direction,
                "nodes": list(nodes.values()), "edges": list(edges.values()),
                "missing": [], "truncated": truncated}

    @staticmethod
    def _edge(link: dict[str, Any], graph_id: str, edge_key: str,
              nodes: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        subject, obj = nodes.get(cast(str, link.get("subjectId"))), nodes.get(cast(str, link.get("objectId")))
        if subject is None or obj is None:
            return None
        data = {"subject": subject["id"], "predicate": link.get("predicate"), "object": obj["id"],
                **strip_reserved(link.get("properties"))}
        return edge_record(graph_id, edge_key, data, backend=BACKEND, extra={"tiled_id": link.get("id")})

    def _one_hop(self, tiled_ids: Sequence[str], direction: str
                 ) -> Iterator[tuple[dict[str, Any] | None, dict[str, Any] | None, bool]]:
        parts, variables = [], {}
        for n, tid in enumerate(tiled_ids):
            variables[f"i{n}"] = tid
            body = []
            if direction in ("both", "out"):
                body.append(f"out: outgoingLinks(limit: {self.link_limit}) {{ {LINK_FIELDS} "
                            f"object {{ {ENTITY_FIELDS} }} }}")
            if direction in ("both", "in"):
                body.append(f"inc: incomingLinks(limit: {self.link_limit}) {{ {LINK_FIELDS} "
                            f"subject {{ {ENTITY_FIELDS} }} }}")
            parts.append(f"e{n}: entity(id: $i{n}) {{ {' '.join(body)} }}")
        decl = ", ".join(f"$i{n}: ID!" for n in range(len(tiled_ids)))
        data = self.client.query(f"query({decl}) {{ {' '.join(parts)} }}", variables)
        for n in range(len(tiled_ids)):
            row = data.get(f"e{n}")
            if row is None:
                raise ContractError(ErrorCode.CONFLICT, "an entity disappeared during traversal")
            for alias, end in (("out", "object"), ("inc", "subject")):
                links = row.get(alias)
                if links is None:
                    continue
                capped = len(links) >= self.link_limit
                if capped:
                    yield None, None, True
                for link in links:
                    yield link, link.get(end), False

    def apply_plan(self, ctx: RequestContext, plan: ChangePlan, approval: ApprovalRecord) -> ImportReceipt:
        """Publication is T08B; this read adapter never writes."""
        raise CapabilityError("apply_plan", BACKEND)

    def update_entity_properties(self, *args: Any, **kwargs: Any) -> None:
        """Entity properties are create-only on 0.2.18."""
        raise CapabilityError("update_entity_properties", BACKEND)

    def compare_and_swap(self, *args: Any, **kwargs: Any) -> None:
        """0.2.18 has no revisions or conditional writes."""
        raise CapabilityError("compare_and_swap", BACKEND)

    def delete_owned(self, *args: Any, **kwargs: Any) -> None:
        """No owner-checked deletion on 0.2.18 without an access policy."""
        raise CapabilityError("owned_deletion", BACKEND)

    def list_complete(self, ctx: RequestContext, graph_id: str) -> list[dict[str, Any]]:
        """A provably complete listing (needed by repair planning) is not available."""
        raise CapabilityError("complete_listing", BACKEND)
