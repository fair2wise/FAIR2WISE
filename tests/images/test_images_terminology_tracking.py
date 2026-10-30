"""PC-1 evidence (A5-1): which ``storage/terminology/`` files on disk are not tracked by Git.

``Dockerfile`` copies the whole directory while ``.gitignore`` ignores ``storage/terminology/*.json``,
so a developer's checkout can ship harvest files a CI build never sees. This test makes the
condition visible in every run (skipped when the checkout is clean, a warning listing the files
otherwise). It changes nothing: the owner decides PC-1 (exclude, track, or :local only); until
then ``tests/images/test_images_context.py`` keeps asserting the directory ships (O-D1) and
``scripts/ci/release_manifest.py`` records the same files as ``shipped_untracked_files``.
"""

from __future__ import annotations

import subprocess
import warnings
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
    if not untracked:
        pytest.skip("storage/terminology holds only tracked files in this checkout (PC-1 condition absent)")
    size = sum((REPO / p).stat().st_size for p in untracked)
    warnings.warn(
        f"PC-1 pending: {len(untracked)} untracked storage/terminology file(s) ({size / 1e6:.0f} MB) would be "
        f"copied into every image built from this checkout: " + ", ".join(untracked),
        stacklevel=1)
