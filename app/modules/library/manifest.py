"""Category manifests (A8): record an uploaded paper in ``<category>/manifest.json``.

The harvest scripts keep one ``manifest.json`` per category (``papers/rsoxs/manifest.json``) listing its papers
with DOI, title, year, ``pdf_path`` (``papers/<category>/<year>/<file>``), ``pdf_sha256`` and an ``ingestion``
state. An upload into a category adds a row of the same shape (``source: "upload"``) so the manifest stays the
inventory of the folder. An existing manifest is only appended to, never re-sorted or rewritten otherwise; a
manifest that cannot be parsed is left untouched (the upload still succeeds and says so).
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Prefix of ``pdf_path`` values: the harvest scripts record paths relative to the repository, where the
#: library root is the ``papers`` folder.
PDF_PATH_PREFIX = "papers"


def _doi_key(doi: str | None) -> str:
    raw = (doi or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    return raw


def _row(*, relpath: str, sha256: str, doi: str | None, title: str | None, year: int | None, now: str) -> dict[str, Any]:
    return {
        "doi": f"https://doi.org/{_doi_key(doi)}" if doi else None,
        "arxiv_id": None,
        "title": title,
        "abstract": None,
        "publication_year": year,
        "publication_date": None,
        "source": "upload",
        "pdf_path": f"{PDF_PATH_PREFIX}/{relpath}",
        "pdf_urls": [],
        "tiled_uri": None,
        "ingestion": {"status": "pending", "kg_version": "v1", "extracted_at": None, "in_kg_at": None,
                      "in_tiled_graph_at": None, "pages_total": None, "error": None},
        "pdf_sha256": sha256,
        "downloaded_at": now,
    }


def record_upload(root: Path, category: str, *, relpath: str, sha256: str, doi: str | None, title: str | None,
                  year: int | None, now: str) -> dict[str, Any]:
    """Add the uploaded paper to ``<root>/<category>/manifest.json`` (created when the category is new).

    Args:
        root: The library root.
        category: The top-level folder the paper was stored under.
        relpath: Path of the PDF relative to ``root`` (``rsoxs/2013/x.pdf``).
        sha256: Hex digest of the PDF bytes.
        doi: DOI read locally from the PDF or its name, if any.
        title: Title read locally, if any.
        year: The year the paper was filed under, or ``None`` for the ``unknown`` folder.
        now: ISO timestamp recorded as ``downloaded_at``.

    Returns:
        ``{"updated": bool, "path": "<category>/manifest.json", "detail": str}``; ``updated`` is false when the
        paper is already listed (same hash, path or DOI with a PDF still on disk) or the manifest is unreadable.
    """
    manifest = root / category / "manifest.json"
    out = {"updated": False, "path": f"{category}/manifest.json", "detail": ""}
    try:
        with open(root / category / ".manifest.lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if manifest.is_file():
                    try:
                        data = json.loads(manifest.read_text(encoding="utf-8"))
                        papers = data.setdefault("papers", []) if isinstance(data, dict) else None
                        papers = papers if isinstance(papers, list) else None
                    except (OSError, ValueError):
                        papers = None
                    if papers is None:
                        out["detail"] = "manifest is not readable JSON with a papers list; left unchanged"
                        return out
                else:
                    data = {"corpus": category, "papers": []}
                    papers = data["papers"]
                path = f"{PDF_PATH_PREFIX}/{relpath}"
                key = _doi_key(doi)
                for existing in papers:
                    same = existing.get("pdf_sha256") == sha256 or existing.get("pdf_path") == path
                    same_doi = bool(key) and _doi_key(existing.get("doi")) == key
                    if same_doi and not same:
                        listed = existing.get("pdf_path")
                        same = bool(listed) and (root / str(listed).removeprefix(f"{PDF_PATH_PREFIX}/")).is_file()
                    if same:
                        out["detail"] = "already listed in the manifest"
                        return out
                papers.append(_row(relpath=relpath, sha256=sha256, doi=doi, title=title, year=year, now=now))
                tmp = manifest.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
                os.replace(tmp, manifest)
                out.update(updated=True, detail="added to the manifest")
                return out
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError as exc:
        logger.warning("manifest update failed for %s: %s", relpath, exc)
        out["detail"] = f"manifest not updated: {exc}"
        return out


__all__ = ("PDF_PATH_PREFIX", "record_upload")
