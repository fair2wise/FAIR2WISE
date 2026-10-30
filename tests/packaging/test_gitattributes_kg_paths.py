"""T26A Q3 (owner decision O-R1, 2026-10-30): KG release paths are binary-safe in Git.

KG artifacts are hashed over their exact stored bytes, so no text conversion may touch them.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PATHS = ["storage/kg/matkg_rsoxs_v9.json", "storage/kg/rsoxs/v9/graph.json", "storage/kg/rsoxs/v9/manifest.json",
         "storage/terminology/extracted_terms_rsoxs_v1.json", "docs/migration/data_releases/releases/r1.json"]


def test_kg_release_paths_are_marked_minus_text():
    probe = subprocess.run(["git", "-C", str(REPO), "check-attr", "text", "--", *PATHS], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("needs a git checkout")
    for line in probe.stdout.strip().splitlines():
        assert line.endswith(": text: unset"), line
