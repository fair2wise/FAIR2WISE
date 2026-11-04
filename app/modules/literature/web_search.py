"""Crossref, arXiv and DuckDuckGo discovery with per-provider status (L-20).

Discovery only: results are external candidates with source attribution. They
are never downloaded, extracted or promoted here, and F2W QA policy decides
whether uningested results reach an answer.

Unlike the donor's ``search_all`` (which logged and returned ``[]`` per failing
source, so a total outage looked like "no results"), :func:`search_all` returns
a :class:`DiscoveryReport` with each provider's status: ``ok``, ``empty``,
``unavailable`` (transport/HTTP failure, with detail) or ``not_installed``.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/web_search.py (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
import logging
from typing import TYPE_CHECKING, Any
import xml.etree.ElementTree as ET

import httpx

from .openalex import EXTERNAL_RESULT

if TYPE_CHECKING:
    from ..json_types import JsonValue

logger = logging.getLogger(__name__)

CROSSREF_BASE = "https://api.crossref.org"
ARXIV_BASE = "https://export.arxiv.org/api/query"
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_MAX = 25

#: A DuckDuckGo text-search callable ``(query, max_results) -> [{"title","href","body"}]``.
DdgSearch = Callable[[str, int], Sequence[dict[str, Any]]]


class _ProviderFailed(Exception):
    """Internal: a provider could not answer."""


@dataclass
class ProviderStatus:
    """How one provider answered: ``ok``, ``empty``, ``unavailable`` or ``not_installed``."""

    status: str
    count: int = 0
    detail: str = ""


@dataclass
class DiscoveryReport:
    """Merged discovery results plus every provider's status.

    ``all_unavailable`` distinguishes a total outage from a search that found nothing.
    """

    query: str
    results: list[dict[str, str]] = field(default_factory=list)
    providers: dict[str, ProviderStatus] = field(default_factory=dict)

    @property
    def all_unavailable(self) -> bool:
        """True when no provider answered at all."""
        return bool(self.providers) and all(p.status in {"unavailable", "not_installed"}
                                            for p in self.providers.values())

    @property
    def partial(self) -> bool:
        """True when some providers answered and some did not."""
        statuses = {p.status for p in self.providers.values()}
        return bool(statuses & {"ok", "empty"}) and bool(statuses & {"unavailable", "not_installed"})

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return {"query": self.query, "results": list(self.results),
                "providers": {k: asdict(v) for k, v in self.providers.items()}}


def _cap(value: JsonValue) -> int:
    try:
        return max(1, min(int(value or 5), _MAX))
    except (TypeError, ValueError):
        return 5


def _item(title: str, url: str, snippet: str, source: str) -> dict[str, str]:
    return {"title": title.strip(), "url": url.strip(), "snippet": snippet.strip()[:400], "source": source,
            "record_kind": EXTERNAL_RESULT}


def crossref_search(http: httpx.Client, query: str, rows: int, *, mailto: str = "") -> list[dict[str, str]]:
    """Crossref ``/works?query=`` (donor ``_do_crossref_search``); raises on failure."""
    query = query.strip()
    if not query:
        return []
    capped = _cap(rows)
    params: dict[str, Any] = {"query": query, "rows": capped,
                              "select": "DOI,title,abstract,container-title,author"}
    if mailto:
        params["mailto"] = mailto
    try:
        response = http.get(f"{CROSSREF_BASE}/works", params=params, timeout=15.0)
    except httpx.HTTPError as exc:
        raise _ProviderFailed(type(exc).__name__) from exc
    if response.status_code != 200:
        raise _ProviderFailed(f"HTTP {response.status_code}")
    items: list[dict[str, str]] = []
    for item in ((response.json() or {}).get("message", {}) or {}).get("items", []):
        title_list = item.get("title") or []
        title = title_list[0] if title_list else ""
        doi = item.get("DOI") or ""
        if not title or not doi:
            continue
        snippet = item.get("abstract") or (item.get("container-title") or [""])[0]
        items.append(_item(title, f"https://doi.org/{doi}", str(snippet), "crossref"))
        if len(items) >= capped:
            break
    return items


def arxiv_search(http: httpx.Client, query: str, max_results: int) -> list[dict[str, str]]:
    """Search the arXiv Atom API (donor ``_do_arxiv_search``); raises on failure."""
    query = query.strip()
    if not query:
        return []
    capped = _cap(max_results)
    try:
        response = http.get(ARXIV_BASE, params={"search_query": f"all:{query}", "start": 0, "max_results": capped,
                                                "sortBy": "relevance", "sortOrder": "descending"}, timeout=15.0)
    except httpx.HTTPError as exc:
        raise _ProviderFailed(type(exc).__name__) from exc
    if response.status_code != 200:
        raise _ProviderFailed(f"HTTP {response.status_code}")
    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise _ProviderFailed("unparseable feed") from exc
    items: list[dict[str, str]] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title_el, link_el = entry.find(f"{_ATOM_NS}title"), entry.find(f"{_ATOM_NS}id")
        summary_el = entry.find(f"{_ATOM_NS}summary")
        title = " ".join((title_el.text or "").split()) if title_el is not None else ""
        url = (link_el.text or "").strip() if link_el is not None else ""
        summary = " ".join((summary_el.text or "").split()) if summary_el is not None else ""
        if title and url:
            items.append(_item(title, url, summary, "arxiv"))
    return items[:capped]


def default_ddg() -> DdgSearch | None:
    """The ``duckduckgo_search`` text search when installed, else ``None`` (optional dependency)."""
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-not-found]
    except ImportError:
        return None

    def search(query: str, max_results: int) -> Sequence[dict[str, Any]]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    return search


def duckduckgo_search(ddg: DdgSearch | None, query: str, max_results: int) -> list[dict[str, str]]:
    """DuckDuckGo text results normalized to the shared shape (donor ``_duckduckgo_results``)."""
    query = query.strip()
    if not query:
        return []
    if ddg is None:
        raise ImportError("duckduckgo_search is not installed")
    capped = _cap(max_results)
    try:
        entries = ddg(query, capped)
    except Exception as exc:  # noqa: BLE001 - library churn and network errors look alike
        raise _ProviderFailed(type(exc).__name__) from exc
    items: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        items.append(_item(str(entry.get("title", "")), str(entry.get("href", entry.get("url", ""))),
                           str(entry.get("body", entry.get("snippet", ""))), "duckduckgo"))
        if len(items) >= capped:
            break
    return items


def search_all(query: str, max_results: int = 5, *, http: httpx.Client, ddg: DdgSearch | None = None,
               providers: Sequence[str] = ("duckduckgo", "crossref", "arxiv"),
               mailto: str = "") -> DiscoveryReport:
    """Run each provider and merge results, preserving attribution and per-provider status."""
    report = DiscoveryReport(query=query)
    runners: dict[str, Callable[[], list[dict[str, str]]]] = {
        "duckduckgo": lambda: duckduckgo_search(ddg, query, max_results),
        "crossref": lambda: crossref_search(http, query, max_results, mailto=mailto),
        "arxiv": lambda: arxiv_search(http, query, max_results),
    }
    for name in providers:
        try:
            found = runners[name]()
        except ImportError as exc:
            report.providers[name] = ProviderStatus("not_installed", detail=str(exc))
            continue
        except _ProviderFailed as exc:
            logger.warning("discovery provider %s failed: %s", name, exc)
            report.providers[name] = ProviderStatus("unavailable", detail=str(exc))
            continue
        report.results.extend(found)
        report.providers[name] = ProviderStatus("ok" if found else "empty", count=len(found))
    return report


__all__ = (
    "ARXIV_BASE", "CROSSREF_BASE", "DdgSearch", "DiscoveryReport", "ProviderStatus", "arxiv_search",
    "crossref_search", "default_ddg", "duckduckgo_search", "search_all",
)
