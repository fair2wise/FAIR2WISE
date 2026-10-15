"""T25C live identity tests. Server-backed cases skip unless ``F2W_TILED_PYTHON`` is set.

Servers (all disposable, 127.0.0.1, ephemeral ports, owned roots under the session base):

* ``live_auth``: multi-user 0.2.18 (``admin`` with Tiled's admin role, ``reader``), plus a
  ``writer`` service account (role admin, API key) created at start; the ALS target.
* ``live_other``: a second multi-user server, for wrong-issuer cases.
* ``live_fresh``: a per-test multi-user server for destructive cases (role downgrade, outage).
* ``live_single``: a seeded single-user server (writer key) for local publication.
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for extra in (REPO / "tests" / "fixtures", REPO / "tests" / "security", REPO / "tests" / "api_wiring",
              REPO / "tests" / "contracts" / "legacy_ui", HERE):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from live_support import create_service_account, loopback_only  # noqa: E402
from tiled_server import start_tiled_instance, tiled_base, tiled_python  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _loopback_network_only(monkeypatch):
    """Every live test may reach only disposable 127.0.0.1 servers."""
    loopback_only(monkeypatch)


def _start(base, **kwargs):
    if tiled_python() is None:
        pytest.skip("F2W_TILED_PYTHON is not set (live identity tests need tiled 0.2.18)")
    return start_tiled_instance(base, **kwargs)


@pytest.fixture(scope="session")
def live_auth(tiled_base):  # noqa: F811
    inst = _start(tiled_base, auth=True, seed=False)
    try:
        inst.writer = create_service_account(inst)  # the configured technical writer (D27)
        yield inst
    finally:
        inst.destroy()


@pytest.fixture(scope="session")
def live_other(tiled_base):  # noqa: F811
    inst = _start(tiled_base, auth=True, seed=False)
    try:
        yield inst
    finally:
        inst.destroy()


@pytest.fixture
def live_fresh(tiled_base):  # noqa: F811
    inst = _start(tiled_base, auth=True, seed=False)
    try:
        inst.writer = create_service_account(inst)
        yield inst
    finally:
        inst.destroy()


@pytest.fixture
def live_single(tiled_base):  # noqa: F811
    inst = _start(tiled_base)
    try:
        yield inst
    finally:
        inst.destroy()
