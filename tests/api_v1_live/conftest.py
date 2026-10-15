"""T14 live tests: ``create_api_app`` on ALS against a disposable multi-user Tiled 0.2.18.

Skipped unless ``F2W_TILED_PYTHON`` is set. The server is started by
``tests/fixtures/tiled_server.py`` on an ephemeral 127.0.0.1 port with generated
passwords; every test may reach only 127.0.0.1 (never the developer's own servers).
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "fixtures", REPO / "tests" / "security", REPO / "tests" / "api_wiring",
              REPO / "tests" / "contracts" / "legacy_ui", REPO / "tests" / "security_live",
              REPO / "tests" / "api_v1"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from live_support import create_service_account, loopback_only  # noqa: E402
from tiled_server import start_tiled_instance, tiled_base, tiled_python  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _loopback_network_only(monkeypatch):
    loopback_only(monkeypatch)


@pytest.fixture(scope="session")
def live_v1(tiled_base):  # noqa: F811
    if tiled_python() is None:
        pytest.skip("F2W_TILED_PYTHON is not set (live tests need tiled 0.2.18)")
    inst = start_tiled_instance(tiled_base, auth=True, seed=False)
    try:
        inst.writer = create_service_account(inst)
        yield inst
    finally:
        inst.destroy()
