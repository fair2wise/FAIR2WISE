"""Acquire an open-access PDF for a DOI into the shared candidate store (L-09/L-10 download half).

One acquisition path for every reader: the store (and legacy index) is asked
first, so a paper F2W already has under any historical filename is never
re-downloaded. Otherwise candidate URLs are gathered in cost order:

1. PDF locations on an OpenAlex work already in hand (free);
2. Unpaywall, every location with a PDF, when a real contact email is configured;
3. Semantic Scholar ``openAccessPdf``;
4. arXiv for ``10.48550/arXiv.*`` DOIs.

Candidate URLs are mapped through F2W's existing OA URL rules
(``download_pdfs.select_openalex_oa_pdfs`` / ``select_unpaywall_oa_pdfs`` /
``rewrite_oa_pdf_url``, B22), repositories first, then fetched with
:class:`.safe_fetch.SafeFetcher`. Acquisition requires the ``ingest.acquire``
capability: reading is not acquiring.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_reading.py (``_download_by_doi``,
``_oa_pdf_urls``, ``_polite_email``) and src/forge/agents/literature/papers.py
(``download_pdf_by_doi``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import logging
from typing import Any, cast

import httpx

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from .legacy import LegacyIndex, doi_key, doi_only
from .openalex import OpenAlexSettings
from .safe_fetch import SafeFetcher
from .store import FileDocumentStore

logger = logging.getLogger(__name__)

ACQUIRE_CAPABILITY = "ingest.acquire"


@dataclass
class AcquireResult:
    """Outcome of an acquisition. ``status``: ``cached`` | ``acquired`` | ``no_oa_pdf`` | ``invalid_doi``."""

    doi: str
    status: str
    ref: str = ""
    source_url: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    lookups: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether a stored source is available."""
        return self.status in {"cached", "acquired"}

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return asdict(self)


def _f2w_urls() -> Any:
    # Imported lazily: download_pdfs configures logging when imported (T04 module, kept as-is).
    from . import download_pdfs

    return download_pdfs


def openalex_work_urls(work: Mapping[str, Any]) -> list[str]:
    """PDF URLs on an OpenAlex work, through F2W's repository-first selector (B22)."""
    urls, _, _ = _f2w_urls().select_openalex_oa_pdfs(dict(work))
    return list(urls)


def unpaywall_urls(payload: Mapping[str, Any]) -> list[str]:
    """PDF URLs from an Unpaywall body: every location, repositories first (F2W selector)."""
    urls, _ = _f2w_urls().select_unpaywall_oa_pdfs(dict(payload))
    return list(urls)


def arxiv_url_for(doi: str) -> str:
    """Direct arXiv PDF URL for an arXiv DOI, else ``""``."""
    key = doi_key(doi)
    if not key.startswith("10.48550/arxiv."):
        return ""
    return f"https://arxiv.org/pdf/{doi_only(doi).split('/', 1)[1][len('arXiv.'):]}"


class PaperAcquirer:
    """Find and store an OA PDF for a DOI, reusing whatever the store already holds.

    Args:
        store: Shared candidate store.
        index: Legacy index (old filenames, DOI aliases).
        fetcher: Guarded fetcher for third-party PDF URLs.
        http: Client for the Unpaywall/S2 metadata lookups (not for PDFs).
        settings: Contact email and S2 key.
        legacy_root_id: Index root the acquisitions are recorded under.
    """

    def __init__(self, store: FileDocumentStore, index: LegacyIndex, fetcher: SafeFetcher, http: httpx.Client,
                 settings: OpenAlexSettings | None = None, *, legacy_root_id: str = "acquired") -> None:
        self.store = store
        self.index = index
        self.fetcher = fetcher
        self.http = http
        self.settings = settings or OpenAlexSettings()
        self.legacy_root_id = legacy_root_id

    def cached(self, ctx: RequestContext, doi: str) -> str | None:
        """A verified stored source for ``doi`` (alias or legacy filename), else ``None``."""
        ref = self.index.find_by_doi(doi)
        if ref and self.store.verify(ctx, ref):
            return ref
        if ref:
            logger.warning("stored source for %s failed its checksum; treating as absent", doi_key(doi))
        return None

    def _lookup(self, name: str, url: str, result: AcquireResult, **kw: Any) -> dict[str, Any] | None:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
        try:
            response = self.http.get(url, timeout=15.0, **kw)
        except httpx.HTTPError as exc:
            result.lookups[name] = f"unavailable:{type(exc).__name__}"
            return None
        if response.status_code != 200:
            result.lookups[name] = f"http_{response.status_code}"
            return None
        result.lookups[name] = "ok"
        try:
            body = response.json()
        except ValueError:
            result.lookups[name] = "unparseable"
            return None
        return body if isinstance(body, dict) else None

    def candidate_urls(self, doi: str, work: Mapping[str, Any] | None, result: AcquireResult) -> list[str]:
        """Candidate PDF URLs in cost order, de-duplicated."""
        bare = doi_only(doi)
        urls: list[str] = []
        if work:
            urls.extend(openalex_work_urls(work))
        email = self.settings.polite_mailto
        if email and _f2w_urls().is_usable_contact_email(email):
            body = self._lookup("unpaywall", f"https://api.unpaywall.org/v2/{bare}", result,
                                params={"email": email})
            if body:
                urls.extend(unpaywall_urls(body))
        else:
            result.lookups["unpaywall"] = "skipped:no_contact_email"
        headers = {"x-api-key": self.settings.s2_api_key} if self.settings.s2_api_key else {}
        body = self._lookup("semantic_scholar", f"https://api.semanticscholar.org/graph/v1/paper/DOI:{bare}",
                            result, params={"fields": "openAccessPdf"}, headers=headers)
        if body:
            pdf = (body.get("openAccessPdf") or {}).get("url")
            if pdf:
                urls.append(str(pdf))
        arxiv = arxiv_url_for(doi)
        if arxiv:
            urls.append(arxiv)
        mapped: list[str] = []
        dl = _f2w_urls()
        for url in urls:
            rewritten = dl.rewrite_oa_pdf_url(url) or dl.listed_oa_pdf_url(url) or ""
            if rewritten and rewritten not in mapped:
                mapped.append(rewritten)
        return cast(list[str], dl.prefer_repository_pdfs(mapped))

    def acquire(self, ctx: RequestContext, doi: str, *, work: Mapping[str, Any] | None = None) -> AcquireResult:
        """Return a stored source for ``doi``, downloading an OA copy if needed.

        Raises:
            ContractError: ``DENIED`` without the ``ingest.acquire`` capability
                (a cached source is still returned without it).
        """
        result = AcquireResult(doi=doi_only(doi), status="no_oa_pdf")
        if not doi_key(doi):
            result.status = "invalid_doi"
            return result
        ref = self.cached(ctx, doi)
        if ref:
            result.status, result.ref = "cached", ref
            return result
        if not ctx.has(ACQUIRE_CAPABILITY):
            raise ContractError(ErrorCode.DENIED, "acquiring a paper needs the ingest.acquire capability",
                                retryable=False)
        for url in self.candidate_urls(doi, work, result):
            fetched = self.fetcher.fetch_pdf(ctx, url, self.store, metadata={"doi": result.doi})
            result.attempts.append({"url": url, "status": fetched.status, "redirects": len(fetched.redirects)})
            if fetched.ok:
                self.index.add_doi_alias(doi, fetched.ref)
                result.status, result.ref, result.source_url = "acquired", fetched.ref, url
                return result
        return result


__all__ = ("ACQUIRE_CAPABILITY", "AcquireResult", "PaperAcquirer", "arxiv_url_for", "openalex_work_urls",
           "unpaywall_urls")
