"""Desired Tiled records for an F2W literature snapshot (T08B; catalog K-01, rewritten).

K-01 (``kg_writer.py``) wrote FORGE-shaped technique/material/publication upserts. F2W maps its own
json2kg payload instead: one entity per node, one link per edge, with the T08A identity mapping
(:mod:`.identity_adapter`). Nothing here talks to Tiled, parses text or derives ids; it is the pure
"what should exist" half of the importer, and the ownership classification of what does exist.

Adapted from als-computing/forge_rsoxs_analysis src/als_knowledge_agent/graph/kg_writer.py (BSD-3-Clause).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote

from . import identity_adapter as ida

RETIRED = "retired"
PENDING = "pending"


def canonical(value: Any) -> str:
    """Canonical JSON used to compare stored and desired properties."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class EntitySpec:
    """Desired entity."""

    node_id: str
    entity_type: str
    name: str
    uri: str
    properties: Dict[str, Any]


@dataclass(frozen=True)
class LinkSpec:
    """Desired link between two node ids."""

    edge_id: str
    subject: str
    predicate: str
    object: str
    properties: Dict[str, Any]


def entity_spec(graph_id: str, node: Mapping[str, Any]) -> EntitySpec:
    """Same field mapping as the T08A reader expects (``category``/``entityType``, ``name``/``label``)."""
    node_id = node["id"]
    return EntitySpec(
        node_id=node_id,
        entity_type=str(node.get("category") or node.get("entityType") or "Unknown"),
        name=str(node.get("name") or node.get("label") or ""),
        uri=ida.entity_uri(graph_id, node_id),
        properties=ida.entity_properties(graph_id, node_id, node))


def link_spec(graph_id: str, edge_id: str, edge: Mapping[str, Any]) -> LinkSpec:
    """Desired link for one snapshot edge (payload minus endpoints, plus ownership markers)."""
    return LinkSpec(edge_id, edge["subject"], str(edge["predicate"]), edge["object"],
                    ida.link_properties(graph_id, edge_id, edge))


def state_uri(graph_id: str, node_id: str, state: str, plan_tag: str) -> str:
    """Return the URI of an in-flight (``pending``) or superseded (``retired``) revision.

    Not a literature URI, so the T08A readers treat such an entity as foreign and never return it.
    """
    return f"{ida.URI_PREFIX}{quote(graph_id, safe='')}/{state}/{quote(node_id, safe='')}/{plan_tag}"


def classify_entity(row: Mapping[str, Any], graph_id: str) -> Optional[str]:
    """``final``/``pending``/``retired`` for an entity this graph owns, ``None`` for anything else.

    Raises:
        ValueError: The row carries this graph's owner markers but a URI that fits no state.
    """
    props = row.get("properties")
    if not isinstance(props, Mapping) or props.get(ida.OWNER_KEY) != ida.LITERATURE_OWNER \
            or props.get(ida.GRAPH_KEY) != graph_id:
        return None
    uri = row.get("uri") or ""
    if ida.parse_entity_uri(uri) == (graph_id, props.get(ida.ENTITY_KEY)):
        return "final"
    prefix = f"{ida.URI_PREFIX}{quote(graph_id, safe='')}/"
    for state in (PENDING, RETIRED):
        if uri.startswith(prefix + state + "/"):
            return state
    raise ValueError(f"entity {row.get('id')} claims graph {graph_id!r} with an unrecognized uri")


def link_owned(row: Mapping[str, Any], graph_id: str) -> Optional[str]:
    """The edge id of an owned link of ``graph_id``, else ``None``."""
    key = ida.literature_link_key(row)
    return key[1] if key is not None and key[0] == graph_id else None


def entity_matches(row: Mapping[str, Any], spec: EntitySpec) -> bool:
    """Stored entity equals the desired one (type, name, every property)."""
    return (row.get("entityType") == spec.entity_type and (row.get("name") or "") == spec.name
            and canonical(row.get("properties") or {}) == canonical(spec.properties))


def link_matches(row: Mapping[str, Any], spec: LinkSpec, subject_tid: str, object_tid: str) -> bool:
    """Stored link equals the desired one between the given entity UUIDs."""
    return (row.get("subjectId") == subject_tid and row.get("objectId") == object_tid
            and row.get("predicate") == spec.predicate
            and canonical(row.get("properties") or {}) == canonical(spec.properties))
