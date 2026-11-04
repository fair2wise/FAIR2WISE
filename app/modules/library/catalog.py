"""Sidecar catalog of the mounted papers folder (A8, T35): ``<F2W_WORKDIR>/library/catalog.sqlite``.

The folder is the source of truth for *which* PDFs exist; the catalog remembers what was computed
about them so a listing does not re-hash or re-parse: sha256, local title and DOI, and the
**verification cache keyed by sha256** (a renamed or duplicated file keeps its verified result;
changed bytes lose it). It lives in the state volume, never in the (user-owned) papers folder, and
holds no PDF bytes. Two processes (API and worker) share it: WAL mode and a busy timeout.
"""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
  relpath TEXT PRIMARY KEY, id TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,
  title TEXT, doi TEXT, doi_source TEXT, meta_status TEXT NOT NULL DEFAULT 'pending',
  index_status TEXT, first_seen REAL NOT NULL, pages INTEGER, indexed_sha256 TEXT);
CREATE INDEX IF NOT EXISTS papers_id ON papers(id);
CREATE INDEX IF NOT EXISTS papers_sha ON papers(sha256);
CREATE TABLE IF NOT EXISTS verifications (
  sha256 TEXT PRIMARY KEY, status TEXT NOT NULL, title TEXT, doi TEXT, detail TEXT, verified_at REAL NOT NULL);
"""

#: Verification outcomes that are settled and therefore cached (the others are retried).
CACHED_VERIFY_STATUSES = frozenset({"verified", "not_found"})


def paper_id(relpath: str) -> str:
    """Opaque stable id of a library paper: the first 16 hex of ``sha256(relpath)``."""
    return hashlib.sha256(relpath.encode("utf-8")).hexdigest()[:16]


class LibraryCatalog:
    """A small thread-safe SQLite store (see the module docstring).

    Args:
        path: Database file (parent directories are created).
        clock: Epoch seconds, injectable.
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30.0)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._con.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:  # pragma: no cover - a filesystem without WAL support
                logger.debug("WAL journal mode unavailable for %s", self.path, exc_info=True)
            self._con.executescript(SCHEMA)
            have = {row["name"] for row in self._con.execute("PRAGMA table_info(papers)")}
            for column, kind in (("pages", "INTEGER"), ("indexed_sha256", "TEXT")):  # T36: catalogs made by T35
                if column not in have:
                    self._con.execute(f"ALTER TABLE papers ADD COLUMN {column} {kind}")
            self._con.commit()

    def _query(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._con.execute(sql, tuple(args)).fetchall())

    def _write(self, sql: str, args: Iterable[Any] = ()) -> None:
        with self._lock:
            self._con.execute(sql, tuple(args))
            self._con.commit()

    # -- papers ---------------------------------------------------------------------------------

    def paper(self, relpath: str) -> dict[str, Any] | None:
        """The row of ``relpath`` or ``None``."""
        rows = self._query("SELECT * FROM papers WHERE relpath=?", (relpath,))
        return dict(rows[0]) if rows else None

    def paper_by_id(self, pid: str) -> dict[str, Any] | None:
        """The row with id ``pid`` or ``None``."""
        rows = self._query("SELECT * FROM papers WHERE id=?", (pid,))
        return dict(rows[0]) if rows else None

    def paper_by_sha(self, sha256: str) -> dict[str, Any] | None:
        """One row with these bytes (the earliest seen) or ``None``."""
        rows = self._query("SELECT * FROM papers WHERE sha256=? ORDER BY first_seen, relpath LIMIT 1", (sha256,))
        return dict(rows[0]) if rows else None

    def upsert_paper(self, *, relpath: str, sha256: str, size: int, mtime: float, title: str | None,
                     doi: str | None, doi_source: str | None, meta_status: str, pages: int | None = None) -> None:
        """Insert or replace a row, keeping ``first_seen``, the index record and (for unchanged bytes) the page count.

        The index record (``index_status`` and ``indexed_sha256``) survives a change of bytes so the paper
        reads ``stale`` until the next index run; a page count is only kept for the same bytes.
        """
        with self._lock:
            old = self.paper(relpath)
            first = old["first_seen"] if old else self._clock()
            index_status = old["index_status"] if old else None
            indexed_sha = old["indexed_sha256"] if old else None
            if pages is None and old and old["sha256"] == sha256:
                pages = old["pages"]
            self._con.execute(
                "INSERT OR REPLACE INTO papers (relpath,id,sha256,size,mtime,title,doi,doi_source,meta_status,"
                "index_status,first_seen,pages,indexed_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (relpath, paper_id(relpath), sha256, int(size), float(mtime), title, doi, doi_source, meta_status,
                 index_status, first, int(pages) if pages else None, indexed_sha))
            self._con.commit()

    def set_index_state(self, relpath: str, *, status: str | None, sha256: str | None, pages: int | None = None,
                        title: str | None = None, doi: str | None = None) -> None:
        """Record the outcome of indexing ``relpath`` (``ok`` / ``no_text``; ``None`` clears it).

        ``pages`` fills a missing page count and ``title`` / ``doi`` fill missing local metadata (they never
        replace a value a probe already found).
        """
        with self._lock:
            self._con.execute("UPDATE papers SET index_status=?, indexed_sha256=? WHERE relpath=?",
                              (status, sha256 if status else None, relpath))
            if pages:
                self._con.execute("UPDATE papers SET pages=? WHERE relpath=? AND (pages IS NULL OR pages=0)",
                                  (int(pages), relpath))
            if title:
                self._con.execute("UPDATE papers SET title=? WHERE relpath=? AND (title IS NULL OR title='')",
                                  (title, relpath))
            if doi:
                self._con.execute("UPDATE papers SET doi=?, doi_source=COALESCE(doi_source,'pdf') "
                                  "WHERE relpath=? AND (doi IS NULL OR doi='')", (doi, relpath))
            self._con.commit()

    def fill_identity(self, relpath: str, *, title: str | None, doi: str | None, source: str) -> None:
        """Fill a missing title or DOI from what the registry told the harvester (never replaces a probe's value)."""
        with self._lock:
            if title:
                self._con.execute("UPDATE papers SET title=? WHERE relpath=? AND (title IS NULL OR title='')",
                                  (title, relpath))
            if doi:
                self._con.execute("UPDATE papers SET doi=?, doi_source=? WHERE relpath=? AND (doi IS NULL OR doi='')",
                                  (doi, source, relpath))
            self._con.commit()

    def prune(self, keep: Iterable[str]) -> int:
        """Delete rows whose relpath is not in ``keep`` (the file is gone); returns how many."""
        keep_set = set(keep)
        with self._lock:
            gone = [r["relpath"] for r in self._con.execute("SELECT relpath FROM papers") if r["relpath"] not in keep_set]
            for rel in gone:
                self._con.execute("DELETE FROM papers WHERE relpath=?", (rel,))
            self._con.commit()
        return len(gone)

    def rows(self) -> list[dict[str, Any]]:
        """All papers joined with their cached verification, ordered by relpath."""
        return [dict(r) for r in self._query(
            "SELECT p.*, v.status AS verify_status, v.title AS verified_title, v.doi AS verified_doi, "
            "v.detail AS verify_detail, v.verified_at AS verified_at FROM papers p "
            "LEFT JOIN verifications v ON v.sha256 = p.sha256 ORDER BY p.relpath")]

    def indexed_count(self) -> int:
        """Papers indexed with text (``index_status='ok'``) for their current bytes (not ``stale``)."""
        return int(self._query("SELECT COUNT(*) AS n FROM papers WHERE index_status='ok' "
                               "AND indexed_sha256=sha256")[0]["n"])

    # -- verification cache ----------------------------------------------------------------------

    def verification(self, sha256: str) -> dict[str, Any] | None:
        """The cached settled verification of these bytes or ``None``."""
        rows = self._query("SELECT * FROM verifications WHERE sha256=?", (sha256,))
        return dict(rows[0]) if rows else None

    def save_verification(self, sha256: str, *, status: str, title: str | None, doi: str | None,
                          detail: str | None) -> None:
        """Cache a settled verification (``verified`` or ``not_found``) for these bytes."""
        if status not in CACHED_VERIFY_STATUSES:
            raise ValueError(f"only settled results are cached, not {status!r}")
        self._write("INSERT OR REPLACE INTO verifications (sha256,status,title,doi,detail,verified_at) "
                    "VALUES (?,?,?,?,?,?)", (sha256, status, title, doi, detail, self._clock()))

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._con.close()


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """Streaming sha256 of a file (not following a link at the leaf)."""
    import os

    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = ("CACHED_VERIFY_STATUSES", "LibraryCatalog", "paper_id", "sha256_file")
