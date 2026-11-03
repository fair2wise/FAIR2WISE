"""Shared T19D fixtures: the large synthetic populations, written once per session, and timings.

Building and writing the three populations takes a few seconds; every scale test reuses
them read-only. Set ``F2W_T19D_TIMINGS=<file>`` to write the measured operation timings and
peak allocations as JSON when the session ends (the T19D evidence report quotes them).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from tests.operations import scale_population as sp
from tests.operations.scale_timing import TIMINGS


@dataclass(frozen=True)
class Population:
    """One written population: file paths, payload size and truth."""

    path: Path
    truth: Any
    extra: Tuple[Path, ...] = ()
    profile: sp.Profile = sp.FULL

    @property
    def budget_multiple(self) -> float:
        return sp.prompt_budget_multiple(self.path, self.profile.scale)


@pytest.fixture(scope="session", params=[
    pytest.param(sp.SMALL, id="small"),
    pytest.param(sp.FULL, id="full", marks=pytest.mark.slow),
])
def profile(request) -> sp.Profile:
    """Population size: one tenth (default suite) or the full T19D size (``slow``).

    The three ``big_*`` populations all derive from this one fixture, so a test that uses
    several of them runs once per size, never once per combination.
    """
    return request.param


@pytest.fixture(scope="session")
def big_graph(tmp_path_factory, profile) -> Population:
    payload, truth = sp.build_graph(profile.graph_entities, profile.graph_works, profile.graph_edges)
    path = sp.write_json(tmp_path_factory.mktemp("t19d-graph") / "syn_big.json", payload)
    return Population(path, truth, profile=profile)


@pytest.fixture(scope="session")
def big_corpus(tmp_path_factory, profile) -> Population:
    payload, truth = sp.build_corpus(profile.corpus_terms, profile.corpus_works)
    path = sp.write_json(tmp_path_factory.mktemp("t19d-corpus") / "terms.json", payload)
    return Population(path, truth, profile=profile)


@pytest.fixture(scope="session")
def big_session(tmp_path_factory, profile) -> Population:
    terms, manifest, truth = sp.build_session_terms(profile.session_terms)
    root = tmp_path_factory.mktemp("t19d-session")
    return Population(sp.write_json(root / "terms.json", terms), truth,
                      (sp.write_json(root / "manifest.json", manifest),), profile=profile)


@pytest.fixture(scope="session")
def timings() -> Dict[str, Dict[str, Any]]:
    """The session's timing table (shared with :func:`scale_timing.measure`)."""
    return TIMINGS


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest hook signature
    target = os.environ.get("F2W_T19D_TIMINGS")
    if target and TIMINGS:
        Path(target).write_text(json.dumps(TIMINGS, indent=2, sort_keys=True), encoding="utf-8")
