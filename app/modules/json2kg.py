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
import hashlib
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

# Precompile regex pattern for performance
_CLEAN_PATTERN = re.compile(r"[^A-Za-z0-9\-]")
_PUB_FIELDS = (
    "publication_year",
    "paper_title",
    "authors",
    "institutions",
    "doi",
    "journal",
    "volume",
    "issue",
    "pages_range",
    "abstract_text",
    "keywords",
)
# Overlay slots (e.g. RSoXS) and extractor extras that are not core MatKG node keys.
_PASSTHROUGH_SLOTS = (
    "importance",
    "photon_energy_eV",
    "absorption_edge",
    "scattering_technique",
    "polarization",
    "technique_type",
)
_TERM_INPUT_ONLY = {
    "term",
    "name",
    "definition",
    "relations",
    "source_paper",
    "paper_authors",
    "id_prefix",
    "related_id",
}
_KNOWN_ID_PREFIXES = ("matkg:", "beamline:")


def copy_passthrough_slots(term: Dict[str, Any], node: Dict[str, Any]) -> None:
    """Copy overlay/extra term fields onto a node without overwriting core keys."""
    for key in _PASSTHROUGH_SLOTS:
        if key in node:
            continue
        value = term.get(key)
        if value not in (None, "", [], {}):
            node[key] = value
    for key, value in term.items():
        if key in node or key in _TERM_INPUT_ONLY or key in _PUB_FIELDS:
            continue
        if value not in (None, "", [], {}):
            node[key] = value


def split_curie(value: str) -> Tuple[str, str] | None:
    """Return (prefix, local) if value already uses a known graph prefix."""
    text = (value or "").strip()
    for curie in _KNOWN_ID_PREFIXES:
        if text.startswith(curie):
            return curie[:-1], text[len(curie):]
    return None


def make_id(term: str, prefix: str = "matkg") -> str:
    """
    Convert a human-readable term into a graph node ID.

    - Prepends ``prefix`` (default ``matkg``)
    - Preserves ``matkg:`` / ``beamline:`` CURIEs
    - Removes all characters except letters, digits, and hyphens
    - Removes spaces
    """
    parsed = split_curie(term)
    if parsed:
        prefix, term = parsed
        cleaned = _CLEAN_PATTERN.sub("", term.replace(" ", "").replace("_", "-"))
        return f"{prefix}:{cleaned}"
    cleaned = _CLEAN_PATTERN.sub("", term.replace(" ", ""))
    return f"{prefix}:{cleaned}"


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


def normalize_domain_features(features: Any) -> List[Dict[str, Any]]:
    """Normalize schema-driven CodeSnippet domain feature entries."""
    normalized: List[Dict[str, Any]] = []
    for feature in ensure_list(features):
        if not isinstance(feature, dict):
            continue
        name = feature.get("feature_name")
        value = feature.get("feature_value")
        if not name or value in (None, "", []):
            continue
        normalized.append({
            "feature_name": str(name),
            "feature_value": str(value),
            "feature_units": feature.get("feature_units"),
            "feature_source_text": feature.get("feature_source_text"),
        })
    return normalized


def normalize_publications(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build embedded publication records without smearing scalar metadata."""
    explicit = record.get("publications")
    if isinstance(explicit, list):
        publications = []
        for pub in explicit:
            if not isinstance(pub, dict):
                continue
            source = str(pub.get("source_paper") or "").strip()
            clean = {
                key: pub.get(key)
                for key in _PUB_FIELDS
                if pub.get(key) not in (None, "", [])
            }
            if source:
                clean = {"source_paper": source, **clean}
            if clean:
                publications.append(clean)
        if publications:
            return sorted(publications, key=lambda p: str(p.get("source_paper", "")))

    source_meta = record.get("source_metadata") or {}
    if isinstance(source_meta, dict):
        publications = []
        for source in sorted(str(k) for k in source_meta if str(k).strip()):
            meta = source_meta.get(source)
            if not isinstance(meta, dict):
                continue
            clean = {
                key: meta.get(key)
                for key in _PUB_FIELDS
                if meta.get(key) not in (None, "", [])
            }
            publications.append({"source_paper": source, **clean})
        if publications:
            return publications

    source_papers = ensure_list(record.get("source_papers") or record.get("source_paper"))
    if len(source_papers) <= 1:
        clean = {
            key: record.get(key)
            for key in _PUB_FIELDS
            if record.get(key) not in (None, "", [])
        }
        if clean or source_papers:
            source = str(source_papers[0]) if source_papers else str(record.get("source_paper") or "")
            return [{"source_paper": source, **clean} if source else clean]
    return []


def _accept_code_snippet(snip: Dict[str, Any], code_body: str, strict: bool) -> bool:
    """Return True when a code snippet should become a graph node."""
    if not code_body:
        return False
    if snip.get("curated"):
        return True
    has_anchor = bool(
        snip.get("function_name")
        or re.search(r"\b(def |class |import )", code_body)
    )
    if len(code_body) < 150 or not has_anchor:
        return False
    if not strict:
        return True
    return (
        code_body.count("(") == code_body.count(")")
        and code_body.count("[") == code_body.count("]")
    )


def make_code_snippet_node(snip: Dict[str, Any], prefix: str = "matkg") -> Dict[str, Any]:
    """
    Build a CodeSnippet node from a code_snippets entry.

    Node ID includes MD5 hash of code body to prevent collisions.
    """
    fn_name = snip.get("function_name") or ""
    paper = snip.get("source_paper", "unknown")
    page = snip.get("page", 0)
    code_hash = hashlib.md5((snip.get("code_snippet") or "").encode()).hexdigest()[:8]
    explicit = snip.get("id")
    if explicit:
        node_id = make_id(str(explicit), prefix=snip.get("id_prefix") or prefix)
    else:
        raw_id = f"snippet_{fn_name}_{paper}_p{page}_{code_hash}" if fn_name else f"snippet_{paper}_p{page}_{code_hash}"
        node_id = make_id(raw_id, prefix=snip.get("id_prefix") or prefix)
    name = f"{fn_name} snippet" if fn_name else f"code snippet ({paper} p.{page})"

    return {
        "id": node_id,
        "name": name,
        "category": "CodeSnippet",
        "type": "matkg:CodeSnippet",
        "description": snip.get("code_description") or "",
        "pages": [page] if page else [],
        "source_papers": [paper] if paper else [],
        "context_snippets": [],
        "formula": "",
        "formula_validation": {},
        "properties": [],
        "publication_year": snip.get("publication_year"),
        "paper_title": snip.get("paper_title"),
        "source_metadata": snip.get("source_metadata", {}),
        "publications": normalize_publications(snip),
        "authors": ensure_list(snip.get("authors")),
        "paper_authors": ensure_list(snip.get("paper_authors")),
        "doi": snip.get("doi"),
        "function_name": fn_name or None,
        "code_snippet": snip.get("code_snippet"),
        "code_language": snip.get("code_language"),
        "code_description": snip.get("code_description"),
        "code_domain": snip.get("code_domain"),
        "domain_features": normalize_domain_features(snip.get("domain_features")),
        "source_type": snip.get("source_type"),
        "repo_url": snip.get("repo_url"),
        "repo_owner": snip.get("repo_owner"),
        "repo_name": snip.get("repo_name"),
        "repo_default_branch": snip.get("repo_default_branch"),
        "repo_commit_sha": snip.get("repo_commit_sha"),
        "source_file_path": snip.get("source_file_path"),
        "source_file_url": snip.get("source_file_url"),
        "source_start_line": snip.get("source_start_line"),
        "source_end_line": snip.get("source_end_line"),
        "repository_license": snip.get("repository_license"),
        "license_warning": snip.get("license_warning"),
        "source_score": snip.get("source_score"),
    }


def build_graph(
    raw_terms: Iterable[Dict[str, Any]],
    code_snippets: List[Dict[str, Any]] | None = None,
    *,
    default_id_prefix: str = "matkg",
    strict_snippets: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Build a MatKG-compatible graph from raw term records and (optionally)
    code_snippets produced by the code snippet extraction pass.

    Returns a dict with keys:
      - "things": list of node dicts
      - "associations": list of edge dicts
    """
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()

    # Add CodeSnippet nodes from code snippet extraction
    # Track snippet nodes by (source_paper, page) for wiring to term nodes later
    snippets_by_paper_page: Dict[Tuple[str, int], List[str]] = {}
    for snip in (code_snippets or []):
        code_body = (snip.get("code_snippet") or "").strip()
        if not _accept_code_snippet(snip, code_body, strict_snippets):
            logging.debug(
                "Skipping snippet (curated=%s, len=%d): %r",
                bool(snip.get("curated")),
                len(code_body),
                code_body[:60],
            )
            continue

        snippet_node = make_code_snippet_node(
            snip, prefix=snip.get("id_prefix") or default_id_prefix
        )
        if snippet_node["id"] not in nodes:
            nodes[snippet_node["id"]] = snippet_node
        paper = snip.get("source_paper", "")
        page = snip.get("page", 0)
        snippets_by_paper_page.setdefault((paper, page), []).append(snippet_node["id"])
        # Also index by paper-only for broader wiring
        snippets_by_paper_page.setdefault((paper, 0), []).append(snippet_node["id"])

    for term in raw_terms:
        name = term.get("term") or term.get("name") or "UNKNOWN"
        term_prefix = str(term.get("id_prefix") or default_id_prefix)
        tid = make_id(str(term.get("id") or name), prefix=term_prefix)

        # Terms mis-categorized as CodeSnippet by the LLM: demote to Unknown.
        # Real CodeSnippet nodes come exclusively from code_snippets.
        # Also remap removed XRayScatteringAnalysis → ExperimentalTechnique.
        if term.get("category") == "CodeSnippet":
            term = dict(term)
            term.setdefault("raw_category", "CodeSnippet")
            term["category"] = "Unknown"
        elif term.get("category") == "XRayScatteringAnalysis":
            term = dict(term)
            term.setdefault("raw_category", "XRayScatteringAnalysis")
            term["category"] = "ExperimentalTechnique"

        # Create node if new
        if tid not in nodes:
            nodes[tid] = {
                "id": tid,
                "name": name,
                "category": term.get("category", "Unknown"),
                "raw_category": term.get("raw_category"),
                "description": term.get("definition", "") or "N/A",
                "pages": ensure_list(term.get("pages")),
                "source_papers": ensure_list(term.get("source_papers")),
                "context_snippets": ensure_list(term.get("context_snippets")),
                "formula": term.get("formula", "") or "",
                "formula_validation": term.get("formula_validation", {}) or {},
                "properties": ensure_list(term.get("properties")),
                "publication_year": term.get("publication_year"),
                "paper_title": term.get("paper_title"),
                "source_metadata": term.get("source_metadata", {}),
                "publications": normalize_publications(term),
                "authors": ensure_list(term.get("authors")),
                "institutions": ensure_list(term.get("institutions")),
                "doi": term.get("doi"),
                "journal": term.get("journal"),
                "volume": term.get("volume"),
                "issue": term.get("issue"),
                "pages_range": term.get("pages_range"),
                "abstract_text": term.get("abstract_text"),
                "keywords": ensure_list(term.get("keywords")),
            }
            copy_passthrough_slots(term, nodes[tid])
        else:
            existing = nodes[tid]
            incoming = term.get("category", "Unknown")
            if existing.get("category") in (None, "", "Unknown") and incoming not in (None, "", "Unknown"):
                existing["category"] = incoming
                if term.get("raw_category"):
                    existing["raw_category"] = term.get("raw_category")
            if name and (
                not existing.get("name")
                or existing.get("name") == "UNKNOWN"
                or split_curie(str(existing.get("name") or ""))
            ):
                existing["name"] = name
            definition = term.get("definition") or ""
            if definition and existing.get("description") in (None, "", "N/A"):
                existing["description"] = definition
            papers = existing.setdefault("source_papers", [])
            for paper in ensure_list(term.get("source_papers")):
                if paper not in papers:
                    papers.append(paper)
            copy_passthrough_slots(term, existing)

        # Process relations
        for rel in ensure_list(term.get("relations")):
            tgt = rel.get("related_id") or rel.get("related_term")
            if not tgt:
                continue
            rid = make_id(str(tgt), prefix=term_prefix)

            # stub for unseen target
            if rid not in nodes:
                nodes[rid] = {
                    "id": rid,
                    "name": tgt,
                    "category": "Unknown",
                    "raw_category": None,
                    "description": "",
                    "pages": [],
                    "source_papers": [],
                    "context_snippets": [],
                    "formula": "",
                    "formula_validation": {},
                    "properties": [],
                    "publications": [],
                }

            pred = f"rel:{rel.get('relation', 'RELATED_TO')}"
            sig = (tid, pred, rid)
            if sig in seen:
                continue
            seen.add(sig)

            evidence = ensure_list(rel.get("evidence"))
            edge = {
                "subject": tid,
                "predicate": pred,
                "object": rid,
                "has_evidence": "; ".join(evidence) if evidence else None,
            }
            if rel.get("raw_predicate") or rel.get("raw_relation"):
                edge["raw_predicate"] = rel.get("raw_predicate") or rel.get("raw_relation")
            edges.append(edge)

    # Wire has_code_snippet edges: any term node from same paper gets linked
    # to CodeSnippet nodes from that paper. Prefer same-page match, fall back
    # to same-paper.
    wired_snippets: Set[str] = set()
    for nid, node in nodes.items():
        if node.get("category") == "CodeSnippet":
            continue
        node_papers = node.get("source_papers") or []
        node_pages = node.get("pages") or []
        for paper in node_papers:
            # try same-page first
            for pg in node_pages:
                for snip_id in snippets_by_paper_page.get((paper, pg), []):
                    sig = (nid, "rel:has_code_snippet", snip_id)
                    if sig not in seen:
                        seen.add(sig)
                        edges.append({
                            "subject": nid,
                            "predicate": "rel:has_code_snippet",
                            "object": snip_id,
                            "has_evidence": None,
                        })
                        wired_snippets.add(snip_id)
            # paper-level fallback for snippets not yet wired
            for snip_id in snippets_by_paper_page.get((paper, 0), []):
                if snip_id in wired_snippets:
                    continue
                sig = (nid, "rel:has_code_snippet", snip_id)
                if sig not in seen:
                    seen.add(sig)
                    edges.append({
                        "subject": nid,
                        "predicate": "rel:has_code_snippet",
                        "object": snip_id,
                        "has_evidence": None,
                    })
                    wired_snippets.add(snip_id)

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
    snippets = data.get("code_snippets", []) if isinstance(data, dict) else []
    meta = data.get("metadata") if isinstance(data, dict) else None
    prefix = "matkg"
    if isinstance(meta, dict) and meta.get("id_prefix"):
        prefix = str(meta["id_prefix"])
    graph = build_graph(terms, code_snippets=snippets, default_id_prefix=prefix)
    if isinstance(meta, dict) and meta:
        graph["metadata"] = meta

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
        snippets = data.get("code_snippets", []) if isinstance(data, dict) else []
        meta = data.get("metadata") if isinstance(data, dict) else None
        prefix = "matkg"
        if isinstance(meta, dict) and meta.get("id_prefix"):
            prefix = str(meta["id_prefix"])
        graph = build_graph(terms, code_snippets=snippets, default_id_prefix=prefix)
        if isinstance(meta, dict) and meta:
            graph["metadata"] = meta
        args.output_json.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
        logging.info(
            "Wrote %d nodes (%d snippets) and %d edges → %s",
            len(graph["things"]),
            sum(1 for n in graph["things"] if n.get("category") == "CodeSnippet"),
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


def test_code_snippet_node():
    """CodeSnippet nodes built from code_snippets and wired to term nodes from same paper."""
    terms = [{
        "term": "peak detection",
        "category": "ExperimentalTechnique",
        "definition": "Identification of scattering peaks in 1D intensity profiles",
        "pages": [1],
        "source_papers": ["SCIPY_DOCS.pdf"],
    }]
    snips = [{
        "function_name": "find_scattering_peaks",
        "code_snippet": "import numpy as np\nfrom scipy.signal import find_peaks, peak_widths, savgol_filter\ndef find_scattering_peaks(q, intensity):\n    y = np.asarray(intensity, dtype=float)\n    y_smooth = savgol_filter(y, window_length=11, polyorder=3)\n    peaks, props = find_peaks(y_smooth, prominence=0.05 * np.max(y_smooth))\n    return peaks, props",
        "code_language": "python",
        "code_description": "Processes 1D scattering data by smoothing to identify peaks.",
        "domain_features": [{
            "feature_name": "scattering_technique",
            "feature_value": "SAXS/WAXS",
            "feature_units": None,
            "feature_source_text": "Processes 1D scattering data",
        }],
        "page": 1,
        "source_paper": "SCIPY_DOCS.pdf",
    }]
    graph = build_graph(terms, code_snippets=snips)
    snippets = [n for n in graph["things"] if n["category"] == "CodeSnippet"]
    assert len(snippets) == 1
    snippet = snippets[0]

    assert "find_scattering_peaks" in snippet["code_snippet"]
    assert snippet["code_language"] == "python"
    assert snippet["function_name"] == "find_scattering_peaks"
    assert snippet["domain_features"][0]["feature_name"] == "scattering_technique"

    # edge wired from term node to snippet
    has_code_edges = [e for e in graph["associations"] if e["predicate"] == "rel:has_code_snippet"]
    assert len(has_code_edges) >= 1
    assert any(e["object"] == snippet["id"] for e in has_code_edges)


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
