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

from collections.abc import Callable
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import re
import tempfile
import time
from typing import TypedDict
from urllib.parse import quote, quote_plus, urlencode, urljoin
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
    """A metadata lookup was refused by the network policy or did not return usable JSON."""


def hardened_get_bytes(url: str, *, headers: dict[str, str] | None = None, timeout: float = 30.0,
                       policy: NetworkPolicy | None = None, client: httpx.Client | None = None,
                       max_bytes: int = JSON_MAX_BYTES) -> bytes:
    """GET ``url`` under the egress policy and return the raw body (same rules as :func:`hardened_get_json`).

    Every hop is checked with :meth:`NetworkPolicy.check_url` (http/https only, ports 80/443, no credentials,
    public addresses), redirects are followed manually up to the policy limit, and the body is capped at
    ``max_bytes``.

    Raises:
        JSONFetchError: blocked URL or redirect target, non-200 status, oversized body, transport error
            or too many redirects.
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
                    if response.status_code != 200:
                        raise JSONFetchError(f"HTTP {response.status_code}")
                    body = bytearray()
                    for chunk in response.iter_bytes(64 * 1024):
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise JSONFetchError("response too large")
            except httpx.HTTPError as exc:
                raise JSONFetchError(f"network error ({type(exc).__name__})") from exc
            return bytes(body)
        raise JSONFetchError("too many redirects")
    finally:
        if own:
            http.close()


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


#: Metadata hosts of the retained download agent. All three already sat behind the legacy clients; the
#: policy has no host allowlist (scheme, port, credential and public-address checks apply to any host).
OPENALEX_URL = "https://api.openalex.org"
ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
UNPAYWALL_URL = "https://api.unpaywall.org/v2"
JSON_HEADERS = {"User-Agent": DOWNLOAD_HEADERS["User-Agent"], "Accept": "application/json"}
ATOM_HEADERS = {"User-Agent": DOWNLOAD_HEADERS["User-Agent"], "Accept": "application/atom+xml"}

GetJson = Callable[..., JsonValue]
GetBytes = Callable[..., bytes]


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


def openalex_search(search_text: str, per_page: int, *, oa_only: bool = True, mailto: str | None = None,
                    api_key: str | None = None, get_json: GetJson = hardened_get_json) -> list[dict[str, JsonValue]]:
    """``pyalex.Works().search(text)[.filter(open_access={"is_oa": True})].get(per_page=N)`` over the hardened getter.

    Raises:
        ValueError: ``per_page`` outside 1..200 (pyalex's own rule) or an answer without ``results``.
        JSONFetchError: transport or policy failure.
    """
    if not isinstance(per_page, int) or per_page < 1 or per_page > 200:
        raise ValueError("per_page should be an integer between 1 and 200")
    query = f"search={quote_plus(search_text)}"
    if oa_only:
        query += "&filter=open_access.is_oa:true"
    query += f"&per-page={per_page}"
    if mailto:
        query += f"&mailto={quote_plus(mailto)}"
    body = get_json(f"{OPENALEX_URL}/works?{query}", headers=_openalex_headers(mailto, api_key))
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValueError("Unknown response format")
    return [row for row in body["results"] if isinstance(row, dict)]


def openalex_work(work_id: str, *, mailto: str | None = None, api_key: str | None = None,
                  get_json: GetJson = hardened_get_json) -> dict[str, JsonValue]:
    """``pyalex.Works()[work_id]`` over the hardened getter (the single-work answer must carry an ``id``)."""
    url = f"{OPENALEX_URL}/works/{quote_plus(work_id)}"
    if mailto:
        url += f"?mailto={quote_plus(mailto)}"
    body = get_json(url, headers=_openalex_headers(mailto, api_key))
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


_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
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
    """Parse an arXiv API Atom answer into entries, in document order.

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
        return []
    if _DOCTYPE_RE.search(text):
        logger.warning("arXiv answer carries a DTD/entity declaration; ignored")
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
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
        })
    return out


def arxiv_search(search_text: str, max_results: int, *, get_bytes: GetBytes = hardened_get_bytes,
                 retries: int = ARXIV_RETRIES) -> list[ArxivEntry]:
    """``arxiv.Client(page_size=N, delay_seconds=0, num_retries=2).results(Search(query, N, Relevance))``.

    One page (``page_size == max_results``), the package's URL and its retry rule (``retries`` extra attempts
    after a failed request, no pause, as ``delay_seconds=0``); a policy refusal is not retried.
    """
    args = {"search_query": search_text, "id_list": "", "sortBy": "relevance", "sortOrder": "descending",
            "start": "0", "max_results": str(max_results)}
    url = f"{ARXIV_QUERY_URL}?{urlencode(args)}"
    for attempt in range(retries + 1):
        try:
            body = get_bytes(url, headers=ATOM_HEADERS)
        except JSONFetchError as exc:
            if attempt >= retries or str(exc).startswith("blocked URL"):
                raise
            continue
        return parse_arxiv_atom(body)
    return []  # pragma: no cover - the loop returns or raises


def unpaywall_oa_pdfs(doi: str, email: str, *, timeout: float = 15.0,
                      get_json: GetJson = hardened_get_json) -> tuple[list[str], list[str]]:
    """Hardened twin of :func:`download_pdfs.fetch_unpaywall_oa_pdfs` (same filters, same result, same warnings)."""
    raw = re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi or "").strip(), flags=re.I)
    if not raw.lower().startswith("10.") or not is_usable_contact_email(email):
        return [], []
    try:
        body = get_json(f"{UNPAYWALL_URL}/{quote(raw, safe=UNPAYWALL_SAFE)}?{urlencode({'email': email})}",
                        headers=JSON_HEADERS, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - any lookup failure means "no extra URLs"
        logger.warning("Unpaywall lookup failed for %s (%s)", raw, exc)
        return [], []
    if not isinstance(body, dict):
        return [], []
    return select_unpaywall_oa_pdfs(body)


#: Characters ``requests`` leaves unquoted in a URL path (so the old and new Unpaywall URLs agree).
UNPAYWALL_SAFE = "!$&'()*+,/:;=@[]~%"


__all__ = ("ARXIV_QUERY_URL", "DOWNLOAD_HEADERS", "ArxivEntry", "JSONFetchError", "default_fetcher",
           "hardened_download_pdf", "hardened_get_bytes", "hardened_get_json", "openalex_search", "openalex_work",
           "arxiv_search", "parse_arxiv_atom", "unpaywall_oa_pdfs")
