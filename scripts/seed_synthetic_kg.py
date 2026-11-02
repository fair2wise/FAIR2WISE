#!/usr/bin/env python
"""Seed the local knowledge graph with a large, entirely synthetic literature graph.

WHY. The graph pages (/knowledge-graph, /kg/paper, /graph?dataSource=kg) are
drawn by one canvas viewer whose hard cases -- thousands of nodes, tens of
thousands of edges, papers with wide section/figure fan-out -- only show up on
a graph that size. A fresh local stack has none: filling it with real papers
needs OpenAlex, PDFs and an LLM, and PDFs may not enter this repository. So
this writes a graph that has the SHAPE of an ingested corpus and none of its
content.

NOTHING HERE IS REAL. Every name is generated ("Synthetic paper 0042",
"Author 0137", "Material 07"); no publication carries a DOI or an OpenAlex id,
so none can be mistaken for, or folded into, a real record; every entity is
written with ``source="synthetic_seed"`` so it can be found and cleared. The
RNG is seeded, so a run is reproducible.

THE SHAPE. Per paper: sections (``has_section``) and figures under each
section (``has_figure``), authors (``authored_by``), materials and techniques
it mentions, figures that mention a material, and citations of EARLIER papers
only (``cites``, so the citation graph is acyclic like a real one). Authors,
materials and techniques are shared pools, so the graph is one connected
corpus rather than a forest of stars. The default is sized to the viewer's
large-graph budget: about 10,000 entities and 26,000 links.

F2W PORT (T29, X-05). DEVELOPER TOOL; nothing here touches a live graph. The
command line only prints counts (``--dry-run``) or writes the graph to a JSON file
(``--out``) for the viewer's file backend. The donor's ``TiledKGStore`` write path is
not carried over (F2W graph writes go through an approved ``ChangePlan``); the
:func:`write_graph` function still accepts any store-shaped object (``batch_writes``,
``upsert_node``, ``upsert_edge``) so a test or a local fake can receive the rows.

Usage::

    python scripts/seed_synthetic_kg.py --dry-run                  # counts only
    python scripts/seed_synthetic_kg.py --out /tmp/synthetic.json  # write a JSON file
    python scripts/seed_synthetic_kg.py --papers 50 --out small.json

Adapted from als-computing/forge_rsoxs_analysis scripts/seed_synthetic_kg.py (BSD-3-Clause).
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
import json
import logging
from pathlib import Path
import random
import time
from typing import Any

logger = logging.getLogger("seed_synthetic_kg")

SOURCE = "synthetic_seed"
TECHNIQUES = (
    "GIWAXS",
    "GISAXS",
    "SAXS",
    "WAXS",
    "RSoXS",
    "XRR",
    "NEXAFS",
    "TEM",
    "AFM",
    "XPS",
    "DSC",
    "UV-vis",
)


@dataclass(frozen=True)
class Node:
    node_id: str
    entity_type: str
    name: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    relation: str


@dataclass
class SyntheticGraph:
    nodes: list[Node]
    edges: list[Edge]

    def counts(self) -> dict[str, Any]:
        return {
            "entities": len(self.nodes),
            "links": len(self.edges),
            "by_type": dict(Counter(n.entity_type for n in self.nodes)),
            "by_relation": dict(Counter(e.relation for e in self.edges)),
        }


def build_graph(
    papers: int = 500,
    *,
    seed: int = 7,
    authors: int = 600,
    materials: int = 80,
    sections_per_paper: tuple[int, int] = (3, 7),
    figures_per_section: tuple[int, int] = (1, 4),
    authors_per_paper: tuple[int, int] = (2, 6),
    citations_per_paper: tuple[int, int] = (2, 16),
) -> SyntheticGraph:
    """The whole graph, in write order: every node before any edge that touches it.

    Pure -- no store, no network -- so the shape is testable on its own.
    """
    rng = random.Random(seed)
    nodes: list[Node] = []
    edges: list[Edge] = []

    def node(node_id: str, entity_type: str, name: str, **props: Any) -> str:
        nodes.append(Node(node_id, entity_type, name, {"synthetic": True, **props}))
        return node_id

    author_ids = [
        node(f"author:synthetic-{i:04d}", "author", f"Author {i:04d}") for i in range(authors)
    ]
    material_ids = [
        node(f"material:synthetic-{i:02d}", "material", f"Material {i:02d}")
        for i in range(materials)
    ]
    technique_ids = [
        node(f"technique:synthetic-{t.lower()}", "technique", f"{t} (synthetic)")
        for t in TECHNIQUES
    ]

    paper_ids: list[str] = []
    for p in range(papers):
        year = 2000 + (p * 26) // max(
            1, papers
        )  # older papers first, so citations point back in time
        pid = node(
            f"publication:synthetic-{p:04d}", "publication", f"Synthetic paper {p:04d}", year=year
        )
        for a in rng.sample(author_ids, rng.randint(*authors_per_paper)):
            edges.append(Edge(pid, a, "authored_by"))
        for m in rng.sample(material_ids, rng.randint(1, 3)):
            edges.append(Edge(pid, m, "mentions"))
        for t in rng.sample(technique_ids, rng.randint(1, 3)):
            edges.append(Edge(pid, t, "mentions"))
        figure_no = 0
        for s in range(rng.randint(*sections_per_paper)):
            sid = node(f"paper_section:synthetic-{p:04d}-{s}", "paper_section", f"Section {s + 1}")
            edges.append(Edge(pid, sid, "has_section"))
            for _ in range(rng.randint(*figures_per_section)):
                figure_no += 1
                fid = node(
                    f"paper_figure:synthetic-{p:04d}-{figure_no}",
                    "paper_figure",
                    f"Figure {figure_no}",
                )
                edges.append(Edge(sid, fid, "has_figure"))
                for m in rng.sample(material_ids, rng.randint(1, 2)):
                    edges.append(Edge(fid, m, "mentions"))
        if paper_ids:
            for cited in rng.sample(
                paper_ids, min(len(paper_ids), rng.randint(*citations_per_paper))
            ):
                edges.append(Edge(pid, cited, "cites"))
        paper_ids.append(pid)
    return SyntheticGraph(nodes, edges)


def write_graph(store: Any, graph: SyntheticGraph, *, chunk: int = 2000) -> dict[str, int]:
    """Write nodes, then edges, in batches. Returns what the store accepted."""
    written = {"nodes": 0, "edges": 0}
    started = time.monotonic()
    for start in range(0, len(graph.nodes), chunk):
        with store.batch_writes():
            for n in graph.nodes[start : start + chunk]:
                if store.upsert_node(
                    n.node_id, n.entity_type, name=n.name, source=SOURCE, properties=n.properties
                ):
                    written["nodes"] += 1
        logger.info(
            "nodes %d/%d (%.0f s)",
            min(start + chunk, len(graph.nodes)),
            len(graph.nodes),
            time.monotonic() - started,
        )
    for start in range(0, len(graph.edges), chunk):
        with store.batch_writes():
            for e in graph.edges[start : start + chunk]:
                if store.upsert_edge(
                    e.source, e.target, e.relation, confidence=1.0, evidence=SOURCE
                ):
                    written["edges"] += 1
        logger.info(
            "edges %d/%d (%.0f s)",
            min(start + chunk, len(graph.edges)),
            len(graph.edges),
            time.monotonic() - started,
        )
    return written


def to_json(graph: SyntheticGraph) -> dict[str, Any]:
    """The graph as plain JSON (``nodes`` with ``id``/``entity_type``/``name``, ``edges`` with ``relation``)."""
    return {
        "source": SOURCE,
        "nodes": [{"id": n.node_id, "entity_type": n.entity_type, "name": n.name, "properties": n.properties}
                  for n in graph.nodes],
        "edges": [asdict(e) for e in graph.edges],
    }


def main(argv: list[str] | None = None) -> int:
    """Print counts, and write ``--out`` unless ``--dry-run``."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--papers", type=int, default=500, help="number of synthetic papers (default 500)")
    parser.add_argument("--seed", type=int, default=7, help="RNG seed (default 7)")
    parser.add_argument("--dry-run", action="store_true", help="print the counts and write nothing")
    parser.add_argument("--out", type=Path, default=None, help="write the graph to this JSON file")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    graph = build_graph(args.papers, seed=args.seed)
    counts = graph.counts()
    logger.info("synthetic graph: %d entities, %d links", counts["entities"], counts["links"])
    logger.info("  by type: %s", counts["by_type"])
    logger.info("  by relation: %s", counts["by_relation"])
    if args.dry_run or args.out is None:
        if args.out is None and not args.dry_run:
            logger.info("no --out given; nothing written")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(to_json(graph), indent=1), encoding="utf-8")
    logger.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
