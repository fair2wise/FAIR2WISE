"""Synthetic KG payloads (no paper text) and a test-only Tiled seeder (T08A).

The seeder writes F2W graphs into a *disposable* test server with the mapping in
``app/modules/graph/identity_adapter.py``. It is a test helper, not the T08B importer: no
plan, journal, receipt or verification.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from app.modules.graph import identity_adapter as ida

REPO = Path(__file__).resolve().parents[2]
JSON_KG_EXPECTED = REPO / "tests" / "contracts" / "json_kg" / "fixtures" / "expected"


def things_payload() -> Dict[str, Any]:
    """json2kg-family payload with publication fields, a dangling target and a duplicate triple."""
    return {
        "things": [
            {"id": "matkg:P3HT", "name": "P3HT", "category": "Material", "description": "polymer",
             "source_papers": ["paper_a.pdf"], "publications": [{"source_paper": "paper_a.pdf", "doi": "10.1/a"}],
             "context_snippets": [{"text": "synthetic", "source_paper": "paper_a.pdf", "page": 1}],
             "confidence": 0.7},
            {"id": "matkg:RSoXS", "name": "RSoXS", "category": "ExperimentalTechnique", "description": "N/A",
             "source_papers": ["paper_a.pdf", "paper_b.pdf"]},
            {"id": "matkg:OPV", "name": "Organic photovoltaics", "category": "Application",
             "source_papers": ["paper_b.pdf"]},
            {"id": "matkg:Isolated", "name": "Isolated", "category": "Unknown", "source_papers": []},
        ],
        "associations": [
            {"subject": "matkg:P3HT", "predicate": "rel:STUDIED_BY", "object": "matkg:RSoXS",
             "has_evidence": None},
            {"subject": "matkg:RSoXS", "predicate": "rel:USED_IN", "object": "matkg:OPV", "has_evidence": "e1"},
            {"subject": "matkg:OPV", "predicate": "rel:RELATED_TO", "object": "matkg:Ghost", "has_evidence": None},
        ],
        "metadata": {"id_prefix": "matkg", "note": "synthetic"},
    }


def nodes_payload() -> Dict[str, Any]:
    """``ingest_rsoxs_v3``-family payload (``nodes``/``edges``, ``label``, ``papers``, own ``graph_id``)."""
    return {
        "graph_id": "rsoxs_v3",
        "corpus_revision": "r1",
        "nodes": [
            {"id": "rsoxs:PSbPMMA", "label": "PS-b-PMMA", "entityType": "Material", "graph_id": "legacy-gid",
             "papers": ["p1"], "slots": {"x": 1}, "aliases": ["PS-PMMA"]},
            {"id": "rsoxs:RSoXS", "label": "RSoXS", "entityType": "Technique", "graph_id": "legacy-gid",
             "papers": ["p1"], "slots": {}, "aliases": []},
        ],
        "edges": [
            {"subject": "rsoxs:PSbPMMA", "predicate": "rel:measured_by", "object": "rsoxs:RSoXS", "type": "t"},
            {"subject": "rsoxs:PSbPMMA", "predicate": "rel:measured_by", "object": "rsoxs:RSoXS", "type": "t2"},
        ],
        "prov_metadata": {"generated": "synthetic"},
    }


def write(path: Path, payload: Dict[str, Any]) -> Path:
    """Write ``payload`` with the json2kg serializer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


CREATE_ENTITY = "mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }"
CREATE_LINK = "mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }"


def seed_graph(instance: Any, graph_id: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """Write ``payload`` into a disposable Tiled server; returns ``node_id -> tiled UUID``."""
    nodes = payload.get("things") or payload.get("nodes") or []
    edges = payload.get("associations") or payload.get("edges") or []
    ids: Dict[str, str] = {}
    for node in nodes:
        props = ida.entity_properties(graph_id, node["id"], node)
        row = instance.graphql(CREATE_ENTITY, {"i": {
            "entityType": str(node.get("category") or node.get("entityType") or "Unknown"),
            "name": str(node.get("name") or node.get("label") or ""),
            "uri": ida.entity_uri(graph_id, node["id"]), "properties": props}})["createEntity"]
        ids[node["id"]] = row["id"]
    for eid, edge in ida.assign_edge_ids(edges):
        if edge["subject"] not in ids or edge["object"] not in ids:
            continue  # dangling targets have no entity to link to
        instance.graphql(CREATE_LINK, {"i": {"subjectId": ids[edge["subject"]], "predicate": edge["predicate"],
                                             "objectId": ids[edge["object"]],
                                             "properties": ida.link_properties(graph_id, eid, edge)}})
    return ids
