"""Request-context helpers for the graph tests (unique module name: plain `conftest` imports
collide when several test directories are collected in one run)."""
from datetime import datetime, timezone

from app.modules.contracts.context import (
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
