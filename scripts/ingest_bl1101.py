#!/usr/bin/env python3
"""CLI wrapper for the BL 11.0.1.2 ops KG ingest.

Replay from repo root:
  python3 scripts/ingest_bl1101.py
  python3 scripts/ingest_bl1101.py --from-scratch
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.modules.bl1101_ingest import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
