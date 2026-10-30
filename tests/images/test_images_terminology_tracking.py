"""PC-1 evidence (A5-1): which ``storage/terminology/`` files on disk are not tracked by Git.

``Dockerfile`` copies the whole directory while ``.gitignore`` ignores ``storage/terminology/*.json``,
so a developer's checkout could ship harvest files a CI build never sees. Owner decision PC-1
(D3.1, O-D3, 2026-10-30) tracked the harvest files and dropped the ``.gitignore`` rule; this test
now fails when a new untracked file appears under ``storage/terminology`` (the release manifest
refuses such a candidate, ``REFUSE_SHIPPED_UNTRACKED_FILES``), and
``tests/images/test_images_context.py`` keeps asserting the directory ships (O-D1).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TERMINOLOGY = REPO / "storage" / "terminology"


def _tracked() -> set:
    probe = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--", "storage/terminology"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("needs a git checkout")
    return {p for p in probe.stdout.split("\0") if p}


def untracked_terminology_files() -> list:
    tracked = _tracked()
    on_disk = sorted(p.relative_to(REPO).as_posix() for p in TERMINOLOGY.rglob("*") if p.is_file())
    return [p for p in on_disk if p not in tracked]


def test_terminology_files_shipped_by_the_dockerfile_are_tracked(record_property):
    untracked = untracked_terminology_files()
    record_property("untracked_terminology_files", untracked)
    size = sum((REPO / p).stat().st_size for p in untracked)
    assert not untracked, (
        f"{len(untracked)} untracked storage/terminology file(s) ({size / 1e6:.0f} MB) would be copied into "
        f"every image built from this checkout; track them (PC-1, O-D3) or move them out: " + ", ".join(untracked))
