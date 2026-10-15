"""Graph port tests (T08A). Server-backed tests skip unless ``F2W_TILED_PYTHON`` is set."""
import sys
from pathlib import Path

import pytest

# Same convention as tests/tiled: a site-packages ``tests`` package can shadow ours.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tiled_server import tiled_base  # noqa: E402,F401

from graph_contexts import make_ctx  # noqa: E402


@pytest.fixture
def ctx():
    return make_ctx()
