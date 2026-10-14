"""Tiled deployment tests (T20B). Server-backed tests skip unless ``F2W_TILED_PYTHON`` is set."""
import sys
from pathlib import Path

# Same convention as tests/api_wiring: a site-packages ``tests`` package can shadow ours.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))

from tiled_server import (  # noqa: E402,F401
    tiled_auth_instance,
    tiled_base,
    tiled_empty_instance,
    tiled_instance,
    tiled_session_instance,
)
