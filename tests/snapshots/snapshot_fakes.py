"""Shared constants for the T23B snapshot tests."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests/contracts/json_kg/fixtures"
INPUTS = FIXTURES / "inputs"
EXPECTED = FIXTURES / "expected"
COMMIT = "a" * 40
