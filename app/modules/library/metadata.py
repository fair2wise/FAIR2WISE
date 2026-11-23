"""Bibliographic metadata of library papers that is already stored (A8): year, authors, abstract.

The catalog (``catalog.sqlite``) keeps only title, DOI, page count and the verification result per paper. The rest
lives in the per-category ``<category>/manifest.json`` rows the harvest scripts and uploads write (``publication_year``,
``abstract``, and ``authors`` when a writer stored them), and in the year folder a paper is filed under. This module reads
those, read-only, so the list API can show what is stored and say plainly what is not. It fetches nothing.

A field that no source holds for a paper is ``None`` and is named in ``metadata_missing``; it is never guessed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import threading
from typing import Any

from .arrange import is_valid_folder_name

logger = logging.getLogger(__name__)

#: Longest abstract the list returns (the manifest keeps up to 4000 characters); longer ones are cut and flagged.
MAX_ABSTRACT_CHARS = 2000
#: Most authors the list returns per paper (the rest is a count the UI shows as ``+N``).
MAX_AUTHORS = 40
#: The fields this module reports on, in display order.
FIELDS = ("year", "authors", "abstract")

_YEAR_FOLDER = re.compile(r"^(?:19|20)\d{2}$")
_PREFIX = "papers/"


def _doi_key(value: object) -> str:
    raw = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    return raw


def _author_name(entry: object) -> str:
    if isinstance(entry, str):
        return " ".join(entry.split())
    if isinstance(entry, dict):
        author = entry.get("author")
        for source in (entry, author if isinstance(author, dict) else {}):
            for key in ("name", "display_name"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return " ".join(value.split())
    return ""


def clean_authors(value: object) -> tuple[list[str], int]:
    """``(names, total)``: author names from a manifest value (list of strings or dicts, or one ``;``-separated string)."""
    if isinstance(value, str):
        value = [part for part in value.split(";")]
    if not isinstance(value, list):
        return [], 0
    names = [name for name in (_author_name(entry) for entry in value) if name]
    return names[:MAX_AUTHORS], len(names)


def clean_abstract(value: object) -> tuple[str | None, bool]:
    """``(text | None, truncated)`` for a manifest abstract (whitespace collapsed, cut at :data:`MAX_ABSTRACT_CHARS`)."""
    if not isinstance(value, str):
        return None, False
    text = " ".join(value.split())
    if not text:
        return None, False
    return (text[:MAX_ABSTRACT_CHARS].rstrip() + "…", True) if len(text) > MAX_ABSTRACT_CHARS else (text, False)


def folder_year(relpath: str) -> int | None:
    """The year of ``<category>/<year>/<file>`` paths (the ``unknown`` folder and the root give ``None``)."""
    parts = relpath.split("/")
    return int(parts[1]) if len(parts) >= 3 and _YEAR_FOLDER.match(parts[1]) else None


class ManifestMetadata:
    """Read-only lookup of stored year, authors and abstract from the category manifests (cached by file stat)."""

    def __init__(self) -> None:
        """Create an empty cache."""
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}

    def _rows(self, manifest: Path) -> list[dict[str, Any]]:
        try:
            stat = manifest.stat()
        except OSError:
            return []
        key = str(manifest)
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] == stat.st_mtime_ns and hit[1] == stat.st_size:
                return hit[2]
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            rows = [r for r in data.get("papers", []) if isinstance(r, dict)] if isinstance(data, dict) else []
        except (OSError, ValueError):
            logger.warning("manifest %s is not readable JSON; its metadata is reported as not stored", manifest.parent.name)
            rows = []
        with self._lock:
            self._cache[key] = (stat.st_mtime_ns, stat.st_size, rows)
        return rows

    def index(self, root: Path) -> _Index:
        """The lookup over every category manifest under ``root``."""
        by_path: dict[str, dict[str, Any]] = {}
        by_sha: dict[str, dict[str, Any]] = {}
        by_doi: dict[str, dict[str, Any]] = {}
        try:
            categories = sorted(c for c in root.iterdir()
                                if is_valid_folder_name(c.name) and c.is_dir() and not c.is_symlink())
        except OSError:
            categories = []
        for category in categories:
            for row in self._rows(category / "manifest.json"):
                path = str(row.get("pdf_path") or "").removeprefix(_PREFIX)
                if path:
                    by_path.setdefault(path, row)
                if row.get("pdf_sha256"):
                    by_sha.setdefault(str(row["pdf_sha256"]), row)
                if _doi_key(row.get("doi")):
                    by_doi.setdefault(_doi_key(row.get("doi")), row)
        return _Index(by_path, by_sha, by_doi)


class _Index:
    def __init__(self, by_path: dict[str, dict[str, Any]], by_sha: dict[str, dict[str, Any]],
                 by_doi: dict[str, dict[str, Any]]) -> None:
        self.by_path, self.by_sha, self.by_doi = by_path, by_sha, by_doi

    def describe(self, relpath: str, sha256: str, doi: str | None) -> dict[str, Any]:
        """The metadata fields of one paper.

        Returns:
            ``year`` (``int | None``), ``year_source`` (``manifest`` | ``folder`` | ``None``), ``authors``
            (``list[str] | None``), ``authors_total`` (int), ``abstract`` (``str | None``), ``abstract_truncated`` and
            ``metadata_missing`` (the names among :data:`FIELDS` no source holds).
        """
        row = self.by_path.get(relpath) or self.by_sha.get(sha256) or (self.by_doi.get(_doi_key(doi)) if doi else None) or {}
        year, source = None, None
        stored_year = row.get("publication_year")
        if isinstance(stored_year, int) and not isinstance(stored_year, bool) and 1000 <= stored_year <= 2200:
            year, source = stored_year, "manifest"
        elif (from_path := folder_year(relpath)) is not None:
            year, source = from_path, "folder"
        names, total = clean_authors(row.get("authors"))
        abstract, truncated = clean_abstract(row.get("abstract"))
        out: dict[str, Any] = {
            "year": year, "year_source": source, "authors": names or None, "authors_total": total,
            "abstract": abstract, "abstract_truncated": truncated,
        }
        out["metadata_missing"] = [f for f in FIELDS if out[f] is None]
        return out


__all__ = ("FIELDS", "MAX_ABSTRACT_CHARS", "MAX_AUTHORS", "ManifestMetadata", "clean_abstract", "clean_authors",
           "folder_year")
