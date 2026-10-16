"""Graph Knowledge Operations over GraphPort (task T19C; inventory OP03; T17 K-10, K-11, RT-03/07/08/59).

Four reviewed, typed Tools compute over the *whole* selected graph with code and return
bounded :class:`~.reports.OperationReport` results (no model call):

``kg_compute_statistics``  structural statistics of one graph (``kg-statistics/1``)
``kg_compare_snapshots``   growth/retention across 2-12 ordered graphs (``kg-growth/1``)
``kg_paper_subgraph``      the subgraph derived from one Work (``kg-paper-subgraph/1``)
``kg_recent_entities``     entities created after a time, newest first (``kg-recent/1``)

**Population and counting units.** A graph is one ``graph_id`` (graphs are never merged
or joined by label). *Entities* are distinct node ids. *Relations* are distinct
``(subject, predicate, object)`` triples with all three present (the
``scripts/analyze_kgs.py`` unit); raw edge records, incomplete records, duplicate
records and dangling triples (an endpoint with no node) are reported separately so the
legacy collapse is visible. *Papers* are **distinct Works** (H3 K1) built from the paper
keys entities cite (``source_papers``/``papers``, plus ``publications`` DOIs), with
:mod:`.corpus`'s ``work-identity/1``; distinct paper keys are reported alongside. The
``legacy`` block of ``kg_compute_statistics`` reproduces
``analyze_kgs.calculate_structural_metrics`` exactly (parity-tested against the script);
``kg_compare_snapshots`` reproduces ``calculate_growth_metrics`` but takes its paper
denominator from distinct Works in each graph instead of the file name (the legacy CLI
is unchanged). Structural counts are not measures of scientific truth.

**Backends.** Reads go through the configured GraphPort only:

* JSON (``JsonGraphBackend``): the frozen snapshot, revision ``sha256:<file hash>``,
  ``exactness="snapshot"``; ``nodes``/``edges`` files are read too (``analyze_kgs`` reads
  them as empty).
* Tiled 0.2.18 (``TiledGraphBackend``): complete offset paging of ``entities`` and
  ``links`` with the backend's credential, filtered to owned literature records of the
  graph (``identity_adapter``); FORGE and unmarked records never count. There is no
  revision, so ``exactness="live_unisolated"`` and a warning says concurrent writes may
  be missed. Endpoints are mapped back to F2W node ids.

**Complete vs partial.** Every scan runs under a :class:`~.reports.ScanControl` bound
(records and deadline). Reaching it stops the scan: the report is
``coverage.completeness="partial"`` with the reason, and the Tool status is
``PARTIAL``, never ``OK`` (OPS-03). Preview truncation is recorded separately
(``display_truncated``) and never changes a total (OPS-04).

**Read-only.** No operation writes, repairs, deduplicates or publishes. Outputs carry ids,
names, categories, predicates, counts and paper keys only: never descriptions, context
snippets or source text (D19).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from ..agent_runtime.executor import is_control_flow
from ..agent_runtime.skills import Skill
from ..contracts import ContractError, CostClass, Effect, ErrorCode, RequestContext, ToolResult, ToolSpec
from .corpus import WORK_IDENTITY_METHOD, WorkIndex, doi_key, paper_key, record_for
from .reports import (
    Coverage,
    OperationReport,
    ScanControl,
    error_result,
    merge_warnings,
    report_to_result,
    stamp,
    store_report_artifact,
    top_items,
    utcnow,
)

OPERATION_VERSION = "1.0.0"
TOOL_STATS = "kg_compute_statistics"
TOOL_COMPARE = "kg_compare_snapshots"
TOOL_SUBGRAPH = "kg_paper_subgraph"
TOOL_RECENT = "kg_recent_entities"
METHOD_STATS = "kg-statistics/1"
METHOD_GROWTH = "kg-growth/1"
METHOD_SUBGRAPH = "kg-paper-subgraph/1"
METHOD_RECENT = "kg-recent/1"
MAX_COMPARE_GRAPHS = 12
HISTOGRAM_TOP = 25

_ENT_Q = ("query($l: Int!, $o: Int!) { entities(limit: $l, offset: $o) "
          "{ id name entityType uri properties createdAt } }")
_LINK_Q = ("query($l: Int!, $o: Int!) { links(limit: $l, offset: $o) "
           "{ id predicate subjectId objectId properties } }")

UNITS_STATS = {
    "entities": "distinct node ids in the graph",
    "edge_records": "raw edge records (associations/edges/links) of the graph",
    "relations": "distinct (subject, predicate, object) triples with all three present (analyze_kgs unit)",
    "incomplete_edge_records": "edge records missing subject, predicate or object",
    "duplicate_edge_records": "complete edge records repeating an earlier triple",
    "dangling_relations": "distinct triples with an endpoint that has no node record",
    "self_loop_relations": "distinct triples whose subject equals their object",
    "unique_predicates": "distinct predicates over distinct triples",
    "entities_with_relations": "entities that are subject or object of at least one distinct triple",
    "isolated_entities": "entities in no distinct triple",
    "entities_by_category": "entities per category (top 25; category_count is the full number)",
    "relations_by_predicate": "distinct triples per predicate (top 25)",
    "paper_keys": "distinct paper keys (basenames) cited by entities",
    "distinct_works": f"distinct Works cited by entities ({WORK_IDENTITY_METHOD}; H3 K1)",
    "entities_with_citations": "entities citing at least one paper key",
    "legacy": "analyze_kgs.calculate_structural_metrics over (entities, relations), unchanged",
}


# =====================================================================================
# population reader
# =====================================================================================

@dataclass(frozen=True)
class EntityRow:
    """What the operations use from one entity (no description, snippet or source text)."""

    id: str
    name: str
    category: str
    citations: Tuple[str, ...] = ()
    publication_dois: Tuple[Tuple[str, str], ...] = ()  # (paper key, doi)
    created_at: Optional[str] = None


@dataclass
class GraphScan:
    """One graph read through GraphPort."""

    graph_id: str
    backend: str
    revision: str
    exactness: str
    entities: Dict[str, EntityRow] = field(default_factory=dict)
    edges: List[Tuple[str, Any, Any, Any]] = field(default_factory=list)
    scan_complete: bool = True
    reasons: List[str] = field(default_factory=list)
    records_scanned: int = 0
    unresolved_link_endpoints: int = 0

    def coverage(self) -> Coverage:
        """Coverage of this scan."""
        reasons = list(self.reasons)
        return Coverage.of(scan_complete=self.scan_complete, records_scanned=self.records_scanned,
                           exactness=self.exactness, reasons=reasons)


def _row_from_data(node_id: str, name: str, category: str, data: Mapping[str, Any],
                   created_at: Optional[str] = None) -> EntityRow:
    cites: List[str] = []
    for key in ("source_papers", "papers"):
        value = data.get(key)
        values = value if isinstance(value, list) else [value] if isinstance(value, str) and value else []
        for v in values:
            k = paper_key(v)
            if k and k not in cites:
                cites.append(k)
        if values:
            break
    pubs: List[Tuple[str, str]] = []
    for pub in data.get("publications") or []:
        if isinstance(pub, Mapping):
            k, d = paper_key(pub.get("source_paper")), doi_key(pub.get("doi"))
            if k and d:
                pubs.append((k, d))
                if k not in cites:
                    cites.append(k)
    return EntityRow(id=node_id, name=name, category=category, citations=tuple(cites),
                     publication_dois=tuple(pubs), created_at=created_at)


def scan_graph(port: Any, ctx: RequestContext, graph_id: str, control: Optional[ScanControl] = None) -> GraphScan:
    """Read every entity and edge of ``graph_id`` through ``port`` within ``control``'s bounds.

    Raises:
        ContractError: ``DENIED`` outside the request scope, ``NOT_FOUND`` for an unknown
            JSON graph, ``UNAVAILABLE`` when the backend cannot be read, and
            ``UNSUPPORTED_CAPABILITY`` for a backend that can neither list nor iterate.
    """
    from ..graph.ports import require_scope

    require_scope(ctx, graph_id)
    control = control or ScanControl()
    if hasattr(port, "snapshot") and callable(getattr(port, "snapshot")):
        return _scan_json(port, ctx, graph_id, control)
    if hasattr(port, "client") and port.capabilities().backend == "tiled":
        return _scan_tiled(port, graph_id, control)
    if hasattr(port, "iter_entities") and hasattr(port, "iter_edges"):
        return _scan_iterators(port, ctx, graph_id, control)
    from ..contracts.errors import CapabilityError

    raise CapabilityError("complete_listing", getattr(port.capabilities(), "backend", "unknown"))


def _scan_json(port: Any, ctx: RequestContext, graph_id: str, control: ScanControl) -> GraphScan:
    snap = port.snapshot(ctx, graph_id)
    scan = GraphScan(graph_id=graph_id, backend="json", revision=f"sha256:{snap.sha256}", exactness="snapshot")
    for node_id, node in snap.nodes.items():
        if not control.tick():
            return _stopped(scan, control, "entities")
        scan.records_scanned += 1
        scan.entities[node_id] = _row_from_data(node_id, str(node.get("name") or node.get("label") or ""),
                                                str(node.get("category") or node.get("entityType") or ""), node)
    for eid, edge in snap.edges:
        if not control.tick():
            return _stopped(scan, control, "edges")
        scan.records_scanned += 1
        scan.edges.append((eid, edge.get("subject"), edge.get("predicate"), edge.get("object")))
    return scan


def _scan_iterators(port: Any, ctx: RequestContext, graph_id: str, control: ScanControl) -> GraphScan:
    caps = port.capabilities()
    scan = GraphScan(graph_id=graph_id, backend=caps.backend, revision=f"{caps.backend}-live",
                     exactness="snapshot" if caps.complete_listing else "live_unisolated")
    for rec in port.iter_entities(ctx, graph_id):
        if not control.tick():
            return _stopped(scan, control, "entities")
        scan.records_scanned += 1
        scan.entities[rec["id"]] = _row_from_data(rec["id"], rec.get("name") or "", rec.get("category") or "",
                                                  rec.get("data") or {})
    for rec in port.iter_edges(ctx, graph_id):
        if not control.tick():
            return _stopped(scan, control, "edges")
        scan.records_scanned += 1
        scan.edges.append((rec.get("id"), rec.get("subject"), rec.get("predicate"), rec.get("object")))
    return scan


def _stopped(scan: GraphScan, control: ScanControl, phase: str) -> GraphScan:
    scan.scan_complete = False
    scan.reasons.append(f"{control.stopped_reason or 'scan stopped'} while reading {phase}")
    return scan


def _pages(client: Any, query: str, field_name: str, page_size: int, control: ScanControl
           ) -> Iterable[Optional[Dict[str, Any]]]:
    """Yield rows of one listing; yields ``None`` once when ``control`` stops the scan."""
    offset = 0
    while True:
        rows = client.query(query, {"l": page_size, "o": offset}).get(field_name) or []
        for row in rows:
            if not control.tick():
                yield None
                return
            yield row
        if len(rows) < page_size:
            return
        offset += len(rows)


def _scan_tiled(port: Any, graph_id: str, control: ScanControl) -> GraphScan:
    from ..graph.identity_adapter import literature_entity_key, literature_link_key, strip_reserved

    started = utcnow().isoformat()
    scan = GraphScan(graph_id=graph_id, backend="tiled", revision=f"tiled-live:{graph_id}@{started}",
                     exactness="live_unisolated")
    by_tiled: Dict[str, str] = {}
    page = max(1, int(getattr(port, "page_size", 500)))
    for row in _pages(port.client, _ENT_Q, "entities", page, control):
        if row is None:
            _stopped(scan, control, "entities")
            scan.reasons.append("relations were not read because the entity listing is incomplete")
            return scan
        scan.records_scanned += 1
        key = literature_entity_key(row)
        if key is None or key[0] != graph_id:
            continue  # FORGE, unmarked or another graph's record: never in this population
        node_id = key[1]
        if node_id in scan.entities:
            scan.reasons.append(f"duplicate Tiled identity for node {node_id!r}; the first record is used")
            continue
        by_tiled[str(row.get("id"))] = node_id
        scan.entities[node_id] = _row_from_data(node_id, str(row.get("name") or ""), str(row.get("entityType") or ""),
                                                strip_reserved(row.get("properties")),
                                                created_at=row.get("createdAt"))
    for link in _pages(port.client, _LINK_Q, "links", page, control):
        if link is None:
            return _stopped(scan, control, "links")
        scan.records_scanned += 1
        lkey = literature_link_key(link)
        if lkey is None or lkey[0] != graph_id:
            continue
        subj, obj = by_tiled.get(str(link.get("subjectId"))), by_tiled.get(str(link.get("objectId")))
        if subj is None or obj is None:
            scan.unresolved_link_endpoints += 1
        scan.edges.append((lkey[1], subj, link.get("predicate"), obj))
    return scan


# =====================================================================================
# metrics
# =====================================================================================

def legacy_structural_metrics(entities: Set[str], relations: Set[Tuple[Any, Any, Any]]) -> Dict[str, Any]:
    """``analyze_kgs.calculate_structural_metrics``, unchanged."""
    if not entities:
        return {"entity_relation_ratio": 0, "entities_with_relations_pct": 0, "avg_relations_per_entity": 0,
                "unique_predicates": 0}
    in_rel: Set[Any] = set()
    predicates: Set[Any] = set()
    counts: Dict[Any, int] = defaultdict(int)
    for subj, pred, obj in relations:
        in_rel.add(subj)
        in_rel.add(obj)
        predicates.add(pred)
        counts[subj] += 1
        counts[obj] += 1
    connected = in_rel & entities
    return {
        "entity_relation_ratio": round(len(relations) / len(entities), 2) if entities else 0,
        "entities_with_relations_pct": round(100 * len(connected) / len(entities), 1),
        "avg_relations_per_entity": round(sum(counts.values()) / len(entities), 2),
        "unique_predicates": len(predicates),
    }


def legacy_growth_metrics(cur_e: Set[str], cur_r: Set[Tuple[Any, Any, Any]], prev_e: Set[str],
                          prev_r: Set[Tuple[Any, Any, Any]], cur_papers: int, prev_papers: int) -> Dict[str, Any]:
    """``analyze_kgs.calculate_growth_metrics``, unchanged (paper counts supplied by the caller)."""
    if not prev_e:
        return {"entity_retention": None, "relation_retention": None,
                "entity_growth_rate": round(len(cur_e) / cur_papers, 1) if cur_papers else 0,
                "relation_growth_rate": round(len(cur_r) / cur_papers, 1) if cur_papers else 0,
                "new_entities": len(cur_e), "new_relations": len(cur_r)}
    added = cur_papers - prev_papers
    new_e, new_r = cur_e - prev_e, cur_r - prev_r
    return {
        "entity_retention": round(100 * len(prev_e & cur_e) / len(prev_e), 1),
        "relation_retention": round(100 * len(prev_r & cur_r) / len(prev_r), 1) if prev_r else 0,
        "entity_growth_rate": round(len(new_e) / added, 1) if added > 0 else 0,
        "relation_growth_rate": round(len(new_r) / added, 1) if added > 0 else 0,
        "new_entities": len(new_e), "new_relations": len(new_r),
    }


def triples(scan: GraphScan) -> Tuple[Set[Tuple[Any, Any, Any]], int]:
    """``(distinct complete triples, complete record count)``."""
    complete = [(s, p, o) for _eid, s, p, o in scan.edges if s and p and o]
    return set(complete), len(complete)


def work_index(scans: Sequence[GraphScan]) -> WorkIndex:
    """One Work index over every paper key the scans cite (so Work ids agree across graphs)."""
    meta: Dict[str, List[Dict[str, Any]]] = {}
    for scan in scans:
        for row in scan.entities.values():
            for key in row.citations:
                meta.setdefault(key, [])
            for key, doi in row.publication_dois:
                meta.setdefault(key, []).append({"doi": doi})
    return WorkIndex.build(record_for(k, v) for k, v in meta.items())


def works_cited(scan: GraphScan, works: WorkIndex) -> Set[str]:
    """Distinct Work ids cited by entities of ``scan``."""
    return {works.work_of[k] for row in scan.entities.values() for k in row.citations if k in works.work_of}


def graph_statistics(scan: GraphScan, works: Optional[WorkIndex] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(metrics, preview)`` for one scanned graph."""
    works = works or work_index([scan])
    ids = set(scan.entities)
    distinct, complete_records = triples(scan)
    preds: Counter = Counter(p for _s, p, _o in distinct)
    cats: Counter = Counter(row.category or "UNKNOWN" for row in scan.entities.values())
    in_rel = {s for s, _p, _o in distinct} | {o for _s, _p, o in distinct}
    keys = {k for row in scan.entities.values() for k in row.citations}
    dangling = [(s, p, o) for s, p, o in distinct if s not in ids or o not in ids]
    cited = works_cited(scan, works)
    metrics = {
        "entities": len(ids),
        "edge_records": len(scan.edges),
        "relations": len(distinct),
        "incomplete_edge_records": len(scan.edges) - complete_records,
        "duplicate_edge_records": complete_records - len(distinct),
        "dangling_relations": len(dangling),
        "self_loop_relations": sum(1 for s, _p, o in distinct if s == o),
        "unique_predicates": len(preds),
        "entities_with_relations": len(in_rel & ids),
        "isolated_entities": len(ids - in_rel),
        "category_count": len(cats),
        "entities_by_category": dict(top_items(cats, HISTOGRAM_TOP)),
        "relations_by_predicate": dict(top_items(preds, HISTOGRAM_TOP)),
        "paper_keys": len(keys),
        "distinct_works": len(cited),
        "entities_with_citations": sum(1 for row in scan.entities.values() if row.citations),
        "legacy": legacy_structural_metrics(ids, distinct),
    }
    if scan.unresolved_link_endpoints:
        metrics["unresolved_link_endpoints"] = scan.unresolved_link_endpoints
    preview = {"dangling_relations": [[s, p, o] for s, p, o in sorted(dangling, key=str)[:10]]}
    return metrics, preview


# =====================================================================================
# operations
# =====================================================================================

def _scope_and_revisions(scans: Sequence[GraphScan]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    return ({"graph_ids": [s.graph_id for s in scans], "backends": sorted({s.backend for s in scans})},
            {s.graph_id: s.revision for s in scans})


def _combined_coverage(scans: Sequence[GraphScan]) -> Coverage:
    complete = all(s.scan_complete for s in scans)
    exact = "snapshot" if all(s.exactness == "snapshot" for s in scans) else "live_unisolated"
    reasons = [f"{s.graph_id}: {r}" for s in scans for r in s.reasons]
    return Coverage.of(scan_complete=complete, records_scanned=sum(s.records_scanned for s in scans),
                       exactness=exact, reasons=reasons)


def _identity_warnings(scans: Sequence[GraphScan]) -> List[str]:
    out = []
    for s in scans:
        if s.unresolved_link_endpoints:
            out.append(f"{s.graph_id}: {s.unresolved_link_endpoints} literature links have an endpoint outside the "
                       "graph's owned entities; they count as dangling")
    return out


def compute_statistics(port: Any, ctx: RequestContext, graph_id: str,
                       control: Optional[ScanControl] = None) -> OperationReport:
    """``kg_compute_statistics`` over one graph."""
    started = utcnow()
    scan = scan_graph(port, ctx, graph_id, control)
    metrics, preview = graph_statistics(scan)
    scope, revisions = _scope_and_revisions([scan])
    warnings = _identity_warnings([scan])
    if not metrics["entities"] and scan.scan_complete:
        warnings.append("the graph has no entities; all counts are zero for the complete (empty) population")
    report = OperationReport(
        operation=TOOL_STATS, operation_version=OPERATION_VERSION, method=METHOD_STATS,
        parameters={"graph_id": graph_id}, scope=scope, source_revisions=revisions,
        population=f"Every entity and edge record of graph {graph_id!r} visible to this request (one graph; "
                   "never merged with another).",
        counting_units=dict(UNITS_STATS), coverage=scan.coverage(), metrics=metrics, preview=preview,
        warnings=tuple(warnings),
    )
    return stamp(report, started)


def compare_snapshots(port: Any, ctx: RequestContext, graph_ids: Sequence[str],
                      control: Optional[ScanControl] = None) -> OperationReport:
    """``kg_compare_snapshots``: growth between consecutive graphs, in the given order."""
    started = utcnow()
    if not 2 <= len(graph_ids) <= MAX_COMPARE_GRAPHS or len(set(graph_ids)) != len(graph_ids):
        raise ContractError(ErrorCode.INVALID_INPUT, f"give 2-{MAX_COMPARE_GRAPHS} distinct graph ids in order")
    control = control or ScanControl()
    scans: List[GraphScan] = []
    for gid in graph_ids:
        scans.append(scan_graph(port, ctx, gid, control))
        if not scans[-1].scan_complete:
            break
    works = work_index(scans)
    steps: List[Dict[str, Any]] = []
    prev: Optional[Tuple[Set[str], Set[Tuple[Any, Any, Any]], Set[str]]] = None
    warnings = _identity_warnings(scans)
    for scan in scans:
        ids = set(scan.entities)
        rel, _n = triples(scan)
        cited = works_cited(scan, works)
        row: Dict[str, Any] = {"graph_id": scan.graph_id, "entities": len(ids), "relations": len(rel),
                               "distinct_works": len(cited), "complete": scan.scan_complete}
        if prev is None:
            row["legacy_growth"] = legacy_growth_metrics(ids, rel, set(), set(), len(cited), 0)
        else:
            p_ids, p_rel, p_works = prev
            row["legacy_growth"] = legacy_growth_metrics(ids, rel, p_ids, p_rel, len(cited), len(p_works))
            row.update({"removed_entities": len(p_ids - ids), "removed_relations": len(p_rel - rel),
                        "new_works": len(cited - p_works), "removed_works": len(p_works - cited)})
            if p_ids and ids and not (p_ids & ids):
                warnings.append(f"{scan.graph_id} shares no node id with the previous graph: ids are graph-scoped, "
                                "so retention is 0 when id schemes differ (no label join is attempted)")
        steps.append(row)
        prev = (ids, rel, cited)
    scope, revisions = _scope_and_revisions(scans)
    coverage = _combined_coverage(scans)
    if len(scans) < len(graph_ids):
        coverage = Coverage.of(scan_complete=False, records_scanned=coverage.records_scanned,
                               exactness=coverage.exactness,
                               reasons=(*coverage.reasons, f"graphs not read: {list(graph_ids[len(scans):])}"))
    report = OperationReport(
        operation=TOOL_COMPARE, operation_version=OPERATION_VERSION, method=METHOD_GROWTH,
        parameters={"graph_ids": list(graph_ids)}, scope=scope, source_revisions=revisions,
        population="Every entity and edge of each listed graph, compared in the given order by node id and by "
                   "(subject, predicate, object) triple.",
        counting_units={
            "entities": UNITS_STATS["entities"], "relations": UNITS_STATS["relations"],
            "distinct_works": UNITS_STATS["distinct_works"],
            "legacy_growth": "analyze_kgs.calculate_growth_metrics; papers = distinct Works cited by each graph "
                             "(the legacy CLI parses a paper count from the file name instead)",
            "removed_entities": "node ids in the previous graph absent from this one",
            "removed_relations": "distinct triples in the previous graph absent from this one",
            "new_works": "distinct Works cited here and not in the previous graph",
        },
        coverage=coverage, metrics={"steps": steps}, warnings=tuple(merge_warnings(warnings)),
    )
    return stamp(report, started)


def paper_subgraph(port: Any, ctx: RequestContext, graph_id: str, paper: str, *, max_nodes: int = 50,
                   max_edges: int = 100, control: Optional[ScanControl] = None) -> OperationReport:
    """``kg_paper_subgraph``: entities citing one Work and the relations among them."""
    started = utcnow()
    scan = scan_graph(port, ctx, graph_id, control)
    works = work_index([scan])
    work_id = works.resolve(paper)
    if work_id is None:
        if not scan.scan_complete:
            raise ContractError(ErrorCode.UNAVAILABLE, "the scan stopped before the paper was found; its absence "
                                                        "is unknown, not established", retryable=True)
        raise ContractError(ErrorCode.NOT_FOUND, f"no Work matching {paper!r} is cited in graph {graph_id!r}")
    keys = set(works.members[work_id])
    members = {nid for nid, row in scan.entities.items() if keys & set(row.citations)}
    distinct, _n = triples(scan)
    internal = sorted(((s, p, o) for s, p, o in distinct if s in members and o in members), key=str)
    boundary = [(s, p, o) for s, p, o in distinct if (s in members) != (o in members)]
    neighbours = {o if s in members else s for s, _p, o in boundary} & set(scan.entities)
    degree: Counter = Counter()
    for s, _p, o in internal:
        degree[s] += 1
        degree[o] += 1
    ordered = sorted(members, key=lambda n: (-degree[n], n))
    cats: Counter = Counter(scan.entities[n].category or "UNKNOWN" for n in members)
    metrics = {
        "work_id": work_id, "paper_keys": sorted(keys)[:20], "paper_key_count": len(keys),
        "entities": len(members), "internal_relations": len(internal), "boundary_relations": len(boundary),
        "neighbour_entities": len(neighbours), "entities_by_category": dict(top_items(cats, HISTOGRAM_TOP)),
    }
    preview = {
        "nodes": [[n, scan.entities[n].name, scan.entities[n].category] for n in ordered[:max_nodes]],
        "edges": [[s, p, o] for s, p, o in internal[:max_edges]],
    }
    scope, revisions = _scope_and_revisions([scan])
    report = OperationReport(
        operation=TOOL_SUBGRAPH, operation_version=OPERATION_VERSION, method=METHOD_SUBGRAPH,
        parameters={"graph_id": graph_id, "paper": paper, "max_nodes": max_nodes, "max_edges": max_edges},
        scope=scope, source_revisions=revisions,
        population=(f"Entities of graph {graph_id!r} that cite any paper key of the resolved Work, and the distinct "
                    "triples among them; boundary relations link one member to a non-member."),
        counting_units={"entities": "distinct node ids citing the Work", "internal_relations": UNITS_STATS["relations"],
                        "boundary_relations": "distinct triples with exactly one endpoint in the subgraph",
                        "neighbour_entities": "distinct existing non-member endpoints of boundary relations",
                        "work_id": f"{WORK_IDENTITY_METHOD} Work id"},
        coverage=scan.coverage(), metrics=metrics, preview=preview,
        display_truncated=len(members) > max_nodes or len(internal) > max_edges,
        warnings=tuple(_identity_warnings([scan])),
    )
    return stamp(report, started)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ContractError(ErrorCode.INVALID_INPUT, "since must be an ISO 8601 timestamp") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def recent_entities(port: Any, ctx: RequestContext, graph_id: str, *, since: str = "", limit: int = 25,
                    control: Optional[ScanControl] = None) -> OperationReport:
    """``kg_recent_entities``: entities created after ``since`` (all when empty), newest first."""
    started = utcnow()
    floor = _parse_time(since) if since else None
    scan = scan_graph(port, ctx, graph_id, control)
    missing = [n for n, row in scan.entities.items() if not row.created_at]
    if missing:
        raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                            f"{len(missing)} entities of {graph_id!r} carry no creation time ({scan.backend} backend); "
                            "use kg_compare_snapshots to see what a newer version added")
    stamped = [(_parse_time(row.created_at or ""), n) for n, row in scan.entities.items()]
    chosen = sorted(((t, n) for t, n in stamped if floor is None or t > floor), key=lambda x: (-x[0].timestamp(), x[1]))
    cats: Counter = Counter(scan.entities[n].category or "UNKNOWN" for _t, n in chosen)
    metrics = {"entities_created": len(chosen), "entities_total": len(scan.entities),
               "newest": chosen[0][0].isoformat() if chosen else None,
               "oldest_in_window": chosen[-1][0].isoformat() if chosen else None,
               "entities_by_category": dict(top_items(cats, HISTOGRAM_TOP))}
    preview = {"entities": [[n, scan.entities[n].name, scan.entities[n].category, t.isoformat()]
                            for t, n in chosen[:limit]]}
    scope, revisions = _scope_and_revisions([scan])
    report = OperationReport(
        operation=TOOL_RECENT, operation_version=OPERATION_VERSION, method=METHOD_RECENT,
        parameters={"graph_id": graph_id, "since": since, "limit": limit}, scope=scope, source_revisions=revisions,
        population=f"Entities of graph {graph_id!r} whose backend creation time is after 'since'.",
        counting_units={"entities_created": "distinct node ids created after 'since'",
                        "entities_total": UNITS_STATS["entities"]},
        coverage=scan.coverage(), metrics=metrics, preview=preview, display_truncated=len(chosen) > limit,
        warnings=tuple(_identity_warnings([scan])),
    )
    return stamp(report, started)


# =====================================================================================
# Tools
# =====================================================================================

_GRAPH_ID = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}
_COMMON_WHEN_NOT = ("It never changes, repairs or publishes the graph, does not read paper text, and does not judge "
                    "scientific correctness: structural counts are not measures of truth.")
_EVIDENCE = ("No per-record EvidenceRef: an aggregate cites its computation report (method, source_revisions, "
             "coverage); artifact_refs carries the private full report when an artifact store is configured.")
_OUT = {"type": "object",
        "properties": {"operation": {"type": "string"}, "method": {"type": "string"}, "coverage": {"type": "object"},
                       "metrics": {"type": "object"}, "counting_units": {"type": "object"},
                       "preview": {"type": "object"}},
        "required": ["operation", "method", "coverage", "metrics", "counting_units"]}


def _spec(name: str, description: str, when: str, schema: Dict[str, Any]) -> ToolSpec:
    return ToolSpec(name=name, version=OPERATION_VERSION, domain="operations", description=description,
                    when_to_use=when, when_not_to_use=_COMMON_WHEN_NOT, input_schema=schema, output_schema=_OUT,
                    effects=frozenset({Effect.READ_CORPUS}), cost_class=CostClass.CHEAP,
                    required_capabilities=frozenset({"chat.ask"}), evidence_contract=_EVIDENCE, idempotent=True)


KG_COMPUTE_STATISTICS = _spec(
    TOOL_STATS,
    "Structural statistics of one literature knowledge graph computed by code over every entity and edge "
    "(no model call): entities, distinct relations, duplicates, dangling relations, category/predicate "
    "histograms and distinct Works cited, each with its counting unit and coverage.",
    "The user asks how big a graph is, how many entities/relations/papers it has, or how it is distributed "
    "over categories or predicates. Relay totals from metrics with their units; say 'at least' when coverage "
    "is partial.",
    {"type": "object", "properties": {"graph_id": _GRAPH_ID}, "required": ["graph_id"], "additionalProperties": False},
)
KG_COMPARE_SNAPSHOTS = _spec(
    TOOL_COMPARE,
    "Growth and retention across 2-12 graph versions in the given order (no model call): entities and "
    "relations added, retained and removed, and distinct Works per version.",
    "The user asks how a graph grew between versions, what a new version added, or for a growth series.",
    {"type": "object", "properties": {"graph_ids": {"type": "array", "items": _GRAPH_ID, "minItems": 2,
                                                    "maxItems": MAX_COMPARE_GRAPHS, "uniqueItems": True}},
     "required": ["graph_ids"], "additionalProperties": False},
)
KG_PAPER_SUBGRAPH = _spec(
    TOOL_SUBGRAPH,
    "The part of one graph derived from one paper (no model call): every entity citing the Work (resolved "
    "from a DOI, arXiv id or paper key), the relations among them and how many relations leave the subgraph; "
    "a bounded node/edge preview.",
    "The user asks what a graph holds about one specific paper, or for that paper's subgraph.",
    {"type": "object", "properties": {"graph_id": _GRAPH_ID, "paper": {"type": "string", "minLength": 3,
                                                                       "maxLength": 300},
                                      "max_nodes": {"type": "integer", "minimum": 1, "maximum": 200},
                                      "max_edges": {"type": "integer", "minimum": 1, "maximum": 400}},
     "required": ["graph_id", "paper"], "additionalProperties": False},
)
KG_RECENT_ENTITIES = _spec(
    TOOL_RECENT,
    "Entities added to a live (Tiled) graph after a given time, newest first, with the full count (no model "
    "call). Frozen JSON snapshots have no creation times and return an unsupported-capability error.",
    "The user asks what was recently added to a published graph.",
    {"type": "object", "properties": {"graph_id": _GRAPH_ID, "since": {"type": "string", "maxLength": 40},
                                      "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
     "required": ["graph_id"], "additionalProperties": False},
)
GRAPH_SPECS = (KG_COMPUTE_STATISTICS, KG_COMPARE_SNAPSHOTS, KG_PAPER_SUBGRAPH, KG_RECENT_ENTITIES)

ANALYZE_KNOWLEDGE_GRAPH_SKILL = Skill(
    name="analyze_knowledge_graph",
    description="Counts, growth, per-paper subgraphs and recent additions of the literature graphs, computed by code.",
    when_to_use=(
        "Questions whose answer needs the whole graph: totals, distributions, growth between versions, what a "
        "graph holds about one paper. Use the metrics and their counting units; preview lists are examples; "
        "when coverage.completeness is partial, the figures are lower bounds."
    ),
    tools=tuple(s.name for s in GRAPH_SPECS),
    required_capabilities=frozenset({"chat.ask"}),
)


class GraphOperationsTool:
    """The four graph Tools bound to one GraphPort.

    Args:
        port: ``JsonGraphBackend``, ``TiledGraphBackend`` or another GraphPort that can
            iterate a graph.
        graph_ids: Graph ids this deployment exposes (``None``: whatever the port and the
            request scope allow). An id outside it is ``NOT_FOUND``.
        artifact_store: Optional ``DocumentStore`` for the private full report.
        max_records: Scan bound for one inline call (larger scans: run as a job).
    """

    specs = GRAPH_SPECS
    skill = ANALYZE_KNOWLEDGE_GRAPH_SKILL

    def __init__(self, port: Any, *, graph_ids: Optional[Sequence[str]] = None, artifact_store: Any = None,
                 max_records: int = 500_000) -> None:
        self.port = port
        self._graph_ids = None if graph_ids is None else tuple(graph_ids)
        self._store = artifact_store
        self._max = max_records

    def registrations(self) -> List[Tuple[ToolSpec, Callable[[Any], ToolResult]]]:
        """``(spec, fn)`` pairs for ``build_registry(extra=...)``."""
        return [(spec, self._runner(spec.name)) for spec in self.specs]

    def _check(self, graph_id: str) -> None:
        known = self._graph_ids
        if known is None and hasattr(self.port, "graph_ids"):
            known = tuple(self.port.graph_ids())
        if known is not None and graph_id not in known:
            raise ContractError(ErrorCode.NOT_FOUND, f"graph {graph_id!r} is not configured")

    def operation_names(self) -> Tuple[str, ...]:
        """Operations this suite runs (job dispatch)."""
        return tuple(spec.name for spec in self.specs)

    def compute_operation(self, ctx: RequestContext, operation: str, args: Mapping[str, Any],
                          control: Optional[ScanControl] = None) -> OperationReport:
        """Job entry point (same as :meth:`compute`)."""
        return self.compute(ctx, operation, args, control)

    def compute(self, ctx: RequestContext, operation: str, args: Mapping[str, Any],
                control: Optional[ScanControl] = None) -> OperationReport:
        """Run one graph operation by Tool name (also the job entry point)."""
        control = control or ScanControl(self._max, deadline=ctx.deadline)
        if operation == TOOL_COMPARE:
            ids = [str(g) for g in args.get("graph_ids") or []]
            for gid in ids:
                self._check(gid)
            return compare_snapshots(self.port, ctx, ids, control)
        gid = str(args.get("graph_id") or "")
        self._check(gid)
        if operation == TOOL_STATS:
            return compute_statistics(self.port, ctx, gid, control)
        if operation == TOOL_SUBGRAPH:
            return paper_subgraph(self.port, ctx, gid, str(args.get("paper") or ""),
                                  max_nodes=int(args.get("max_nodes", 50)), max_edges=int(args.get("max_edges", 100)),
                                  control=control)
        if operation == TOOL_RECENT:
            return recent_entities(self.port, ctx, gid, since=str(args.get("since") or ""),
                                   limit=int(args.get("limit", 25)), control=control)
        raise ContractError(ErrorCode.INVALID_INPUT, f"unknown graph operation {operation!r}")

    def _runner(self, name: str) -> Callable[[Any], ToolResult]:
        def run(inv: Any) -> ToolResult:
            provenance_id = f"f2w-op:{uuid.uuid4().hex}"
            try:
                report = self.compute(inv.ctx, name, inv.args)
                store = self._store if self._store is not None else getattr(inv, "artifact_store", None)
                ref = store_report_artifact(store, inv.ctx, report)
            except ContractError as exc:
                return error_result(name, exc, provenance_id=provenance_id)
            except Exception as exc:  # noqa: BLE001 - typed result, no server detail
                if is_control_flow(exc):
                    raise
                return error_result(name, ContractError(ErrorCode.INTERNAL, f"{name} failed ({type(exc).__name__})"),
                                    provenance_id=provenance_id)
            return report_to_result(name, report, provenance_id=provenance_id, artifact_refs=(ref,) if ref else ())

        run.__name__ = f"run_{name}"
        return run


__all__: Tuple[str, ...] = (
    "ANALYZE_KNOWLEDGE_GRAPH_SKILL", "GRAPH_SPECS", "GraphOperationsTool", "GraphScan", "KG_COMPARE_SNAPSHOTS",
    "KG_COMPUTE_STATISTICS", "KG_PAPER_SUBGRAPH", "KG_RECENT_ENTITIES", "TOOL_COMPARE", "TOOL_RECENT",
    "TOOL_STATS", "TOOL_SUBGRAPH", "compare_snapshots", "compute_statistics", "graph_statistics",
    "legacy_growth_metrics", "legacy_structural_metrics", "paper_subgraph", "recent_entities", "scan_graph",
    "work_index",
)
