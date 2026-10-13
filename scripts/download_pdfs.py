#!/usr/bin/env python3
"""CLI facade for :mod:`app.modules.literature.download_pdfs` (D12).

The implementation and all module state (rate-limit floor, host delays) live in
the canonical package module. This facade re-exports its names so the harvest
scripts' ``import download_pdfs as dl`` keeps working; patch the canonical
module, not this one.

Usage:
    python scripts/download_pdfs.py --keyword "organic photovoltaics" --target ./pdfs
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:  # checkout-only script; app/ is not on sys.path
    sys.path.insert(0, str(_REPO_ROOT))

from app.modules.literature import download_pdfs as _impl  # noqa: E402

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})

if __name__ == "__main__":
    _impl.main()
