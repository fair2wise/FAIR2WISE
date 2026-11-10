#!/usr/bin/env python3
"""Compute the expected KG evidence of the demo questions offline with the real lexical retriever.

Read-only and offline: loads the given JSON KG files with the product's ``RetrievalAgent`` (lexical backend, no
model, no network) and records, per question, the top retrieved node ids and whether the off-domain gate fires.
Run it from a clean export of the repository (importing the app reads ``.env`` next to it)::

    python scripts/demo/qa_expected_nodes.py --questions docs/migration/demo/qa_questions.json \
        --graph storage/kg/matkg_rsoxs_v1.json --graph storage/kg/matkg_bl1101_v4.json [--top 8] [--check]

``--graph`` may name a release exported from the demo container (for example the ``e2e_demo`` graph.json) so
its questions can be curated: read the printed top hits, then put the ids in ``expected_node_ids``. With ``--check``
every curated id must be within ``retrieval_rank_max`` hits (hit order is not stable between processes, membership is).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("KG_RAG_RETRIEVAL_BACKEND", "lexical")
os.environ.setdefault("KG_RAG_FORCE_CPU", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def _run(args: argparse.Namespace) -> int:
    from app.modules.f2w_agent.retrieval_agent import RetrievalAgent, _off_domain

    data = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    agent = RetrievalAgent(graph_files=args.graph, graph_source="json", source_rag=False)
    await agent.reload_kg(args.graph[0], graph_source="json", graph_files=args.graph)
    graphs = await agent._ensure_graphs()
    failed = 0
    for item in data["questions"]:
        hits = agent._fanout_hits(item["question"], graphs)
        off = _off_domain(item["question"], hits, graphs)
        top = [hit.id for hit in hits[: args.top]]
        print(f"{item['id']:<22} hits={len(hits):<5} off_domain={off!s:<5} {top[:3]}")
        ids = [hit.id for hit in hits]
        for node in item.get("expected_node_ids") or []:
            rank = ids.index(node) if node in ids else None
            print(f"    expected {node}: rank {rank}")
            if rank is None or rank >= data.get("retrieval_rank_max", 40):
                failed += 1
    print("check:", "FAILED" if failed else "ok")
    return 1 if (args.check and failed) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--graph", action="append", required=True)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--check", action="store_true", help="exit 1 when an expected node is not within the rank limit")
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
