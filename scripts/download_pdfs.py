#!/usr/bin/env python3
"""
download_pdfs.py

Search and download full-text PDFs of papers from arXiv (or OpenAlex).
Save each PDF to a directory with filename '[DOI].pdf'.

Usage:
    python download_pdfs.py \
        --keyword "organic photovoltaics" \
        --target ./pdfs \
        --max-results 100
"""

import os
import re
import time
import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)


PDF_MAGIC = b"%PDF-"

# Repository hosts that serve legal OA PDFs without a publisher session.
# Keep in sync with DownloadAgent. Do not add publisher landing pages.
RELIABLE_PDF_HOSTS = (
    "arxiv.org",
    "biorxiv.org",
    "chemrxiv.org",
    "core.ac.uk",
    "europepmc.org",
    "escholarship.org",
    "hal.archives-ouvertes.fr",
    "hal.science",
    "medrxiv.org",
    "ncbi.nlm.nih.gov",
    "osti.gov",
    "plos.org",
    "zenodo.org",
)

# Unpaywall and the OpenAlex polite pool need a real contact address; sending a
# template value is worse than sending none.
_PLACEHOLDER_EMAIL_RE = re.compile(
    r"@(example\.(com|org|net)|test|localhost|invalid|domain\.com)$|"
    r"^(you|user|me|someone|changeme|your[._-]?email)@",
    re.I,
)

DOWNLOAD_HEADERS = {
    "User-Agent": "FAIRtoWISE-harvest/1.0 (legal OA PDF fetch)",
    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
}

_ARXIV_ID_RE = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf|html|ftp)/|10\.48550/arxiv\.)"
    r"([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z\-]+/\d{7}(?:v\d+)?)",
    re.I,
)
_PMCID_RE = re.compile(r"PMC(\d+)", re.I)
_RXIV_CONTENT_RE = re.compile(
    r"^(https?://(?:www\.)?(?:bio|med|chem)rxiv\.org/content/.+?)(?:\.(?:full\.)?pdf)?/?$",
    re.I,
)


def _host(url: str) -> str:
    try:
        return (urlparse(str(url).strip()).hostname or "").lower()
    except ValueError:
        return ""


def _host_allowed(host: str) -> bool:
    if not host:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in RELIABLE_PDF_HOSTS)


def _is_scihub_host(host: str) -> bool:
    lowered = (host or "").lower()
    if not lowered:
        return False
    return "sci-hub" in lowered or lowered.startswith("scihub.") or ".scihub." in lowered


def _looks_like_pdf_url(url: str) -> bool:
    parsed = urlparse(str(url).strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    lowered = str(url).lower()
    query = parse_qs(parsed.query)
    if path.lower().endswith(".pdf") or "/pdf" in path.lower():
        return True
    if "osti.gov" in host and "/servlets/purl/" in path.lower():
        return True
    if "europepmc.org" in host and (query.get("pdf") or "/pdf" in path.lower()):
        return True
    if "plos.org" in host and (
        "printable" in lowered or query.get("type") == ["printable"] or "file" in path.lower()
    ):
        return True
    if "zenodo.org" in host and "/files/" in path.lower():
        return True
    if "core.ac.uk" in host and (
        path.lower().endswith(".pdf") or "/download/" in path.lower()
    ):
        return True
    if "escholarship.org" in host and "/content/" in path.lower() and path.lower().endswith(".pdf"):
        return True
    return False


def _pmcid_from_text(text: str) -> Optional[str]:
    match = _PMCID_RE.search(text or "")
    if match:
        return f"PMC{match.group(1)}"
    numeric = re.search(r"/pmc/articles/(\d+)", text or "", re.I)
    if numeric:
        return f"PMC{numeric.group(1)}"
    return None


def europepmc_pdf_url(pmcid: str) -> str:
    ident = pmcid if str(pmcid).upper().startswith("PMC") else f"PMC{pmcid}"
    return f"https://europepmc.org/articles/{ident.upper()}?pdf=render"


def rewrite_oa_pdf_url(url: str, *, osti_biblio_ok: bool = False) -> Optional[str]:
    """Map a known OA landing page to a direct PDF URL, or keep an already-PDF URL.

    Returns None for publisher pages, PubMed abstracts, and other non-PDF landings.
    Does not invent DOIs.
    """
    raw = str(url or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not _host_allowed(host):
        return None
    path = parsed.path or ""

    arxiv_id = None
    arxiv_match = _ARXIV_ID_RE.search(raw)
    if arxiv_match and "arxiv.org" in host:
        arxiv_id = arxiv_match.group(1)
    if arxiv_id:
        return f"https://arxiv.org/pdf/{arxiv_id}"

    pmcid = _pmcid_from_text(raw)
    if pmcid and ("ncbi.nlm.nih.gov" in host or "europepmc.org" in host):
        if "ncbi.nlm.nih.gov" in host and "/pmc/" not in raw.lower() and "/articles/pmc" not in raw.lower():
            return None
        return europepmc_pdf_url(pmcid)

    if any(name in host for name in ("biorxiv.org", "medrxiv.org", "chemrxiv.org")):
        if path.lower().endswith(".pdf"):
            return raw
        content = _RXIV_CONTENT_RE.match(raw.split("?")[0])
        if content:
            return f"{content.group(1)}.full.pdf"
        return None

    if "osti.gov" in host:
        if "/servlets/purl/" in path.lower():
            return raw
        if osti_biblio_ok:
            biblio = re.search(r"/biblio/(\d+)", path)
            if biblio:
                return f"https://www.osti.gov/servlets/purl/{biblio.group(1)}"
        return None

    if "escholarship.org" in host:
        return raw if _looks_like_pdf_url(raw) else None

    if "zenodo.org" in host:
        return raw if _looks_like_pdf_url(raw) else None

    if "plos.org" in host:
        return raw if _looks_like_pdf_url(raw) else None

    if "hal.science" in host or "hal.archives-ouvertes.fr" in host:
        if path.lower().endswith(".pdf") or "/file/" in path.lower() or "/document" in path.lower():
            return raw
        return None

    if "core.ac.uk" in host:
        if path.lower().endswith(".pdf") or "/download/" in path.lower():
            return raw
        return None

    if _looks_like_pdf_url(raw):
        return raw
    return None


def is_reliable_pdf_url(url: str, *, osti_biblio_ok: bool = False) -> bool:
    """True when the URL is an allowlisted OA host and maps to a fetchable PDF."""
    return rewrite_oa_pdf_url(url, osti_biblio_ok=osti_biblio_ok) is not None


def listed_oa_pdf_url(url: str, *, osti_biblio_ok: bool = False) -> Optional[str]:
    """Keep an OpenAlex/Unpaywall pdf_url, including publisher hosts.

    Repository landings are rewritten as before. Publisher hosts are kept only
    when the source already listed a pdf_url (no invented landings). Sci-Hub is
    rejected. HTML login walls are rejected later by the PDF magic-byte check.
    """
    raw = str(url or "").strip()
    if not raw:
        return None
    rewritten = rewrite_oa_pdf_url(raw, osti_biblio_ok=osti_biblio_ok)
    if rewritten:
        return rewritten
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if _is_scihub_host(_host(raw)):
        return None
    if _looks_like_pdf_url(raw):
        return raw
    return None


def prefer_repository_pdfs(urls: Iterable[str]) -> List[str]:
    """Stable-sort so allowlisted repository PDFs are tried before publishers."""
    repos: List[str] = []
    others: List[str] = []
    seen: set[str] = set()
    for url in urls:
        cleaned = str(url or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        if _host_allowed(_host(cleaned)):
            repos.append(cleaned)
        else:
            others.append(cleaned)
    return repos + others


def _dedupe_urls(urls: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for url in urls:
        cleaned = str(url or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def arxiv_id_from_text(*parts: Any) -> Optional[str]:
    blob = " ".join(str(part or "") for part in parts)
    match = _ARXIV_ID_RE.search(blob)
    return match.group(1) if match else None


def pmcid_from_openalex_work(work: Dict[str, Any]) -> Optional[str]:
    ids = work.get("ids") if isinstance(work.get("ids"), dict) else {}
    for value in (ids.get("pmcid"), work.get("pmcid")):
        pmcid = _pmcid_from_text(str(value or ""))
        if pmcid:
            return pmcid
    for loc in [work.get("best_oa_location"), work.get("primary_location"), *(work.get("locations") or [])]:
        if not isinstance(loc, dict):
            continue
        for key in ("pdf_url", "landing_page_url"):
            pmcid = _pmcid_from_text(str(loc.get(key) or ""))
            if pmcid:
                return pmcid
    return None


def _iter_openalex_locations(work: Dict[str, Any]) -> List[Dict[str, Any]]:
    locs: List[Dict[str, Any]] = []
    seen: set[tuple] = set()
    for loc in [work.get("best_oa_location"), work.get("primary_location"), *(work.get("locations") or [])]:
        if not isinstance(loc, dict):
            continue
        key = (loc.get("pdf_url"), loc.get("landing_page_url"))
        if key in seen:
            continue
        seen.add(key)
        locs.append(loc)
    return locs


def select_openalex_oa_pdfs(work: Dict[str, Any]) -> Tuple[List[str], Optional[str], List[str]]:
    """OA PDF URLs from an OpenAlex work. Repositories first; listed publisher pdf_urls last."""
    sourced: List[Tuple[str, str]] = []
    loc_urls = []
    for loc in _iter_openalex_locations(work):
        loc_urls.extend([loc.get("pdf_url"), loc.get("landing_page_url")])
    arxiv_id = arxiv_id_from_text(work.get("id"), work.get("doi"), *loc_urls)
    best = work.get("best_oa_location") if isinstance(work.get("best_oa_location"), dict) else {}
    for loc in _iter_openalex_locations(work):
        is_best = loc is best
        if loc.get("is_oa") is not True and not is_best:
            continue
        osti_ok = True
        for key in ("pdf_url", "landing_page_url"):
            rewritten = rewrite_oa_pdf_url(str(loc.get(key) or ""), osti_biblio_ok=osti_ok)
            if rewritten:
                sourced.append((rewritten, "openalex"))
        listed = listed_oa_pdf_url(str(loc.get("pdf_url") or ""), osti_biblio_ok=osti_ok)
        if listed:
            sourced.append((listed, "openalex"))
    oa = work.get("open_access") if isinstance(work.get("open_access"), dict) else {}
    if oa.get("is_oa") is True:
        oa_url = str(oa.get("oa_url") or "")
        rewritten = rewrite_oa_pdf_url(oa_url)
        if rewritten:
            sourced.append((rewritten, "openalex"))
        listed = listed_oa_pdf_url(oa_url)
        if listed:
            sourced.append((listed, "openalex"))
    pmcid = pmcid_from_openalex_work(work)
    if pmcid:
        sourced.append((europepmc_pdf_url(pmcid), "openalex"))
    if arxiv_id:
        sourced.append((f"https://arxiv.org/pdf/{arxiv_id}", "openalex"))
    urls = prefer_repository_pdfs(url for url, _ in sourced)
    sources = _dedupe_urls(source for url, source in sourced if url in urls)
    if sourced and not sources:
        sources = ["openalex"]
    if any(src == "openalex" for _, src in sourced):
        sources = ["openalex"]
    return urls, arxiv_id, sources if urls else []


def select_unpaywall_oa_pdfs(payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """OA PDF URLs from an Unpaywall JSON body. Repositories first; listed publisher pdf_urls last."""
    urls: List[str] = []
    locations = []
    best = payload.get("best_oa_location")
    if isinstance(best, dict):
        locations.append(best)
    for loc in payload.get("oa_locations") or []:
        if isinstance(loc, dict):
            locations.append(loc)
    for loc in locations:
        if loc.get("is_oa") is False:
            continue
        host_type = str(loc.get("host_type") or "").lower()
        pdf = str(loc.get("url_for_pdf") or "").strip()
        landing = str(loc.get("url") or "").strip()
        if host_type == "publisher":
            listed = listed_oa_pdf_url(pdf, osti_biblio_ok=True)
            if listed:
                urls.append(listed)
            else:
                rewritten = rewrite_oa_pdf_url(landing, osti_biblio_ok=True)
                if rewritten:
                    urls.append(rewritten)
            continue
        for candidate in (pdf, landing):
            rewritten = rewrite_oa_pdf_url(candidate, osti_biblio_ok=True)
            if rewritten:
                urls.append(rewritten)
    urls = prefer_repository_pdfs(urls)
    return urls, (["unpaywall"] if urls else [])


def is_usable_contact_email(email: Optional[str]) -> bool:
    """True for an address safe to send to Unpaywall / the OpenAlex polite pool."""
    candidate = str(email or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", candidate):
        return False
    return not _PLACEHOLDER_EMAIL_RE.search(candidate)


def fetch_unpaywall_oa_pdfs(doi: str, email: str, *, timeout: float = 15.0) -> Tuple[List[str], List[str]]:
    """Look up Unpaywall by DOI. Requires a real contact email. Does not invent DOIs."""
    raw = re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi or "").strip(), flags=re.I)
    if not raw.lower().startswith("10.") or not is_usable_contact_email(email):
        return [], []
    try:
        response = requests.get(
            f"https://api.unpaywall.org/v2/{raw}",
            params={"email": email},
            timeout=timeout,
            headers=DOWNLOAD_HEADERS,
        )
        if not response.ok:
            logging.warning("Unpaywall lookup failed for %s (%s)", raw, response.status_code)
            return [], []
        body = response.json()
    except Exception as exc:
        logging.warning("Unpaywall lookup failed for %s (%s)", raw, exc)
        return [], []
    if not isinstance(body, dict):
        return [], []
    return select_unpaywall_oa_pdfs(body)


# Informal polite-pool gaps. CLI --delay is applied as a floor on top of these.
OA_HOST_DELAYS: Dict[str, float] = {
    "api.semanticscholar.org": 1.2,
    "api.core.ac.uk": 1.0,
    "www.osti.gov": 1.0,
    "osti.gov": 1.0,
    "www.ebi.ac.uk": 1.0,
    "api.crossref.org": 1.0,
    "api.unpaywall.org": 1.0,
    "api.openalex.org": 0.2,
}

S2_BATCH_SIZE = 500
S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_delay_floor = 1.0
_last_host_hit: Dict[str, float] = {}


def set_delay_floor(seconds: float) -> None:
    """Set the minimum delay applied to every OA resolver host."""
    global _delay_floor
    try:
        _delay_floor = max(0.0, float(seconds))
    except (TypeError, ValueError):
        _delay_floor = 1.0


def host_delay(host: str) -> float:
    hostname = (host or "").lower()
    specific = OA_HOST_DELAYS.get(hostname)
    if specific is None:
        return _delay_floor
    return max(_delay_floor, specific)


def _respect_rate_limit(url: str) -> None:
    host = _host(url)
    wait_for = host_delay(host)
    last = _last_host_hit.get(host)
    if last is not None and wait_for > 0:
        gap = wait_for - (time.monotonic() - last)
        if gap > 0:
            time.sleep(gap)
    _last_host_hit[host] = time.monotonic()


def bare_doi(doi: Optional[str]) -> Optional[str]:
    """Strip a doi.org prefix. Returns None when the value is not a DOI."""
    raw = re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi or "").strip(), flags=re.I)
    if not raw.lower().startswith("10."):
        return None
    return raw


def _trusted_oa_urls(
    candidates: Iterable[str],
    source: str,
    *,
    osti_biblio_ok: bool = False,
) -> Tuple[List[str], List[str]]:
    urls: List[str] = []
    for url in candidates:
        rewritten = rewrite_oa_pdf_url(str(url or ""), osti_biblio_ok=osti_biblio_ok)
        if rewritten:
            urls.append(rewritten)
    urls = prefer_repository_pdfs(urls)
    return urls, ([source] if urls else [])


def _json_headers() -> Dict[str, str]:
    return {**DOWNLOAD_HEADERS, "Accept": "application/json"}


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=45),
    retry=retry_if_exception_type((requests.RequestException,)),
)
def _oa_get(url: str, **kwargs):
    _respect_rate_limit(url)
    timeout = kwargs.pop("timeout", 15.0)
    response = requests.get(url, timeout=timeout, **kwargs)
    if response.status_code in _RETRYABLE_STATUS:
        response.raise_for_status()
    return response


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=45),
    retry=retry_if_exception_type((requests.RequestException,)),
)
def _oa_post(url: str, **kwargs):
    _respect_rate_limit(url)
    timeout = kwargs.pop("timeout", 30.0)
    response = requests.post(url, timeout=timeout, **kwargs)
    if response.status_code in _RETRYABLE_STATUS:
        response.raise_for_status()
    return response


def select_semantic_scholar_oa_pdfs(paper: Optional[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """Allowlisted OA PDF from a Semantic Scholar paper object."""
    if not isinstance(paper, dict):
        return [], []
    oa = paper.get("openAccessPdf") if isinstance(paper.get("openAccessPdf"), dict) else {}
    return _trusted_oa_urls([str(oa.get("url") or "")], "semantic-scholar")


def fetch_semantic_scholar_batch_oa_pdfs(
    dois: Iterable[str],
    *,
    timeout: float = 30.0,
) -> Dict[str, Tuple[List[str], List[str]]]:
    """Batch-lookup Semantic Scholar openAccessPdf. Keyed by each input DOI."""
    ordered = [str(doi) for doi in dois]
    out: Dict[str, Tuple[List[str], List[str]]] = {doi: ([], []) for doi in ordered}
    indexed: List[Tuple[str, str]] = []
    seen_ids: set[str] = set()
    originals_for: Dict[str, List[str]] = {}
    for doi in ordered:
        bare = bare_doi(doi)
        if not bare:
            continue
        s2_id = f"DOI:{bare}"
        originals_for.setdefault(s2_id, [])
        if doi not in originals_for[s2_id]:
            originals_for[s2_id].append(doi)
        if s2_id in seen_ids:
            continue
        seen_ids.add(s2_id)
        indexed.append((doi, s2_id))
    if not indexed:
        return out
    logging.info("Semantic Scholar batch lookup for %d DOI(s)", len(indexed))
    for start in range(0, len(indexed), S2_BATCH_SIZE):
        chunk = indexed[start : start + S2_BATCH_SIZE]
        ids = [s2_id for _, s2_id in chunk]
        logging.info(
            "Semantic Scholar batch %d–%d of %d",
            start + 1,
            start + len(chunk),
            len(indexed),
        )
        try:
            response = _oa_post(
                S2_BATCH_URL,
                params={"fields": "openAccessPdf"},
                json={"ids": ids},
                timeout=timeout,
                headers=_json_headers(),
            )
            if not response.ok:
                logging.warning("Semantic Scholar batch failed (%s)", response.status_code)
                continue
            body = response.json()
        except Exception as exc:
            logging.warning("Semantic Scholar batch failed (%s)", exc)
            continue
        if not isinstance(body, list):
            logging.warning("Semantic Scholar batch returned a non-list payload")
            continue
        for (_orig, s2_id), paper in zip(chunk, body):
            urls, sources = select_semantic_scholar_oa_pdfs(
                paper if isinstance(paper, dict) else None
            )
            for original in originals_for.get(s2_id, [_orig]):
                out[original] = (urls, sources)
    return out


def select_core_oa_pdfs(payload: Any) -> Tuple[List[str], List[str]]:
    """CORE search/work JSON → allowlisted PDF URLs."""
    works: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            works.extend(item for item in results if isinstance(item, dict))
        elif payload.get("downloadUrl") or payload.get("download_url"):
            works.append(payload)
    elif isinstance(payload, list):
        works.extend(item for item in payload if isinstance(item, dict))
    candidates: List[str] = []
    for work in works:
        for key in ("downloadUrl", "download_url"):
            if work.get(key):
                candidates.append(str(work[key]))
        for link in work.get("links") or []:
            if not isinstance(link, dict):
                continue
            kind = str(link.get("type") or "").lower()
            if kind and kind not in {"download", "pdf", "fulltext"}:
                continue
            if link.get("url"):
                candidates.append(str(link["url"]))
    return _trusted_oa_urls(candidates, "core")


def fetch_core_oa_pdfs(doi: str, api_key: Optional[str], *, timeout: float = 15.0) -> Tuple[List[str], List[str]]:
    """Look up CORE.ac.uk by DOI. API key goes in Authorization, never the query."""
    bare = bare_doi(doi)
    key = str(api_key or os.environ.get("CORE_API_KEY") or "").strip()
    if not bare or not key:
        return [], []
    try:
        response = _oa_get(
            "https://api.core.ac.uk/v3/search/works",
            params={"q": f'doi:"{bare}"', "limit": 5},
            timeout=timeout,
            headers={**_json_headers(), "Authorization": f"Bearer {key}"},
        )
        if not response.ok:
            logging.warning("CORE lookup failed for %s (%s)", bare, response.status_code)
            return [], []
        body = response.json()
    except Exception as exc:
        logging.warning("CORE lookup failed for %s (%s)", bare, exc)
        return [], []
    return select_core_oa_pdfs(body)


def select_osti_oa_pdfs(payload: Any) -> Tuple[List[str], List[str]]:
    """OSTI records JSON → allowlisted PURL / repository PDFs."""
    records: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        records.extend(item for item in payload if isinstance(item, dict))
    elif isinstance(payload, dict):
        for key in ("records", "hits", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                records.extend(item for item in value if isinstance(item, dict))
                break
        if not records and (payload.get("osti_id") or payload.get("ostiId")):
            records.append(payload)
    candidates: List[str] = []
    for record in records:
        osti_id = record.get("osti_id") or record.get("ostiId")
        if osti_id:
            candidates.append(f"https://www.osti.gov/servlets/purl/{osti_id}")
        for link in record.get("links") or []:
            if isinstance(link, str):
                candidates.append(link)
                continue
            if not isinstance(link, dict):
                continue
            for key in ("href", "url", "URL"):
                if link.get(key):
                    candidates.append(str(link[key]))
    return _trusted_oa_urls(candidates, "osti", osti_biblio_ok=True)


def fetch_osti_oa_pdfs(doi: str, *, timeout: float = 15.0) -> Tuple[List[str], List[str]]:
    """Look up DOE public-access copies on OSTI by DOI. Keyless."""
    bare = bare_doi(doi)
    if not bare:
        return [], []
    try:
        logging.info("OSTI lookup for %s", bare)
        response = _oa_get(
            "https://www.osti.gov/api/v1/records",
            params={"doi": bare},
            timeout=timeout,
            headers=_json_headers(),
        )
        if not response.ok:
            logging.warning("OSTI lookup failed for %s (%s)", bare, response.status_code)
            return [], []
        body = response.json()
    except Exception as exc:
        logging.warning("OSTI lookup failed for %s (%s)", bare, exc)
        return [], []
    return select_osti_oa_pdfs(body)


def select_europepmc_search_oa_pdfs(payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Europe PMC search JSON → PMC render URLs (and other allowlisted PDFs)."""
    if not isinstance(payload, dict):
        return [], []
    result_list = payload.get("resultList") if isinstance(payload.get("resultList"), dict) else {}
    results = result_list.get("result") or []
    if isinstance(results, dict):
        results = [results]
    candidates: List[str] = []
    for record in results:
        if not isinstance(record, dict):
            continue
        pmcid = _pmcid_from_text(str(record.get("pmcid") or ""))
        if pmcid:
            candidates.append(europepmc_pdf_url(pmcid))
        full_text = record.get("fullTextUrlList") if isinstance(record.get("fullTextUrlList"), dict) else {}
        for item in full_text.get("fullTextUrl") or []:
            if not isinstance(item, dict):
                continue
            style = str(item.get("documentStyle") or "").lower()
            if style and style != "pdf":
                continue
            if item.get("url"):
                candidates.append(str(item["url"]))
    return _trusted_oa_urls(candidates, "europepmc")


def fetch_europepmc_search_oa_pdfs(doi: str, *, timeout: float = 15.0) -> Tuple[List[str], List[str]]:
    """Find a PMCID / OA PDF from a bare DOI via Europe PMC search. Keyless."""
    bare = bare_doi(doi)
    if not bare:
        return [], []
    try:
        logging.info("Europe PMC search for %s", bare)
        response = _oa_get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={
                "query": f'DOI:"{bare}"',
                "format": "json",
                "resultType": "core",
                "pageSize": 5,
            },
            timeout=timeout,
            headers=_json_headers(),
        )
        if not response.ok:
            logging.warning("Europe PMC search failed for %s (%s)", bare, response.status_code)
            return [], []
        body = response.json()
    except Exception as exc:
        logging.warning("Europe PMC search failed for %s (%s)", bare, exc)
        return [], []
    if not isinstance(body, dict):
        return [], []
    return select_europepmc_search_oa_pdfs(body)


def select_crossref_tdm_pdfs(payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Mine Crossref `link` for text-mining PDFs. Allowlist still gates hosts."""
    if not isinstance(payload, dict):
        return [], []
    message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    links = message.get("link") or []
    candidates: List[str] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        intended = str(link.get("intended-application") or "").lower()
        ctype = str(link.get("content-type") or "").lower()
        if intended != "text-mining":
            continue
        if ctype != "application/pdf":
            continue
        if link.get("URL"):
            candidates.append(str(link["URL"]))
    return _trusted_oa_urls(candidates, "crossref-tdm")


def fetch_crossref_tdm_pdfs(doi: str, *, timeout: float = 15.0) -> Tuple[List[str], List[str]]:
    """Crossref work links with intended-application=text-mining. No TDM token."""
    bare = bare_doi(doi)
    if not bare:
        return [], []
    try:
        logging.info("Crossref TDM lookup for %s", bare)
        response = _oa_get(
            f"https://api.crossref.org/works/{quote(bare, safe='')}",
            timeout=timeout,
            headers=_json_headers(),
        )
        if not response.ok:
            logging.warning("Crossref TDM lookup failed for %s (%s)", bare, response.status_code)
            return [], []
        body = response.json()
    except Exception as exc:
        logging.warning("Crossref TDM lookup failed for %s (%s)", bare, exc)
        return [], []
    if not isinstance(body, dict):
        return [], []
    return select_crossref_tdm_pdfs(body)


def search_arxiv(keyword: str, max_results: int):
    """
    Search arXiv using arxiv.py wrapper.
    Returns a list of arxiv.Result objects.
    """
    import arxiv

    client = arxiv.Client()
    search = arxiv.Search(
        query=keyword,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance,
        sort_order=arxiv.SortOrder.Descending
    )
    return client.results(search)


def _cleanup_partial(path: Path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def download_pdf(url: str, dest: str, retries=3):
    """
    Download PDF from URL and save to dest file.
    Handles retries, timeout, streaming, and rejects non-PDF payloads.
    """
    dest_path = Path(dest)
    tmp_path = dest_path.with_name(dest_path.name + ".part")

    for attempt in range(1, retries + 1):
        try:
            logging.debug(f"Downloading {url}")
            _cleanup_partial(tmp_path)
            resp = requests.get(url, stream=True, timeout=30, headers=DOWNLOAD_HEADERS)
            resp.raise_for_status()

            header = b""
            pending = []
            validated = False
            invalid_response = False
            content_type = (resp.headers.get("Content-Type") or "").lower()

            with open(tmp_path, 'wb') as f:
                for chunk in resp.iter_content(1024*32):
                    if not chunk:
                        continue
                    if not validated:
                        pending.append(chunk)
                        header += chunk
                        if len(header) < len(PDF_MAGIC):
                            continue
                        if not header.startswith(PDF_MAGIC):
                            invalid_response = True
                            break
                        for buffered in pending:
                            f.write(buffered)
                        pending = []
                        validated = True
                        continue
                    f.write(chunk)

            if invalid_response:
                _cleanup_partial(tmp_path)
                logging.warning(
                    "Rejected non-PDF response from %s (Content-Type: %s)",
                    url,
                    content_type or "unknown",
                )
                return False

            if not validated:
                _cleanup_partial(tmp_path)
                logging.warning("Rejected empty or truncated PDF response from %s", url)
                return False

            os.replace(tmp_path, dest_path)
            logging.info(f"Saved PDF to {dest}")
            return True
        except Exception as e:
            _cleanup_partial(tmp_path)
            logging.warning(f"Attempt {attempt} failed: {e}")
            time.sleep(5)
    logging.error(f"Failed to download {url}")
    return False


def run_arxiv_workflow(keyword: str, target_dir: str, max_results: int):
    os.makedirs(target_dir, exist_ok=True)
    for result in search_arxiv(keyword, max_results):
        doi = (result.doi or result.get_short_id()).replace('/', '_')
        pdf_url = result.pdf_url
        filename = os.path.join(target_dir, f"{doi}.pdf")
        if os.path.exists(filename):
            logging.info(f"Already exists: {filename}")
            continue
        success = download_pdf(pdf_url, filename)
        time.sleep(3)  # respect rate limits


def run_openalex_workflow(keyword: str, target_dir: str, max_results: int):
    from pyalex import Works

    os.makedirs(target_dir, exist_ok=True)
    works = Works().search(title=keyword).per_page(max_results).execute()
    for item in works:
        doi = item.doi.replace('/', '_') if item.doi else None
        url = item.primary_location.pdf_url if item.primary_location else None
        if not doi or not url:
            logging.debug(f"Skipping missing DOI/pdf: {item.id}")
            continue
        filename = os.path.join(target_dir, f"{doi}.pdf")
        if os.path.exists(filename):
            continue
        download_pdf(url, filename)
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--keyword', required=True)
    parser.add_argument('--target', default='./pdfs')
    parser.add_argument('--max-results', type=int, default=500)
    parser.add_argument('--source', choices=['arxiv', 'openalex'], default='arxiv')
    args = parser.parse_args()

    logging.info(f"Starting download: {args.keyword}")
    if args.source == 'openalex':
        run_openalex_workflow(args.keyword, args.target, args.max_results)
    else:
        run_arxiv_workflow(args.keyword, args.target, args.max_results)


if __name__ == '__main__':
    main()
