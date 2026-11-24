"""Free PDF links for an open-access paper beyond the ones OpenAlex lists as ``pdf_url`` (owner decision 2026-11-24).

The Harvester only ever follows links on **open-access locations** of a work (``is_oa`` true, or the best open-access
location). Never a location OpenAlex does not call open access, never a metered route (``content.openalex.org`` is
deliberately not used). Each link comes with the rule that found it, so a candidate records how its PDF was reached.

Rules, tried lazily in this order (a rule runs only if the earlier ones gave no PDF):

``oa_pdf_url``
    any ``pdf_url`` of an open-access location, whatever its shape (the selector of the shared downloader keeps only URLs
    that look like PDFs; the ``%PDF-`` signature check of the downloader is the real test).
``zenodo_record``
    a Zenodo record page: the record's own file list (one JSON request to ``zenodo.org/api/records/<id>``); the first
    file whose key ends in ``.pdf``.
``bhl_part`` / ``bhl_item``
    a Biodiversity Heritage Library part or item page: ``/partpdf/<id>`` or ``/itempdf/<id>``.
``landing_citation_pdf_url``
    any other landing page of an open-access location (a handle, a SciELO or DSpace or OJS page, a publisher page of an
    open-access article): the page's ``citation_pdf_url`` meta tag, the Highwire/Google Scholar standard that
    repositories and journals publish for exactly this purpose. At most :data:`MAX_LANDING_FETCHES` pages per paper, each
    at most :data:`LANDING_MAX_BYTES`.

Every request here goes through the hardened getters (scheme, port and public-address check on every hop, byte cap).
The links it returns are fetched by the caller's downloader under the same checks, with the ``%PDF-`` signature and size
caps.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
import html
import re
from typing import Any
from urllib.parse import urlsplit

from ..literature.legacy_download import GetBytes, GetJson, JSONFetchError, hardened_get_bytes, hardened_get_json

#: Landing pages fetched per paper, and the most bytes read from one.
MAX_LANDING_FETCHES = 2
LANDING_MAX_BYTES = 1024 * 1024

_ZENODO = re.compile(r"^/(?:records?|doi)/(\d+)/?$")
_BHL = re.compile(r"^/(part|item)/(\d+)/?$")
_META = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r"""([A-Za-z_:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_SCIHUB = re.compile(r"sci-?hub", re.IGNORECASE)


@dataclass(frozen=True)
class ResolvedLink:
    """One URL to try and the rule that produced it."""

    url: str
    rule: str


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_http(url: str) -> bool:
    try:
        return urlsplit(url).scheme in ("http", "https") and bool(_host(url))
    except ValueError:
        return False


def oa_locations(work: Mapping[str, Any]) -> list[dict[str, str]]:
    """``[{pdf_url, landing_page_url}]`` of the open-access locations of an OpenAlex work (best first, no duplicates).

    A location counts only when OpenAlex marks it ``is_oa`` (or it is the work's ``best_oa_location``); a closed
    location is never returned, so nothing downstream can follow it.
    """
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    best = work.get("best_oa_location")
    for loc in [best, *(work.get("locations") or [])]:
        if not isinstance(loc, Mapping):
            continue
        if loc.get("is_oa") is not True and loc is not best:
            continue
        pdf = str(loc.get("pdf_url") or "").strip()
        landing = str(loc.get("landing_page_url") or "").strip()
        key = (pdf, landing)
        if (not pdf and not landing) or key in seen:
            continue
        seen.add(key)
        out.append({"pdf_url": pdf, "landing_page_url": landing})
    return out


def citation_pdf_url(page: bytes) -> str | None:
    """The absolute ``citation_pdf_url`` of an HTML page (``<meta name=... content=...>`` in either order), else ``None``."""
    text = page.decode("utf-8", errors="replace")
    for tag in _META.findall(text):
        attrs = {m.group(1).lower(): html.unescape(m.group(2) if m.group(2) is not None else m.group(3) or "")
                 for m in _ATTR.finditer(tag)}
        if attrs.get("name", "").lower() == "citation_pdf_url":
            url = attrs.get("content", "").strip()
            if _is_http(url):
                return url
    return None


def zenodo_pdf(record_id: str, get_json: GetJson) -> str | None:
    """The first ``.pdf`` file of a Zenodo record from the record's own API answer, else ``None``."""
    body = get_json(f"https://zenodo.org/api/records/{record_id}", timeout=20.0)
    files = body.get("files") if isinstance(body, dict) else None
    for entry in files if isinstance(files, list) else []:
        if not isinstance(entry, dict) or not str(entry.get("key") or "").lower().endswith(".pdf"):
            continue
        links = entry.get("links")
        url = str(links.get("self") or links.get("download") or "") if isinstance(links, dict) else ""
        if _is_http(url) and _host(url).endswith("zenodo.org"):
            return url
    return None


def resolve_links(work_locations: list[dict[str, str]], known: set[str], *, get_json: GetJson = hardened_get_json,
                  get_bytes: GetBytes = hardened_get_bytes,
                  on_error: Callable[[str, str, Exception], None] | None = None) -> Iterator[ResolvedLink]:
    """Yield further links for the open-access locations of one paper, lazily, rule by rule.

    Args:
        work_locations: Output of :func:`oa_locations`.
        known: URLs the caller already has (they are not yielded again).
        get_json: Hardened JSON getter (tests inject a fake).
        get_bytes: Hardened byte getter for landing pages (tests inject a fake).
        on_error: Called with ``(rule, host, exception)`` when a lookup fails; the lookup is then skipped.
    """
    seen = set(known)

    def fresh(url: str, rule: str) -> ResolvedLink | None:
        if not _is_http(url) or url in seen or _SCIHUB.search(_host(url)):
            return None
        seen.add(url)
        return ResolvedLink(url, rule)

    for loc in work_locations:   # rule 1: any pdf_url of an open-access location
        link = fresh(loc["pdf_url"], "oa_pdf_url") if loc["pdf_url"] else None
        if link:
            yield link
    landings = [loc["landing_page_url"] for loc in work_locations if loc["landing_page_url"]]
    generic: list[str] = []
    for landing in dict.fromkeys(landings):   # rules 2-3: known repositories
        if not _is_http(landing):
            continue
        parts = urlsplit(landing)
        host = _host(landing)
        if host.endswith("zenodo.org") and (m := _ZENODO.match(parts.path)):
            try:
                url = zenodo_pdf(m.group(1), get_json)
            except (JSONFetchError, ValueError, OSError) as exc:
                if on_error:
                    on_error("zenodo_record", host, exc)
                url = None
            link = fresh(url, "zenodo_record") if url else None
            if link:
                yield link
        elif host in ("www.biodiversitylibrary.org", "biodiversitylibrary.org") and (m := _BHL.match(parts.path)):
            kind = m.group(1)
            link = fresh(f"https://www.biodiversitylibrary.org/{kind}pdf/{m.group(2)}", f"bhl_{kind}")
            if link:
                yield link
        else:
            generic.append(landing)
    fetched = 0
    for landing in generic:   # rule 4: the landing page's own citation_pdf_url
        if fetched >= MAX_LANDING_FETCHES:
            break
        fetched += 1
        try:
            page = get_bytes(landing, headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"},
                             timeout=20.0, max_bytes=LANDING_MAX_BYTES)
        except (JSONFetchError, ValueError, OSError) as exc:
            if on_error:
                on_error("landing_citation_pdf_url", _host(landing), exc)
            continue
        url = citation_pdf_url(page)
        link = fresh(url, "landing_citation_pdf_url") if url else None
        if link:
            yield link


__all__ = ("LANDING_MAX_BYTES", "MAX_LANDING_FETCHES", "ResolvedLink", "citation_pdf_url", "oa_locations", "resolve_links",
           "zenodo_pdf")
