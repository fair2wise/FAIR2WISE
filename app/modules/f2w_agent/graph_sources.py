"""Selectable, read-only graph sources for the web UI graph view (owner instruction 2026-11-06).

"In the UI we need to support reading both json KGs and Tiled KGs." The UI graph view reads one graph
through ``GET /graph`` (the workspace JSON file chosen by Settings). This module adds a second, opt-in way:
a request names a **source id** and the server answers from that source in the same node/edge wire shape.

Source ids
    ``json:<name>``
        A JSON KG file the server lists itself: ``storage/kg/*.json`` (and registered KG bundles) on every
        profile, plus the adopted release snapshots ``<workdir>/storage_kg/<corpus>/<vN>/graph.json`` on
        ``:local`` only.
    ``tiled:<graph_id>``
        One F2W literature graph (``f2w:kg/<graph_id>/...`` records) in the deployment's own managed Tiled,
        offered on ``:local`` only. Only *final* records are shown: pending, retired and the release marker
        are never nodes (the T08A reader rule), and entities of other owners (FORGE) are ignored.

Safety
    The id from a request is only ever a key into a catalog the server built itself from its own listings
    (:meth:`GraphSources.listing`). It is never joined to a path, parsed as a URL or used to pick a Tiled
    target; the Tiled client is fixed at startup and the credential never leaves it. A malformed or unknown
    id is ``unknown_source`` (404). Both kinds are read-only; edits stay refused for any ``source``.

Cost
    A Tiled read is one complete entity scan and one link scan, kept for :data:`TILED_TTL_SECONDS` and shared
    by every source and request in that window (a failed scan is remembered for :data:`TILED_ERROR_TTL_SECONDS`
    so a down Tiled is not hammered). JSON files are cached by ``(path, mtime, size)``, at most four parsed
    graphs. Every method is synchronous and blocking: route handlers call them through ``asyncio.to_thread``.
    A graph is cut at :data:`MAX_NODES` nodes (``truncated``); a JSON file over :data:`MAX_JSON_BYTES` is refused.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
import threading
import time
from typing import Protocol

from ..contracts.json_types import JsonObject
from ..graph import identity_adapter as ida
from ..graph import literature_writer as lw
from ..graph.snapshot_manifest import SNAPSHOT_ID_RE

logger = logging.getLogger(__name__)

#: Server-side bounds (documented in docs/migration/reviews/H8/ui_graph_sources.md).
MAX_NODES = 100_000
MAX_JSON_BYTES = 128 * 1024 * 1024
TILED_TTL_SECONDS = 20.0
TILED_ERROR_TTL_SECONDS = 3.0
JSON_CACHE_SIZE = 4
MAX_HOPS = 4
NEIGHBORHOOD_MAX_NODES = 500
MAX_SEARCH_RESULTS = 25

KIND_JSON = "json"
KIND_TILED = "tiled"
#: A source id as a request may spell it; anything else is refused before the catalog lookup.
SOURCE_ID_RE = re.compile(r"^(json|tiled):[A-Za-z0-9][A-Za-z0-9_.@/-]{0,119}$")
_NAME_CLEAN_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_CORPUS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_TILED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@/-]{0,119}$")
DEFAULT_PREDICATE = "rel:related_to"


class SourceError(Exception):
    """A typed, client-presentable failure of a source read.

    Args:
        code: Stable machine code (``unknown_source``, ``source_unavailable``, ``source_too_large``,
            ``source_unreadable``, ``source_inconsistent``, ``read_only_source``).
        status: HTTP status the route answers with.
        message: Safe text: never a path, a Tiled URL or a credential.
    """

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message

    def detail(self) -> dict[str, str]:
        """The ``detail`` object of the HTTP error body."""
        return {"code": self.code, "message": self.message}


def read_only_error() -> SourceError:
    """The refusal of any edit that names a source."""
    return SourceError("read_only_source", 400, "Graph sources are read-only; switch to the default graph to edit")


class GraphScanner(Protocol):
    """The two complete reads of a Tiled graph server this module needs (no write method is reachable)."""

    def scan_entities(self) -> list[JsonObject]:
        """Every entity row (``id name entityType uri properties``)."""
        ...

    def scan_links(self) -> list[JsonObject]:
        """Every link row (``id predicate subjectId objectId properties``)."""
        ...


@dataclass(frozen=True)
class JsonFile:
    """One JSON KG file the server listed: ``name`` is the id stem, ``graph_id`` the wire ``graph_id``."""

    name: str
    graph_id: str
    label: str
    path: Path


@dataclass(frozen=True)
class SourceGraph:
    """One loaded graph in raw MatKG form (``nodes`` as stored, ``edges`` as subject/predicate/object)."""

    source_id: str
    kind: str
    graph_id: str
    graph_label: str
    revision: str
    nodes: tuple[JsonObject, ...]
    edges: tuple[JsonObject, ...]
    truncated: bool = False
    by_id: Mapping[str, JsonObject] = field(default_factory=dict, repr=False)
    adjacency: Mapping[str, tuple[int, ...]] = field(default_factory=dict, repr=False)

    @property
    def data(self) -> JsonObject:
        """The graph in the new MatKG JSON form (``nodes``/``edges``) for the linked-code lookup."""
        return {"nodes": list(self.nodes), "edges": list(self.edges)}

    def subgraph(self, node_ids: Iterable[str]) -> SourceGraph:
        """The induced subgraph over ``node_ids`` (edges with both ends inside), same identity fields."""
        keep = [i for i in dict.fromkeys(node_ids) if i in self.by_id]
        inside = set(keep)
        nodes = tuple(self.by_id[i] for i in keep)
        edges = tuple(e for e in self.edges if e["subject"] in inside and e["object"] in inside)
        return build_source_graph(self.source_id, self.kind, self.graph_id, self.graph_label, self.revision,
                                  nodes, edges, cap=None)


@dataclass(frozen=True)
class SourceInfo:
    """One row of the ``GET /v1/graph/sources`` listing."""

    id: str
    kind: str
    label: str
    read_only: bool = True
    available: bool = True
    node_count: int | None = None
    edge_count: int | None = None
    revision: str | None = None
    detail: str | None = None
    #: The ``graph_id`` the Knowledge Operations (``GET /v1/operations/*``, T19E) know this graph by: the wire
    #: ``graph_id`` of a JSON file, the ``f2w_graph_id`` of a Tiled graph. Not a path and not a request key here.
    graph_id: str | None = None

    def as_json(self) -> JsonObject:
        """The wire form."""
        return {"id": self.id, "kind": self.kind, "label": self.label, "read_only": self.read_only,
                "available": self.available, "node_count": self.node_count, "edge_count": self.edge_count,
                "revision": self.revision, "detail": self.detail, "graph_id": self.graph_id}


@dataclass(frozen=True)
class SourceListing:
    """The selectable sources and the state of the Tiled side (``ok``, ``unavailable`` or ``not_offered``)."""

    sources: tuple[SourceInfo, ...]
    tiled_status: str
    tiled_detail: str | None = None

    def as_json(self) -> JsonObject:
        """The wire form."""
        return {"sources": [s.as_json() for s in self.sources],
                "tiled": {"status": self.tiled_status, "detail": self.tiled_detail}}


def build_source_graph(source_id: str, kind: str, graph_id: str, graph_label: str, revision: str,
                       nodes: Sequence[JsonObject], edges: Sequence[JsonObject], *,
                       cap: int | None = MAX_NODES) -> SourceGraph:
    """Index ``nodes``/``edges``; cut at ``cap`` nodes (``truncated``) keeping only edges between kept nodes."""
    truncated = cap is not None and len(nodes) > cap
    kept = tuple(nodes[:cap]) if truncated else tuple(nodes)
    by_id = {str(n["id"]): n for n in kept}
    edge_list = tuple(e for e in edges if e["subject"] in by_id and e["object"] in by_id) if truncated \
        else tuple(edges)
    adjacency: dict[str, list[int]] = {}
    for idx, edge in enumerate(edge_list):
        adjacency.setdefault(edge["subject"], []).append(idx)
        if edge["object"] != edge["subject"]:
            adjacency.setdefault(edge["object"], []).append(idx)
    return SourceGraph(source_id, kind, graph_id, graph_label, revision, kept, edge_list, truncated, by_id,
                       {k: tuple(v) for k, v in adjacency.items()})


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_json_graph(data: object) -> tuple[list[JsonObject], list[JsonObject]]:
    """Nodes and ``subject/predicate/object`` edges of an old (``things``/``associations``) or new MatKG file."""
    if not isinstance(data, dict):
        return [], []
    raw_nodes = data.get("things") or data.get("nodes") or []
    raw_edges = data.get("associations") or data.get("edges") or []
    nodes = [n for n in raw_nodes if isinstance(n, dict) and _text(n.get("id"))] \
        if isinstance(raw_nodes, list) else []
    for node in nodes:
        node["id"] = _text(node["id"])
    edges: list[JsonObject] = []
    if isinstance(raw_edges, list):
        for raw in raw_edges:
            if not isinstance(raw, dict):
                continue
            subject = _text(raw.get("subject") or raw.get("source"))
            target = _text(raw.get("object") or raw.get("target"))
            if subject and target:
                edges.append({"subject": subject, "predicate": _text(raw.get("predicate")) or DEFAULT_PREDICATE,
                              "object": target})
    return nodes, edges


# --------------------------------------------------------------------------- Tiled mapping

@dataclass(frozen=True)
class TiledGraph:
    """One F2W literature graph found in a Tiled scan."""

    graph_id: str
    revision: str
    label: str
    nodes: tuple[JsonObject, ...]
    edges: tuple[JsonObject, ...]
    conflict: str | None = None


def tiled_node(row: Mapping[str, object], node_id: str) -> JsonObject:
    """A raw MatKG node from a final entity row: the stored node payload, topped up from the row fields."""
    props = row.get("properties")
    raw: JsonObject = ida.strip_reserved(props if isinstance(props, Mapping) else None)
    raw["id"] = node_id
    if not _text(raw.get("name")) and not _text(raw.get("label")) and _text(row.get("name")):
        raw["name"] = _text(row.get("name"))
    if not any(_text(raw.get(k)) for k in ("category", "type", "entity_type", "entityType")) \
            and _text(row.get("entityType")):
        raw["category"] = _text(row.get("entityType"))
    return raw


def _marker_revision(marker: Mapping[str, object] | None) -> tuple[str, str]:
    """``(revision, release name)`` from a release marker row (``unversioned``/empty without one)."""
    props = marker.get("properties") if marker else None
    if not isinstance(props, Mapping):
        return "unversioned", ""
    snapshot = _text(props.get("snapshot_id"))
    sha = _text(props.get("graph_sha256"))
    revision = f"{snapshot}@{sha[:12]}" if snapshot and sha else (snapshot or "unversioned")
    return revision, snapshot


def build_tiled_graphs(entities: Sequence[Mapping[str, object]],
                       links: Sequence[Mapping[str, object]]) -> dict[str, TiledGraph]:
    """Group a complete scan into F2W literature graphs (final records only).

    Rules: an entity is a node of graph ``G`` only when :func:`identity_adapter.literature_entity_key` accepts it
    (owner marker, URI and properties agree); pending, retired and release-marker rows are not nodes; a link is an
    edge only when it is owned by ``G`` and both of its endpoints are final nodes of ``G``. Two final entities for
    one node (0.2.18 has no uniqueness) mark the graph ``conflict`` instead of picking one.
    """
    nodes: dict[str, dict[str, JsonObject]] = {}
    tiled_ids: dict[str, dict[str, str]] = {}
    markers: dict[str, Mapping[str, object]] = {}
    conflicts: dict[str, str] = {}
    for row in entities:
        props = row.get("properties")
        if not isinstance(props, Mapping) or props.get(ida.OWNER_KEY) != ida.LITERATURE_OWNER:
            continue
        key = ida.literature_entity_key(row)
        if key is not None:
            gid, node_id = key
            if not _TILED_ID_RE.match(gid):
                continue
            tid = str(row.get("id") or "")
            if node_id in nodes.setdefault(gid, {}):
                conflicts[gid] = f"two Tiled entities claim node {node_id!r}"
                continue
            nodes[gid][node_id] = tiled_node(row, node_id)
            tiled_ids.setdefault(gid, {})[tid] = node_id
            continue
        marker_gid = props.get(ida.GRAPH_KEY)
        if (isinstance(marker_gid, str) and row.get("uri") == lw.release_uri(marker_gid)
                and props.get(ida.ENTITY_KEY) == lw.RELEASE_NODE):
            if marker_gid in markers:
                conflicts[marker_gid] = "two live release markers"
            markers[marker_gid] = row
    edges: dict[str, list[JsonObject]] = {gid: [] for gid in nodes}
    for link in links:
        key = ida.literature_link_key(link)
        if key is None or key[0] not in nodes:
            continue
        gid = key[0]
        ids = tiled_ids[gid]
        subject, target = ids.get(str(link.get("subjectId"))), ids.get(str(link.get("objectId")))
        if subject is None or target is None:
            continue
        edges[gid].append({"subject": subject, "predicate": _text(link.get("predicate")) or DEFAULT_PREDICATE,
                           "object": target})
    out: dict[str, TiledGraph] = {}
    for gid in sorted(nodes):
        revision, release = _marker_revision(markers.get(gid))
        label = f"{gid} / {release}" if release else gid
        out[gid] = TiledGraph(gid, revision, label, tuple(nodes[gid].values()), tuple(edges[gid]),
                              conflicts.get(gid))
    return out


@dataclass
class _TiledScan:
    taken: float
    ttl: float
    graphs: dict[str, TiledGraph]
    error: SourceError | None = None


# --------------------------------------------------------------------------- the catalog

@dataclass(frozen=True)
class _Entry:
    info: SourceInfo
    json_file: JsonFile | None = None
    tiled_id: str | None = None


class GraphSources:
    """Server-built catalog of the selectable UI graph sources and the reads over them.

    Args:
        flat_json: Lists the JSON KG files of the workspace (already resolved and allowlisted by the caller).
        release_root: ``<workdir>/storage_kg`` whose ``<corpus>/<vN>/graph.json`` releases are offered, or ``None``.
        tiled: Read-only scanner of the deployment's own Tiled, or ``None`` (not offered).
        clock: Monotonic seconds, injected by tests.
        tiled_ttl: Seconds a complete Tiled scan is reused.
    """

    def __init__(self, *, flat_json: Callable[[], Sequence[JsonFile]], release_root: Path | None = None,
                 tiled: GraphScanner | None = None, clock: Callable[[], float] = time.monotonic,
                 tiled_ttl: float = TILED_TTL_SECONDS) -> None:
        self._flat_json = flat_json
        self._release_root = release_root
        self._tiled = tiled
        self._clock = clock
        self._tiled_ttl = tiled_ttl
        self._lock = threading.Lock()
        self._scan: _TiledScan | None = None
        self._json_cache: OrderedDict[tuple[str, int, int], SourceGraph] = OrderedDict()

    # -- wiring
    def attach_tiled(self, scanner: GraphScanner) -> None:
        """Install the trusted read-only Tiled scanner (startup wiring only; never from a request)."""
        self._tiled = scanner
        self._scan = None

    @property
    def tiled_offered(self) -> bool:
        """Whether a Tiled scanner is wired."""
        return self._tiled is not None

    # -- JSON files
    def _json_files(self) -> list[tuple[str, JsonFile]]:
        entries: list[tuple[str, JsonFile]] = []
        used: set[str] = set()
        for item in self._flat_json():
            stem = _NAME_CLEAN_RE.sub("_", item.name).strip("_.-") or "graph"
            sid = base = f"json:{stem}"
            n = 2
            while sid in used:
                sid, n = f"{base}-{n}", n + 1
            used.add(sid)
            entries.append((sid, item))
        root = self._release_root
        if root is not None and root.is_dir() and not root.is_symlink():
            for graph in sorted(root.glob("*/*/graph.json")):
                snap_dir, corpus_dir = graph.parent, graph.parent.parent
                if (graph.is_symlink() or snap_dir.is_symlink() or corpus_dir.is_symlink()
                        or not _CORPUS_RE.match(corpus_dir.name) or not SNAPSHOT_ID_RE.match(snap_dir.name)
                        or not graph.is_file()):
                    continue
                sid = f"json:{corpus_dir.name}/{snap_dir.name}"
                entries.append((sid, JsonFile(f"{corpus_dir.name}/{snap_dir.name}", corpus_dir.name,
                                              f"{corpus_dir.name} / {snap_dir.name} (release)", graph)))
        return entries

    @staticmethod
    def _json_key(path: Path) -> tuple[str, int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return (str(path), st.st_mtime_ns, st.st_size)

    def _load_json(self, sid: str, item: JsonFile) -> SourceGraph:
        key = self._json_key(item.path)
        if key is None:
            raise SourceError("source_unreadable", 502, "The graph file could not be read")
        with self._lock:
            cached = self._json_cache.get(key)
            if cached is not None:
                self._json_cache.move_to_end(key)
                return cached
        if key[2] > MAX_JSON_BYTES:
            raise SourceError("source_too_large", 413, "The graph file is too large to open in the viewer")
        try:
            with item.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            raise SourceError("source_unreadable", 502, "The graph file could not be read") from None
        nodes, edges = normalize_json_graph(data)
        graph = build_source_graph(sid, KIND_JSON, item.graph_id, item.label, f"{key[1]}:{key[2]}", nodes, edges)
        with self._lock:
            self._json_cache[key] = graph
            while len(self._json_cache) > JSON_CACHE_SIZE:
                self._json_cache.popitem(last=False)
        return graph

    def _cached_json_counts(self, item: JsonFile) -> tuple[int | None, int | None, str | None]:
        key = self._json_key(item.path)
        with self._lock:
            graph = self._json_cache.get(key) if key else None
        return (len(graph.nodes), len(graph.edges), graph.revision) if graph else (None, None, None)

    # -- Tiled
    def _tiled_scan(self) -> _TiledScan:
        scanner = self._tiled
        assert scanner is not None
        with self._lock:
            current = self._scan
            if current is not None and self._clock() - current.taken < current.ttl:
                return current
            try:
                scan = _TiledScan(self._clock(), self._tiled_ttl,
                                  build_tiled_graphs(scanner.scan_entities(), scanner.scan_links()))
            except Exception as exc:  # any scanner failure is "unavailable", never an empty graph
                logger.warning("Tiled graph source scan failed: %s", type(exc).__name__)
                scan = _TiledScan(self._clock(), TILED_ERROR_TTL_SECONDS, {},
                                  SourceError("source_unavailable", 503, "Tiled graph is unavailable"))
            self._scan = scan
            return scan

    # -- catalog
    def _json_entries(self) -> dict[str, _Entry]:
        entries: dict[str, _Entry] = {}
        for sid, item in self._json_files():
            nodes, edges, revision = self._cached_json_counts(item)
            entries[sid] = _Entry(SourceInfo(sid, KIND_JSON, item.label, node_count=nodes, edge_count=edges,
                                             revision=revision, graph_id=item.graph_id), json_file=item)
        return entries

    @staticmethod
    def _tiled_entries(scan: _TiledScan) -> dict[str, _Entry]:
        entries: dict[str, _Entry] = {}
        for gid, tg in scan.graphs.items():
            sid = f"tiled:{gid}"
            entries[sid] = _Entry(SourceInfo(
                sid, KIND_TILED, tg.label, available=tg.conflict is None, node_count=len(tg.nodes),
                edge_count=len(tg.edges), revision=tg.revision,
                detail=None if tg.conflict is None else "inconsistent records in Tiled", graph_id=gid),
                tiled_id=gid)
        return entries

    def listing(self) -> SourceListing:
        """The selectable sources (JSON files and releases first, then Tiled graphs) and the Tiled status."""
        entries = self._json_entries()
        if self._tiled is None:
            return SourceListing(tuple(e.info for e in entries.values()), "not_offered",
                                 "Tiled is not offered by this deployment")
        scan = self._tiled_scan()
        if scan.error is not None:
            return SourceListing(tuple(e.info for e in entries.values()), "unavailable", scan.error.message)
        entries.update(self._tiled_entries(scan))
        return SourceListing(tuple(e.info for e in entries.values()), "ok")

    # -- reads
    def graph(self, source_id: str) -> SourceGraph:
        """The whole graph of ``source_id`` (cut at :data:`MAX_NODES`).

        Raises:
            SourceError: ``unknown_source`` for a malformed or unlisted id; the load errors otherwise.
        """
        if not isinstance(source_id, str) or not SOURCE_ID_RE.match(source_id):
            raise SourceError("unknown_source", 404, "Unknown graph source")
        if source_id.startswith("json:"):
            entry = self._json_entries().get(source_id)
            if entry is None or entry.json_file is None:
                raise SourceError("unknown_source", 404, "Unknown graph source")
            return self._load_json(source_id, entry.json_file)
        if self._tiled is None:
            raise SourceError("unknown_source", 404, "Unknown graph source")
        scan = self._tiled_scan()
        if scan.error is not None:
            raise SourceError("source_unavailable", 503, "Tiled graph is unavailable")
        tg = scan.graphs.get(source_id.removeprefix("tiled:"))
        if tg is None:
            raise SourceError("unknown_source", 404, "Unknown graph source")
        if tg.conflict is not None:
            raise SourceError("source_inconsistent", 502, "The Tiled graph has inconsistent records")
        return build_source_graph(source_id, KIND_TILED, tg.graph_id, f"{tg.graph_id} (Tiled)", tg.revision,
                                  tg.nodes, tg.edges)

    @staticmethod
    def search(graph: SourceGraph, query: str, limit: int) -> list[tuple[JsonObject, float]]:
        """Lexical ranking over label, id and description (exact label 1.0 ... description 0.3), best first."""
        needle = query.strip().lower()
        if not needle:
            return []
        scored: list[tuple[float, int, JsonObject]] = []
        for idx, node in enumerate(graph.nodes):
            label = _text(node.get("name") or node.get("label")).lower()
            node_id = str(node["id"]).lower()
            description = _text(node.get("description") or node.get("definition")).lower()
            if label == needle:
                score = 1.0
            elif label.startswith(needle):
                score = 0.8
            elif needle in label:
                score = 0.6
            elif needle in node_id:
                score = 0.5
            elif needle in description:
                score = 0.3
            else:
                continue
            scored.append((score, idx, node))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [(node, score) for score, _idx, node in scored[:max(1, min(limit, MAX_SEARCH_RESULTS))]]

    @staticmethod
    def neighborhood(graph: SourceGraph, node_id: str, hops: int) -> SourceGraph | None:
        """Undirected breadth-first neighborhood (``hops`` clamped to 1..:data:`MAX_HOPS`, capped nodes)."""
        if node_id not in graph.by_id:
            return None
        hops = max(1, min(int(hops), MAX_HOPS))
        seen = {node_id: 0}
        order = [node_id]
        queue: deque[str] = deque([node_id])
        while queue and len(order) < NEIGHBORHOOD_MAX_NODES:
            current = queue.popleft()
            if seen[current] >= hops:
                continue
            for idx in graph.adjacency.get(current, ()):
                edge = graph.edges[idx]
                other = edge["object"] if edge["subject"] == current else edge["subject"]
                if other not in seen and other in graph.by_id and len(order) < NEIGHBORHOOD_MAX_NODES:
                    seen[other] = seen[current] + 1
                    order.append(other)
                    queue.append(other)
        return graph.subgraph(order)


__all__ = ("DEFAULT_PREDICATE", "GraphScanner", "GraphSources", "JsonFile", "MAX_NODES", "SOURCE_ID_RE",
           "SourceError", "SourceGraph", "SourceInfo", "SourceListing", "TiledGraph", "build_source_graph",
           "build_tiled_graphs", "normalize_json_graph", "read_only_error", "tiled_node")
