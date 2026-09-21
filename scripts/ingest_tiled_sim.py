#!/usr/bin/env python3
"""CLI: generate a seeded Tiled-sim fixture and ingest it into the next bl1101 vN.

Replay from repo root:
  python3 scripts/ingest_tiled_sim.py --generate-only
  python3 scripts/ingest_tiled_sim.py --from-graph storage/kg/matkg_bl1101_v4.json --snapshot 5
  python3 scripts/ingest_tiled_sim.py --to-tiled

--to-tiled writes ESAF/Proposal/Sample/BlueskyRun into local Tiled GraphQL
(TILED_URI, default http://127.0.0.1:8001). Requires TILED_API_KEY. Idempotent
by uri/name. Never overwrites matkg_bl1101_v4.json. Seed is 20260916.
Does not touch the literature extract file. Does not write ALS production Tiled.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.modules.tiled_sim import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
