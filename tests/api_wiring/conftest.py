"""Fixtures for the T18B wiring tests (``als``, ``local``; see ``_wiring_support``)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _wiring_support import als, local  # noqa: E402,F401
