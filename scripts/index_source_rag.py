#!/usr/bin/env python3
"""Build hybrid source-RAG indexes (literature PDFs and bl1101 ops docs).

Two directories, never one mashed corpus:

  storage/source_index/literature/
  storage/source_index/ops_bl1101/

Default literature mode is a small gold/sample set so this does not block
on embedding every harvested PDF. Index the rest later with ``--literature all``.

Usage (from repo root):
  python3 scripts/index_source_rag.py
  python3 scripts/index_source_rag.py --literature gold --ops
  python3 scripts/index_source_rag.py --literature all
  python3 scripts/index_source_rag.py --ops-only
  python3 scripts/index_source_rag.py --literature gold --limit 12
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.modules.hybrid_rag.pipeline import (  # noqa: E402
    build_literature_index,
    build_ops_index,
    literature_index_root,
    ops_index_root,
)

LOGGER = logging.getLogger("index_source_rag")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--literature",
        choices=("gold", "all", "skip"),
        default="gold",
        help="Literature PDF index. gold=sample Work set (default); all=every on-disk harvest PDF.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=12,
        help="Max literature Works to index (0 = no cap). Ignored for --literature skip.",
    )
    parser.add_argument("--ops", action="store_true", default=True, help="Build the ops-doc index (default).")
    parser.add_argument("--no-ops", action="store_true", help="Skip the ops-doc index.")
    parser.add_argument("--ops-only", action="store_true", help="Only build the bl1101 ops-doc index.")
    parser.add_argument("--from-scratch", action="store_true", help="Rewrite indexes instead of incremental merge.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    incremental = not args.from_scratch
    lit_mode = "skip" if args.ops_only else args.literature
    do_ops = False if args.no_ops else True
    if args.ops_only:
        do_ops = True
        lit_mode = "skip"

    results = {}
    if lit_mode != "skip":
        limit = 0 if lit_mode == "all" else max(0, args.limit)
        LOGGER.info("Indexing literature PDFs mode=%s limit=%s → %s", lit_mode, limit, literature_index_root())
        results["literature"] = build_literature_index(
            mode=lit_mode,
            limit=limit,
            incremental=incremental,
        )
    if do_ops:
        LOGGER.info("Indexing bl1101 ops docs → %s", ops_index_root())
        results["ops"] = build_ops_index(incremental=incremental)

    print(json.dumps(results, indent=2, default=str))
    if not results:
        LOGGER.error("Nothing to index")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
