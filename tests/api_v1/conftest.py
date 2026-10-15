"""Fixtures for the T14 ``/v1`` tests (``local``, ``als``; see ``_v1_support``)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _v1_support import build_als, build_local  # noqa: E402


@pytest.fixture
def local(monkeypatch, tmp_path):
    return build_local(monkeypatch, tmp_path)


@pytest.fixture
def als(monkeypatch, tmp_path):
    return build_als(monkeypatch, tmp_path)
