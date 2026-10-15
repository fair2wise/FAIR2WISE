"""T18C closure tests. Server-backed cases skip unless ``F2W_TILED_PYTHON`` is set.

Every test may reach only 127.0.0.1, never the developer's own servers (8011, 8765,
8002, 8004, 5057); no model, provider or ALS host is contacted.
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
for extra in (REPO / "tests" / "fixtures", REPO / "tests" / "security", REPO / "tests" / "api_wiring",
              REPO / "tests" / "contracts" / "legacy_ui", REPO / "tests" / "security_live",
              REPO / "tests" / "graph_import", REPO / "tests" / "graph", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from tiled_server import tiled_base  # noqa: E402,F401  (session fixture)
from closure_support import loopback_guard  # noqa: E402


@pytest.fixture(autouse=True)
def _loopback_network_only(monkeypatch):
    loopback_guard(monkeypatch)
