"""T26C rehearsal fixtures: the disposable-server fixtures of the graph-import suite (skip without F2W_TILED_PYTHON)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))

from tiled_server import start_tiled_instance, tiled_base, tiled_python, tiled_seeded_pool  # noqa: E402,F401
