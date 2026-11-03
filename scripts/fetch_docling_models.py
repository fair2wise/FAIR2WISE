#!/usr/bin/env python3
"""Fetch Docling's layout, table-structure and OCR model weights at pinned revisions.

The weights are not part of the repository or of ``pixi.lock``. The image build (Dockerfile,
``runtime-base``) and CI both call this script, so one place names the exact revisions. Each model
lands in the folder layout Docling reads through ``F2W_DOCLING_ARTIFACTS_PATH`` (the repo id with
``/`` replaced by ``--``), so a process with ``HF_HUB_OFFLINE=1`` never downloads anything.

The figure-OCR checkpoints (T30: RapidOCR, ``torch`` backend, English) go to ``<dest>/RapidOcr``, where
Docling looks for them. Docling resolves the files from the registry of the ``rapidocr`` release in the
lock (``v3.10.0`` URLs); each file is verified against the SHA256 that registry lists, so the pin is the
locked ``rapidocr`` version plus those hashes. ``--no-ocr`` skips them.

Usage:
    python scripts/fetch_docling_models.py --dest /opt/fair2wise/docling

Bump a revision deliberately and rerun the parser tests (tests/literature/test_docling_parser.py,
tests/evals_binary) with the new weights: ``docling-layout-heron`` was resolved from ``main`` and
``docling-models`` from the ``v2.3.0`` tag Docling itself pins, both on 2026-10-30.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from typing import Any

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


OCR_FOLDER = "RapidOcr"
OCR_BACKEND = "torch"
OCR_LANG = "en"


def registry_sha256(registry: Any) -> dict[str, str]:
    """Map checkpoint file name -> SHA256 from a RapidOCR model registry (nested mapping)."""
    found: dict[str, str] = {}

    def walk(node: Any) -> None:
        if not hasattr(node, "items"):
            return
        url = node.get("model_dir") or node.get("path")
        digest = node.get("SHA256")
        if isinstance(url, str) and isinstance(digest, str):
            found[url.rstrip("/").rsplit("/", 1)[-1]] = digest.lower()
        for value in node.values():
            walk(value)

    walk(registry)
    return found


def verify_checkpoints(folder: Path, registry: Any) -> list[Path]:
    """Check every file in ``folder`` that the registry lists against its SHA256.

    Raises:
        ValueError: A listed file does not match, or no file in ``folder`` is listed.
    """
    expected = registry_sha256(registry)
    checked: list[Path] = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        want = expected.get(path.name)
        if want is None:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != want:
            raise ValueError(f"{path.name}: sha256 {digest} != registry {want}")
        checked.append(path)
    if not checked:
        raise ValueError(f"no checkpoint in {folder} is listed in the RapidOCR registry")
    return checked


def fetch_ocr(dest: Path) -> Path:
    """Download and verify the Docling/RapidOCR checkpoints under ``dest/RapidOcr``.

    Raises:
        ImportError: Docling or RapidOCR is not installed.
        ValueError: A downloaded file does not match the registry hash.
    """
    from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel
    from rapidocr.inference_engine.base import InferSession

    folder = RapidOcrModel.download_models(backend=OCR_BACKEND, local_dir=dest / OCR_FOLDER, lang=OCR_LANG)
    verify_checkpoints(folder, InferSession.model_info)
    return folder


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, required=True, help="Docling artifacts directory")
    parser.add_argument("--no-ocr", action="store_true", help="skip the figure-OCR checkpoints")
    args = parser.parse_args(argv)
    for path in fetch(args.dest):
        print(f"fetched {path}")
    if not args.no_ocr:
        print(f"fetched {fetch_ocr(args.dest)} (sha256 verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
