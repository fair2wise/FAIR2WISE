"""Graph port tests (T08A). Server-backed tests skip unless ``F2W_TILED_PYTHON`` is set."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Same convention as tests/tiled: a site-packages ``tests`` package can shadow ours.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tiled_server import tiled_base  # noqa: E402,F401

from app.modules.contracts.context import (  # noqa: E402
    AuthorizationMode,
    DeploymentProfile,
    Principal,
    RequestContext,
)


def make_ctx(scope=()):
    """A ``:local`` context limited to ``scope`` (empty = unrestricted, as InMemoryGraphPort)."""
    return RequestContext(profile=DeploymentProfile.LOCAL, mode=AuthorizationMode.LOCAL_TRUSTED,
                          principal=Principal(subject="local-operator"), request_id="t08a",
                          corpus_scope=frozenset(scope))


def make_als_ctx(scope=()):
    """An ALS-dev context (verified admin) for scope tests."""
    return RequestContext(profile=DeploymentProfile.ALS_DEV, mode=AuthorizationMode.TILED_AUTHENTICATED,
                          principal=Principal(subject="alice", issuer="https://tiled-staging.als.lbl.gov",
                                              is_admin=True, verified_at=datetime.now(timezone.utc)),
                          request_id="t08a-als", corpus_scope=frozenset(scope))


@pytest.fixture
def ctx():
    return make_ctx()
