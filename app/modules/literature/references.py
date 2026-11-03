"""A paper's printed reference list, parsed from GROBID TEI (L-14, T29 B5).

GROBID is optional and OFF unless a base URL is passed: with none, :func:`grobid_references`
returns ``None`` ("not configured", distinct from ``[]``, "GROBID read no references") and
makes no request. The HTTP client is injected, so nothing here reaches a network on its own.
An entry GROBID could not structure still appears with its raw string so indices stay aligned
with the printed list, and an entry without a DOI stays an unresolved reference.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/reference_parsing.py (``ParsedReference``,
``parse_references_tei``, ``grobid_references``) (BSD-3-Clause). The donor's ``GROBID_URL``
environment read is replaced by an explicit argument.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET  # noqa: S405 - input is a local operator-run service; expat refuses entity bombs

from .statements import Basis, ProvenanceStatement

logger = logging.getLogger(__name__)

#: Stamped into cache names and statements so a parser change re-reads instead of trusting old answers.
REFERENCE_PARSER_VERSION = "grobid-0.9.1/refparse-v1"
PRODUCER = f"references@{REFERENCE_PARSER_VERSION}"
TEI = {"tei": "http://www.tei-c.org/ns/1.0"}


@dataclass(frozen=True)
class ParsedReference:
    """One entry of a paper's reference list (``index`` is 1-based, in print order)."""

    index: int
    raw: str
    title: str = ""
    authors: tuple[str, ...] = ()
    year: int | None = None
    journal: str = ""
    volume: str = ""
    first_page: str = ""
    doi: str = ""
    arxiv: str = ""

    @property
    def key(self) -> str:
        """Content key: the DOI, else the arXiv id, else a title+year fold."""
        if self.doi:
            return "doi:" + self.doi.lower()
        if self.arxiv:
            return "arxiv:" + self.arxiv.lower()
        fold = re.sub(r"[^a-z0-9]", "", self.title.lower())[:80]
        return f"title:{fold}:{self.year or ''}"


def _text(el: ET.Element | None) -> str:
    return "" if el is None else re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _first_text(el: ET.Element, *paths: str) -> str:
    for path in paths:
        found = _text(el.find(path, TEI))
        if found:
            return found
    return ""


def _person(pers: ET.Element) -> str:
    parts = [_text(f) for f in pers.findall("tei:forename", TEI)] + [_text(pers.find("tei:surname", TEI))]
    return " ".join(p for p in parts if p)


def _authors(bibl: ET.Element) -> tuple[str, ...]:
    """Article authors, else the monograph's; never both (a chapter's monograph persons are editors)."""
    for scope in ("tei:analytic", "tei:monogr"):
        people = [p for p in (_person(x) for x in bibl.findall(f"{scope}/tei:author/tei:persName", TEI)) if p]
        if people:
            return tuple(people)
    return ()


def _clean_doi(value: str) -> str:
    text = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text if text.startswith("10.") else ""


def _year(bibl: ET.Element) -> int | None:
    for date in bibl.findall(".//tei:imprint/tei:date", TEI):
        when = (date.get("when") or _text(date))[:4]
        if when.isdigit():
            return int(when)
    return None


def _first_page(bibl: ET.Element) -> str:
    for scope in bibl.findall(".//tei:imprint/tei:biblScope[@unit='page']", TEI):
        page = scope.get("from") or _text(scope)
        if page:
            return page
    return ""


def parse_references_tei(tei_xml: bytes | str) -> list[ParsedReference]:
    """Every ``<listBibl>/<biblStruct>`` of a GROBID TEI document, in order.

    Raises:
        xml.etree.ElementTree.ParseError: The body is not XML (a service fault, not "no references").
    """
    root = ET.fromstring(tei_xml)  # noqa: S314
    out: list[ParsedReference] = []
    for index, bibl in enumerate(root.iterfind(".//tei:listBibl/tei:biblStruct", TEI), 1):
        title = _first_text(bibl, "tei:analytic/tei:title[@type='main']", "tei:monogr/tei:title[@type='main']",
                            "tei:analytic/tei:title", "tei:monogr/tei:title[@level='m']")
        out.append(ParsedReference(
            index=index,
            raw=_first_text(bibl, "tei:note[@type='raw_reference']") or title,
            title=title,
            authors=_authors(bibl),
            year=_year(bibl),
            journal=_first_text(bibl, "tei:monogr/tei:title[@level='j']"),
            volume=_first_text(bibl, ".//tei:imprint/tei:biblScope[@unit='volume']"),
            first_page=_first_page(bibl),
            doi=_clean_doi(_first_text(bibl, ".//tei:idno[@type='DOI']")),
            arxiv=re.sub(r"^arxiv:\s*", "", _first_text(bibl, ".//tei:idno[@type='arXiv']"), flags=re.IGNORECASE),
        ))
    return out


def _cache_file(pdf: Path, cache_dir: Path | None) -> Path:
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    return (cache_dir or pdf.parent) / f"{digest}.refs.{REFERENCE_PARSER_VERSION.replace('/', '_')}.json"


def grobid_references(pdf: Path, *, base_url: str = "", http: Any = None, timeout: float = 180.0,
                      cache_dir: Path | None = None) -> list[ParsedReference] | None:
    """GROBID ``processReferences`` for one PDF, cached by content hash and parser version.

    Args:
        pdf: The PDF on disk.
        base_url: GROBID service URL; empty means not configured.
        http: An ``httpx``-like client with ``post`` (required when ``base_url`` is set, so a
            test or the caller's hardened client is always explicit).
        timeout: Per-request timeout.
        cache_dir: Cache directory; defaults to beside the PDF.

    Returns:
        The references, ``[]`` when GROBID found none, ``None`` when not configured (nothing sent).
    """
    base = base_url.strip().rstrip("/")
    if not base:
        return None
    if http is None:
        raise ValueError("grobid_references needs an explicit http client when a base_url is given")
    cache = _cache_file(pdf, cache_dir)
    if cache.exists():
        try:
            return [ParsedReference(**{**r, "authors": tuple(r.get("authors") or ())})
                    for r in json.loads(cache.read_text())]
        except (ValueError, TypeError):  # an unreadable cache is re-fetched
            logger.debug("unreadable reference cache %s; re-fetching", cache, exc_info=True)
    with pdf.open("rb") as handle:
        resp = http.post(f"{base}/api/processReferences", files={"input": (pdf.name, handle, "application/pdf")},
                         data={"includeRawCitations": "1", "consolidateCitations": "0"}, timeout=timeout)
    resp.raise_for_status()
    refs = parse_references_tei(resp.content)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([asdict(r) for r in refs]))
    return refs


def reference_statements(refs: list[ParsedReference], *, work_id: str, source_ref: str = "",
                         known_dois: set[str] | None = None) -> list[ProvenanceStatement]:
    """One text-grounded ``cites_reference`` statement per printed reference.

    ``resolved`` is true only when the reference carries a DOI that is in ``known_dois``;
    every other reference stays unresolved with its raw string (no Work is minted for it).
    """
    known = {d.lower() for d in (known_dois or set())}
    return [
        ProvenanceStatement(
            kind="reference", subject=work_id, predicate="cites_reference", object=ref.key,
            basis=Basis.TEXT_GROUNDED, producer=PRODUCER, source_ref=source_ref,
            resolved=bool(ref.doi) and ref.doi.lower() in known,
            attributes={"index": ref.index, "raw": ref.raw, "title": ref.title, "year": ref.year,
                        "doi": ref.doi, "arxiv": ref.arxiv},
        ) for ref in refs
    ]


__all__ = ("PRODUCER", "ParsedReference", "REFERENCE_PARSER_VERSION", "grobid_references",
           "parse_references_tei", "reference_statements")
