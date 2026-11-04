"""Batched, cached, multi-source bibliographic resolver (OpenAlex -> Semantic Scholar -> Crossref).

Resolves many DOIs or DOI-less references with the cost model the donor
verified: free OpenAlex singletons for few DOIs, 50-DOI OR-filter batches for
many, Semantic Scholar 500-id batches for what OpenAlex lacked, Crossref
singletons for the rest, and S2 title-match then Crossref ``query.bibliographic``
for references without a DOI (accepted only when title and year agree).

A cache row with a record is a positive (90 days); a row without one is a
*verified negative* (30 days, every source answered "no"). A transport failure,
5xx or spent quota writes nothing: "could not ask" is not "nobody knows".

Bibliographic resolution is a *candidate*, not Work identity: a ``Resolved``
record says which provider knows a DOI, never that a local PDF is that Work
(see :mod:`.intake` for the verified association).

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/literature/resolver_batch.py
(BSD-3-Clause). The OpenAlex breaker and credentials come from an injected
:class:`~.openalex.OpenAlexClient` instead of module globals.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Protocol, cast

import httpx

from .openalex import OpenAlexClient, quota_pause_seconds, redact, retry_after_seconds

logger = logging.getLogger(__name__)

OPENALEX = "https://api.openalex.org"
S2 = "https://api.semanticscholar.org/graph/v1"
CROSSREF = "https://api.crossref.org"
NEGATIVE_TTL = timedelta(days=30)
POSITIVE_TTL = timedelta(days=90)
OA_BATCH = 50
S2_BATCH = 500
PREFER_BATCH_ABOVE = 200
TITLE_MATCH_FLOOR = 0.8
OA_SELECT = (
    "id,doi,title,display_name,authorships,publication_year,primary_location,"
    "best_oa_location,open_access,cited_by_count,referenced_works,referenced_works_count,"
    "abstract_inverted_index,topics,keywords,type,ids,biblio,language"
)
S2_FIELDS = "externalIds,title,year,venue,authors,referenceCount,citationCount,openAccessPdf,publicationTypes"


def normalise_doi(value: object) -> str:
    """A DOI as ``10.x/y``, lowercased; ``""`` when ``value`` is not one."""
    text = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text if text.startswith("10.") and "/" in text else ""


class TokenBucket:
    """Thread-safe token bucket: ``rate_per_s`` sustained, ``burst`` at once (clock/sleep injectable)."""

    def __init__(self, rate_per_s: float, burst: int = 1, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if rate_per_s <= 0:
            raise ValueError("rate_per_s must be positive")
        self.rate = float(rate_per_s)
        self.capacity = max(1, int(burst))
        self.tokens = float(self.capacity)
        self._clock = clock
        self._sleep = sleep
        self.updated = clock()
        self.lock = threading.Lock()

    def take(self) -> None:
        """Block until one token is available, then spend it."""
        with self.lock:
            while True:
                now = self._clock()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                self._sleep((1 - self.tokens) / self.rate)


@dataclass(frozen=True)
class Resolved:
    """One identifier's resolution.

    Attributes:
        key: ``"doi:10.x/y"`` or ``"title:<fold>:<year>"``.
        source: ``openalex`` | ``semantic_scholar`` | ``crossref`` | ``s2_title`` |
            ``crossref_title`` | ``none`` (verified negative).
        record: The provider's record, or ``None`` for a verified negative.
        fetched_at: ISO-8601 UTC.
        cost_usd: OpenAlex usage-pricing cost of this answer.
    """

    key: str
    source: str
    record: dict[str, Any] | None
    fetched_at: str
    cost_usd: float = 0.0


class ResolverCache:
    """One row per identifier key; a row with no record says "asked; nobody has it"."""

    def __init__(self, path: str | Path = ":memory:", *, positive_ttl: timedelta = POSITIVE_TTL,
                 negative_ttl: timedelta = NEGATIVE_TTL) -> None:
        target = str(path)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self.path = target
        self.positive_ttl = positive_ttl
        self.negative_ttl = negative_ttl
        self.lock = threading.Lock()
        self.con = sqlite3.connect(target, check_same_thread=False, isolation_level=None, timeout=30.0)
        if target != ":memory:":
            self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("CREATE TABLE IF NOT EXISTS resolved(key TEXT PRIMARY KEY, source TEXT NOT NULL, "
                         "record TEXT, fetched_at TEXT NOT NULL, cost_usd REAL NOT NULL DEFAULT 0)")

    def get(self, key: str) -> Resolved | None:
        """The live row for ``key``, or ``None`` when absent or expired."""
        with self.lock:
            row = self.con.execute("SELECT source, record, fetched_at, cost_usd FROM resolved WHERE key=?",
                                   (key,)).fetchone()
        if not row:
            return None
        source, record, fetched_at, cost = row
        try:
            age = datetime.now(UTC) - datetime.fromisoformat(fetched_at)
        except ValueError:  # an unreadable timestamp is a miss
            return None
        if age > (self.negative_ttl if record is None else self.positive_ttl):
            return None
        return Resolved(key, source, json.loads(record) if record else None, fetched_at, cost)

    def put(self, resolved: Resolved) -> None:
        """Insert or replace a row."""
        record = json.dumps(resolved.record) if resolved.record is not None else None
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO resolved VALUES(?,?,?,?,?)",
                             (resolved.key, resolved.source, record, resolved.fetched_at, resolved.cost_usd))

    def count(self, *, negative: bool | None = None) -> int:
        """Rows in the cache; ``negative=True`` counts only verified negatives."""
        clause = {None: "", True: " WHERE record IS NULL", False: " WHERE record IS NOT NULL"}[negative]
        with self.lock:
            return int(self.con.execute(f"SELECT COUNT(*) FROM resolved{clause}").fetchone()[0])

    def close(self) -> None:
        """Close the connection."""
        with self.lock:
            self.con.close()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ReferenceLike(Protocol):
    """What :meth:`BatchResolver.resolve_references` reads from a parsed reference."""

    index: int
    title: str
    year: int | None
    journal: str
    volume: str
    first_page: str
    doi: str

    @property
    def key(self) -> str:
        """Cache key identifying the reference."""
        ...


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _shingles(text: str, n: int = 6) -> set[str]:
    return {text[i:i + n] for i in range(max(0, len(text) - n + 1))} or {text}


def title_similarity(a: str, b: str) -> float:
    """Shared 6-character shingles of the folded titles over the shorter title's count."""
    fa, fb = _fold(a), _fold(b)
    if not fa or not fb:
        return 0.0
    sa, sb = _shingles(fa), _shingles(fb)
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


def _title_year_agree(ref: ReferenceLike, title: str, year: object) -> bool:
    if title_similarity(ref.title, title) < TITLE_MATCH_FLOOR:
        return False
    if ref.year is None or year in (None, ""):
        return True
    try:
        return abs(int(ref.year) - int(cast(int | str, year))) <= 1
    except (TypeError, ValueError):  # an unknown year does not exclude the match
        return True


def _crossref_year(item: dict[str, Any]) -> Any:
    for name in ("issued", "published-print", "published-online"):
        parts = (item.get(name) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            return parts[0][0]
    return None


class _Unanswered(Exception):
    """A source could not be asked (transport, 5xx, quota). Never cached."""


class BatchResolver:
    """Resolve DOIs and parsed references across OpenAlex, Semantic Scholar and Crossref.

    Args:
        openalex: Client whose breaker, credentials and HTTP client are shared, so
            a quota pause seen here pauses intake and tools too.
        cache: Answer cache; defaults to an in-memory one.
        http: HTTP client for S2/Crossref; defaults to ``openalex.http``.
        sleep: Backoff sleep, injectable.
        buckets: ``(openalex, s2, crossref)`` token buckets.
    """

    def __init__(self, openalex: OpenAlexClient, *, cache: ResolverCache | None = None,
                 http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 buckets: tuple[TokenBucket, TokenBucket, TokenBucket] | None = None) -> None:
        self.openalex = openalex
        self.cache = cache if cache is not None else ResolverCache()
        self.http = http or openalex.http
        self.s2_key = openalex.settings.s2_api_key
        self.mailto = openalex.settings.polite_mailto
        self._sleep = sleep
        self.oa_bucket, self.s2_bucket, self.cr_bucket = buckets or (
            TokenBucket(8.0, 8), TokenBucket(0.9, 1), TokenBucket(2.5, 3))
        self.requests: dict[str, int] = {"openalex": 0, "semantic_scholar": 0, "crossref": 0}
        self.cost_usd = 0.0

    # -- OpenAlex -------------------------------------------------------------------------------

    def _oa_params(self, **extra: object) -> dict[str, Any]:
        return {"select": OA_SELECT, **extra, **self.openalex.settings.auth_params()}

    def _oa_get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        breaker = self.openalex.breaker
        for attempt in range(3):
            if breaker.state() is not None:
                raise _Unanswered("openalex quota paused")
            self.oa_bucket.take()
            try:
                self.requests["openalex"] += 1
                resp = self.http.get(f"{OPENALEX}/{path}", params=params)
            except httpx.HTTPError as exc:
                logger.debug("OpenAlex transport: %s", redact(type(exc).__name__))
                self._sleep(2 ** attempt)
                continue
            if resp.status_code in (200, 404):
                return resp
            pause_s = quota_pause_seconds(resp, time.time())
            if pause_s is not None:
                breaker.pause(pause_s, f"HTTP {resp.status_code}")
                raise _Unanswered("openalex quota paused")
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = retry_after_seconds(resp)
                self._sleep(retry_after if retry_after is not None else 2 ** attempt)
                continue
            raise _Unanswered(f"openalex HTTP {resp.status_code}")
        raise _Unanswered("openalex retries exhausted")

    def openalex_singletons(self, dois: Sequence[str]) -> dict[str, dict[str, Any] | None]:
        """Free; one call per DOI. Absent from the result means could not ask."""
        out: dict[str, dict[str, Any] | None] = {}
        for doi in dois:
            try:
                resp = self._oa_get(f"works/https://doi.org/{doi}", self._oa_params())
            except _Unanswered as exc:
                if "paused" in str(exc):
                    break
                continue
            out[doi] = resp.json() if resp.status_code == 200 else None
        return out

    def openalex_batch(self, dois: Sequence[str]) -> dict[str, dict[str, Any] | None]:
        """$0.0001 per 50 DOIs. A DOI the batch answered without is a verified negative."""
        out: dict[str, dict[str, Any] | None] = {}
        safe = [d for d in dois if "|" not in d and "," not in d]
        for start in range(0, len(safe), OA_BATCH):
            chunk = safe[start:start + OA_BATCH]
            try:
                resp = self._oa_get("works", self._oa_params(filter="doi:" + "|".join(chunk),
                                                             **{"per-page": OA_BATCH}))
            except _Unanswered as exc:
                if "paused" in str(exc):
                    break
                continue
            if resp.status_code != 200:
                continue
            self.cost_usd += 0.0001
            found = {normalise_doi(w.get("doi")): w for w in (resp.json().get("results") or [])}
            for doi in chunk:
                out[doi] = found.get(doi)
        unsafe = [d for d in dois if d not in set(safe)]
        if unsafe:
            out.update(self.openalex_singletons(unsafe))
        return out

    # -- Semantic Scholar -----------------------------------------------------------------------

    def s2_batch(self, ids: Sequence[str]) -> dict[str, dict[str, Any] | None]:
        """500 ids per POST (``DOI:10...``); S2 answers ``null`` for unknown ids."""
        out: dict[str, dict[str, Any] | None] = {}
        headers = {"x-api-key": self.s2_key} if self.s2_key else {}
        for start in range(0, len(ids), S2_BATCH):
            chunk = list(ids[start:start + S2_BATCH])
            for attempt in range(3):
                self.s2_bucket.take()
                try:
                    self.requests["semantic_scholar"] += 1
                    resp = self.http.post(f"{S2}/paper/batch", params={"fields": S2_FIELDS}, json={"ids": chunk},
                                          headers=headers)
                except httpx.HTTPError:
                    self._sleep(2 ** attempt)
                    continue
                if resp.status_code == 200:
                    body = resp.json()
                    if isinstance(body, list) and len(body) == len(chunk):
                        for ident, rec in zip(chunk, body):
                            out[ident] = rec if isinstance(rec, dict) else None
                    break
                if resp.status_code in (429, 500, 502, 503, 504):
                    self._sleep(2 ** attempt)
                    continue
                break
        return out

    # -- Crossref -------------------------------------------------------------------------------

    def _crossref_params(self, **extra: object) -> dict[str, Any]:
        return {**extra, **({"mailto": self.mailto} if self.mailto else {})}

    def crossref_doi(self, doi: str) -> dict[str, Any] | None:
        """The Crossref record, ``None`` for a 404; raises :class:`_Unanswered` otherwise."""
        self.cr_bucket.take()
        try:
            self.requests["crossref"] += 1
            resp = self.http.get(f"{CROSSREF}/works/{doi}", params=self._crossref_params())
        except httpx.HTTPError as exc:
            raise _Unanswered(f"crossref {type(exc).__name__}") from exc
        if resp.status_code == 200:
            return cast(dict[str, Any] | None, resp.json().get("message"))
        if resp.status_code == 404:
            return None
        raise _Unanswered(f"crossref HTTP {resp.status_code}")

    def crossref_bibliographic(self, ref: ReferenceLike) -> dict[str, Any] | None:
        """DOI-less references: ``query.bibliographic`` plus a title/year agreement check."""
        query = " ".join(filter(None, [ref.title, ref.journal, str(ref.year or ""), ref.volume, ref.first_page]))
        self.cr_bucket.take()
        try:
            self.requests["crossref"] += 1
            resp = self.http.get(f"{CROSSREF}/works", params=self._crossref_params(**{
                "query.bibliographic": query, "rows": 3,
                "select": "DOI,title,author,issued,container-title,volume,page"}))
        except httpx.HTTPError as exc:
            raise _Unanswered(f"crossref {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise _Unanswered(f"crossref HTTP {resp.status_code}")
        for item in (resp.json().get("message") or {}).get("items") or []:
            if _title_year_agree(ref, " ".join(item.get("title") or []), _crossref_year(item)):
                return cast(dict[str, Any], item)
        return None

    def s2_title_match(self, ref: ReferenceLike) -> dict[str, Any] | None:
        """S2 ``paper/search/match`` best hit, accepted only if title and year agree."""
        self.s2_bucket.take()
        try:
            self.requests["semantic_scholar"] += 1
            resp = self.http.get(f"{S2}/paper/search/match", params={"query": ref.title, "fields": S2_FIELDS},
                                 headers={"x-api-key": self.s2_key} if self.s2_key else {})
        except httpx.HTTPError as exc:
            raise _Unanswered(f"s2 {type(exc).__name__}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise _Unanswered(f"s2 HTTP {resp.status_code}")
        data = resp.json().get("data") or []
        if data and _title_year_agree(ref, data[0].get("title", ""), data[0].get("year")):
            return cast(dict[str, Any], data[0])
        return None

    # -- orchestration --------------------------------------------------------------------------

    def resolve_dois(self, dois: Iterable[str], *,
                     prefer_batch_above: int = PREFER_BATCH_ABOVE) -> dict[str, Resolved]:
        """``{doi: Resolved}`` for every DOI with an answer, cached or new.

        A DOI absent from the result could not be asked (quota, transport) and is
        retried next time; ``record=None`` is a verified negative.
        """
        wanted = sorted({d for d in (normalise_doi(x) for x in dois) if d})
        results: dict[str, Resolved] = {}
        todo: list[str] = []
        for doi in wanted:
            hit = self.cache.get("doi:" + doi)
            if hit is not None:
                results[doi] = hit
            else:
                todo.append(doi)
        if not todo:
            return results
        failed: set[str] = set()
        batched = len(todo) > prefer_batch_above
        oa = self.openalex_batch(todo) if batched else self.openalex_singletons(todo)
        per_doi_cost = 0.0001 / OA_BATCH if batched else 0.0
        failed.update(d for d in todo if d not in oa)
        for doi, rec in oa.items():
            if rec is not None:
                results[doi] = Resolved("doi:" + doi, "openalex", rec, _now(), per_doi_cost)
        remaining = [d for d in todo if d not in results]
        if remaining:
            s2 = self.s2_batch([f"DOI:{d}" for d in remaining])
            for doi in remaining:
                ident = f"DOI:{doi}"
                if ident not in s2:
                    failed.add(doi)
                elif s2[ident]:
                    results[doi] = Resolved("doi:" + doi, "semantic_scholar", s2[ident], _now())
        for doi in [d for d in todo if d not in results]:
            try:
                rec = self.crossref_doi(doi)
            except _Unanswered:
                failed.add(doi)
                continue
            if rec:
                results[doi] = Resolved("doi:" + doi, "crossref", rec, _now())
            elif doi not in failed:
                results[doi] = Resolved("doi:" + doi, "none", None, _now())
        for doi in todo:
            if doi in results:
                self.cache.put(results[doi])
        return results

    def resolve_references(self, refs: Sequence[ReferenceLike]) -> dict[int, Resolved]:
        """Reference index -> positive resolution (DOI path first, then title match)."""
        by_doi = {r.index: normalise_doi(r.doi) for r in refs if normalise_doi(r.doi)}
        resolved = self.resolve_dois(by_doi.values())
        out = {i: resolved[d] for i, d in by_doi.items() if d in resolved and resolved[d].record}
        for ref in refs:
            if ref.index in out or not ref.title:
                continue
            hit = self.cache.get(ref.key)
            if hit is not None:
                if hit.record:
                    out[ref.index] = hit
                continue
            rec: dict[str, Any] | None = None
            source = "none"
            answered = True
            try:
                rec = self.s2_title_match(ref)
                source = "s2_title" if rec else source
            except _Unanswered:
                answered = False
            if rec is None:
                try:
                    rec = self.crossref_bibliographic(ref)
                    source = "crossref_title" if rec else source
                except _Unanswered:
                    answered = False
            if rec is None and not answered:
                continue
            res = Resolved(ref.key, source, rec, _now())
            self.cache.put(res)
            if rec:
                out[ref.index] = res
        return out


__all__ = (
    "BatchResolver", "CROSSREF", "OA_BATCH", "PREFER_BATCH_ABOVE", "ReferenceLike", "Resolved", "ResolverCache",
    "S2_BATCH", "TITLE_MATCH_FLOOR", "TokenBucket", "normalise_doi", "title_similarity",
)
