"""Category manifests (A8): record an uploaded paper in ``<category>/manifest.json``.

The harvest scripts keep one ``manifest.json`` per category (``papers/rsoxs/manifest.json``) listing its papers
with DOI, title, year, ``pdf_path`` (``papers/<category>/<year>/<file>``), ``pdf_sha256`` and an ``ingestion``
state. An upload into a category adds a row of the same shape (``source: "upload"``) so the manifest stays the
inventory of the folder. An existing manifest is only appended to, never re-sorted or rewritten otherwise; a
manifest that cannot be parsed is left untouched (the upload still succeeds and says so).
"""

from __future__ import annotations

from collections.abc import Callable
import fcntl
import json
import logging
from pathlib import Path
from typing import Any

from .bibmeta import cap_abstract, clean_author_list

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


def _row(*, relpath: str, sha256: str, doi: str | None, title: str | None, year: int | None, now: str,
         source: str = "upload", arxiv_id: str | None = None, authors: list[str] | None = None,
         abstract: str | None = None, provenance: str | None = None, supplied_by: str | None = None,
         venue: str | None = None) -> dict[str, Any]:
    row = {
        "doi": f"https://doi.org/{_doi_key(doi)}" if doi else None,
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": clean_author_list(authors) or None,
        "abstract": cap_abstract(abstract),
        "publication_year": year,
        "publication_date": None,
        "venue": " ".join(str(venue).split())[:300] if venue else None,
        "source": source,
        "pdf_path": f"{PDF_PATH_PREFIX}/{relpath}",
        "pdf_urls": [],
        "tiled_uri": None,
        "ingestion": {"status": "pending", "kg_version": "v1", "extracted_at": None, "in_kg_at": None,
                      "in_tiled_graph_at": None, "pages_total": None, "error": None},
        "pdf_sha256": sha256,
        "downloaded_at": now,
    }
    if provenance:   # a file a person supplied by hand: how it was matched and who supplied it
        row["provenance"] = provenance
        if supplied_by:
            row["supplied_by"] = supplied_by
    return row


def record_upload(root: Path, category: str, *, relpath: str, sha256: str, doi: str | None, title: str | None,
                  year: int | None, now: str, source: str = "upload", arxiv_id: str | None = None,
                  authors: list[str] | None = None, abstract: str | None = None, provenance: str | None = None,
                  supplied_by: str | None = None, venue: str | None = None) -> dict[str, Any]:
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
        source: The row's ``source`` (``upload`` or the registry a harvest found it in).
        arxiv_id: The arXiv id when known.
        authors: Author display names from the registry, when known (stored as ``authors``; ``None`` when empty).
        abstract: The abstract from the registry, when known (stored capped as ``abstract``).
        provenance: ``user_supplied`` (or a confirmed variant) for a PDF a person supplied by hand; stored on the row.
        supplied_by: Who supplied it (the acting principal's subject), stored next to ``provenance``.
        venue: The journal or repository the registry names, when known (stored as ``venue``).

    Returns:
        ``{"updated": bool, "path": "<category>/manifest.json", "detail": str}``; ``updated`` is false when the
        paper is already listed (same hash, path or DOI with a PDF still on disk) or the manifest is unreadable.
    """
    manifest = root / category / "manifest.json"
    out = {"updated": False, "path": f"{category}/manifest.json", "detail": ""}
    try:
        with (root / category / ".manifest.lock").open("a+") as lock:
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
                papers.append(_row(relpath=relpath, sha256=sha256, doi=doi, title=title, year=year, now=now,
                                   source=source, arxiv_id=arxiv_id, authors=authors, abstract=abstract,
                                   provenance=provenance, supplied_by=supplied_by, venue=venue))
                tmp = manifest.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
                tmp.replace(manifest)
                out.update(updated=True, detail="added to the manifest")
                return out
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError as exc:
        logger.warning("manifest update failed for %s: %s", relpath, exc)
        out["detail"] = f"manifest not updated: {exc}"
        return out


def remove_upload(root: Path, category: str, *, sha256: str) -> bool:
    """Remove the row with this ``pdf_sha256`` from ``<root>/<category>/manifest.json`` (undo of a filed upload).

    Only that one row is removed, under the same lock and with the same atomic rewrite as :func:`record_upload`; every
    other row keeps its place. Returns whether a row was removed (``False`` for an unreadable or absent manifest).
    """
    manifest = root / category / "manifest.json"
    try:
        with (root / category / ".manifest.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                papers = data.get("papers") if isinstance(data, dict) else None
                if not isinstance(papers, list):
                    return False
                kept = [row for row in papers if not (isinstance(row, dict) and row.get("pdf_sha256") == sha256)]
                if len(kept) == len(papers):
                    return False
                data["papers"] = kept
                tmp = manifest.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
                tmp.replace(manifest)
                return True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except (OSError, ValueError):
        return False


def is_empty(value: object) -> bool:
    """Whether a stored ``authors`` or ``abstract`` value counts as missing (absent, null, blank or an empty list)."""
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not any(str(v).strip() for v in value)
    return value is None


def fill_missing(row: dict[str, Any], *, authors: list[str] | None, abstract: str | None, title: str | None = None,
                 venue: str | None = None, year: int | None = None) -> list[str]:
    """Set ``authors`` / ``abstract`` (and with ``title`` / ``venue`` / ``year``, the repair fields) where the row has none.

    A non-empty stored value is never replaced, with one exception: a stored ``title`` that is junk (a file path, an
    ``f2w://`` URI, a file name, a tool's placeholder: :func:`~app.modules.term_extractor.pub_clean.is_junk_title`) is replaced
    by a real one.

    Returns:
        The names of the fields filled (``[]`` when nothing changed).
    """
    from ..term_extractor.pub_clean import is_junk_title

    filled: list[str] = []
    new_authors = clean_author_list(authors)
    if new_authors and is_empty(row.get("authors")):
        row["authors"] = new_authors
        filled.append("authors")
    new_abstract = cap_abstract(abstract)
    if new_abstract and is_empty(row.get("abstract")):
        row["abstract"] = new_abstract
        filled.append("abstract")
    clean_title = " ".join(str(title or "").split())
    if clean_title and not is_junk_title(clean_title) and (is_empty(row.get("title")) or is_junk_title(row.get("title"))):
        row["title"] = clean_title
        filled.append("title")
    clean_venue = " ".join(str(venue or "").split())[:300]
    if clean_venue and is_empty(row.get("venue")):
        row["venue"] = clean_venue
        filled.append("venue")
    if isinstance(year, int) and 1000 <= year <= 2200 and row.get("publication_year") is None:
        row["publication_year"] = year
        filled.append("publication_year")
    return filled


def fill_row_metadata(manifest: Path, match: Callable[[dict[str, Any]], bool], *, authors: list[str] | None,
                      abstract: str | None, title: str | None = None, venue: str | None = None,
                      year: int | None = None) -> list[str]:
    """Fill the missing authors/abstract of the manifest row ``match`` selects, writing the manifest atomically.

    Runs under the same per-category lock as :func:`record_upload`, re-reads the file (so a row added meanwhile is
    kept), changes only empty fields of the first matching row and replaces the file only when something changed.
    An unreadable manifest, a missing row or an unwritable folder leaves everything as it was.

    Returns:
        The names of the fields filled.
    """
    if not (authors or abstract or title or venue or year) or not manifest.is_file():
        return []
    try:
        with (manifest.parent / ".manifest.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                papers = data.get("papers") if isinstance(data, dict) else None
                if not isinstance(papers, list):
                    return []
                row = next((r for r in papers if isinstance(r, dict) and match(r)), None)
                filled = (fill_missing(row, authors=authors, abstract=abstract, title=title, venue=venue, year=year)
                          if row is not None else [])
                if filled:
                    tmp = manifest.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
                    tmp.replace(manifest)
                return filled
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except (OSError, ValueError) as exc:
        logger.warning("manifest metadata not updated in %s: %s", manifest.parent.name, type(exc).__name__)
        return []


__all__ = ("PDF_PATH_PREFIX", "fill_missing", "fill_row_metadata", "is_empty", "record_upload")
