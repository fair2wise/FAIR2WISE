from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
from app.modules.schema.registry import SchemaRegistry
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS


@pytest.fixture(scope="session")
def registry() -> SchemaRegistry:
    return SchemaRegistry.discover()


@pytest.fixture(autouse=True)
def _quiet(caplog):
    caplog.set_level(logging.ERROR)


@pytest.fixture()
def storage_kg(tmp_path) -> Path:
    path = tmp_path / "storage" / "kg"
    path.mkdir(parents=True)
    return path


@pytest.fixture()
def terms_file(tmp_path):
    """A private copy of the core_dict fixture input (callers' files are never the repo fixtures)."""
    dest = tmp_path / "inputs" / "core_dict.terms.json"
    dest.parent.mkdir()
    shutil.copy2(INPUTS / "core_dict.terms.json", dest)
    return dest


@pytest.fixture()
def make(storage_kg, terms_file, registry):
    """``make(corpus="synthetic", **overrides) -> SnapshotResult``."""

    def _make(corpus="synthetic", **overrides):
        args = dict(corpus=corpus, terms_path=terms_file, storage_kg=storage_kg,
                    terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT, registry=registry)
        args.update(overrides)
        return create_snapshot(SnapshotRequest(**args))

    return _make
