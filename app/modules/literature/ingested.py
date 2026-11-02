"""Which papers are already ingested, so a folder intake does not repeat them (L-06).

Two kinds of "already done", kept separate:

* **content in the graph or staged** - a publication whose sections are in the
  graph, or a complete staged package, by folded DOI (donor semantics: a bare
  publication node from metadata alone does not count);
* **this source through this pipeline** - ``(source sha256, pipeline fingerprint)``
  pairs completed. A new parser or extraction pipeline revision is *not* skipped
  just because the DOI was seen before (plan T05: skip decisions keyed by
  source+pipeline revision, not DOI alone).

The graph is read through plain iterables of section/publication records so this
module does not depend on a graph backend (T08 provides the GraphPort reads).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/ingested_papers.py (``IngestedLookup``,
``doi_key``, ``graph_ingested_keys``, ``staged_keys``, ``IngestedPapers``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import logging
from pathlib import Path
import re
import threading
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_PACKAGE_HEAD_BYTES = 64 * 1024
_WORK_DOI = re.compile(r'"work"\s*:\s*\{.*?"doi"\s*:\s*"([^"]*)"', re.DOTALL)
_COMPLETE = re.compile(r'"complete"\s*:\s*(true|false)')


class IngestedLookup(Protocol):
    """What a folder intake needs: ask before ingesting, record after."""

    def reason(self, doi: str, *, source_sha256: str = "", pipeline: str = "") -> str | None:
        """Return why this paper should be skipped, or ``None`` to ingest it."""
        ...

    def record(self, doi: str, *, source_sha256: str = "", pipeline: str = "") -> None:
        """Remember a paper that was ingested."""
        ...


def fold_key(value: str) -> str:
    """Alphanumerics only, lowercased (the repo-wide identifier fold)."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def doi_key(value: str) -> str:
    """One DOI spelling to its folded key (``publication:``/URL prefixes dropped)."""
    text = str(value or "").strip()
    if text.lower().startswith("publication:"):
        text = text[len("publication:"):]
    lowered = text.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    return fold_key(text)


def _publication_keys(publication: Mapping[str, Any]) -> set[str]:
    keys = {doi_key(str(publication.get(name) or "")) for name in ("doi", "openalex_id", "id", "publication_id")}
    keys.discard("")
    return keys


def graph_ingested_keys(sections: Iterable[Mapping[str, Any]],
                        publications: Iterable[Mapping[str, Any]]) -> set[str]:
    """Folded DOI keys of every publication whose *content* (a section) is in the graph."""
    section_keys = {doi_key(str(s.get("publication_id") or "")) for s in sections}
    section_keys.discard("")
    keys = set(section_keys)
    for publication in publications:
        if _publication_keys(publication) & section_keys:
            doi = doi_key(str(publication.get("doi") or ""))
            if doi:
                keys.add(doi)
    return keys


def _package_doi(path: Path) -> tuple[bool, str]:
    with path.open("rb") as stream:
        head = stream.read(_PACKAGE_HEAD_BYTES).decode("utf-8", "replace")
    complete, doi = _COMPLETE.search(head), _WORK_DOI.search(head)
    if complete and doi:
        return complete.group(1) == "true", doi.group(1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("complete") is True, str((payload.get("work") or {}).get("doi") or "")


def staged_keys(root: Path | None) -> set[str]:
    """Folded DOI keys of every complete package in a staging directory (``*/paper.json``)."""
    keys: set[str] = set()
    if root is None or not Path(root).is_dir():
        return keys
    for path in Path(root).glob("*/paper.json"):
        try:
            complete, doi = _package_doi(path)
        except (OSError, ValueError) as exc:
            logger.warning("staged package unreadable, not counted as ingested: %s (%s)", path.name, exc)
            continue
        key = doi_key(doi)
        if complete and key:
            keys.add(key)
    return keys


class IngestedPapers:
    """Ingested DOIs, completed (source, pipeline) pairs, and what this run did so far. Thread-safe.

    Args:
        in_graph: Folded DOI keys with content in the graph.
        staged: Folded DOI keys with a complete staged package.
        completed: ``(source_sha256, pipeline)`` pairs already processed.
        doi_only: When ``True`` (donor behaviour) a DOI in the graph or staged
            is skipped regardless of pipeline; when ``False`` only an identical
            ``(source, pipeline)`` pair is skipped.
    """

    def __init__(self, in_graph: Iterable[str] = (), staged: Iterable[str] = (),
                 completed: Iterable[tuple[str, str]] = (), *, doi_only: bool = True) -> None:
        self._graph = set(in_graph)
        self._staged = set(staged)
        self._completed = set(completed)
        self._this_run: set[str] = set()
        self.doi_only = doi_only
        self._lock = threading.Lock()

    def reason(self, doi: str, *, source_sha256: str = "", pipeline: str = "") -> str | None:
        """Why this paper should be skipped, or ``None`` to ingest it."""
        key = doi_key(doi)
        with self._lock:
            if source_sha256 and pipeline and (source_sha256, pipeline) in self._completed:
                return "this source was already processed by this pipeline revision"
            if not key:
                return None
            if key in self._this_run:
                return "ingested earlier in this run from another file"
            if self.doi_only and key in self._graph:
                return "already in the graph (its sections are there)"
            if self.doi_only and key in self._staged:
                return "already staged for import (a complete package is waiting)"
        return None

    def record(self, doi: str, *, source_sha256: str = "", pipeline: str = "") -> None:
        """Remember a paper this run ingested."""
        key = doi_key(doi)
        with self._lock:
            if key:
                self._this_run.add(key)
            if source_sha256 and pipeline:
                self._completed.add((source_sha256, pipeline))

    def __len__(self) -> int:
        return len(self._graph | self._staged)


__all__ = ("IngestedLookup", "IngestedPapers", "doi_key", "fold_key", "graph_ingested_keys", "staged_keys")
