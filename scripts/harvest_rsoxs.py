#!/usr/bin/env python3
"""Launch facade: harvest open-access RSoXS papers.

The implementation moved to ``app/modules/commands/harvest_rsoxs.py`` (T17B) and is installed as the console script
``f2w-harvest``. This file keeps ``python scripts/harvest_rsoxs.py ...`` working in a checkout and re-exports the module's
names; patch the canonical module (``app.modules.commands.harvest_rsoxs``), not this one.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:  # checkout-only launch; app/ is not on sys.path
    sys.path.insert(0, str(_REPO_ROOT))

from app.modules.commands import harvest_rsoxs as _impl  # noqa: E402

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})

if __name__ == "__main__":
    sys.exit(_impl.main())
