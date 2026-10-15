"""Shared fakes for T10 tests: clock, contexts, crash, fake pipelines."""

from __future__ import annotations

from typing import Any, Dict

from app.modules.contracts.context import (
    AuthorizationMode,
    DeploymentProfile,
    Principal,
    RequestContext,
)
from app.modules.jobs.keys import OperationInputs


class Clock:
    """Manual epoch clock."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Crash(BaseException):
    """Simulates a process dying: not an ``Exception``, so no handler records anything."""


def local_ctx(*caps: str, **kw: Any) -> RequestContext:
    base: Dict[str, Any] = dict(
        profile=DeploymentProfile.LOCAL,
        mode=AuthorizationMode.LOCAL_TRUSTED,
        principal=Principal(subject="local-operator"),
        request_id="req-t10",
        capabilities=frozenset(
            caps or ("ingest.extract", "json.build", "graph.publish", "curation.approve", "jobs.admin")
        ),
    )
    base.update(kw)
    return RequestContext(**base)


def inputs(**kw: Any) -> OperationInputs:
    base: Dict[str, Any] = dict(
        corpus_scope=("rsoxs",),
        source_sha256="a" * 64,
        source_version="v1",
        parser_version="p1",
        extractor_version="e1",
        model="m1",
        prompt_revision="pr1",
        schema_revision="s1",
        vocabulary_revision="v1",
        params={"chunk": 100},
    )
    base.update(kw)
    return OperationInputs(**base)
