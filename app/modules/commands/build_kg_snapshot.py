#!/usr/bin/env python3
r"""Freeze an extracted-terms JSON into an immutable versioned KG snapshot (T23B).

Console script: ``f2w-kg-snapshot`` (``scripts/build_kg_snapshot.py`` stays as a launch facade).

Calls ``app.modules.graph.snapshot.create_snapshot``: json2kg builds the graph, the bytes are frozen under
``storage/kg/<corpus>/<snapshot-id>/`` with a sidecar manifest plus the byte-identical flat compatibility copy.
No Tiled, network, shell or Git process is used, and an existing snapshot is never overwritten.

  python3 scripts/build_kg_snapshot.py --corpus rsoxs --terms storage/terminology/extracted_terms_rsoxs_v1.json \
      --extraction-revision extract_terms:2.1:lbl/cborg-chat --run-id 2026-10-14
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import logging
from pathlib import Path
import sys

from ..cli_output import output
from ..graph.snapshot import SnapshotRequest, create_snapshot


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the snapshot command line."""
    parser = argparse.ArgumentParser(description="Create an immutable versioned JSON KG snapshot")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--terms", required=True, type=Path, help="extracted-terms JSON (read only)")
    parser.add_argument("--storage-kg", type=Path, default=None, help="default: <repo>/storage/kg")
    parser.add_argument("--snapshot-id", default=None, help="v<N>; default: next free number")
    parser.add_argument("--schema", default=None, help="registered schema id (default: chosen from the corpus)")
    parser.add_argument("--parent", default=None, help="parent snapshot id")
    parser.add_argument("--parent-relation", default="rebuild")
    parser.add_argument("--parent-data-commit", default=None)
    parser.add_argument("--extraction-revision", action="append", default=[],
                        help="extractor:version[:model] (repeatable)")
    parser.add_argument("--source-sha256", action="append", default=[], help="per-source sha256 (repeatable)")
    parser.add_argument("--corpus-revision-id", default=None)
    parser.add_argument("--terms-ref", default=None, help="repo-relative path or private: reference")
    parser.add_argument("--code-commit", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--profile", default="local", choices=["local", "als-dev", "als-prod", "ci"])
    parser.add_argument("--owner-waiver-ref", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def _revision(text: str) -> dict[str, str]:
    parts = text.split(":", 2)
    if len(parts) < 2:
        raise ValueError(f"--extraction-revision must be extractor:version[:model], got {text!r}")
    out = {"extractor": parts[0], "version": parts[1]}
    if len(parts) == 3:
        out["model"] = parts[2]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: freeze the snapshot and print a JSON summary; exit status 0 or 1."""
    args = parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s: %(message)s")
    try:
        result = create_snapshot(SnapshotRequest(
            corpus=args.corpus, terms_path=args.terms, storage_kg=args.storage_kg, schema_id=args.schema,
            snapshot_id=args.snapshot_id, parent_snapshot_id=args.parent, parent_relation=args.parent_relation,
            parent_data_commit=args.parent_data_commit,
            extraction_revisions=[_revision(r) for r in args.extraction_revision],
            source_sha256=args.source_sha256, corpus_revision_id=args.corpus_revision_id,
            terms_ref=args.terms_ref, code_commit=args.code_commit, run_id=args.run_id, profile=args.profile,
            owner_waiver_ref=args.owner_waiver_ref))
    except (ValueError, LookupError, OSError) as exc:
        output.info(f"ERROR: {exc}")
        return 1
    output.info(json.dumps({
        "snapshot_id": result.snapshot_id, "corpus": result.corpus, "directory": str(result.directory),
        "graph_sha256": result.graph_sha256, "flat_copy": str(result.flat_path),
        "validation": result.validation_status, "disclosure": result.disclosure.status,
        "commit_ready": result.commit_ready, "warnings": result.warnings}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
