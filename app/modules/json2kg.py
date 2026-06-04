#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
json2kg.py -- Optimized conversion of extracted_terms JSON → MatKG graph.json

Features:
  - Precompiled regex for ID cleaning
  - Efficient list handling
  - Structured logging with configurable verbosity
  - Robust error handling
  - Type hints and concise docstrings
  - Full utilization of extracted term fields: formula, formula_validation, properties
  - Pytest test suite included below
"""
import json
import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

# Precompile regex pattern for performance
_CLEAN_PATTERN = re.compile(r"[^A-Za-z0-9\-]")


def make_id(term: str) -> str:
    """
    Convert a human-readable term into a MatKG node ID.

    - Prepends "matkg:"
    - Removes all characters except letters, digits, and hyphens
    - Removes spaces
    """
    cleaned = _CLEAN_PATTERN.sub("", term.replace(" ", ""))
    return f"matkg:{cleaned}"


def ensure_list(val: Any) -> List[Any]:
    """
    Guarantee that the return is a list:
      - None     → []
      - scalar   → [val]
      - list     → val
    """
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


def make_xray_node(snip: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build an XRayScatteringAnalysis node from an xray_code_snippets entry.

    Node ID is derived from source_paper + page + scattering_technique so each
    unique extraction gets its own node.
    """
    technique = snip.get("scattering_technique") or "XRayScattering"
    paper = snip.get("source_paper", "unknown")
    page = snip.get("page", 0)
    raw_id = f"{technique}_{paper}_p{page}"
    node_id = make_id(raw_id)
    name = f"{technique} analysis ({paper} p.{page})"

    return {
        "id": node_id,
        "name": name,
        "category": "XRayScatteringAnalysis",
        "type": "matkg:XRayScatteringAnalysis",
        "description": snip.get("code_description") or "",
        "pages": [page] if page else [],
        "source_papers": [paper] if paper else [],
        "context_snippets": [],
        "formula": "",
        "formula_validation": {},
        "properties": [],
        # XRayScatteringAnalysis-specific slots
        "scattering_technique": snip.get("scattering_technique"),
        "peak_positions": ensure_list(snip.get("peak_positions")),
        "d_spacing": ensure_list(snip.get("d_spacing")),
        "peak_assignments": ensure_list(snip.get("peak_assignments")),
        "code_snippet": snip.get("code_snippet"),
        "code_language": snip.get("code_language"),
        "code_description": snip.get("code_description"),
    }


def build_graph(
    raw_terms: Iterable[Dict[str, Any]],
    xray_code_snippets: List[Dict[str, Any]] | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Build a MatKG-compatible graph from raw term records and (optionally)
    xray_code_snippets produced by the x-ray scattering extraction pass.

    Returns a dict with keys:
      - "things": list of node dicts
      - "associations": list of edge dicts
    """
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()

    # Add XRayScatteringAnalysis nodes from code snippet extraction
    for snip in (xray_code_snippets or []):
        node = make_xray_node(snip)
        if node["id"] not in nodes:
            nodes[node["id"]] = node

    for term in raw_terms:
        name = term.get("term") or term.get("name") or "UNKNOWN"
        tid = make_id(name)

        # Create node if new
        if tid not in nodes:
            nodes[tid] = {
                "id": tid,
                "name": name,
                "category": term.get("category", "Unknown"),
                "description": term.get("definition", "") or "N/A",
                "pages": ensure_list(term.get("pages")),
                "source_papers": ensure_list(term.get("source_papers")),
                "context_snippets": ensure_list(term.get("context_snippets")),
                "formula": term.get("formula", "") or "",
                "formula_validation": term.get("formula_validation", {}) or {},
                "properties": ensure_list(term.get("properties")),
            }

        # Process relations
        for rel in ensure_list(term.get("relations")):
            tgt = rel.get("related_term")
            if not tgt:
                continue
            rid = make_id(tgt)

            # stub for unseen target
            if rid not in nodes:
                nodes[rid] = {
                    "id": rid,
                    "name": tgt,
                    "category": "Unknown",
                    "description": "",
                    "pages": [],
                    "source_papers": [],
                    "context_snippets": [],
                    "formula": "",
                    "formula_validation": {},
                    "properties": [],
                }

            pred = f"rel:{rel.get('relation', 'RELATED_TO')}"
            sig = (tid, pred, rid)
            if sig in seen:
                continue
            seen.add(sig)

            evidence = ensure_list(rel.get("evidence"))
            edges.append({
                "subject": tid,
                "predicate": pred,
                "object": rid,
                "has_evidence": "; ".join(evidence) if evidence else None,
            })

    return {"things": list(nodes.values()), "associations": edges}


def convert_terms_to_graph(input_json: Path, output_json: Path) -> Dict[str, Any]:
    """
    Convert extracted_terms JSON into MatKG graph JSON.

    Args:
        input_json: Path to input terms JSON
        output_json: Path where graph JSON will be written

    Returns:
        The constructed graph dict
    """
    with input_json.open("r", encoding="utf-8") as f:
        data = json.load(f)

    terms = data.get("terms") if isinstance(data, dict) and "terms" in data else data
    xray_snippets = data.get("xray_code_snippets", []) if isinstance(data, dict) else []
    graph = build_graph(terms, xray_code_snippets=xray_snippets)

    output_json.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    return graph


def parse_args() -> argparse.Namespace:
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Convert extracted_terms JSON → MatKG graph JSON"
    )
    parser.add_argument(
        "input_json", type=Path,
        help="Path to input JSON file"
    )
    parser.add_argument(
        "output_json", type=Path,
        help="Path to output graph JSON file"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Increase output verbosity"
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point for CLI."""
    args = parse_args()
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(stream=sys.stdout, level=level, format="%(levelname)s: %(message)s")

    try:
        with args.input_json.open("r", encoding="utf-8") as f:
            data = json.load(f)
        terms = data.get("terms") if isinstance(data, dict) and "terms" in data else data
        xray_snippets = data.get("xray_code_snippets", []) if isinstance(data, dict) else []
        graph = build_graph(terms, xray_code_snippets=xray_snippets)
        args.output_json.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
        logging.info(
            "Wrote %d nodes (%d xray) and %d edges → %s",
            len(graph["things"]),
            sum(1 for n in graph["things"] if n.get("category") == "XRayScatteringAnalysis"),
            len(graph["associations"]),
            args.output_json,
        )
    except Exception as e:
        logging.error("Failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()


# ----------------------- Pytest Test Suite -----------------------
# To run: pytest test_json2kg.py

def test_make_id_simple():
    assert make_id("P3HT") == "matkg:P3HT"
    assert make_id("Bulk Heterojunction OPV") == "matkg:BulkHeterojunctionOPV"
    assert make_id("pAQM-2TV") == "matkg:pAQM-2TV"


def test_ensure_list():
    assert ensure_list(None) == []
    assert ensure_list(5) == [5]
    assert ensure_list([1, 2, 3]) == [1, 2, 3]


def test_build_graph_fields():
    raw = [{
        "term": "X",
        "definition": "Def",
        "category": "Cat",
        "formula": "H2O",
        "formula_validation": {"status": "ok"},
        "properties": [{"property": "density", "value": 1}]
    }]
    graph = build_graph(raw)
    node = {n['id']: n for n in graph['things']}['matkg:X']
    assert node['formula'] == "H2O"
    assert node['formula_validation']['status'] == "ok"
    assert node['properties'][0]['property'] == "density"


def test_build_graph_minimal(tmp_path):
    raw = [{"term": "A", "relations": [{"related_term": "B", "relation": "TEST"}]}]
    graph = build_graph(raw)
    assert len(graph["things"]) == 2
    assert len(graph["associations"]) == 1
    edge = graph["associations"][0]
    assert edge["predicate"] == "rel:TEST"
    assert edge["has_evidence"] is None


def test_xray_code_snippet_node():
    """XRayScatteringAnalysis nodes are built from xray_code_snippets."""
    snips = [{
        "scattering_technique": "GIWAXS",
        "peak_positions": ["q = 0.38 A^-1"],
        "d_spacing": ["d = 16.5 A"],
        "peak_assignments": ["(100) lamellar peak"],
        "code_snippet": "peaks, _ = find_peaks(intensity)",
        "code_language": "python",
        "code_description": "Finds peaks in intensity profile.",
        "page": 3,
        "source_paper": "test_paper.pdf",
    }]
    graph = build_graph([], xray_code_snippets=snips)
    assert len(graph["things"]) == 1
    node = graph["things"][0]
    assert node["category"] == "XRayScatteringAnalysis"
    assert node["scattering_technique"] == "GIWAXS"
    assert node["code_snippet"] == "peaks, _ = find_peaks(intensity)"
    assert node["peak_positions"] == ["q = 0.38 A^-1"]


def test_cli(tmp_path):
    in_json = tmp_path / "in.json"
    out_json = tmp_path / "out.json"
    data = {"terms": [{"term": "X"}]}
    in_json.write_text(json.dumps(data))
    sys.argv = ["json2kg.py", str(in_json), str(out_json)]
    main()
    out = json.loads(out_json.read_text())
    assert "things" in out and "associations" in out
    assert len(out["things"]) == 1
    assert out["things"][0]["id"] == "matkg:X"
