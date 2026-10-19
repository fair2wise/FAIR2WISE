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

    @property
    def budget_multiple(self) -> float:
        return sp.prompt_budget_multiple(self.path)


@pytest.fixture(scope="session")
def big_graph(tmp_path_factory) -> Population:
    payload, truth = sp.build_graph()
    path = sp.write_json(tmp_path_factory.mktemp("t19d-graph") / "syn_big.json", payload)
    return Population(path, truth)


@pytest.fixture(scope="session")
def big_corpus(tmp_path_factory) -> Population:
    payload, truth = sp.build_corpus()
    path = sp.write_json(tmp_path_factory.mktemp("t19d-corpus") / "terms.json", payload)
    return Population(path, truth)


@pytest.fixture(scope="session")
def big_session(tmp_path_factory) -> Population:
    terms, manifest, truth = sp.build_session_terms()
    root = tmp_path_factory.mktemp("t19d-session")
    return Population(sp.write_json(root / "terms.json", terms), truth, (sp.write_json(root / "manifest.json", manifest),))


@pytest.fixture(scope="session")
def timings() -> Dict[str, Dict[str, Any]]:
    """The session's timing table (shared with :func:`scale_timing.measure`)."""
    return TIMINGS


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest hook signature
    target = os.environ.get("F2W_T19D_TIMINGS")
    if target and TIMINGS:
        Path(target).write_text(json.dumps(TIMINGS, indent=2, sort_keys=True), encoding="utf-8")
