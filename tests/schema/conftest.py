from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.modules.schema.loading import load_helper
from app.modules.schema.registry import SchemaRegistry

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def registry() -> SchemaRegistry:
    return SchemaRegistry.discover()


@pytest.fixture(scope="session")
def matkg_helper(registry):
    return load_helper(registry.get("matkg_schema"))


@pytest.fixture(scope="session")
def rsoxs_helper(registry):
    return load_helper(registry.get("rsoxs_schema"))


@pytest.fixture(autouse=True)
def _quiet_schema_logs(caplog):
    caplog.set_level(logging.ERROR)
