"""``download_pdf(url, dest) -> bool`` on top of the hardened T05 fetcher (addendum A7 T29, B29).

The legacy :class:`~app.modules.f2w_agent.download_agent.DownloadAgent` saves PDFs to
``<dir>/<name>.pdf`` with the signature ``download_pdf(url, dest) -> bool``. This keeps that
contract and replaces the transport with :class:`.safe_fetch.SafeFetcher`: scheme, port,
credential and private-address checks on every redirect hop, a byte limit, a wall-clock
deadline and PDF magic-byte verification before anything is stored.

Visible behaviour is preserved: ``True`` only when a valid PDF is at ``dest``; otherwise
``False`` and nothing (no partial file) at ``dest``; a non-PDF answer is not retried; HTTP and
network failures are retried up to ``retries`` times with a pause between attempts. Bytes are
fetched into a private temporary content-addressed store and copied out atomically, so
``dest`` never holds a partial file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, TypedDict
from urllib.parse import quote, quote_plus, urlencode, urljoin, urlsplit
import xml.etree.ElementTree as ET

import httpx

from ..contracts.context import AuthorizationMode, DeploymentProfile, Principal, RequestContext
from ..contracts.json_types import JsonValue
from .download_pdfs import is_usable_contact_email, select_unpaywall_oa_pdfs
from .safe_fetch import BlockedURL, NetworkPolicy, SafeFetcher
from .store import FileDocumentStore, StoreError

logger = logging.getLogger(__name__)

#: Same request headers as the legacy downloader (some repositories reject anonymous clients).
DOWNLOAD_HEADERS = {
    "User-Agent": "FAIRtoWISE-harvest/1.0 (legal OA PDF fetch)",
    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
}
#: Statuses that may succeed on a retry (the legacy code retried every exception).
RETRYABLE_PREFIXES = ("failed:",)


def _internal_context() -> RequestContext:
    """Context for the private temporary store: it grants nothing and authorises nothing."""
    return RequestContext(profile=DeploymentProfile.LOCAL, mode=AuthorizationMode.LOCAL_TRUSTED,
                          principal=Principal(subject="local-operator"), request_id="legacy-download",
                          capabilities=frozenset())


def default_fetcher(policy: NetworkPolicy | None = None) -> SafeFetcher:
    """A :class:`SafeFetcher` on a fresh ``httpx.Client`` carrying the legacy headers."""
    return SafeFetcher(httpx.Client(headers=DOWNLOAD_HEADERS), policy)


def hardened_download_pdf(url: str, dest: str, retries: int = 3, *, fetcher: SafeFetcher | None = None,
                          sleep: Callable[[float], None] = time.sleep, retry_pause: float = 5.0) -> bool:
    """Download ``url`` to ``dest`` through the hardened fetcher; ``True`` on success.

    Args:
        url: PDF URL (already filtered to reliable hosts by the caller).
        dest: Destination file path.
        retries: Attempts for retryable failures (HTTP errors, network, timeout).
        fetcher: Injected fetcher (tests); a default one is built and closed otherwise.
        sleep: Pause function between attempts.
        retry_pause: Seconds between attempts.
    """
    dest_path = Path(dest)
    ctx = _internal_context()
    own = fetcher is None
    active = fetcher or default_fetcher()
    try:
        for attempt in range(1, max(1, retries) + 1):
            with tempfile.TemporaryDirectory(prefix="f2w-dl-") as scratch:
                store = FileDocumentStore(Path(scratch))
                result = active.fetch_pdf(ctx, url, store)
                if result.ok:
                    try:
                        store.copy_out(ctx, result.ref, dest_path, overwrite=True)
                    except (OSError, StoreError) as exc:
                        logger.warning("Could not save %s (%s)", dest_path.name, type(exc).__name__)
                        return False
                    logger.info("Saved PDF to %s", dest)
                    return True
            logger.warning("Download of %s did not succeed (%s)", url, result.status)
            if not result.status.startswith(RETRYABLE_PREFIXES) or attempt >= retries:
                break
            sleep(retry_pause)
        return False
    finally:
        if own:
            active.http.close()


#: Largest JSON metadata answer accepted by :func:`hardened_get_json` (bytes).
JSON_MAX_BYTES = 8 * 1024 * 1024


class JSONFetchError(Exception):
    """A metadata lookup was refused by the network policy or did not return usable JSON.

    ``status`` is the final HTTP status when the failure was a non-200 answer (``None`` otherwise) and
    ``retry_after`` the parsed ``Retry-After`` header of that answer in seconds (``None`` when absent or
    unparseable). The message is unchanged (``"HTTP 429"``), so callers keying on the text still work.
    """

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


#: Statuses :func:`retry_after_wait` honours: a rate limit, or an overload that names when to come back.
RETRY_AFTER_STATUSES = frozenset({429, 503})
#: Longest pause a ``Retry-After`` header can ask for (seconds); larger values are clamped to this.
RETRY_AFTER_CAP_SECONDS = 60.0


@dataclass(frozen=True)
class HardenedResponse:
    """The final answer of :func:`hardened_get_response`: body (2xx only), status and response headers.

    ``repr`` shows the status and sizes only: response headers may carry cookies or other credentials and
    must never reach a log line.
    """

    body: bytes
    status: int
    headers: Mapping[str, str]

    def __repr__(self) -> str:
        return f"HardenedResponse(status={self.status}, bytes={len(self.body)}, headers={len(self.headers)})"

    def retry_after_seconds(self, now: Callable[[], float] = time.time) -> float | None:
        """The ``Retry-After`` header as seconds (delta or HTTP-date, never negative); ``None`` when absent/unparseable."""
        return _retry_after_seconds(self.headers.get("retry-after"), now)


def _retry_after_seconds(value: str | None, now: Callable[[], float]) -> float | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, when.timestamp() - now())


def hardened_get_response(url: str, *, headers: dict[str, str] | None = None, timeout: float = 30.0,
                          policy: NetworkPolicy | None = None, client: httpx.Client | None = None,
                          max_bytes: int = JSON_MAX_BYTES) -> HardenedResponse:
    """GET ``url`` under the egress policy and return the final status, headers and (for a 2xx) the body.

    The transport primitive of :func:`hardened_get_bytes` and :func:`hardened_get_json`: every hop is checked
    with :meth:`NetworkPolicy.check_url` (http/https only, ports 80/443, no credentials, public addresses),
    redirects are followed manually up to the policy limit, and the body is capped at ``max_bytes``. Unlike the
    two wrappers it does not turn a non-200 answer into an error: the caller sees the status and the response
    headers (``Retry-After`` for a 429/503) and decides. The body of a non-2xx answer is not read.

    Raises:
        JSONFetchError: blocked URL or redirect target, oversized body, transport error or too many redirects.
    """
    active_policy = policy or NetworkPolicy()
    own = client is None
    http = client or httpx.Client(headers=DOWNLOAD_HEADERS)
    try:
        current = url
        for _hop in range(active_policy.max_redirects + 1):
            try:
                active_policy.check_url(current)
            except BlockedURL as exc:
                raise JSONFetchError(f"blocked URL ({exc.reason})") from exc
            try:
                with http.stream("GET", current, headers=headers, follow_redirects=False,
                                 timeout=timeout) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        if not location:
                            raise JSONFetchError(f"redirect without location (HTTP {response.status_code})")
                        current = urljoin(current, location)
                        continue
                    body = bytearray()
                    if 200 <= response.status_code < 300:
                        for chunk in response.iter_bytes(64 * 1024):
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise JSONFetchError("response too large")
                    answer_headers = {key: response.headers[key] for key in response.headers.keys()}
            except httpx.HTTPError as exc:
                raise JSONFetchError(f"network error ({type(exc).__name__})") from exc
            return HardenedResponse(bytes(body), response.status_code, answer_headers)
        raise JSONFetchError("too many redirects")
    finally:
        if own:
            http.close()


def hardened_get_bytes(url: str, *, headers: dict[str, str] | None = None, timeout: float = 30.0,
                       policy: NetworkPolicy | None = None, client: httpx.Client | None = None,
                       max_bytes: int = JSON_MAX_BYTES) -> bytes:
    """GET ``url`` under the egress policy and return the raw body (same rules as :func:`hardened_get_json`).

    Built on :func:`hardened_get_response`; the behaviour is unchanged: only a 200 answer yields its body, any other
    status raises ``JSONFetchError("HTTP <status>")`` (now carrying ``status`` and a parsed ``retry_after`` for the
    callers that honour it; the headers themselves are not exposed).

    Raises:
        JSONFetchError: blocked URL or redirect target, non-200 status, oversized body, transport error
            or too many redirects.
    """
    answer = hardened_get_response(url, headers=headers, timeout=timeout, policy=policy, client=client,
                                   max_bytes=max_bytes)
    if answer.status != 200:
        raise JSONFetchError(f"HTTP {answer.status}", status=answer.status, retry_after=answer.retry_after_seconds())
    return answer.body


def hardened_get_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 30.0,
                      policy: NetworkPolicy | None = None, client: httpx.Client | None = None,
                      max_bytes: int = JSON_MAX_BYTES) -> JsonValue:
    """GET ``url`` and parse the body as JSON under the same egress policy as the PDF fetcher.

    Used by the harvest scripts and :class:`~app.modules.f2w_agent.download_agent.DownloadAgent` for Crossref,
    OpenAlex, arXiv-adjacent and Unpaywall metadata lookups, which previously went through
    ``urllib.request.urlopen``, ``pyalex`` or ``requests`` (any scheme, silent redirects, unbounded body).
    The transport rules are those of :func:`hardened_get_bytes`.

    Args:
        url: Metadata endpoint.
        headers: Request headers (for example ``User-Agent``); never put secrets in the URL.
        timeout: Per-request timeout in seconds.
        policy: Egress policy; the default :class:`NetworkPolicy` when omitted.
        client: Injected ``httpx.Client`` (tests); a default one is built and closed otherwise.
        max_bytes: Body byte limit.

    Returns:
        The decoded JSON document.

    Raises:
        JSONFetchError: blocked URL or redirect target, non-200 status, oversized body, transport error
            or a body that is not valid UTF-8 JSON.
    """
    body = hardened_get_bytes(url, headers=headers, timeout=timeout, policy=policy, client=client,
                              max_bytes=max_bytes)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise JSONFetchError("not valid JSON") from exc


def retry_after_wait(exc: BaseException) -> float | None:
    """Seconds to wait before the one polite retry, or ``None`` when ``exc`` does not call for one.

    Only a :class:`JSONFetchError` for a 429 or 503 **with** a parseable ``Retry-After`` qualifies (a 429 without
    one, such as an exhausted daily quota, fails as before: nothing says when to come back); the value is clamped
    to :data:`RETRY_AFTER_CAP_SECONDS`.
    """
    if not isinstance(exc, JSONFetchError) or exc.status not in RETRY_AFTER_STATUSES or exc.retry_after is None:
        return None
    return min(max(exc.retry_after, 0.0), RETRY_AFTER_CAP_SECONDS)


def _polite_call(getter: Callable[..., Any], url: str, *, sleep: Callable[[float], None], **kw: Any) -> Any:  # noqa: ANN401  # Any: the getter's own result (bytes or JSON)
    """Call ``getter(url, **kw)``; on a 429/503 with ``Retry-After`` wait that long (capped) once and retry once.

    The second failure propagates unchanged. Only the host and the status are logged, never the URL (it may
    carry a ``mailto``) and never a header.
    """
    try:
        return getter(url, **kw)
    except JSONFetchError as exc:
        wait = retry_after_wait(exc)
        if wait is None:
            raise
        logger.info("HTTP %s from %s: honouring Retry-After, pausing %.0f s before one retry",
                    exc.status, urlsplit(url).hostname, wait)
        sleep(wait)
        return getter(url, **kw)


class RateLimiter:
    """Spaces requests at least ``min_interval`` seconds apart per process (thread-safe; injectable clock and sleep).

    :meth:`wait` blocks until the interval since the last request has passed and reserves the slot; :meth:`mark`
    records the end of a request (the arXiv package measured its delay from there). ``clock`` and ``sleep``
    default to :func:`time.monotonic` and :func:`time.sleep`, looked up at call time so tests can patch them.
    """

    def __init__(self, min_interval: float, *, clock: Callable[[], float] | None = None,
                 sleep: Callable[[float], None] | None = None) -> None:
        self.min_interval = float(min_interval)
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self._lock = threading.Lock()

    def now(self) -> float:
        """The limiter's clock."""
        return (self._clock or time.monotonic)()

    def sleep(self, seconds: float) -> None:
        """The limiter's pause function (also used for ``Retry-After`` waits on the same path)."""
        (self._sleep or time.sleep)(seconds)

    def wait(self, interval: float | None = None) -> float:
        """Pause until ``max(interval, min_interval)`` seconds have passed since the last request; return the pause."""
        floor = max(self.min_interval, interval or 0.0)
        with self._lock:
            pause = 0.0 if self._last is None else max(0.0, floor - (self.now() - self._last))
            if pause > 0:
                self.sleep(pause)
            self._last = self.now()
        return pause

    def mark(self) -> None:
        """Record the end of a request (never moves the last-request time backwards)."""
        with self._lock:
            now = self.now()
            self._last = now if self._last is None else max(self._last, now)


#: arXiv's terms of use: no more than one request every three seconds (owner decision 2026-11-09).
ARXIV_MIN_INTERVAL_SECONDS = 3.0
#: The process-wide arXiv spacing shared by every ``arxiv_results``/``arxiv_search`` caller.
ARXIV_LIMITER = RateLimiter(ARXIV_MIN_INTERVAL_SECONDS)


def _sleeper(sleep: Callable[[float], None] | None) -> Callable[[float], None]:
    return sleep if sleep is not None else time.sleep


#: Metadata hosts of the retained download agent. All three already sat behind the legacy clients; the
#: policy has no host allowlist (scheme, port, credential and public-address checks apply to any host).
OPENALEX_URL = "https://api.openalex.org"
ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
UNPAYWALL_URL = "https://api.unpaywall.org/v2"
JSON_HEADERS = {"User-Agent": DOWNLOAD_HEADERS["User-Agent"], "Accept": "application/json"}
ATOM_HEADERS = {"User-Agent": DOWNLOAD_HEADERS["User-Agent"], "Accept": "application/atom+xml"}

GetJson = Callable[..., JsonValue]
GetBytes = Callable[..., bytes]


#: The OpenAlex key's environment name. It is read from the process environment only (owner decision 2026-11-09):
#: :mod:`app.modules.dotenv_entry` drops a ``.env``-sourced value, and the container start script passes the
#: variable through only when it is already exported.
OPENALEX_API_KEY_ENV = "OPENALEX_API_KEY"


def openalex_api_key(env: Mapping[str, str] | None = None) -> str | None:
    """The exported ``OPENALEX_API_KEY`` (stripped), or ``None`` when unset or blank.

    Read at request time from ``env`` (the process environment by default), so nothing holds the key between
    requests: it is never stored on an object, never in a ``repr``, never logged and only ever sent as the
    ``Authorization: Bearer`` header built by :func:`_openalex_headers`.
    """
    source = os.environ if env is None else env
    value = (source.get(OPENALEX_API_KEY_ENV) or "").strip()
    return value or None


def _openalex_headers(mailto: str | None, api_key: str | None) -> dict[str, str]:
    """Polite-pool and auth headers exactly as pyalex sent them (``From``, ``Authorization: Bearer``).

    The key travels only in the header, never in the URL, and is never logged.
    """
    headers = dict(JSON_HEADERS)
    if mailto:
        headers["From"] = mailto
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _quote_oa_value(value: object) -> str:
    """Quote a filter value as pyalex does: booleans lower-case, everything else ``quote_plus``."""
    if isinstance(value, bool):
        return str(value).lower()
    return quote_plus(str(value))


def _flatten_filter(filters: Mapping[str, Any], prefix: str = "") -> str:
    """Flatten nested filters like pyalex: ``{"open_access": {"is_oa": True}}`` -> ``open_access.is_oa:true``."""
    parts: list[str] = []
    for key, value in filters.items():
        path = f"{prefix}.{key}" if prefix else key
        parts.append(_flatten_filter(value, path) if isinstance(value, Mapping) else f"{path}:{_quote_oa_value(value)}")
    return ",".join(parts)


def _check_per_page(per_page: int | None) -> None:
    if per_page is not None and (not isinstance(per_page, int) or per_page < 1 or per_page > 200):
        raise ValueError("per_page should be an integer between 1 and 200")


def openalex_get(endpoint: str, *, search: str | None = None, filters: Mapping[str, Any] | None = None,
                 search_first: bool = True, per_page: int | None = None, cursor: str | None = None,
                 mailto: str | None = None, api_key: str | None = None,
                 get_json: GetJson = hardened_get_json,
                 sleep: Callable[[float], None] | None = None) -> tuple[list[dict[str, JsonValue]], str | None]:
    """One list request, ``pyalex.<Endpoint>().search(..).filter(..).get(per_page=N)`` over the hardened getter.

    The parameter order is pyalex's (``search`` and ``filter`` in call order, then ``cursor``, ``per-page``);
    ``mailto`` is appended (the documented polite-pool parameter, also sent as the ``From`` header).

    Args:
        endpoint: ``works`` or ``authors``.
        search: ``search=`` text.
        filters: Nested filter mapping, flattened like pyalex (``{"open_access": {"is_oa": True}}``).
        search_first: ``True`` for ``.search(..).filter(..)`` order, ``False`` for ``.filter(..).search(..)``.
        per_page: Page size, 1..200 (pyalex's own rule).
        cursor: Cursor token (``"*"`` starts a cursor walk).
        mailto: Contact address (``From`` header and ``mailto`` parameter), when usable.
        api_key: OpenAlex key; sent as ``Authorization: Bearer`` only, never in the URL or a log line.
        get_json: The hardened getter (patched by tests).
        sleep: Pause function for the one ``Retry-After`` wait (:func:`retry_after_wait`); ``time.sleep`` by default.

    Returns:
        The result rows and the next cursor (``None`` at the end or when the answer has none).

    Raises:
        ValueError: ``per_page`` out of range or an answer without ``results``.
        JSONFetchError: transport or policy failure.
    """
    _check_per_page(per_page)
    search_part = [f"search={quote_plus(search)}"] if search is not None else []
    filter_part = [f"filter={_flatten_filter(filters)}"] if filters else []
    parts = search_part + filter_part if search_first else filter_part + search_part
    if cursor is not None:
        parts.append(f"cursor={quote_plus(cursor)}")
    if per_page is not None:
        parts.append(f"per-page={per_page}")
    if mailto:
        parts.append(f"mailto={quote_plus(mailto)}")
    query = "&".join(parts)
    body = _polite_call(get_json, f"{OPENALEX_URL}/{endpoint}" + (f"?{query}" if query else ""),
                        headers=_openalex_headers(mailto, api_key), sleep=_sleeper(sleep))
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValueError("Unknown response format")
    meta = body.get("meta")
    next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
    return [row for row in body["results"] if isinstance(row, dict)], next_cursor if isinstance(next_cursor, str) else None


def openalex_pages(*, search: str | None = None, filters: Mapping[str, Any] | None = None, search_first: bool = True,
                   per_page: int | None = None, n_max: int = 10000, endpoint: str = "works",
                   mailto: str | None = None, api_key: str | None = None,
                   get_json: GetJson = hardened_get_json,
                   sleep: Callable[[float], None] | None = None) -> Iterator[list[dict[str, JsonValue]]]:
    """``.paginate(per_page=N)`` (pyalex's cursor walk, ``n_max`` 10000) over :func:`openalex_get`; yields pages."""
    cursor: str | None = "*"
    seen = 0
    while cursor is not None and not (n_max and seen >= n_max):
        rows, cursor = openalex_get(endpoint, search=search, filters=filters, search_first=search_first,
                                    per_page=per_page, cursor=cursor, mailto=mailto, api_key=api_key,
                                    get_json=get_json, sleep=sleep)
        seen += len(rows)
        yield rows


def openalex_search(search_text: str, per_page: int, *, oa_only: bool = True, mailto: str | None = None,
                    api_key: str | None = None, get_json: GetJson = hardened_get_json,
                    sleep: Callable[[float], None] | None = None) -> list[dict[str, JsonValue]]:
    """``pyalex.Works().search(text)[.filter(open_access={"is_oa": True})].get(per_page=N)`` over the hardened getter.

    Raises:
        ValueError: ``per_page`` outside 1..200 (pyalex's own rule) or an answer without ``results``.
        JSONFetchError: transport or policy failure.
    """
    rows, _cursor = openalex_get("works", search=search_text,
                                 filters={"open_access": {"is_oa": True}} if oa_only else None, per_page=per_page,
                                 mailto=mailto, api_key=api_key, get_json=get_json, sleep=sleep)
    return rows


def openalex_work(work_id: str, *, mailto: str | None = None, api_key: str | None = None,
                  get_json: GetJson = hardened_get_json,
                  sleep: Callable[[float], None] | None = None) -> dict[str, JsonValue]:
    """``pyalex.Works()[work_id]`` over the hardened getter (the single-work answer must carry an ``id``)."""
    url = f"{OPENALEX_URL}/works/{quote_plus(work_id)}"
    if mailto:
        url += f"?mailto={quote_plus(mailto)}"
    body = _polite_call(get_json, url, headers=_openalex_headers(mailto, api_key), sleep=_sleeper(sleep))
    if not isinstance(body, dict) or "id" not in body:
        raise ValueError("Unknown response format")
    return body


class ArxivEntry(TypedDict):
    """The fields of an arXiv Atom ``<entry>`` the agent consumes."""

    short_id: str
    pdf_url: str | None
    doi: str
    title: str
    summary: str
    year: int | None
    published: datetime


_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
ARXIV_RETRIES = 2
_DOCTYPE_RE = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _atom_text(entry: ET.Element, tag: str) -> str | None:
    found = entry.find(tag)
    return None if found is None else found.text


def _atom_time(text: str | None) -> datetime | None:
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def parse_arxiv_atom(body: bytes) -> list[ArxivEntry]:
    """Parse an arXiv API Atom answer into entries, in document order (see :func:`parse_arxiv_feed`)."""
    return parse_arxiv_feed(body)[0]


def parse_arxiv_feed(body: bytes) -> tuple[list[ArxivEntry], int]:
    """Parse an arXiv API Atom answer into ``(entries in document order, opensearch totalResults)``.

    Safe by construction: the body must be UTF-8 and may not carry a DTD or entity declaration (arXiv
    answers never do), so no entity expansion or external reference is possible; the stdlib parser then only
    sees plain elements. Like the ``arxiv`` package, a malformed document yields no entries and an entry
    without ``<id>``, ``<updated>`` or ``<published>`` is skipped. Entries whose id is not an
    ``arxiv.org/abs/`` URL (the API's error entries) are skipped too.
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("arXiv answer is not UTF-8; ignored")
        return [], 0
    if _DOCTYPE_RE.search(text):
        logger.warning("arXiv answer carries a DTD/entity declaration; ignored")
        return [], 0
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return [], 0
    total_text = _atom_text(root, f"{_OPENSEARCH}totalResults")
    try:
        total = int((total_text or "").strip())
    except ValueError:
        total = 0
    out: list[ArxivEntry] = []
    for entry in root.iterfind(f"{_ATOM}entry"):
        entry_id = _atom_text(entry, f"{_ATOM}id")
        updated = _atom_time(_atom_text(entry, f"{_ATOM}updated"))
        published = _atom_time(_atom_text(entry, f"{_ATOM}published"))
        if not entry_id or updated is None or published is None or "arxiv.org/abs/" not in entry_id:
            continue
        pdf_urls = [link.get("href") for link in entry.iterfind(f"{_ATOM}link")
                    if link.get("href") is not None and link.get("title") == "pdf"]
        out.append({
            "short_id": entry_id.split("arxiv.org/abs/")[-1],
            "pdf_url": pdf_urls[0] if pdf_urls else None,
            "doi": _atom_text(entry, f"{_ARXIV}doi") or "",
            "title": re.sub(r"\s+", " ", _atom_text(entry, f"{_ATOM}title") or ""),
            "summary": _atom_text(entry, f"{_ATOM}summary") or "",
            "year": published.year,
            "published": published,
        })
    return out, total


def arxiv_results(search_text: str, max_results: int, *, sort_by: str = "relevance", page_size: int | None = None,
                  delay_seconds: float = 0.0, retries: int = ARXIV_RETRIES,
                  get_bytes: GetBytes = hardened_get_bytes, sleep: Callable[[float], None] | None = None,
                  clock: Callable[[], float] | None = None, limiter: RateLimiter | None = None) -> Iterator[ArxivEntry]:
    """``arxiv.Client(page_size, delay_seconds, num_retries).results(Search(query, max_results, sort_by))``, lazily.

    Same request URLs as the package (one page of ``page_size``, default ``max_results``; the next page while
    fewer than ``max_results`` were yielded and ``opensearch:totalResults`` is not reached), the same retry rule
    (``retries`` extra attempts after a failed or, past page one, empty page; a policy refusal is not retried)
    and a ``delay_seconds`` floor between consecutive requests that is never below
    :data:`ARXIV_MIN_INTERVAL_SECONDS` (arXiv's one-request-per-3-s rule; owner decision 2026-11-09). The spacing is
    kept by a limiter shared by every caller in the process, so two searches back to back are spaced too. A 429 or
    503 answer with ``Retry-After`` is waited out once (capped, :func:`retry_after_wait`) before the package's own
    retry rule applies. Entries are yielded as they arrive, so a failure on page N leaves pages before N consumed,
    as with the package.

    Args:
        search_text: ``search_query`` text.
        max_results: Total entries to yield (``Search.max_results``).
        sort_by: ``relevance`` or ``submittedDate`` (descending, like ``SortOrder.Descending``).
        page_size: Entries per request; ``max_results`` when omitted.
        delay_seconds: Minimum seconds between consecutive requests (``Client.delay_seconds``); floored at 3 s.
        retries: Extra attempts after a failed request (``Client.num_retries``).
        get_bytes: The hardened getter (patched by tests).
        sleep: Pause function; given, a private limiter on it is used instead of the shared one (tests).
        clock: Monotonic clock; given, a private limiter on it is used instead of the shared one (tests).
        limiter: The :class:`RateLimiter` to space requests with; the process-wide :data:`ARXIV_LIMITER` when
            neither it nor ``sleep``/``clock`` is given.
    """
    if max_results <= 0:
        return
    size = page_size or max_results
    offset = yielded = 0
    first_page = True
    if limiter is None:
        limiter = ARXIV_LIMITER if sleep is None and clock is None else RateLimiter(
            ARXIV_MIN_INTERVAL_SECONDS, clock=clock, sleep=sleep)
    interval = max(delay_seconds, ARXIV_MIN_INTERVAL_SECONDS)

    def spaced_get(request_url: str, **kw: Any) -> bytes:  # noqa: ANN401  # Any: forwarded getter keyword arguments
        limiter.wait(interval)
        try:
            return get_bytes(request_url, **kw)
        finally:
            limiter.mark()

    while True:
        args = {"search_query": search_text, "id_list": "", "sortBy": sort_by, "sortOrder": "descending",
                "start": str(offset), "max_results": str(size)}
        url = f"{ARXIV_QUERY_URL}?{urlencode(args)}"
        entries: list[ArxivEntry] = []
        total = 0
        for attempt in range(retries + 1):
            try:
                body = _polite_call(spaced_get, url, headers=ATOM_HEADERS, sleep=limiter.sleep)
            except JSONFetchError as exc:
                if attempt >= retries or str(exc).startswith("blocked URL"):
                    raise
                continue
            entries, total = parse_arxiv_feed(body)
            if entries or first_page:
                break
            if attempt >= retries:
                raise JSONFetchError("unexpected empty page")
        if not entries:
            return
        for entry in entries:
            yield entry
            yielded += 1
            if yielded >= max_results:
                return
        offset += len(entries)
        if offset >= total:
            return
        first_page = False


def arxiv_search(search_text: str, max_results: int, *, get_bytes: GetBytes = hardened_get_bytes,
                 retries: int = ARXIV_RETRIES, limiter: RateLimiter | None = None) -> list[ArxivEntry]:
    """``arxiv.Client(page_size=N, delay_seconds=0, num_retries=2).results(Search(query, N, Relevance))`` as a list.

    ``delay_seconds=0`` is now floored at :data:`ARXIV_MIN_INTERVAL_SECONDS` by :func:`arxiv_results`.
    """
    return list(arxiv_results(search_text, max_results, get_bytes=get_bytes, retries=retries, limiter=limiter))


@dataclass(frozen=True)
class ArxivHit:
    """The ``arxiv.Result`` attributes the harvest scripts read, built from a parsed :class:`ArxivEntry`."""

    short_id: str
    title: str
    summary: str
    doi: str
    pdf_url: str | None
    published: datetime

    @classmethod
    def from_entry(cls, entry: ArxivEntry) -> ArxivHit:
        """Adapt a parsed entry."""
        return cls(entry["short_id"], entry["title"], entry["summary"], entry["doi"], entry["pdf_url"],
                   entry["published"])

    def get_short_id(self) -> str:
        """``arxiv.Result.get_short_id``."""
        return self.short_id


def unpaywall_oa_pdfs(doi: str, email: str, *, timeout: float = 15.0, get_json: GetJson = hardened_get_json,
                      sleep: Callable[[float], None] | None = None) -> tuple[list[str], list[str]]:
    """Hardened twin of :func:`download_pdfs.fetch_unpaywall_oa_pdfs` (same filters, same result, same warnings).

    A 429/503 with ``Retry-After`` is waited out once (:func:`retry_after_wait`) before the lookup counts as failed.
    """
    raw = re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi or "").strip(), flags=re.I)
    if not raw.lower().startswith("10.") or not is_usable_contact_email(email):
        return [], []
    try:
        body = _polite_call(get_json, f"{UNPAYWALL_URL}/{quote(raw, safe=UNPAYWALL_SAFE)}?{urlencode({'email': email})}",
                            headers=JSON_HEADERS, timeout=timeout, sleep=_sleeper(sleep))
    except Exception as exc:  # noqa: BLE001 - any lookup failure means "no extra URLs"
        logger.warning("Unpaywall lookup failed for %s (%s)", raw, exc)
        return [], []
    if not isinstance(body, dict):
        return [], []
    return select_unpaywall_oa_pdfs(body)


#: Characters ``requests`` leaves unquoted in a URL path (so the old and new Unpaywall URLs agree).
UNPAYWALL_SAFE = "!$&'()*+,/:;=@[]~%"


__all__ = ("ARXIV_LIMITER", "ARXIV_MIN_INTERVAL_SECONDS", "ARXIV_QUERY_URL", "DOWNLOAD_HEADERS", "RETRY_AFTER_CAP_SECONDS",
           "ArxivEntry", "ArxivHit", "HardenedResponse", "JSONFetchError", "RateLimiter", "default_fetcher",
           "hardened_download_pdf", "hardened_get_bytes", "hardened_get_json", "hardened_get_response", "openalex_api_key",
           "openalex_get", "openalex_pages", "openalex_search", "openalex_work", "arxiv_results", "arxiv_search",
           "parse_arxiv_atom", "parse_arxiv_feed", "retry_after_wait", "unpaywall_oa_pdfs")
