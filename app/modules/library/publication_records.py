"""The library's own record of a paper as the source of truth for its publication metadata.

A harvested paper's manifest row already holds the real title, authors, year, DOI and abstract the registry (OpenAlex or
arXiv) gave. The extractor used to read the PDF's Info fields and first-page text instead, which for scans and re-saved
files are tool names, user names, file paths and file dates (pilot 2026-11-25: a title ``f2w://path/2010-463.vp``, an author
``Administrator``, a postal address as authors, a 1904 monograph dated 2008). This module reads the manifest rows, judges
which values are real, and offers the overlay the extractor and the recovery job apply:

* the library record wins for every field it has;
* a PDF value fills a field the record lacks, but only when it is not junk (:func:`is_junk_title`,
  :func:`is_junk_author`);
* a file path, an ``f2w://`` URI or a file name is never a title.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from ..term_extractor.pub_clean import (
    FIELDS,
    is_junk_author,
    is_junk_title,
    merge_known_publication,
    sanitize_pdf_meta,
)
from .bibmeta import cap_abstract, clean_author_list
from .manifest import PDF_PATH_PREFIX


def meta_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Publication metadata of one manifest row: only fields that are present and not junk."""
    out: dict[str, Any] = {}
    title = " ".join(str(row.get("title") or "").split())
    if title and not is_junk_title(title):
        out["paper_title"] = title
    authors = [a for a in clean_author_list(row.get("authors") if isinstance(row.get("authors"), list) else []) if not is_junk_author(a)]
    if authors:
        out["authors"] = authors
    year = row.get("publication_year")
    if isinstance(year, int) and 1000 <= year <= 2200:
        out["publication_year"] = year
    doi = str(row.get("doi") or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
    if doi:
        out["doi"] = doi
    venue = " ".join(str(row.get("venue") or "").split())
    if venue:
        out["journal"] = venue
    abstract = cap_abstract(row.get("abstract"))
    if abstract:
        out["abstract_text"] = abstract
    return out


class PublicationRecords:
    """The manifest rows of a library folder, looked up by PDF file name (and sha256 when two files share a name)."""

    def __init__(self, root: Path) -> None:
        self._by_name: dict[str, list[Mapping[str, Any]]] = {}
        try:
            manifests = sorted(root.glob("*/manifest.json"))
        except OSError:
            manifests = []
        for manifest in manifests:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for row in (data.get("papers") if isinstance(data, dict) else None) or []:
                if isinstance(row, dict) and row.get("pdf_path"):
                    name = Path(str(row["pdf_path"]).removeprefix(f"{PDF_PATH_PREFIX}/")).name
                    self._by_name.setdefault(name, []).append(row)

    def source_for(self, name: str, sha256: str | None = None) -> str | None:
        """The registry a library file came from (the manifest row's ``source``: ``openalex``, ``arxiv``, ``upload`` ...), or ``None``."""
        rows = self._by_name.get(name) or []
        if len(rows) > 1 and sha256:
            rows = [r for r in rows if r.get("pdf_sha256") == sha256]
        if len(rows) != 1:
            return None
        source = str(rows[0].get("source") or "").strip().lower()
        return source or None

    def for_file(self, name: str, sha256: str | None = None) -> dict[str, Any] | None:
        """The publication metadata of the library file ``name``, or ``None`` (no row, or two rows and no hash to tell)."""
        rows = self._by_name.get(name) or []
        if len(rows) > 1 and sha256:
            rows = [r for r in rows if r.get("pdf_sha256") == sha256]
        if len(rows) != 1:
            return None
        return meta_from_row(rows[0]) or None


__all__ = ("FIELDS", "PublicationRecords", "is_junk_author", "is_junk_title", "merge_known_publication", "meta_from_row",
           "sanitize_pdf_meta")
