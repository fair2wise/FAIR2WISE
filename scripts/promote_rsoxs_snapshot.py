#!/usr/bin/env python3
"""Snapshot-copy live RSoXS terms, then json2kg the copy into matkg_rsoxs_vN.

Never writes storage/terminology/extracted_terms_rsoxs_v1.json (the live extract
file). Never overwrites an existing matkg_rsoxs_vN.json unless --output is an
explicit new path.

  python3 scripts/promote_rsoxs_snapshot.py
  python3 scripts/promote_rsoxs_snapshot.py --terms-snapshot storage/terminology/extracted_terms_rsoxs_v1_20260917T044424Z.json --output storage/kg/matkg_rsoxs_v2.json
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.modules.tiled_sim import (  # noqa: E402
    promote_rsoxs_snapshot,
    snapshot_copy_terms,
)

LIVE_TERMS = REPO_ROOT / "storage/terminology/extracted_terms_rsoxs_v1.json"
SIDECAR = REPO_ROOT / "storage/fixtures/rsoxs_snapshot_promote.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote a copy of RSoXS extract terms to matkg_rsoxs_vN")
    parser.add_argument("--terms-snapshot", type=Path, default=None)
    parser.add_argument("--live-terms", type=Path, default=LIVE_TERMS)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--sidecar", type=Path, default=SIDECAR)
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    snapshot = args.terms_snapshot
    if snapshot is None:
        snapshot = snapshot_copy_terms(args.live_terms)
        print(f"copied live terms → {snapshot} (live file untouched)")
    result = promote_rsoxs_snapshot(terms_snapshot=snapshot, output_kg=args.output)
    snap = result["snapshot"]
    kg_path = Path(result["kg_path"]).resolve()
    terms_path = Path(result["terms_snapshot"]).resolve()
    args.sidecar.parent.mkdir(parents=True, exist_ok=True)
    import json

    args.sidecar.write_text(
        json.dumps(
            {
                "live_terms": "storage/terminology/extracted_terms_rsoxs_v1.json",
                "live_file_written": False,
                "terms_snapshot": str(terms_path.relative_to(REPO_ROOT)),
                "kg_path": str(kg_path.relative_to(REPO_ROOT)),
                **{k: snap[k] for k in ("written_at", "sha256", "nodes", "edges", "unique_papers", "term_count")},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"{result['kg_path']}: {snap['nodes']} nodes, {snap['edges']} edges "
        f"from {snap['term_count']} terms / {snap['unique_papers']} papers"
    )
    print(f"sidecar {args.sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
