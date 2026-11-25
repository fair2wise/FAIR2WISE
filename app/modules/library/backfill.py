"""Backfill authors and abstracts of already-harvested papers into the category manifests (P3b).

Papers harvested before the manifest kept authors and abstracts have ``abstract: null`` and no ``authors``. This
module walks ``<library>/<category>/manifest.json``, and for each row with a DOI (OpenAlex) or an arXiv id (arXiv)
that is missing either field it asks the source once and fills **only the missing fields**; a non-empty stored value
is never replaced. Rows without a DOI or arXiv id are reported as ``unfillable``: nothing is guessed from a title.

* A dry run (the default of the command) never touches the network and writes nothing: it counts what would be asked.
* ``apply`` fetches through the hardened helpers the Harvester uses (egress policy, ``OPENALEX_API_KEY`` as an
  ``Authorization`` header, the process-wide 3 s arXiv spacing, one ``Retry-After`` wait) and rewrites a manifest
  atomically under its per-category lock, one row at a time, so an interrupted run loses nothing and a rerun skips
  every row that is already filled.
* Hosts touched: ``api.openalex.org`` and ``export.arxiv.org`` only.

Disclosure: the report holds counts, library-relative PDF paths and short codes. Authors, abstracts and titles are
never put in a log line, a report or an error message.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
import time
from typing import Any

from ..literature.legacy_download import (
    ArxivEntry,
    JSONFetchError,
    arxiv_lookup,
    hardened_get_json,
    openalex_api_key,
    openalex_get,
)
from .arrange import is_valid_folder_name
from .bibmeta import cap_abstract, clean_author_list, openalex_abstract, openalex_authors
from ..term_extractor.pub_clean import is_junk_title
from .manifest import PDF_PATH_PREFIX, fill_row_metadata, is_empty

logger = logging.getLogger(__name__)

#: Fields OpenAlex is asked for (the minimum the backfill needs).
OPENALEX_SELECT = "id,doi,authorships,abstract_inverted_index"
#: What the repair also asks for: the title, the year and the venue (``primary_location.source``).
OPENALEX_SELECT_REPAIR = "id,doi,title,publication_year,primary_location,authorships,abstract_inverted_index"
OPENALEX_SOURCE, ARXIV_SOURCE = "openalex", "arxiv"
#: Outcome codes of one row (the report counts each).
OUTCOMES = ("updated", "not_found", "no_data", "failed", "unfillable", "already_filled", "deferred", "would_fetch")

_ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z][a-z\-]*(?:\.[A-Za-z]{2})?/\d{7})(?:v\d+)?$")

#: ``fetch(doi) -> {"authors", "abstract"} | None`` (``None``: the source has no such work).
OpenAlexFetch = Callable[[str], Mapping[str, Any] | None]
#: ``fetch(arxiv id) -> {"authors", "abstract"} | None``.
ArxivFetch = Callable[[str], Mapping[str, Any] | None]


class BackfillAbort(Exception):
    """The run cannot continue (no OpenAlex key, quota spent); the message is a short code, never a source text."""


@dataclass
class RowResult:
    """The outcome of one manifest row (``pdf_path`` is the row's library-relative path; no bibliographic text)."""

    category: str
    pdf_path: str
    outcome: str
    code: str = ""
    filled: list[str] = field(default_factory=list)


@dataclass
class BackfillReport:
    """What a run did."""

    applied: bool
    rows: list[RowResult] = field(default_factory=list)
    unreadable_manifests: list[str] = field(default_factory=list)
    stopped: str = ""

    def counts(self) -> dict[str, int]:
        """Row counts by outcome (every name in :data:`OUTCOMES`, zero when none), plus ``failed`` by code."""
        tally = Counter(r.outcome for r in self.rows)
        return {name: tally.get(name, 0) for name in OUTCOMES}

    def failed_codes(self) -> dict[str, int]:
        """The ``failed`` rows grouped by short code."""
        return dict(Counter(r.code for r in self.rows if r.outcome == "failed"))

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe summary: counts, failure codes and the rows that need a person's eye."""
        return {
            "mode": "apply" if self.applied else "dry-run", "counts": self.counts(), "failed_codes": self.failed_codes(),
            "unreadable_manifests": self.unreadable_manifests, "stopped": self.stopped or None,
            "rows": [{"category": r.category, "pdf_path": r.pdf_path, "outcome": r.outcome, "code": r.code,
                      "filled": r.filled}
                     for r in self.rows if r.outcome not in ("already_filled", "unfillable")],
            "unfillable": [{"category": r.category, "pdf_path": r.pdf_path} for r in self.rows
                           if r.outcome == "unfillable"],
        }


def _doi_key(value: object) -> str:
    raw = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    return raw


def _arxiv_key(value: object) -> str:
    raw = str(value or "").strip()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv:", "arXiv:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    return raw if _ARXIV_ID.match(raw) else ""


def openalex_fetcher(*, mailto: str | None = None, get_json: Callable[..., Any] = hardened_get_json,
                     sleep: Callable[[float], None] | None = None, repair: bool = False) -> OpenAlexFetch:
    """A fetch of one work by DOI over the hardened OpenAlex helper (key from the environment, header only).

    Raises (when called):
        BackfillAbort: ``openalex_no_key`` without ``OPENALEX_API_KEY``; ``openalex_quota`` on a 402/429 that names
            no wait.
        JSONFetchError: any other transport or policy failure (the caller records a code).
    """
    def fetch(doi: str) -> Mapping[str, Any] | None:
        key = openalex_api_key()
        if key is None:
            raise BackfillAbort("openalex_no_key")
        try:
            rows, _ = openalex_get("works", filters={"doi": doi}, per_page=1, params={"select": OPENALEX_SELECT_REPAIR if repair else OPENALEX_SELECT},
                                   mailto=mailto, api_key=key, get_json=get_json, sleep=sleep)
        except JSONFetchError as exc:
            if exc.status in (402, 429):
                raise BackfillAbort("openalex_quota") from exc
            raise
        work = rows[0] if rows else None
        if work is None:
            return None
        found: dict[str, Any] = {"authors": openalex_authors(work), "abstract": openalex_abstract(work)}
        if repair:
            location = work.get("primary_location")
            source = location.get("source") if isinstance(location, Mapping) else None
            year = work.get("publication_year")
            found.update(title=" ".join(str(work.get("title") or "").split()) or None,
                         venue=(source.get("display_name") if isinstance(source, Mapping) else None) or None,
                         year=year if isinstance(year, int) else None)
        return found

    return fetch


def arxiv_fetcher(lookup: Callable[[str], ArxivEntry | None] = arxiv_lookup, *, repair: bool = False) -> ArxivFetch:
    """A fetch of one arXiv entry by id over the hardened, rate-limited arXiv helper."""
    def fetch(arxiv_id: str) -> Mapping[str, Any] | None:
        entry = lookup(arxiv_id)
        if entry is None:
            return None
        found: dict[str, Any] = {"authors": clean_author_list(entry.get("authors")), "abstract": cap_abstract(entry.get("summary"))}
        if repair:
            found.update(title=" ".join(str(entry.get("title") or "").split()) or None, venue="arXiv", year=None)
        return found

    return fetch


def _failure_code(source: str, exc: Exception) -> str:
    if isinstance(exc, JSONFetchError):
        if exc.status is not None:
            return f"{source}_http_{exc.status}"
        return f"{source}_blocked" if str(exc).startswith("blocked URL") else f"{source}_unreachable"
    if isinstance(exc, ValueError):
        return f"{source}_bad_response"
    return f"{source}_error"


def _manifests(root: Path, only: str | None) -> list[Path]:
    try:
        names = sorted(c.name for c in root.iterdir()
                       if is_valid_folder_name(c.name) and c.is_dir() and not c.is_symlink())
    except OSError:
        return []
    return [root / n / "manifest.json" for n in names if only in (None, n) and (root / n / "manifest.json").is_file()]


def _matcher(row: Mapping[str, Any]) -> Callable[[dict[str, Any]], bool]:
    path, sha, doi = row.get("pdf_path"), row.get("pdf_sha256"), _doi_key(row.get("doi"))

    def match(other: dict[str, Any]) -> bool:
        if path:
            return bool(other.get("pdf_path") == path)
        if sha:
            return bool(other.get("pdf_sha256") == sha)
        return bool(doi) and _doi_key(other.get("doi")) == doi

    return match


def backfill(root: Path, *, apply: bool = False, limit: int | None = None, delay: float = 1.0,
             category: str | None = None, openalex: OpenAlexFetch | None = None, arxiv: ArxivFetch | None = None,
             sleep: Callable[[float], None] = time.sleep, repair: bool = False) -> BackfillReport:
    """Fill the missing authors/abstracts of the manifest rows under ``root`` (see the module docstring).

    Args:
        root: The library directory (``F2W_LIBRARY_DIR``); only manifests directly under its categories are read,
            and only with ``apply`` is anything written (the manifests, nothing else).
        apply: Fetch and write. ``False`` is a dry run: no network, no write.
        limit: Ask the sources for at most this many rows (``None``: all); the rest are ``deferred``. A dry run
            counts the rows it would ask (``would_fetch``) under the same limit.
        delay: Seconds to wait between rows that were asked of a source (the arXiv 3 s floor applies on top).
        category: Only this category folder.
        openalex: The OpenAlex fetch (tests pass a fake); the hardened one by default.
        arxiv: The arXiv fetch (tests pass a fake); the hardened one by default.
        sleep: Pause function for ``delay``.
        repair: Also repair the publication record: a missing or junk title (a file path, an ``f2w://`` URI, a file name)
            is replaced by the registry's, and a missing ``venue`` and ``publication_year`` are filled. Without it only
            authors and abstracts are filled, as before.

    Returns:
        The report. A :class:`BackfillAbort` (no key, quota spent) stops the run: the remaining rows are
        ``deferred`` and ``stopped`` holds the short code.
    """
    report = BackfillReport(applied=apply)
    oa = openalex or openalex_fetcher(repair=repair)
    ax = arxiv or arxiv_fetcher(repair=repair)
    asked = 0
    for manifest in _manifests(root, category):
        cat = manifest.parent.name
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report.unreadable_manifests.append(f"{cat}/manifest.json")
            continue
        papers = data.get("papers") if isinstance(data, dict) else None
        if not isinstance(papers, list):
            report.unreadable_manifests.append(f"{cat}/manifest.json")
            continue
        for row in (r for r in papers if isinstance(r, dict)):
            where = str(row.get("pdf_path") or row.get("doi") or row.get("arxiv_id") or "?").removeprefix(
                f"{PDF_PATH_PREFIX}/")
            need_authors, need_abstract = is_empty(row.get("authors")), is_empty(row.get("abstract"))
            need_record = repair and (is_empty(row.get("title")) or is_junk_title(row.get("title"))
                                      or is_empty(row.get("venue")) or row.get("publication_year") is None)
            if not (need_authors or need_abstract or need_record):
                report.rows.append(RowResult(cat, where, "already_filled"))
                continue
            doi, arxiv_id = _doi_key(row.get("doi")), _arxiv_key(row.get("arxiv_id"))
            if not doi and not arxiv_id:
                report.rows.append(RowResult(cat, where, "unfillable", "no_doi_or_arxiv_id"))
                continue
            if report.stopped or (limit is not None and asked >= limit):
                report.rows.append(RowResult(cat, where, "deferred"))
                continue
            if not apply:
                asked += 1
                report.rows.append(RowResult(cat, where, "would_fetch"))
                continue
            if asked:
                sleep(delay)
            asked += 1
            result = _fetch_row(oa, ax, doi, arxiv_id, need_authors, need_abstract, need_record)
            result.category, result.pdf_path = cat, where
            if result.outcome == "found":
                filled = fill_row_metadata(manifest, _matcher(row), authors=result.authors, abstract=result.abstract,
                                           title=result.title if repair else None, venue=result.venue if repair else None,
                                           year=result.year if repair else None)
                result.outcome, result.filled = ("updated", filled) if filled else ("no_data", [])
            if result.code in ("openalex_no_key", "openalex_quota"):
                report.stopped = result.code
                result.outcome = "deferred"
            report.rows.append(RowResult(cat, where, result.outcome, result.code, result.filled))
    return report


@dataclass
class _Fetched:
    outcome: str
    code: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    title: str | None = None
    venue: str | None = None
    year: int | None = None
    filled: list[str] = field(default_factory=list)
    category: str = ""
    pdf_path: str = ""


def _fetch_row(oa: OpenAlexFetch, ax: ArxivFetch, doi: str, arxiv_id: str, need_authors: bool,
               need_abstract: bool, need_record: bool = False) -> _Fetched:
    """Ask OpenAlex (DOI), then arXiv (id) when something is still missing; combine what they returned."""
    out = _Fetched("not_found")
    errors: list[str] = []
    for source, ask, key in ((OPENALEX_SOURCE, oa, doi), (ARXIV_SOURCE, ax, arxiv_id)):
        if not key or (not (need_authors and not out.authors) and not (need_abstract and not out.abstract)
                       and not (need_record and not (out.title and out.venue and out.year))):
            continue
        try:
            got = ask(key)
        except BackfillAbort as exc:
            return _Fetched("failed", str(exc))
        except Exception as exc:  # noqa: BLE001 - one row's failure must not stop the run; only a short code is kept
            errors.append(_failure_code(source, exc))
            continue
        if got is None:
            continue
        out.outcome = "found"
        out.authors = out.authors or list(got.get("authors") or [])
        out.abstract = out.abstract or got.get("abstract")
        out.title = out.title or got.get("title")
        out.venue = out.venue or got.get("venue")
        out.year = out.year or got.get("year")
    if out.outcome == "not_found" and errors:
        return _Fetched("failed", errors[0])
    return out


__all__ = ("BackfillAbort", "BackfillReport", "OUTCOMES", "RowResult", "arxiv_fetcher", "backfill", "openalex_fetcher")
