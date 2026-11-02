"""Read helpers over versioned JSON KG files (T08A).

Wraps, does not replace, the existing readers:

* graph identity: ``multi_kg.graph_id_for_path`` for flat files. A versioned snapshot
  ``storage/kg/<corpus>/<vN>/graph.json`` (json_kg_contract.md section 4.1) is mapped to
  its flat compatibility name ``matkg_<corpus>_<vN>.json`` first, so it gets the same
  ``graph_id`` (``rsoxs_v4``, ``bl1101``...) and every snapshot does not collapse to ``graph``.
* payload tolerance: the same rule as ``kg_rag_api.KnowledgeGraph``:
  ``things or nodes`` and ``associations or edges``; nodes are dicts with a truthy ``id``;
  a repeated node id keeps the **last** record (dict-comprehension semantics there).
* both shape families in ``docs/migration/data_releases/kg_inventory.json``:
  ``things``/``associations`` (json2kg, legacy core-10, empty placeholders) and
  ``nodes``/``edges`` (``ingest_rsoxs_v3``).

Nothing is re-derived (no ``make_id``), normalized or rewritten: :func:`export_payload`
reproduces the stored structure, and for json2kg output :func:`serialize_like_json2kg`
reproduces the stored bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from app.modules.f2w_agent.multi_kg import graph_id_for_path

from .identity_adapter import assign_edge_ids
from .ports import ContractError, ErrorCode

FAMILY_THINGS = "things_associations"
FAMILY_NODES = "nodes_edges"


def graph_id_for_snapshot(path: Path) -> str:
    """``graph_id`` for a flat KG file or a ``<corpus>/<vN>/graph.json`` snapshot."""
    path = Path(path)
    if path.name == "graph.json" and path.parent.name and path.parent.parent.name:
        return graph_id_for_path(f"matkg_{path.parent.parent.name}_{path.parent.name}.json")
    return graph_id_for_path(str(path))


@dataclass
class GraphSnapshot:
    """One loaded KG file.

    Attributes:
        graph_id: Graph identity (see :func:`graph_id_for_snapshot`).
        path: Source file.
        sha256: Hash of the bytes as stored.
        family: :data:`FAMILY_THINGS` or :data:`FAMILY_NODES`.
        raw: The parsed document, unchanged.
        nodes: ``id -> node`` in file order (last duplicate wins, as ``KnowledgeGraph``).
        edges: ``(edge_id, edge)`` in file order.
        out_index / in_index: adjacency by node id -> edge ids.
    """

    graph_id: str
    path: Path
    sha256: str
    family: str
    raw: dict[str, Any]
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    out_index: dict[str, list[int]] = field(default_factory=dict)
    in_index: dict[str, list[int]] = field(default_factory=dict)

    @property
    def node_key(self) -> str:
        """Top-level key that holds nodes in this file."""
        return "things" if self.family == FAMILY_THINGS else "nodes"

    @property
    def edge_key(self) -> str:
        """Top-level key that holds edges in this file."""
        return "associations" if self.family == FAMILY_THINGS else "edges"


def _family(data: Mapping[str, Any]) -> str:
    if data.get("things") or ("things" in data and not data.get("nodes")):
        return FAMILY_THINGS
    if "nodes" in data:
        return FAMILY_NODES
    if "associations" in data:
        return FAMILY_THINGS
    raise ContractError(ErrorCode.INVALID_INPUT, "KG JSON has neither things/associations nor nodes/edges")


def load_snapshot(path: Path, *, graph_id: str | None = None) -> GraphSnapshot:
    """Load and index one KG file.

    Raises:
        ContractError: ``UNAVAILABLE`` if the file cannot be read (an unavailable graph is
            never reported as an empty one); ``INVALID_INPUT`` if it is not a KG document;
            ``CONFLICT`` if a sibling ``manifest.json`` names a different ``graph.sha256``.
    """
    path = Path(path)
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise ContractError(ErrorCode.UNAVAILABLE,
                            f"graph file {path.name} is unavailable: {exc.strerror}") from None
    digest = hashlib.sha256(blob).hexdigest()
    manifest = path.with_name("manifest.json")
    if path.name == "graph.json" and manifest.exists():
        try:
            expected = json.loads(manifest.read_text(encoding="utf-8"))["graph"]["sha256"]
        except (OSError, ValueError, KeyError, TypeError):
            raise ContractError(ErrorCode.INVALID_INPUT, f"unreadable manifest for {path}") from None
        if expected != digest:
            raise ContractError(ErrorCode.CONFLICT, f"{path} does not match its manifest graph.sha256")
    try:
        data = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ContractError(ErrorCode.INVALID_INPUT, f"{path.name} is not JSON") from None
    if not isinstance(data, dict):
        raise ContractError(ErrorCode.INVALID_INPUT, f"{path.name} is not a KG JSON object")
    snap = GraphSnapshot(graph_id=graph_id or graph_id_for_snapshot(path), path=path, sha256=digest,
                         family=_family(data), raw=data)
    raw_nodes = data.get("things") or data.get("nodes") or []
    raw_edges = data.get("associations") or data.get("edges") or []
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise ContractError(ErrorCode.INVALID_INPUT, f"{path.name} nodes/edges must be lists")
    snap.nodes = {n["id"]: n for n in raw_nodes if isinstance(n, dict) and n.get("id")}
    snap.edges = list(assign_edge_ids(e for e in raw_edges if isinstance(e, dict)))
    for idx, (_eid, edge) in enumerate(snap.edges):
        snap.out_index.setdefault(str(edge.get("subject")), []).append(idx)
        snap.in_index.setdefault(str(edge.get("object")), []).append(idx)
    return snap


def export_payload(snap: GraphSnapshot) -> dict[str, Any]:
    """The stored document, rebuilt from the loaded snapshot (round-trip check)."""
    return cast(dict[str, Any], json.loads(json.dumps(snap.raw, ensure_ascii=False)))


def serialize_like_json2kg(payload: Mapping[str, Any]) -> bytes:
    """Serialize with json2kg's rule (``indent=2, ensure_ascii=False``, no trailing newline)."""
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
