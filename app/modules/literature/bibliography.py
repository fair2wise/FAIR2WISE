"""Provenance-tagged bibliographic records and the effective-publication read rule (L-07/L-08).

A publication ingested while the registry was unreachable carries only its DOI
(``metadata_source="pdf_only"``). Instead of rewriting the publication (immutable
source metadata), a *bibliographic record* is proposed: normalized to one field
set whichever provider answered, content-addressed on ``(source, version, record)``
and linked newest-wins. Readers fill gaps from the newest record; the
publication's own non-empty value always wins.

Enrichment is a provenance-tagged statement about the paper ("OpenAlex says its
title is ..."), never evidence that the paper itself made a claim.

This module only *proposes* (:func:`propose_backfill`); applying the record and
link is a graph write through T08's GraphPort and the approved publication path.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/bibliography_backfill.py
(``normalise_record``, ``record_digest``, ``needs_backfill``, ``BackfillResult``,
``backfill_publication``) and src/forge/kg/bibliography.py (``is_placeholder_name``,
``newest_bibliography_link``, ``effective_publication``, ``author_names``) (BSD-3-Clause).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Tuple

#: Bumped when :func:`normalise_record` changes shape.
BIBLIO_VERSION = "1"
#: Placeholder predicate name until T08 maps it into the F2W graph vocabulary
#: (the donor's ``forge:hasBibliography`` is not imported as an authority).
HAS_BIBLIOGRAPHY = "hasBibliography"
BIBLIOGRAPHIC_FIELDS: Tuple[str, ...] = (
    "authors", "year", "journal", "doi", "openalex_id", "referenced_works", "topics", "oa_status",
)


class ResolvedLike(Protocol):
    """A :class:`.resolver_batch.Resolved`, structurally."""

    source: str
    record: Optional[Dict[str, Any]]
    fetched_at: str


def _bare_doi(value: object) -> str:
    text = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def _year(value: object) -> Optional[int]:
    try:
        year = int(str(value)[:4])
    except (TypeError, ValueError):
        return None
    return year if 1600 <= year <= 2200 else None


def _strip(value: object, prefix: str) -> str:
    text = str(value or "")
    return text[len(prefix):] if text.startswith(prefix) else text


def _from_openalex(w: Mapping[str, Any]) -> Dict[str, Any]:
    authors = []
    for authorship in w.get("authorships") or []:
        author = authorship.get("author") or {}
        name = str(author.get("display_name") or "").strip()
        if name:
            authors.append({"name": name, "openalex_id": _strip(author.get("id"), "https://openalex.org/")})
    location = w.get("primary_location") or {}
    return {
        "title": str(w.get("title") or w.get("display_name") or ""), "year": _year(w.get("publication_year")),
        "journal": str((location.get("source") or {}).get("display_name") or ""), "authors": authors,
        "openalex_id": _strip(w.get("id"), "https://openalex.org/"), "doi": _bare_doi(w.get("doi")),
        "referenced_works": [_strip(r, "https://openalex.org/") for r in w.get("referenced_works") or []],
        "topics": [t.get("display_name") for t in w.get("topics") or [] if t.get("display_name")],
        "oa_status": (w.get("open_access") or {}).get("oa_status"),
        "best_oa_license": (w.get("best_oa_location") or {}).get("license"),
    }


def _from_semantic_scholar(rec: Mapping[str, Any]) -> Dict[str, Any]:
    external = rec.get("externalIds") or {}
    return {
        "title": str(rec.get("title") or ""), "year": _year(rec.get("year")),
        "journal": str(rec.get("venue") or ""),
        "authors": [{"name": str(a.get("name") or "").strip()} for a in rec.get("authors") or []
                    if str(a.get("name") or "").strip()],
        "openalex_id": "", "doi": _bare_doi(external.get("DOI")), "referenced_works": [], "topics": [],
        "oa_status": "open" if (rec.get("openAccessPdf") or {}).get("url") else None, "best_oa_license": None,
    }


def _from_crossref(rec: Mapping[str, Any]) -> Dict[str, Any]:
    year = None
    for name in ("issued", "published-print", "published-online"):
        parts = (rec.get(name) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            year = _year(parts[0][0])
            break
    authors = []
    for a in rec.get("author") or []:
        display = " ".join(p for p in (a.get("given"), a.get("family")) if p).strip() or str(a.get("name") or "")
        if display.strip():
            authors.append({"name": display.strip()})
    return {
        "title": " ".join(rec.get("title") or []).strip(), "year": year,
        "journal": " ".join(rec.get("container-title") or []).strip(), "authors": authors, "openalex_id": "",
        "doi": _bare_doi(rec.get("DOI")), "referenced_works": [], "topics": [], "oa_status": None,
        "best_oa_license": None,
    }


def normalise_record(source: str, record: Mapping[str, Any]) -> Dict[str, Any]:
    """One field set (title, year, journal, authors, ids) whatever provider answered.

    Raises:
        ValueError: For a source with no normalizer (an unreadable record is not a backfill).
    """
    if source.startswith("openalex"):
        return _from_openalex(record)
    if source.startswith("semantic_scholar") or source == "s2_title":
        return _from_semantic_scholar(record)
    if source.startswith("crossref"):
        return _from_crossref(record)
    raise ValueError(f"no normaliser for bibliographic source {source!r}")


def record_digest(source: str, record: Mapping[str, Any]) -> str:
    """Content address of a normalized record: same source and record, same node."""
    payload = json.dumps([source, BIBLIO_VERSION, record], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _fold(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", _bare_doi(value))


def _props(row: Mapping[str, Any]) -> Dict[str, Any]:
    raw = row.get("properties") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def is_placeholder_name(name: object, doi: object = "") -> bool:
    """Whether ``name`` is not a title: empty, ``"unknown"``, or the DOI itself (compared folded)."""
    folded = _fold(name)
    if not folded or folded == "unknown":
        return True
    return bool(doi) and folded == _fold(doi)


def needs_backfill(entity: Mapping[str, Any]) -> bool:
    """Whether a publication carries the pdf-only signature (marker, no OpenAlex id, or DOI as name)."""
    props = _props(entity)
    if str(props.get("metadata_source") or "") == "pdf_only":
        return True
    if not str(props.get("openalex_id") or "").strip():
        return True
    return is_placeholder_name(entity.get("name"), props.get("doi"))


@dataclass(frozen=True)
class BackfillProposal:
    """A proposed bibliographic record and link for one publication (nothing written).

    ``status``: ``proposed`` | ``unchanged`` (newest link already holds this
    digest) | ``no_record`` (verified negative).
    """

    publication_id: str
    status: str
    digest: str = ""
    record: Dict[str, Any] = field(default_factory=dict)
    link: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe form."""
        return asdict(self)


def propose_backfill(publication_id: str, resolved: ResolvedLike, *,
                     newest: Optional[Mapping[str, Any]] = None) -> BackfillProposal:
    """The record and link :func:`backfill_publication` would write, as a reviewable proposal."""
    if not resolved.record:
        return BackfillProposal(publication_id, "no_record")
    record = normalise_record(resolved.source, resolved.record)
    digest = record_digest(resolved.source, record)
    if newest and str(newest.get("record_digest") or "") == digest:
        return BackfillProposal(publication_id, "unchanged", digest)
    node = {**record, "kind": "bibliographic_record", "metadata_source": resolved.source,
            "resolved_at": resolved.fetched_at, "biblio_version": BIBLIO_VERSION, "record_digest": digest}
    link = {"subject": publication_id, "predicate": HAS_BIBLIOGRAPHY, "object_digest": digest,
            "properties": {"metadata_source": resolved.source, "biblio_version": BIBLIO_VERSION,
                           "record_digest": digest,
                           "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}}
    return BackfillProposal(publication_id, "proposed", digest, node, link)


def select_backfill_candidates(publications: Iterable[Mapping[str, Any]],
                               newest: Mapping[str, Mapping[str, Any]], *,
                               refresh: bool = False) -> List[Mapping[str, Any]]:
    """Publications needing a record: pdf-only ones without a link (or all of them with ``refresh``)."""
    out = []
    for publication in publications:
        pub_id = str(publication.get("id") or "")
        if not needs_backfill(publication):
            continue
        if pub_id in newest and not refresh:
            continue
        out.append(publication)
    return out


def newest_bibliography_link(rows: Iterable[Mapping[str, Any]], subject_id: str) -> Optional[Mapping[str, Any]]:
    """Newest ``hasBibliography`` row out of ``subject_id`` (by ``recorded_at``, ``createdAt``, ``id``)."""
    best: Optional[Mapping[str, Any]] = None
    best_key: Optional[Tuple[str, str, str]] = None
    for row in rows:
        if str(row.get("predicate") or "") != HAS_BIBLIOGRAPHY or str(row.get("subjectId") or "") != subject_id:
            continue
        key = (str(_props(row).get("recorded_at") or ""), str(row.get("createdAt") or ""),
               str(row.get("id") or ""))
        if best_key is None or key > best_key:
            best, best_key = row, key
    return best


def _empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def effective_publication(name: str, properties: Mapping[str, Any],
                          record: Optional[Mapping[str, Any]]) -> Tuple[str, Dict[str, Any], bool]:
    """``(name, properties, used)`` with ``record`` filling gaps; own non-empty values always win."""
    merged = dict(properties or {})
    if not record:
        return name, merged, False
    used = False
    for key in BIBLIOGRAPHIC_FIELDS:
        if _empty(merged.get(key)) and not _empty(record.get(key)):
            merged[key] = record[key]
            used = True
    title = str(record.get("title") or "").strip()
    if title and is_placeholder_name(name, merged.get("doi") or record.get("doi")):
        name = title
        used = True
    if used:
        merged["bibliography_source"] = str(record.get("metadata_source") or "")
    return name, merged, used


def author_names(authors: object) -> List[str]:
    """Display names from either author shape (``[{"name": ...}]`` or bare strings)."""
    names: List[str] = []
    for author in authors if isinstance(authors, list) else []:
        value = (str(author.get("name") or author.get("display_name") or "") if isinstance(author, dict)
                 else str(author or ""))
        if value.strip():
            names.append(value.strip())
    return names


__all__ = (
    "BIBLIOGRAPHIC_FIELDS", "BIBLIO_VERSION", "BackfillProposal", "HAS_BIBLIOGRAPHY", "author_names",
    "effective_publication", "is_placeholder_name", "needs_backfill", "newest_bibliography_link",
    "normalise_record", "propose_backfill", "record_digest", "select_backfill_candidates",
)
