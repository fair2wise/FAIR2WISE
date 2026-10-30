"""OpenAlex and Semantic Scholar discovery client with response cache, negative cache and quota breaker.

Behind the ``lit_search_papers``, ``lit_get_papers_by_doi``, ``lit_find_citing_papers``
and ``lit_find_related_papers`` tools (L-01/L-02). Differences from the donor:

* **Injected, not global.** HTTP client, cache, quota breaker, credentials and
  sleep are constructor arguments. Nothing reads the environment or a ``.env``
  at import; :meth:`OpenAlexSettings.from_mapping` takes an explicit mapping.
* **Unavailable is not no-results.** The donor's list helpers returned ``[]`` on a
  transport failure; here they raise :class:`ProviderUnavailable` (or
  :class:`OpenAlexQuotaPaused`), so a tool can say "the provider is down" rather
  than "nothing is published on this".
* **Discovery only.** Results carry ``record_kind="external_search_result"``: they
  are bibliographic candidates, not ingested F2W Works, and nothing here downloads.

Kept from the donor: singleton-vs-filter cost model, positive cache (30 days),
singleton 404 negative cache (7 days, in a separate table), the process-wide
quota breaker that answers from cache while paused, ``Retry-After`` handling,
``OpenAlexNoData`` (an empty dict that says why), credential redaction, bulk
DOI batching (50 per OR-filter) with per-DOI cache priming.

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/literature/openalex.py
(BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Mapping, Sequence, Union, cast

import httpx

from ..contracts.errors import ContractError, ErrorCode

logger = logging.getLogger(__name__)

OPENALEX_BASE = "https://api.openalex.org"
SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1"
DEFAULT_TTL = 30 * 24 * 3600
DEFAULT_NEGATIVE_TTL = 7 * 24 * 3600
MAX_PER_PAGE = 50
BULK_DOI_BATCH = 50
EXTERNAL_RESULT = "external_search_result"

#: OpenAlex answered: there is no such record (a singleton 404, live or cached).
NOT_FOUND = "not_found"
#: The breaker is open: the allowance is spent until ``resume_at``.
QUOTA_PAUSED = "quota_paused"
#: No answer: a transport failure, or 5xx after the retry budget.
UNREACHABLE = "unreachable"
#: A 4xx other than 402/404/429: the request itself is wrong.
REJECTED = "rejected"

_WORK_FIELDS = (
    "id,doi,title,authorships,publication_year,primary_location,"
    "cited_by_count,concepts,abstract_inverted_index,open_access"
)
_BULK_WORK_FIELDS = (
    "id,doi,title,authorships,publication_year,primary_location,"
    "best_oa_location,cited_by_count,concepts,topics,keywords,"
    "abstract_inverted_index,open_access,referenced_works,ids"
)
_FILTER_UNSAFE = (",", "|")
_SINGLETON_COLLECTIONS = frozenset(
    {"works", "authors", "sources", "institutions", "topics", "publishers", "funders", "concepts"})
_BUDGET_MARKERS = ("credit", "budget", "quota", "resets at midnight", "insufficient", "exceeded")
_MAX_RETRY_AFTER_S = 30.0
_PLACEHOLDER_EMAIL_MARKERS = (
    "your-email", "your_email", "youremail", "example.com", "example.org", "changeme", "change-me", "user@",
)
_SECRET_PARAM = re.compile(r"(api_key=)[^&\s\"']+", re.IGNORECASE)


# ---------------------------------------------------------------------------------------------
# Errors and "no data, with the reason attached"
# ---------------------------------------------------------------------------------------------


class ProviderUnavailable(ContractError):
    """A discovery provider could not answer (transport, 5xx, rejected request, quota).

    Args:
        provider: Provider name (``"openalex"``, ``"semantic_scholar"``, ...).
        reason: One of :data:`UNREACHABLE`, :data:`REJECTED`, :data:`QUOTA_PAUSED`.
        detail: Redacted detail for logs and tool warnings.
    """

    def __init__(self, provider: str, reason: str = UNREACHABLE, detail: str = "") -> None:
        super().__init__(ErrorCode.UNAVAILABLE, f"{provider} unavailable ({reason})", retryable=True)
        self.provider = provider
        self.reason = reason
        self.detail = detail


class OpenAlexQuotaPaused(ProviderUnavailable):
    """Raised by the list-returning helpers while the breaker is open (donor ``OpenAlexQuotaPaused``)."""

    def __init__(self, resume_at: float | None, reason: str = "") -> None:
        super().__init__("openalex", QUOTA_PAUSED, reason)
        self.resume_at = resume_at
        when = (datetime.fromtimestamp(resume_at, UTC).isoformat(timespec="seconds")
                if resume_at else "unknown")
        self.args = (f"OpenAlex quota paused until {when} ({reason or 'quota'})",)

    @property
    def resume_at_iso(self) -> str:
        """Resume time as ISO-8601 UTC, or ``""``."""
        if not self.resume_at:
            return ""
        return datetime.fromtimestamp(self.resume_at, UTC).isoformat(timespec="seconds")


class OpenAlexNoData(dict[str, Any]):
    """An empty dict that says why it is empty (donor contract: falsy and ``== {}``)."""

    def __init__(self, reason: str, *, resume_at: float | None = None, detail: str = "") -> None:
        super().__init__()
        self.reason = reason
        self.resume_at = resume_at
        self.detail = detail

    def __repr__(self) -> str:
        return f"OpenAlexNoData(reason={self.reason!r}, resume_at={self.resume_at!r})"


def is_quota_paused(value: object) -> bool:
    """True when ``value`` is the breaker's "allowance spent" answer."""
    return isinstance(value, OpenAlexNoData) and value.reason == QUOTA_PAUSED


def is_not_found(value: object) -> bool:
    """True when OpenAlex answered that the record does not exist."""
    return isinstance(value, OpenAlexNoData) and value.reason == NOT_FOUND


def raise_for_no_data(value: object, provider: str = "openalex") -> None:
    """Raise the typed error a non-answer stands for; do nothing for data or not-found."""
    if not isinstance(value, OpenAlexNoData) or value.reason == NOT_FOUND:
        return
    if value.reason == QUOTA_PAUSED:
        raise OpenAlexQuotaPaused(value.resume_at, value.detail)
    raise ProviderUnavailable(provider, value.reason, value.detail)


def redact(text: object) -> str:
    """``text`` with any ``api_key=<value>`` replaced by ``api_key=***``."""
    return _SECRET_PARAM.sub(r"\1***", str(text))


class _RedactApiKey(logging.Filter):
    """Strip ``api_key=...`` from httpx's request log line (it logs full URLs at INFO)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never drop a record over its formatting
            return True
        if "api_key=" in message.lower():
            record.msg = redact(message)
            record.args = None
        return True


def install_log_redaction() -> None:
    """Add the api_key redaction filter to the ``httpx`` logger (idempotent; not run at import)."""
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactApiKey) for f in httpx_logger.filters):
        httpx_logger.addFilter(_RedactApiKey())


def abstract_from_inverted_index(inv_idx: dict[str, list[int]] | None) -> str:
    """Rebuild a readable abstract from OpenAlex's ``{word: [positions]}`` index."""
    if not inv_idx or not isinstance(inv_idx, dict):
        return ""
    word_pos: list[tuple[int, str]] = []
    for word, positions in inv_idx.items():
        if isinstance(positions, list):
            for pos in positions:
                try:
                    word_pos.append((int(pos), str(word)))
                except (TypeError, ValueError):
                    continue
    word_pos.sort()
    return " ".join(word for _, word in word_pos)


def bare_doi(value: object) -> str:
    """A DOI reduced to ``10.x/y``, lowercased; ``""`` when ``value`` is not one."""
    text = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text if text.startswith("10.") else ""


# ---------------------------------------------------------------------------------------------
# Quota breaker
# ---------------------------------------------------------------------------------------------


class QuotaBreaker:
    """Circuit breaker for a spent provider allowance.

    Only ever extends a pause, so a shorter wait from a second thread cannot
    reopen the gate early. Share one instance per process (the default
    :data:`DEFAULT_BREAKER`) so the intake, the resolver and the tools agree.

    Args:
        clock: Wall-clock epoch seconds (resume times are reported to people).
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._until = 0.0
        self._reason = ""

    def pause(self, seconds: float, reason: str) -> float:
        """Open the breaker for at least 60 s; returns the resume time."""
        until = self._clock() + max(float(seconds), 60.0)
        with self._lock:
            if until > self._until:
                self._until = until
                self._reason = reason
            return self._until

    def state(self) -> dict[str, Any] | None:
        """``{"resume_at", "resume_at_iso", "seconds_left", "reason"}`` while paused, else ``None``."""
        with self._lock:
            until, reason = self._until, self._reason
        now = self._clock()
        if now >= until:
            return None
        return {
            "resume_at": until,
            "resume_at_iso": datetime.fromtimestamp(until, UTC).isoformat(timespec="seconds"),
            "seconds_left": round(until - now, 1), "reason": reason,
        }

    def reset(self) -> None:
        """Close the breaker (tests; an operator who topped up the key)."""
        with self._lock:
            self._until = 0.0
            self._reason = ""


#: The process-wide breaker used when none is injected (donor semantics).
DEFAULT_BREAKER = QuotaBreaker()


def pause_openalex(seconds: float, reason: str) -> float:
    """Donor-compatible: pause :data:`DEFAULT_BREAKER`."""
    return DEFAULT_BREAKER.pause(seconds, reason)


def openalex_pause_state() -> dict[str, Any] | None:
    """Donor-compatible: state of :data:`DEFAULT_BREAKER`."""
    return DEFAULT_BREAKER.state()


def reset_openalex_pause() -> None:
    """Donor-compatible: reset :data:`DEFAULT_BREAKER`."""
    DEFAULT_BREAKER.reset()


def seconds_to_utc_midnight(now: float) -> float:
    """Seconds until the daily allowance resets (OpenAlex's 429 body says midnight UTC)."""
    current = datetime.fromtimestamp(now, UTC)
    midnight = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - current).total_seconds()


def retry_after_seconds(resp: Any) -> float | None:
    """The integer ``Retry-After`` a response names, else ``None`` (dates are not parsed)."""
    try:
        raw = str(resp.headers.get("retry-after", "")).strip()
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def quota_pause_seconds(resp: Any, now: float) -> float | None:
    """How long to pause when ``resp`` says the allowance is spent, else ``None``."""
    status = getattr(resp, "status_code", 0)
    retry_after = retry_after_seconds(resp)
    if status == 402:
        return retry_after if retry_after else seconds_to_utc_midnight(now)
    if status == 429:
        if retry_after is not None and retry_after > _MAX_RETRY_AFTER_S:
            return retry_after
        if retry_after is None:
            try:
                body = str(resp.text or "").lower()
            except Exception:  # noqa: BLE001
                body = ""
            if any(marker in body for marker in _BUDGET_MARKERS):
                return seconds_to_utc_midnight(now)
    return None


# ---------------------------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------------------------


class ResponseCache:
    """SQLite HTTP-response cache with a separate negative (singleton 404) table.

    Same schema as the donor's ``openalex_cache.db`` so an existing cache file
    can be reused. ``":memory:"`` gives a per-instance cache (tests).

    Args:
        path: Database file or ``":memory:"``.
        clock: Epoch seconds, injectable.
    """

    def __init__(self, path: Union[str, Path] = ":memory:", *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        target = str(path)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(target, check_same_thread=False, timeout=30.0)
        self._con.execute("CREATE TABLE IF NOT EXISTS openalex_cache (cache_key TEXT PRIMARY KEY, "
                          "response_json TEXT NOT NULL, fetched_at REAL NOT NULL)")
        self._con.execute("CREATE TABLE IF NOT EXISTS openalex_not_found (cache_key TEXT PRIMARY KEY, "
                          "status INTEGER NOT NULL, fetched_at REAL NOT NULL)")
        self._con.commit()

    @staticmethod
    def key(url: str, params: Mapping[str, Any]) -> str:
        """A key that ignores parameter order but not values (never includes credentials)."""
        payload = json.dumps({"path": url, "params": sorted(dict(params).items())}, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, key: str, ttl: float) -> dict[str, Any] | None:
        """A live positive answer, else ``None``."""
        with self._lock:
            row = self._con.execute("SELECT response_json, fetched_at FROM openalex_cache WHERE cache_key = ?",
                                    (key,)).fetchone()
        if row is None or self._clock() - float(row[1]) > ttl:
            return None
        try:
            return cast(dict[str, Any], json.loads(row[0]))  # Any: cached JSON payload
        except ValueError:
            return None

    def put(self, key: str, data: Mapping[str, Any]) -> None:
        """Store a positive answer."""
        with self._lock:
            self._con.execute("INSERT OR REPLACE INTO openalex_cache VALUES (?, ?, ?)",
                              (key, json.dumps(data, default=str), self._clock()))
            self._con.commit()

    def negative_get(self, key: str, ttl: float) -> bool:
        """Whether a singleton lookup under ``key`` 404'd within ``ttl`` seconds."""
        with self._lock:
            row = self._con.execute("SELECT fetched_at FROM openalex_not_found WHERE cache_key = ?",
                                    (key,)).fetchone()
        return row is not None and self._clock() - float(row[0]) <= ttl

    def negative_put(self, key: str, status: int) -> None:
        """Remember a singleton "no such record"."""
        with self._lock:
            self._con.execute("INSERT OR REPLACE INTO openalex_not_found VALUES (?, ?, ?)",
                              (key, int(status), self._clock()))
            self._con.commit()

    def counts(self) -> dict[str, int]:
        """Row counts (positive, negative), for status reports."""
        with self._lock:
            pos = self._con.execute("SELECT COUNT(*) FROM openalex_cache").fetchone()[0]
            neg = self._con.execute("SELECT COUNT(*) FROM openalex_not_found").fetchone()[0]
        return {"positive": int(pos), "negative": int(neg)}

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._con.close()


# ---------------------------------------------------------------------------------------------
# Settings and client
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenAlexSettings:
    """Credentials and cache lifetimes. Never logged; never part of a cache key.

    Attributes:
        api_key: OpenAlex key, sent as ``api_key`` (mandatory since 2026-02-13).
        mailto: Polite-pool contact; dropped when it is a placeholder.
        s2_api_key: Optional Semantic Scholar key.
        cache_ttl: Positive cache lifetime, seconds.
        negative_ttl: Singleton 404 lifetime, seconds.
    """

    api_key: str = field(default="", repr=False)
    mailto: str = ""
    s2_api_key: str = field(default="", repr=False)
    cache_ttl: float = DEFAULT_TTL
    negative_ttl: float = DEFAULT_NEGATIVE_TTL

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> OpenAlexSettings:
        """Build from an explicit settings mapping (keys as the donor's environment names)."""
        def num(name: str, default: float) -> float:
            try:
                return float(values.get(name, default))
            except (TypeError, ValueError):
                return default
        return cls(api_key=str(values.get("OPENALEX_API_KEY") or ""),
                   mailto=str(values.get("OPENALEX_EMAIL") or ""),
                   s2_api_key=str(values.get("SEMANTIC_SCHOLAR_API_KEY") or ""),
                   cache_ttl=num("OPENALEX_CACHE_TTL", DEFAULT_TTL),
                   negative_ttl=num("OPENALEX_NEGATIVE_TTL", DEFAULT_NEGATIVE_TTL))

    @property
    def polite_mailto(self) -> str:
        """The contact address when it is real, else ``""`` (a placeholder is worse than none)."""
        email = self.mailto.strip()
        if "@" not in email or any(m in email.lower() for m in _PLACEHOLDER_EMAIL_MARKERS):
            return ""
        return email

    def auth_params(self) -> dict[str, str]:
        """``api_key`` and ``mailto`` query parameters, each only when real."""
        params: dict[str, str] = {}
        if self.api_key.strip():
            params["api_key"] = self.api_key.strip()
        if self.polite_mailto:
            params["mailto"] = self.polite_mailto
        return params

    def user_agent(self) -> str:
        """A contactable, non-browser User-Agent."""
        contact = f" (mailto:{self.polite_mailto})" if self.polite_mailto else ""
        return f"FAIR2WISE-literature/1.0{contact}"


def default_http_client(settings: OpenAlexSettings, *, timeout: float = 30.0) -> httpx.Client:
    """An ``httpx.Client`` with the polite User-Agent. Built by trusted host wiring, not at import."""
    return httpx.Client(timeout=timeout, headers={"User-Agent": settings.user_agent()})


class OpenAlexClient:
    """OpenAlex/Semantic Scholar discovery with cache, negative cache and quota breaker.

    Args:
        http: HTTP client (an ``httpx.Client``; tests pass one on ``MockTransport``).
        cache: Response cache; defaults to a private in-memory cache.
        breaker: Quota breaker; defaults to the process-wide :data:`DEFAULT_BREAKER`.
        settings: Credentials and TTLs.
        sleep: Backoff sleep, injectable.
        clock: Epoch seconds for quota arithmetic, injectable.
    """

    def __init__(self, http: httpx.Client, *, cache: ResponseCache | None = None,
                 breaker: QuotaBreaker | None = None, settings: OpenAlexSettings | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time,
                 base_url: str = OPENALEX_BASE, s2_base_url: str = SEMANTIC_SCHOLAR_BASE) -> None:
        self.http = http
        self.cache = cache if cache is not None else ResponseCache(":memory:")
        self.breaker = breaker if breaker is not None else DEFAULT_BREAKER
        self.settings = settings or OpenAlexSettings()
        self._sleep = sleep
        self._clock = clock
        self.base_url = base_url.rstrip("/")
        self.s2_base_url = s2_base_url.rstrip("/")
        #: Requests actually sent (not cache hits), by kind.
        self.requests: dict[str, int] = {"openalex": 0, "semantic_scholar": 0}
        install_log_redaction()

    def _is_singleton(self, url: str) -> bool:
        if not url.startswith(self.base_url):
            return False
        collection, _, ident = url[len(self.base_url):].strip("/").partition("/")
        return collection in _SINGLETON_COLLECTIONS and bool(ident)

    def fetch(self, path: str, params: Mapping[str, Any] | None = None, *, ttl: float | None = None,
              use_cache: bool = True, timeout: float = 15.0, max_attempts: int = 3) -> dict[str, Any]:
        """GET from OpenAlex: cached, credentialed, backed off, behind the breaker.

        Returns parsed JSON on success, otherwise an empty :class:`OpenAlexNoData`
        whose ``reason`` is :data:`NOT_FOUND`, :data:`QUOTA_PAUSED`,
        :data:`UNREACHABLE` or :data:`REJECTED`. Never raises (donor ``fetch_openalex``).
        """
        params = dict(params or {})
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        effective_ttl = float(ttl if ttl is not None else self.settings.cache_ttl)
        key = ResponseCache.key(url, params)
        singleton = self._is_singleton(url)
        if use_cache:
            cached = self.cache.get(key, effective_ttl)
            if cached is not None:
                return cached
            if singleton and self.cache.negative_get(key, self.settings.negative_ttl):
                return OpenAlexNoData(NOT_FOUND, detail="cached 404")
        # The breaker is checked after the cache: a paused process still answers what it knows.
        paused = self.breaker.state()
        if paused is not None:
            return OpenAlexNoData(QUOTA_PAUSED, resume_at=paused["resume_at"], detail=paused["reason"])
        request_params = {**params, **self.settings.auth_params()}
        safe_url = redact(url)
        last_error = ""
        for attempt in range(max_attempts):
            try:
                self.requests["openalex"] += 1
                resp = self.http.get(url, params=request_params, timeout=timeout)
                if resp.status_code == 200:
                    data: dict[str, Any] = resp.json()
                    self.cache.put(key, data)
                    return data
                if resp.status_code == 404 and singleton:
                    self.cache.negative_put(key, 404)
                    return OpenAlexNoData(NOT_FOUND, detail="HTTP 404")
                pause_s = quota_pause_seconds(resp, self._clock())
                if pause_s is not None:
                    reason = f"HTTP {resp.status_code}"
                    resume_at = self.breaker.pause(pause_s, reason)
                    logger.warning("OpenAlex allowance spent (%s); pausing all OpenAlex calls", reason)
                    return OpenAlexNoData(QUOTA_PAUSED, resume_at=resume_at, detail=reason)
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {resp.status_code}"
                    retry_after = retry_after_seconds(resp)
                    if retry_after is not None and retry_after > _MAX_RETRY_AFTER_S:
                        return OpenAlexNoData(UNREACHABLE, detail=last_error)
                    self._sleep(retry_after if retry_after is not None else 2 ** attempt)
                    continue
                logger.warning("OpenAlex %s (no retry): %s", resp.status_code, safe_url)
                return OpenAlexNoData(REJECTED, detail=f"HTTP {resp.status_code}")
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = redact(f"{type(exc).__name__}")
                self._sleep(2 ** attempt)
                continue
            except Exception as exc:  # noqa: BLE001 - best-effort by contract
                logger.warning("OpenAlex unexpected error: %s (%s)", redact(type(exc).__name__), safe_url)
                return OpenAlexNoData(UNREACHABLE, detail=redact(type(exc).__name__))
        logger.warning("OpenAlex failed after %d attempts: %s (%s)", max_attempts, last_error, safe_url)
        return OpenAlexNoData(UNREACHABLE, detail=last_error)

    # -- searches -------------------------------------------------------------------------------

    def search_papers(self, query: str, *, technique: str = "", material: str = "", year_from: int = 0,
                      year_to: int = 0, max_results: int = 10) -> list[dict[str, Any]]:
        """Full-text OpenAlex search; technique/material are appended to the text.

        Raises:
            OpenAlexQuotaPaused: The allowance is spent.
            ProviderUnavailable: OpenAlex did not answer (not an empty result).
        """
        search_text = " ".join(part for part in (query.strip(), technique, material) if part)
        if not search_text:
            return []
        params: dict[str, Any] = {"search": search_text, "per_page": min(max(1, int(max_results)), MAX_PER_PAGE),
                                  "sort": "relevance_score:desc", "select": _WORK_FIELDS}
        filters = []
        if year_from:
            filters.append(f"publication_year:>{int(year_from) - 1}")
        if year_to:
            filters.append(f"publication_year:<{int(year_to) + 1}")
        if filters:
            params["filter"] = ",".join(filters)
        data = self.fetch("works", params, timeout=30.0)
        raise_for_no_data(data)
        return [paper_record(work) for work in data.get("results", [])]

    def lookup_dois(self, dois: Sequence[str], *, limit: int = 10) -> DoiLookup:
        """Resolve DOIs one by one (free singleton form), separating not-found from unavailable."""
        result = DoiLookup()
        for raw in list(dois)[:limit]:
            doi = str(raw).strip().replace("https://doi.org/", "")
            if not doi:
                continue
            work = self.fetch(f"works/doi:{doi}", {}, timeout=15.0)
            if is_quota_paused(work):
                raise OpenAlexQuotaPaused(work.resume_at, work.detail)  # type: ignore[attr-defined]
            if is_not_found(work):
                result.not_found.append(doi)
                continue
            if not work:
                result.unavailable.append(doi)
                continue
            record = paper_record(work, abstract_chars=800)
            record["doi"] = record["doi"] or doi
            record["referenced_works_count"] = len(work.get("referenced_works") or [])
            record["referenced_works"] = list(work.get("referenced_works") or [])
            result.papers.append(record)
        return result

    def get_papers_by_doi(self, dois: Sequence[str]) -> list[dict[str, Any]]:
        """Donor contract: records for the DOIs that resolved; unknown DOIs omitted.

        Raises:
            OpenAlexQuotaPaused: The allowance is spent.
            ProviderUnavailable: Every requested DOI went unanswered.
        """
        lookup = self.lookup_dois(dois)
        if lookup.unavailable and not lookup.papers and not lookup.not_found:
            raise ProviderUnavailable("openalex", UNREACHABLE, "no DOI answered")
        return lookup.papers

    def _prime_single_doi_cache(self, doi: str, work: Mapping[str, Any]) -> None:
        """Store a bulk-fetched work under both keys a later single-DOI resolve uses."""
        key = ResponseCache.key(f"{self.base_url}/works", {"filter": f"doi:{doi}"})
        self.cache.put(key, {"results": [dict(work)], "meta": {"count": 1, "source": "bulk"}})
        self.cache.put(ResponseCache.key(f"{self.base_url}/works/https://doi.org/{doi}", {}), dict(work))

    def fetch_works_by_doi(self, dois: Sequence[str], *, batch: int = BULK_DOI_BATCH,
                           prime_cache: bool = True) -> BulkResult:
        """Resolve many DOIs in OR-filtered batches of up to 50, priming the per-DOI cache.

        Returns:
            :class:`BulkResult` with ``found`` (``{bare_doi: work}``), the DOIs never
            asked, and ``stopped`` (``quota_paused``/``unreachable``) when a batch failed.
        """
        wanted: list[str] = []
        seen: set[str] = set()
        for raw in dois:
            doi = bare_doi(raw)
            if doi and doi not in seen:
                seen.add(doi)
                wanted.append(doi)
        safe = [d for d in wanted if not any(c in d for c in _FILTER_UNSAFE)]
        unsafe = [d for d in wanted if d not in set(safe)]
        out = BulkResult()
        size = max(1, min(int(batch), BULK_DOI_BATCH))
        for start in range(0, len(safe), size):
            chunk = safe[start:start + size]
            data = self.fetch("works", {"filter": "doi:" + "|".join(chunk), "per_page": len(chunk),
                                        "select": _BULK_WORK_FIELDS}, timeout=60.0)
            if not data:
                out.stopped = getattr(data, "reason", UNREACHABLE)
                out.resume_at = getattr(data, "resume_at", None)
                out.not_asked = safe[start:] + unsafe
                return out
            out.batches += 1
            for work in data.get("results") or []:
                doi = bare_doi(work.get("doi"))
                if not doi:
                    continue
                out.found[doi] = work
                if prime_cache:
                    self._prime_single_doi_cache(doi, work)
        for doi in unsafe:
            data = self.fetch(f"works/https://doi.org/{doi}", {})
            if isinstance(data, dict) and data.get("id"):
                out.found[doi] = data
                if prime_cache:
                    self._prime_single_doi_cache(doi, data)
            elif is_quota_paused(data):
                out.stopped = QUOTA_PAUSED
                out.resume_at = getattr(data, "resume_at", None)
                out.not_asked.append(doi)
        return out

    def find_citing_papers(self, openalex_id: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Works citing ``openalex_id``, most-cited first. Discovery only.

        Raises:
            OpenAlexQuotaPaused / ProviderUnavailable: As :meth:`search_papers`.
        """
        work_id = openalex_id if openalex_id.startswith("https://") else f"https://openalex.org/{openalex_id}"
        per_page = min(max(1, int(max_results)), MAX_PER_PAGE)
        data = self.fetch("works", {"filter": f"cites:{work_id}", "per_page": per_page,
                                    "sort": "cited_by_count:desc", "select": _WORK_FIELDS}, timeout=30.0)
        raise_for_no_data(data)
        return [paper_record(work) for work in data.get("results", [])]

    def find_related_papers(self, paper_id: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Semantic Scholar recommendations for ``paper_id`` (a DOI or S2 id). Not cached (POST).

        Raises:
            ProviderUnavailable: Semantic Scholar did not answer (the donor returned ``[]``).
        """
        headers = {"x-api-key": self.settings.s2_api_key} if self.settings.s2_api_key else {}
        try:
            self.requests["semantic_scholar"] += 1
            response = self.http.post(
                f"{self.s2_base_url}/paper/{paper_id}/recommendations", json={"positivePaperIds": [paper_id]},
                params={"limit": min(max(1, int(max_results)), 20),
                        "fields": "title,authors,year,abstract,citationCount,externalIds"},
                headers=headers, timeout=30.0)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("semantic_scholar", UNREACHABLE, type(exc).__name__) from exc
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            reason = REJECTED if 400 <= response.status_code < 500 and response.status_code != 429 else UNREACHABLE
            raise ProviderUnavailable("semantic_scholar", reason, f"HTTP {response.status_code}")
        papers: list[dict[str, Any]] = []
        for rec in (response.json() or {}).get("recommendedPapers", []):
            external = rec.get("externalIds") or {}
            papers.append({
                "openalex_id": "", "doi": external.get("DOI", "") or "", "title": rec.get("title", "") or "",
                "authors": [a.get("name", "") for a in (rec.get("authors") or [])[:5]], "year": rec.get("year"),
                "journal": "", "abstract": (rec.get("abstract") or "")[:500], "concepts": [],
                "citation_count": rec.get("citationCount", 0), "is_oa": False,
                "source": "semantic_scholar", "record_kind": EXTERNAL_RESULT,
            })
        return papers


@dataclass
class DoiLookup:
    """DOI lookup outcome: resolved records, verified not-found, and unanswered DOIs."""

    papers: list[dict[str, Any]] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)


@dataclass
class BulkResult:
    """Bulk DOI resolution outcome. A DOI absent from ``found`` and ``not_asked`` is unknown to OpenAlex."""

    found: dict[str, dict[str, Any]] = field(default_factory=dict)
    not_asked: list[str] = field(default_factory=list)
    stopped: str = ""
    resume_at: float | None = None
    batches: int = 0


def paper_record(work: Mapping[str, Any], *, abstract_chars: int = 500) -> dict[str, Any]:
    """One OpenAlex work reduced to the donor's search-result fields, marked as external.

    Keeps both ``openalex_id`` and ``doi`` so callers never fork on which one they had.
    """
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    abstract = abstract_from_inverted_index(work.get("abstract_inverted_index"))
    return {
        "openalex_id": work.get("id", "") or "",
        "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
        "title": work.get("title", "") or "",
        "authors": [(a.get("author") or {}).get("display_name", "") for a in (work.get("authorships") or [])[:5]],
        "year": work.get("publication_year"),
        "journal": source.get("display_name", "") or "",
        "abstract": abstract[:abstract_chars] if abstract else "",
        "concepts": [c.get("display_name", "") for c in (work.get("concepts") or [])[:8]],
        "citation_count": work.get("cited_by_count", 0),
        "is_oa": bool((work.get("open_access") or {}).get("is_oa")),
        "source": "openalex",
        "record_kind": EXTERNAL_RESULT,
    }


__all__ = (
    "BULK_DOI_BATCH", "BulkResult", "DEFAULT_BREAKER", "DoiLookup", "EXTERNAL_RESULT", "NOT_FOUND",
    "OPENALEX_BASE", "OpenAlexClient", "OpenAlexNoData", "OpenAlexQuotaPaused", "OpenAlexSettings",
    "ProviderUnavailable", "QUOTA_PAUSED", "QuotaBreaker", "REJECTED", "ResponseCache", "UNREACHABLE",
    "abstract_from_inverted_index", "bare_doi", "default_http_client", "install_log_redaction", "is_not_found",
    "is_quota_paused", "openalex_pause_state", "paper_record", "pause_openalex", "quota_pause_seconds",
    "raise_for_no_data", "redact", "reset_openalex_pause", "retry_after_seconds",
)
