#!/usr/bin/env python3
"""Fetch Docling's layout and table-structure model weights at pinned commits.

The weights are not part of the repository or of ``pixi.lock``. The image build (Dockerfile,
``runtime-base``) and CI both call this script, so one place names the exact revisions. Each model
lands in the folder layout Docling reads through ``F2W_DOCLING_ARTIFACTS_PATH`` (the repo id with
``/`` replaced by ``--``), so a process with ``HF_HUB_OFFLINE=1`` never downloads anything.

Usage:
    python scripts/fetch_docling_models.py --dest /opt/fair2wise/docling

Bump a revision deliberately and rerun the parser tests (tests/literature/test_docling_parser.py,
tests/evals_binary) with the new weights: ``docling-layout-heron`` was resolved from ``main`` and
``docling-models`` from the ``v2.3.0`` tag Docling itself pins, both on 2026-10-30.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

#: Hugging Face repo id -> commit that was verified against the parser tests.
PINNED_MODELS: dict[str, str] = {
    "docling-project/docling-layout-heron": "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
    "docling-project/docling-models": "fc0f2d45e2218ea24bce5045f58a389aed16dc23",
}


def model_folder(repo_id: str) -> str:
    """Folder name Docling expects under its artifacts path for ``repo_id``."""
    return repo_id.replace("/", "--")


def fetch(dest: Path) -> list[Path]:
    """Download every pinned model under ``dest``.

    Args:
        dest: Artifacts directory (created if needed).

    Returns:
        The model directories, in :data:`PINNED_MODELS` order.

    Raises:
        ImportError: ``huggingface_hub`` is not installed.
    """
    from huggingface_hub import snapshot_download

    out: list[Path] = []
    for repo_id, revision in PINNED_MODELS.items():
        target = dest / model_folder(repo_id)
        snapshot_download(repo_id, revision=revision, local_dir=str(target))
        out.append(target)
    return out


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, required=True, help="Docling artifacts directory")
    args = parser.parse_args(argv)
    for path in fetch(args.dest):
        print(f"fetched {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
