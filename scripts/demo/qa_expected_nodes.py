#!/usr/bin/env python3
"""Compute the expected KG evidence of the demo questions offline with the real lexical retriever.

Read-only and offline: loads the given JSON KG files with the product's ``RetrievalAgent`` (lexical backend, no
model, no network), all of them at once as the chat does on ``:local`` when several corpora are live, and records,
per question, the top retrieved ``(corpus, node id)`` pairs and whether the off-domain gate fires. Run it from a clean
export of the repository (importing the app reads ``.env`` next to it)::

    python scripts/demo/qa_expected_nodes.py --questions docs/migration/demo/qa_questions.json \
        --graph rsoxs=storage/kg/matkg_rsoxs_v4.json \
        --graph qwen3_235b_580papers=storage/kg/matkg_qwen3_235b_580papers.json [--top 8] [--check] [--time]

``--graph NAME=PATH`` loads PATH as corpus NAME: the file is linked as ``matkg_<NAME>.json`` in a temporary directory so
its graph id (what the chat names on every citation) is the corpus name, as in the live container where each corpus is
staged under its own name. A bare ``--graph PATH`` keeps the file's own graph id. A node id may exist in several corpora
(about 2000 ids are shared between the two demo KGs): identity is the pair ``(corpus, id)``.

Each question names its ``corpus``; with ``--check`` every curated ``expected_node_ids`` entry must be retrieved **from
that corpus** within ``retrieval_rank_max`` hits (hit order is not stable between processes, membership is), and a
question with corpus ``none`` must trip the off-domain gate or retrieve nothing relevant. ``--time`` prints the load and
retrieval times (the offline measurement of ``QA_READINESS.md`` section 10).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ.setdefault("KG_RAG_RETRIEVAL_BACKEND", "lexical")
os.environ.setdefault("KG_RAG_FORCE_CPU", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _stage(specs: list[str], tmp: Path) -> list[str]:
    """Link ``NAME=PATH`` specs as ``<tmp>/<NAME>/matkg_<NAME>.json``; bare paths pass through."""
    out: list[str] = []
    for spec in specs:
        name, sep, path = spec.partition("=")
        if not sep:
            out.append(spec)
            continue
        folder = tmp / name
        folder.mkdir(parents=True, exist_ok=True)
        link = folder / f"matkg_{name}.json"
        link.symlink_to(Path(path).resolve())
        out.append(str(link))
    return out


async def _run(args: argparse.Namespace, files: list[str]) -> int:
    from app.modules.f2w_agent.retrieval_agent import RetrievalAgent, _off_domain

    data = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    agent = RetrievalAgent(graph_files=files, graph_source="json", source_rag=False)
    started = time.perf_counter()
    await agent.reload_kg(files[0], graph_source="json", graph_files=files)
    graphs = await agent._ensure_graphs()
    if args.time:
        sizes = {gid: len(kg.nodes) for gid, kg in graphs.items()}
        print(f"load {time.perf_counter() - started:.2f}s graphs={sizes}")
    failed = 0
    for item in data["questions"]:
        began = time.perf_counter()
        hits = agent._fanout_hits(item["question"], graphs)
        took = time.perf_counter() - began
        off = _off_domain(item["question"], hits, graphs)
        by_graph: dict[str, int] = {}
        for hit in hits:
            by_graph[hit.graph_id] = by_graph.get(hit.graph_id, 0) + 1
        top = [(hit.graph_id, hit.id) for hit in hits[: args.top]]
        timing = f" {took:.2f}s" if args.time else ""
        print(f"{item['id']:<22} corpus={item.get('corpus')!s:<22} hits={len(hits):<5} off_domain={off!s:<5}{timing} "
              f"{by_graph} {top[:3]}")
        corpus = str(item.get("corpus") or "")
        pairs = [(hit.graph_id, hit.id) for hit in hits]
        for node in item.get("expected_node_ids") or []:
            rank = next((i for i, (gid, nid) in enumerate(pairs) if nid == node and gid == corpus), None)
            elsewhere = [gid for gid, nid in pairs if nid == node and gid != corpus]
            print(f"    expected {node} from {corpus}: rank {rank}" + (f" (also in {sorted(set(elsewhere))})" if elsewhere else ""))
            if rank is None or rank >= data.get("retrieval_rank_max", 40):
                failed += 1
        if args.check and not item.get("answerable", True) and not off and corpus == "none":
            print("    unanswerable question did not trip the off-domain gate")
            failed += 1
    print("check:", "FAILED" if failed else "ok")
    return 1 if (args.check and failed) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--graph", action="append", required=True, help="PATH or NAME=PATH (corpus NAME)")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--check", action="store_true", help="exit 1 when an expected node is not within the rank limit")
    ap.add_argument("--time", action="store_true", help="print load and retrieval times")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="qa_corpora_") as tmp:
        return asyncio.run(_run(args, _stage(args.graph, Path(tmp))))


if __name__ == "__main__":
    raise SystemExit(main())
