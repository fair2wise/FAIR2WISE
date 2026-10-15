"""T24C gateway tests: the real ``docker/nginx.conf`` evaluated by ``nginx_model`` in front of the real API."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (HERE, REPO / "tests" / "contracts" / "legacy_ui", REPO / "tests" / "api_wiring",
              REPO / "tests" / "security"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
