#!/usr/bin/env python3
"""Convert extracted_terms JSON into a MatKG graph.json.

json2kg.py -- Optimized conversion of extracted_terms JSON → MatKG graph.json

Features:
  - Precompiled regex for ID cleaning
  - Efficient list handling
  - Structured logging with configurable verbosity
  - Robust error handling
  - Type hints and concise docstrings
  - Full utilization of extracted term fields: formula, formula_validation, properties
"""
import argparse
from collections.abc import Callable, Iterable
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
from typing import Any, cast

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


def copy_passthrough_slots(term: dict[str, Any], node: dict[str, Any]) -> None:
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


def split_curie(value: str) -> tuple[str, str] | None:
    """Return (prefix, local) if value already uses a known graph prefix."""
    text = (value or "").strip()
    for curie in _KNOWN_ID_PREFIXES:
        if text.startswith(curie):
            return curie[:-1], text[len(curie):]
    return None


def make_id(term: str, prefix: str = "matkg") -> str:
    """Convert a human-readable term into a graph node ID.

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


def _union_by(existing: list[Any], incoming: Iterable[Any], key: Callable[[Any], Any]) -> None:
    """Append the ``incoming`` items whose ``key`` is not in ``existing`` yet (order kept, nothing replaced)."""
    seen = {key(item) for item in existing}
    for item in incoming:
        k = key(item)
        if k not in seen:
            seen.add(k)
            existing.append(item)


def _snippet_key(snippet: Any) -> tuple[Any, ...]:  # noqa: ANN401 - a snippet is a dict from JSON (or junk)
    if isinstance(snippet, dict):
        return (snippet.get("source_paper"), snippet.get("page"), snippet.get("text"))
    return ("?", None, repr(snippet))


def merge_term_into_node(term: dict[str, Any], node: dict[str, Any]) -> None:
    """Carry what another record of the same term adds into the node that already exists (nothing is overwritten).

    A node exists before its term is processed when an earlier term named it as a relation target (a stub with empty
    lists) or when the terms file lists the id twice. The later record used to add only category, name, description and
    papers, so its supporting passages, pages, properties, publications and metadata were silently dropped. Now list
    fields are unioned (passages by paper, page and text; pages; properties; publications by source paper), the
    source metadata dict is completed, and scalar fields are filled only where the node has none.
    """
    for list_key in ("pages", "authors", "institutions", "keywords"):
        _union_by(node.setdefault(list_key, []), ensure_list(term.get(list_key)), lambda v: json.dumps(v, sort_keys=True))
    _union_by(node.setdefault("context_snippets", []), ensure_list(term.get("context_snippets")), _snippet_key)
    _union_by(node.setdefault("properties", []), ensure_list(term.get("properties")),
              lambda v: json.dumps(v, sort_keys=True, default=str))
    pubs = node.setdefault("publications", [])
    for pub in normalize_publications(term):
        match = next((p for p in pubs if p.get("source_paper") == pub.get("source_paper")), None)
        if match is None:
            pubs.append(pub)
        else:
            for k, v in pub.items():
                if v not in (None, "", [], {}) and match.get(k) in (None, "", [], {}):
                    match[k] = v
    metadata = node.setdefault("source_metadata", {})
    if isinstance(metadata, dict) and isinstance(term.get("source_metadata"), dict):
        for paper, meta in term["source_metadata"].items():
            metadata.setdefault(paper, meta)
    for scalar in ("formula", "publication_year", "paper_title", "doi", "journal", "volume", "issue", "pages_range",
                   "abstract_text"):
        if node.get(scalar) in (None, "") and term.get(scalar) not in (None, ""):
            node[scalar] = term[scalar]
    if not node.get("formula_validation") and term.get("formula_validation"):
        node["formula_validation"] = term["formula_validation"]


def ensure_list(val: object) -> list[Any]:
    """Guarantee that the return is a list.

    - None     → []
    - scalar   → [val]
    - list     → val
    """
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


def normalize_domain_features(features: object) -> list[dict[str, Any]]:
    """Normalize schema-driven CodeSnippet domain feature entries."""
    normalized: list[dict[str, Any]] = []
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


def normalize_publications(record: dict[str, Any]) -> list[dict[str, Any]]:
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


def _accept_code_snippet(snip: dict[str, Any], code_body: str, strict: bool) -> bool:
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


def make_code_snippet_node(snip: dict[str, Any], prefix: str = "matkg") -> dict[str, Any]:
    """Build a CodeSnippet node from a code_snippets entry.

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
    raw_terms: Iterable[dict[str, Any]],
    code_snippets: list[dict[str, Any]] | None = None,
    *,
    default_id_prefix: str = "matkg",
    strict_snippets: bool = True,
    merge_existing: bool = False,
) -> dict[str, Any]:
    """Build a MatKG-compatible graph from raw term records.

    Optionally also consumes code_snippets produced by the code snippet
    extraction pass.

    Returns a dict with keys:
      - "things": list of node dicts
      - "associations": list of edge dicts

    ``merge_existing`` (the extracted-bundle path, ``kg_update.rebuild_kg``) carries a later record of a term into a node
    that already exists (a relation-target stub or an earlier duplicate id): passages, pages, properties, publications
    and metadata are unioned (:func:`merge_term_into_node`). Off by default: the frozen T23A contract bytes of this
    converter (``docs/migration/json_kg_contract.md``) are unchanged for the CLI and the snapshot path.
    """
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    # Add CodeSnippet nodes from code snippet extraction
    # Track snippet nodes by (source_paper, page) for wiring to term nodes later
    snippets_by_paper_page: dict[tuple[str, int], list[str]] = {}
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
            if merge_existing:
                merge_term_into_node(term, existing)
            copy_passthrough_slots(term, existing)

        # Process relations
        for rel in ensure_list(term.get("relations")):
            tgt = rel.get("related_id") or rel.get("related_term")
            if not tgt:
                continue
            # ``related_id_prefix`` lets a relation reach a node whose prefix differs from the subject's
            # (kg_to_terms writes it when the target id is not a known CURIE).
            rid = make_id(str(tgt), prefix=str(rel.get("related_id_prefix") or term_prefix))

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
    wired_snippets: set[str] = set()
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


def convert_terms_to_graph(input_json: Path, output_json: Path, *, merge_existing: bool = False) -> dict[str, Any]:
    """Convert extracted_terms JSON into MatKG graph JSON.

    Args:
        input_json: Path to input terms JSON
        output_json: Path where graph JSON will be written
        merge_existing: Carry later records of a term into a node that already exists (see :func:`build_graph`).

    Returns:
        The constructed graph dict
    """
    with input_json.open("r", encoding="utf-8") as f:
        data = json.load(f)

    terms = cast(
        list[dict[str, Any]], data.get("terms") if isinstance(data, dict) and "terms" in data else data
    )
    snippets = data.get("code_snippets", []) if isinstance(data, dict) else []
    meta = data.get("metadata") if isinstance(data, dict) else None
    prefix = "matkg"
    if isinstance(meta, dict) and meta.get("id_prefix"):
        prefix = str(meta["id_prefix"])
    graph = build_graph(terms, code_snippets=snippets, default_id_prefix=prefix, merge_existing=merge_existing)
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
        terms = cast(
        list[dict[str, Any]], data.get("terms") if isinstance(data, dict) and "terms" in data else data
    )
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

