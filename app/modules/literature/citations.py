"""Citation links between Works, as provenance-tagged statements (L-17, T29 B5, A-01).

Two routes, kept distinct (plan L-14/L-20 rule):

* **text-grounded**: a DOI printed in the paper's own section text (``via=reference_text``);
* **external enrichment**: OpenAlex ``referenced_works`` (``via=referenced_works``), which is
  a registry's claim and never evidence that the paper itself made one.

A cited DOI that matches no known Work is returned as an *unresolved* statement; no Work is
minted for it. Nothing is written anywhere: statements are candidates for an approved plan.

A-01: the donor's static ``openalex_supplement.json`` (referenced works and OpenAlex ids for
DOI-ingested papers) is not carried over. :func:`enrich_corpus_rows` regenerates the same
repair from OpenAlex records the caller fetched (``OpenAlexClient.fetch_works_by_doi``).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/citation_links.py (``dois_in_text``,
``link_citations_from_sections``, ``link_citations_from_referenced_works``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Any

from .ingested import fold_key
from .statements import Basis, ProvenanceStatement

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>\]\)}]+)", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:"
TEXT_PRODUCER = "citations.text@v1"
OPENALEX_PRODUCER = "citations.openalex@v1"
_OA_PREFIX = "https://openalex.org/"


def dois_in_text(text: str) -> set[str]:
    """Every DOI in ``text``, lowercased, trailing punctuation stripped."""
    found: set[str] = set()
    for match in _DOI_RE.findall(str(text or "")):
        doi = match.rstrip(_TRAILING_PUNCT).lower()
        if doi:
            found.add(doi)
    return found


@dataclass
class CitationResult:
    """Statements plus the counters the donor reported."""

    statements: list[ProvenanceStatement] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)


def _row_keys(row: Mapping[str, Any]) -> set[str]:
    keys = {fold_key(str(row.get(name) or "")) for name in ("doi", "id", "publication_id")}
    keys.discard("")
    return keys


def link_citations_from_sections(corpus: Sequence[Mapping[str, Any]],
                                 sections: Iterable[tuple[str, str, str]]) -> CitationResult:
    """Text-grounded ``cites`` statements from DOIs in section text.

    Args:
        corpus: Known Works as rows with ``id`` and ``doi`` (``id`` is the statement subject/object).
        sections: ``(publication id, section name, text)``; a section whose publication is not in
            ``corpus`` is skipped and counted, never attributed to another paper.
    """
    by_fold = {k: str(row["id"]) for row in corpus for k in _row_keys(row)}
    ids = {str(row["id"]) for row in corpus}
    c = {"sections_scanned": 0, "dois_found": 0, "dois_matched": 0, "self_citations_skipped": 0,
         "sections_without_a_parent": 0}
    out: list[ProvenanceStatement] = []
    seen: set[tuple[str, str]] = set()
    unresolved: set[tuple[str, str]] = set()
    for pub_id, name, text in sections:
        if not text:
            continue
        if pub_id not in ids:
            c["sections_without_a_parent"] += 1
            continue
        c["sections_scanned"] += 1
        for doi in sorted(dois_in_text(text)):
            c["dois_found"] += 1
            target = by_fold.get(fold_key(doi))
            if target is None:
                if (pub_id, doi) not in unresolved:
                    unresolved.add((pub_id, doi))
                    out.append(ProvenanceStatement(
                        kind="citation", subject=pub_id, predicate="cites", object=f"doi:{doi}",
                        basis=Basis.TEXT_GROUNDED, producer=TEXT_PRODUCER, resolved=False,
                        attributes={"doi": doi, "via": "reference_text", "section": name}))
                continue
            c["dois_matched"] += 1
            if target == pub_id:
                c["self_citations_skipped"] += 1
                continue
            if (pub_id, target) in seen:
                continue
            seen.add((pub_id, target))
            out.append(ProvenanceStatement(
                kind="citation", subject=pub_id, predicate="cites", object=target, basis=Basis.TEXT_GROUNDED,
                producer=TEXT_PRODUCER, attributes={"doi": doi, "via": "reference_text", "section": name}))
    c["unique_edges"] = len(seen)
    c["unmatched_dois"] = len({d for _, d in unresolved})
    return CitationResult(out, c)


def _oa_id(value: object) -> str:
    return str(value or "").replace(_OA_PREFIX, "").strip()


def link_citations_from_referenced_works(corpus: Sequence[Mapping[str, Any]]) -> CitationResult:
    """Externally enriched ``cites`` statements from OpenAlex ``referenced_works``.

    Rows need ``id``, ``openalex_id`` and ``referenced_works`` (see :func:`enrich_corpus_rows`).
    These are tagged ``external_enrichment`` and are not claims the paper made.
    """
    oa_to_id: dict[str, str] = {}
    for row in corpus:
        oa = _oa_id(row.get("openalex_id"))
        if oa:
            oa_to_id.setdefault(oa, str(row["id"]))
    c = {"publications": len(corpus), "targets_resolvable": len(oa_to_id), "self_citations_skipped": 0,
         "references_outside_corpus": 0}
    out: list[ProvenanceStatement] = []
    seen: set[tuple[str, str]] = set()
    for row in corpus:
        src = str(row["id"])
        for ref in row.get("referenced_works") or []:
            target = oa_to_id.get(_oa_id(ref))
            if target is None:
                c["references_outside_corpus"] += 1
                continue
            if target == src:
                c["self_citations_skipped"] += 1
                continue
            if (src, target) in seen:
                continue
            seen.add((src, target))
            out.append(ProvenanceStatement(
                kind="citation", subject=src, predicate="cites", object=target,
                basis=Basis.EXTERNAL_ENRICHMENT, producer=OPENALEX_PRODUCER,
                attributes={"via": "referenced_works", "registry": "openalex"}))
    c["unique_edges"] = len(seen)
    return CitationResult(out, c)


def enrich_corpus_rows(corpus: Sequence[Mapping[str, Any]],
                       fetch_works: Callable[[list[str]], Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """Fill ``openalex_id`` / ``referenced_works`` where a row lacks them, from OpenAlex works.

    Replaces the donor's static supplement file. ``fetch_works(dois)`` returns ``{doi: work}``
    (for example ``lambda d: client.fetch_works_by_doi(d).found``); a row's own values win.
    """
    missing = [str(r["doi"]) for r in corpus if r.get("doi") and not (r.get("openalex_id") and r.get("referenced_works") is not None)]
    found = {fold_key(k): v for k, v in (fetch_works(missing) if missing else {}).items()}
    out: list[dict[str, Any]] = []
    for row in corpus:
        new = dict(row)
        work = found.get(fold_key(str(row.get("doi") or "")))
        if work:
            if not new.get("openalex_id") and work.get("id"):
                new["openalex_id"] = _oa_id(work["id"])
            if new.get("referenced_works") is None and work.get("referenced_works") is not None:
                new["referenced_works"] = list(work["referenced_works"])
        out.append(new)
    return out


__all__ = ("CitationResult", "OPENALEX_PRODUCER", "TEXT_PRODUCER", "dois_in_text", "enrich_corpus_rows",
           "link_citations_from_referenced_works", "link_citations_from_sections")
