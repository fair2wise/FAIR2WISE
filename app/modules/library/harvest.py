"""The Harvester (A8, T45): search arXiv and OpenAlex for a topic, download open-access PDFs into a category.

One ``library_harvest`` job, in the worker process:

1. validate the inputs that ride in ``OperationInputs.params`` (``topic``, ``category``, ``schema``, ``sources``,
   ``max_downloads``, ``per_query``): the schema only *labels* the run, it changes neither search nor ranking;
2. search each source (a source that cannot run is logged and skipped), merge and dedupe by DOI / arXiv id;
3. score every candidate against the topic into a tier (:func:`score_candidate`): A, B, or C (never downloaded);
4. for tier A then B in score order download the open-access PDF (every hop checked by ``NetworkPolicy``, byte
   and time limits, ``%PDF-`` check) and file it through :meth:`LibraryService.upload` (year folder and category
   manifest as for an upload), up to ``max_downloads``;
5. a tier A/B paper without a usable open-access PDF is ``paywalled``: a person completes it from the tab (opens
   the publisher in their own browser, saves the PDF, drops it on the candidate). The server never requests a
   paywalled publisher page.

Every candidate and its outcome is kept in the sidecar ``harvest.sqlite`` (run id = job id), so a resumed job
redoes nothing finished and the tab can show every run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from ..contracts.errors import ContractError, ErrorCode
from ..jobs.pipeline import StageContext, StageResult
from ..literature.safe_fetch import BlockedURL, NetworkPolicy
from .arrange import is_valid_folder_name
from .service import LibraryService

logger = logging.getLogger(__name__)

SOURCES = ("arxiv", "openalex")
MAX_TOPIC_CHARS = 200
MAX_DOWNLOADS = 200
MAX_PER_QUERY = 100
STATUSES = ("downloaded", "in_library", "paywalled", "filed", "failed", "skipped", "dismissed")
#: Fetch results meaning "this open-access link is not really open" (the paper becomes ``paywalled``).
PAYWALL_STATUSES = ("refused:not_pdf", "failed:http_401", "failed:http_402", "failed:http_403", "failed:http_451")
PDF_MAGIC = b"%PDF-"

_STOPWORDS = frozenset({"a", "an", "and", "the", "of", "in", "on", "for", "with", "by", "to", "from", "using", "via"})
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+\-]*")
_PHRASE = re.compile(r'"([^"]+)"')
_SCHEMA_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,80}$")


# -- scoring ------------------------------------------------------------------------------------------------


def topic_terms(topic: str) -> list[str]:
    """The distinct search terms of ``topic``: quoted phrases stay whole, other words lose stop words.

    Returns:
        Lower-cased terms in order of first appearance.
    """
    terms: list[str] = []
    for phrase in _PHRASE.findall(topic):
        text = " ".join(_TOKEN.findall(phrase.lower()))
        if text and text not in terms:
            terms.append(text)
    rest = _PHRASE.sub(" ", topic).lower()
    for token in _TOKEN.findall(rest):
        if token not in _STOPWORDS and token not in terms:
            terms.append(token)
    return terms


def _contains(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def score_candidate(topic: str, title: str, abstract: str = "") -> tuple[float, str]:
    """Relevance of one paper to ``topic``: ``(score in 0..1, tier)``.

    Tier A: every topic term appears in the title or abstract and at least half appear in the title. Tier B: at
    least 60 % of the terms appear in the title or abstract. Tier C: the rest (never downloaded). The score is
    ``0.6 * title coverage + 0.4 * overall coverage``.
    """
    terms = topic_terms(topic)
    if not terms:
        return 0.0, "C"
    title_l, both = title.lower(), f"{title} {abstract}".lower()
    in_title = sum(1 for t in terms if _contains(title_l, t)) / len(terms)
    overall = sum(1 for t in terms if _contains(both, t)) / len(terms)
    score = round(0.6 * in_title + 0.4 * overall, 4)
    if overall == 1.0 and in_title >= 0.5:
        return score, "A"
    if overall >= 0.6:
        return score, "B"
    return score, "C"


# -- candidates ---------------------------------------------------------------------------------------------


@dataclass
class Candidate:
    """One paper found by a search."""

    title: str
    source: str
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str = ""
    landing_url: str = ""
    pdf_urls: list[str] = field(default_factory=list)
    tier: str = "C"
    score: float = 0.0

    @property
    def key(self) -> str:
        """Dedupe key: the lower-cased DOI, else the arXiv id without its version."""
        if self.doi:
            return "doi:" + self.doi.lower()
        if self.arxiv_id:
            return "arxiv:" + re.sub(r"v\d+$", "", self.arxiv_id)
        return "title:" + " ".join(_TOKEN.findall(self.title.lower()))


def clean_doi(value: object) -> str | None:
    """A bare DOI (no resolver prefix, lower case) or ``None``."""
    raw = str(value or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
    return raw.lower() or None


Searcher = Callable[[str, int], list[Candidate]]


def arxiv_queries(topic: str) -> list[str]:
    """The arXiv queries of ``topic``: the whole phrase, then every term ANDed."""
    terms = topic_terms(topic)
    if not terms:
        return []
    queries = [f'all:"{" ".join(terms)}"'] if len(terms) > 1 else []
    queries.append(" AND ".join(f'all:"{t}"' for t in terms))
    return queries


def arxiv_searcher() -> Searcher:
    """A searcher over the pinned ``arxiv`` package (newest first, one polite request at a time)."""
    def search(topic: str, per_query: int) -> list[Candidate]:
        import arxiv

        client = arxiv.Client(page_size=min(per_query, 50), delay_seconds=3.0, num_retries=3)
        found: list[Candidate] = []
        for query in arxiv_queries(topic):
            logger.info("arXiv search %r", query)
            request = arxiv.Search(query=query, max_results=per_query, sort_by=arxiv.SortCriterion.SubmittedDate,
                                   sort_order=arxiv.SortOrder.Descending)
            for result in client.results(request):
                arxiv_id = str(result.entry_id).rsplit("/", 1)[-1]
                found.append(Candidate(
                    title=" ".join(str(result.title).split()), source="arxiv", year=result.published.year,
                    doi=clean_doi(result.doi), arxiv_id=arxiv_id, abstract=" ".join(str(result.summary).split()),
                    landing_url=str(result.entry_id), pdf_urls=[f"https://arxiv.org/pdf/{arxiv_id}"]))
        return found

    return search


_OPENALEX_FIELDS = ("id,doi,title,publication_year,primary_location,best_oa_location,locations,open_access,"
                    "abstract_inverted_index")


def openalex_searcher(get_fetch: Callable[[], Callable[..., Any]]) -> Searcher:
    """A searcher over an OpenAlex ``fetch`` (``get_fetch`` may raise when OpenAlex is not configured)."""
    from ..literature.acquire import openalex_work_urls
    from ..literature.openalex import abstract_from_inverted_index, raise_for_no_data

    def search(topic: str, per_query: int) -> list[Candidate]:
        fetch = get_fetch()
        params = {"search": " ".join(topic_terms(topic)) or topic, "per_page": min(per_query, 100),
                  "sort": "relevance_score:desc", "select": _OPENALEX_FIELDS}
        data = fetch("works", params, timeout=30.0)
        raise_for_no_data(data)
        found: list[Candidate] = []
        for work in data.get("results", []):
            doi = clean_doi(work.get("doi"))
            primary = (work.get("primary_location") or {}).get("landing_page_url") or ""
            found.append(Candidate(
                title=" ".join(str(work.get("title") or "").split()), source="openalex",
                year=work.get("publication_year"), doi=doi,
                abstract=abstract_from_inverted_index(work.get("abstract_inverted_index")) or "",
                landing_url=f"https://doi.org/{doi}" if doi else (primary or str(work.get("id") or "")),
                pdf_urls=openalex_work_urls(work)))
        return found

    return search


def merge_candidates(groups: Iterable[Iterable[Candidate]]) -> list[Candidate]:
    """Merge searches into one list, deduped by :attr:`Candidate.key`; the first record wins, PDF links are unioned."""
    merged: dict[str, Candidate] = {}
    for group in groups:
        for cand in group:
            if not cand.title:
                continue
            have = merged.get(cand.key)
            if have is None:
                merged[cand.key] = cand
                continue
            have.pdf_urls.extend(u for u in cand.pdf_urls if u not in have.pdf_urls)
            have.doi = have.doi or cand.doi
            have.arxiv_id = have.arxiv_id or cand.arxiv_id
            have.abstract = have.abstract or cand.abstract
            have.year = have.year or cand.year
            have.landing_url = have.landing_url or cand.landing_url
    return list(merged.values())


# -- download -----------------------------------------------------------------------------------------------

#: ``(url, max_bytes) -> (bytes | None, status)``; status is ``ok`` or a ``SafeFetcher``-style reason.
Downloader = Callable[[str, int], "tuple[bytes | None, str]"]


def pdf_downloader(http: httpx.Client | None = None, policy: NetworkPolicy | None = None) -> Downloader:
    """A downloader that checks every redirect hop against ``policy`` and reads the body into memory.

    Mirrors :meth:`SafeFetcher.fetch_pdf` (scheme/port/public-address check per hop, byte and wall-clock limits,
    ``%PDF-`` signature) but returns the bytes so the caller can file them with the library's own upload path.
    """
    client = http or httpx.Client(headers={"User-Agent": "FAIR2WISE-harvester"})
    rules = policy or NetworkPolicy()

    def download(url: str, max_bytes: int) -> tuple[bytes | None, str]:
        deadline = time.monotonic() + rules.total_timeout
        timeout = httpx.Timeout(rules.read_timeout, connect=rules.connect_timeout)
        current = url
        for _hop in range(rules.max_redirects + 1):
            try:
                rules.check_url(current)
            except BlockedURL as exc:
                return None, f"blocked:{exc.reason}"
            try:
                with client.stream("GET", current, follow_redirects=False, timeout=timeout,
                                   headers={"Accept": "application/pdf,*/*;q=0.1"}) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        if not location:
                            return None, f"failed:http_{response.status_code}"
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        return None, f"failed:http_{response.status_code}"
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        return None, "refused:too_large"
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            return None, "refused:too_large"
                        if time.monotonic() > deadline:
                            return None, "failed:timeout"
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    return (data, "ok") if data.startswith(PDF_MAGIC) else (None, "refused:not_pdf")
            except httpx.TimeoutException:  # reported through the status string
                return None, "failed:timeout"
            except httpx.HTTPError:  # reported through the status string
                return None, "failed:network"
        return None, "blocked:too_many_redirects"

    return download


# -- storage ------------------------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, topic TEXT NOT NULL, category TEXT NOT NULL, schema_label TEXT, sources TEXT NOT NULL,
  max_downloads INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, key TEXT NOT NULL, title TEXT NOT NULL, year INTEGER, doi TEXT,
  arxiv_id TEXT, source TEXT NOT NULL, tier TEXT NOT NULL, score REAL NOT NULL, status TEXT NOT NULL,
  landing_url TEXT, pdf_url TEXT, relpath TEXT, detail TEXT, UNIQUE (run_id, key));
CREATE INDEX IF NOT EXISTS candidates_run ON candidates (run_id);
"""
_CANDIDATE_FIELDS = ("id", "title", "year", "doi", "arxiv_id", "source", "tier", "score", "status", "landing_url",
                     "pdf_url", "relpath", "detail")


def candidate_id(run_id: str, key: str) -> str:
    """Opaque id of one candidate of a run (16 hex)."""
    return hashlib.sha256(f"{run_id}\0{key}".encode()).hexdigest()[:16]


class HarvestStore:
    """The sidecar database of runs and their candidates (thread-safe, one connection)."""

    def __init__(self, path: Path | str) -> None:
        """Open (creating it and its directory) the database at ``path``."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(self.path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.executescript(_SCHEMA)

    def create_run(self, run_id: str, *, topic: str, category: str, schema: str | None, sources: Sequence[str],
                   max_downloads: int) -> None:
        """Record a run (a resumed job keeps its first record)."""
        with self._lock, self._con:
            self._con.execute(
                "INSERT OR IGNORE INTO runs (id,topic,category,schema_label,sources,max_downloads,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_id, topic, category, schema, ",".join(sources), max_downloads, datetime.now(UTC).isoformat()))

    def run(self, run_id: str) -> dict[str, Any] | None:
        """The run record or ``None``."""
        with self._lock:
            row = self._con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run_dto(row) if row else None

    def runs(self) -> list[dict[str, Any]]:
        """Every run, newest first, with its status counts."""
        with self._lock:
            rows = self._con.execute("SELECT * FROM runs ORDER BY created_at DESC, id").fetchall()
        return [self._run_dto(r) for r in rows]

    def _run_dto(self, row: sqlite3.Row) -> dict[str, Any]:
        with self._lock:
            counted = self._con.execute("SELECT status, COUNT(*) AS n FROM candidates WHERE run_id=? GROUP BY status",
                                        (row["id"],)).fetchall()
        counts = {s: 0 for s in STATUSES}
        counts.update({r["status"]: r["n"] for r in counted})
        counts["total"] = sum(r["n"] for r in counted)
        return {"id": row["id"], "topic": row["topic"], "category": row["category"], "schema": row["schema_label"],
                "sources": row["sources"].split(",") if row["sources"] else [],
                "max_downloads": row["max_downloads"], "created_at": row["created_at"], "counts": counts}

    def save_candidate(self, run_id: str, cand: Candidate, *, status: str, pdf_url: str | None = None,
                       relpath: str | None = None, detail: str = "") -> str:
        """Insert or update the candidate's outcome; returns its id."""
        cid = candidate_id(run_id, cand.key)
        with self._lock, self._con:
            self._con.execute(
                "INSERT INTO candidates (id,run_id,key,title,year,doi,arxiv_id,source,tier,score,status,landing_url,"
                "pdf_url,relpath,detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id,key) DO UPDATE SET status=excluded.status, pdf_url=excluded.pdf_url, "
                "relpath=excluded.relpath, detail=excluded.detail",
                (cid, run_id, cand.key, cand.title, cand.year, cand.doi, cand.arxiv_id, cand.source, cand.tier,
                 cand.score, status, cand.landing_url, pdf_url, relpath, detail))
        return cid

    def set_status(self, cid: str, status: str, *, relpath: str | None = None, detail: str = "") -> None:
        """Change a candidate's status (and where its PDF went)."""
        with self._lock, self._con:
            self._con.execute("UPDATE candidates SET status=?, relpath=COALESCE(?, relpath), detail=? WHERE id=?",
                              (status, relpath, detail, cid))

    def finished_keys(self, run_id: str) -> set[str]:
        """Keys of the candidates a resumed job must not redo (any settled outcome)."""
        with self._lock:
            rows = self._con.execute("SELECT key FROM candidates WHERE run_id=?", (run_id,)).fetchall()
        return {r["key"] for r in rows}

    def candidate(self, cid: str) -> dict[str, Any] | None:
        """One candidate (with its ``run_id``) or ``None``."""
        with self._lock:
            row = self._con.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None

    def candidates(self, run_id: str) -> list[dict[str, Any]]:
        """The candidates of a run: best tier and score first."""
        with self._lock:
            rows = self._con.execute("SELECT * FROM candidates WHERE run_id=? ORDER BY tier, score DESC, title",
                                     (run_id,)).fetchall()
        return [{k: r[k] for k in _CANDIDATE_FIELDS} for r in rows]


# -- the harvester ------------------------------------------------------------------------------------------


def file_stem(cand: Candidate) -> str:
    """A plain file name stem: the DOI with ``/`` as ``_`` (the harvest scripts' convention), else the arXiv id."""
    if cand.doi:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", cand.doi.replace("/", "_"))[:120]
    if cand.arxiv_id:
        return "arxiv_" + re.sub(r"[^A-Za-z0-9._-]+", "_", cand.arxiv_id)
    return "_".join(_TOKEN.findall(cand.title.lower()))[:80] or "paper"


class Harvester:
    """Runs harvests and serves their results (the worker runs :meth:`run`; the API reads the store)."""

    def __init__(self, service: LibraryService, store: HarvestStore, *, searchers: Mapping[str, Searcher] | None = None,
                 downloader: Downloader | None = None, delay: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        """Create a harvester.

        Args:
            service: The library (mount, catalog, upload path).
            store: The run and candidate database.
            searchers: ``source -> search`` overriding the real arXiv and OpenAlex searchers (tests).
            downloader: Overrides :func:`pdf_downloader` (tests).
            delay: Seconds between downloads (politeness).
            sleep: Clock for the delay.
        """
        self.service = service
        self.store = store
        self._searchers = dict(searchers) if searchers is not None else None
        self._downloader = downloader
        self.delay = delay
        self._sleep = sleep

    # -- inputs -----------------------------------------------------------------------------------

    @staticmethod
    def parse(params: Mapping[str, Any]) -> dict[str, Any]:
        """Validate the job ``params``.

        Raises:
            ContractError: ``INVALID_INPUT`` naming the problem.
        """
        def bad(message: str) -> ContractError:
            return ContractError(ErrorCode.INVALID_INPUT, message)

        topic = " ".join(str(params.get("topic") or "").split())
        if not topic or len(topic) > MAX_TOPIC_CHARS:
            raise bad(f"topic must be 1-{MAX_TOPIC_CHARS} characters")
        if not topic_terms(topic):
            raise bad("topic has no searchable words")
        category = str(params.get("category") or "").strip()
        if not is_valid_folder_name(category):
            raise bad("category must be a folder name (letters, digits, '_' and '-')")
        schema = params.get("schema")
        if schema is not None and not (isinstance(schema, str) and _SCHEMA_ID.match(schema)):
            raise bad("schema must be a registry id")
        sources = params.get("sources", list(SOURCES))
        if not isinstance(sources, (list, tuple)) or not sources or any(s not in SOURCES for s in sources):
            raise bad(f"sources must be a non-empty subset of {list(SOURCES)}")
        out: dict[str, Any] = {"topic": topic, "category": category, "schema": schema or None,
                               "sources": [s for s in SOURCES if s in sources]}
        for key, default, high in (("max_downloads", 20, MAX_DOWNLOADS), ("per_query", 50, MAX_PER_QUERY)):
            value = params.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= high:
                raise bad(f"{key} must be an integer from 1 to {high}")
            out[key] = value
        return out

    def _search_functions(self) -> Mapping[str, Searcher]:
        if self._searchers is not None:
            return self._searchers
        return {"arxiv": arxiv_searcher(), "openalex": openalex_searcher(self.service._get_fetch)}

    # -- the job ----------------------------------------------------------------------------------

    def run(self, ctx: StageContext) -> StageResult:
        """The ``library_harvest`` stage (see the module docstring)."""
        args = self.parse(ctx.inputs.params)
        run_id = ctx.job.job_id
        self.service._require_root()
        self.store.create_run(run_id, topic=args["topic"], category=args["category"], schema=args["schema"],
                              sources=args["sources"], max_downloads=args["max_downloads"])
        ctx.log(f"harvest {args['topic']!r} into {args['category']!r} from {', '.join(args['sources'])} "
                f"(schema {args['schema'] or 'none'}, up to {args['max_downloads']} downloads)")
        groups, searched = [], 0
        for source in args["sources"]:
            ctx.guard()
            try:
                found = self._search_functions()[source](args["topic"], args["per_query"])
            except Exception as exc:  # noqa: BLE001 - a source that cannot run must not stop the others
                ctx.log(f"{source} search skipped: {exc}", level="warning")
                continue
            searched += 1
            ctx.log(f"{source}: {len(found)} results")
            groups.append(found)
        if searched == 0:
            raise ContractError(ErrorCode.UNAVAILABLE, "no source could be searched (see the log)")
        candidates = merge_candidates(groups)
        for cand in candidates:
            cand.score, cand.tier = score_candidate(args["topic"], cand.title, cand.abstract)
        candidates.sort(key=lambda c: (c.tier, -c.score, c.title))
        ctx.log(f"{len(candidates)} distinct papers; tier A {sum(c.tier == 'A' for c in candidates)}, "
                f"B {sum(c.tier == 'B' for c in candidates)}, C {sum(c.tier == 'C' for c in candidates)}")
        counts = self._process(ctx, run_id, args, candidates)
        ctx.log("done: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        return StageResult(progress={"done": len(candidates), "total": len(candidates), **counts}, data={"run": run_id, "category": args["category"]})

    def _process(self, ctx: StageContext, run_id: str, args: Mapping[str, Any], candidates: list[Candidate]) -> dict[str, int]:
        counts = {"downloaded": 0, "in_library": 0, "paywalled": 0, "failed": 0, "skipped": 0}
        known_dois = {str(r["doi"]).lower() for r in self.service.catalog.rows() if r.get("doi")}
        settled = self.store.finished_keys(run_id)
        for cand in candidates:  # a resumed job keeps what already has an outcome
            if cand.key in settled:
                row = self.store.candidate(candidate_id(run_id, cand.key)) or {}
                if row.get("status") in counts:
                    counts[row["status"]] += 1
        downloader = self._downloader or pdf_downloader()
        max_bytes = self.service.max_upload_bytes
        for index, cand in enumerate(candidates):
            ctx.guard()
            if cand.key in settled:
                continue
            status, detail, relpath, used = self._outcome(ctx, cand, args, counts, known_dois, downloader, max_bytes)
            self.store.save_candidate(run_id, cand, status=status, pdf_url=used, relpath=relpath, detail=detail)
            counts[status] = counts.get(status, 0) + 1
            ctx.progress(done=index + 1, total=len(candidates), **counts)
        return counts

    def _outcome(self, ctx: StageContext, cand: Candidate, args: Mapping[str, Any], counts: Mapping[str, int],
                 known_dois: set[str], downloader: Downloader, max_bytes: int) -> tuple[str, str, str | None, str | None]:
        """``(status, detail, relpath, pdf_url)`` of one candidate."""
        if cand.doi and cand.doi in known_dois:
            return "in_library", "a paper with this DOI is already in the library", None, None
        if cand.tier == "C":
            return "skipped", "below the relevance cut-off", None, None
        if counts["downloaded"] >= args["max_downloads"]:
            return "skipped", "over the download limit", None, None
        if not cand.pdf_urls:
            return "paywalled", "no open-access PDF found", None, None
        last = ""
        for url in cand.pdf_urls:
            ctx.guard()
            data, status = downloader(url, max_bytes)
            self._sleep(self.delay)
            if data is None:
                last = status
                ctx.log(f"{cand.title[:70]}: {status}", level="warning")
                continue
            known = {"year": cand.year, "title": cand.title, "doi": cand.doi, "arxiv_id": cand.arxiv_id,
                     "source": cand.source}
            result = self.service.upload(f"{file_stem(cand)}.pdf", data, args["category"], known=known)
            relpath = result["paper"]["relpath"]
            if result["duplicate"]:
                return "in_library", f"identical to {relpath}", relpath, url
            ctx.log(f"filed {relpath}")
            return "downloaded", "", relpath, url
        if last in PAYWALL_STATUSES:
            return "paywalled", f"the open-access link is not a free PDF ({last})", None, None
        return "failed", last or "download failed", None, None

    # -- reads and the paywalled queue --------------------------------------------------------------

    def file_candidate_pdf(self, cid: str, filename: str | None, data: bytes) -> dict[str, Any]:
        """File a PDF a person supplied for a ``paywalled`` candidate into the run's category.

        Returns:
            ``{"candidate", "paper", "duplicate", "manifest"}``.

        Raises:
            ContractError: ``NOT_FOUND`` for an unknown candidate, ``CONFLICT`` unless it is ``paywalled``.
        """
        row = self._paywalled(cid)
        run = self.store.run(row["run_id"])
        category = run["category"] if run else ""
        known = {"year": row["year"], "title": row["title"], "doi": row["doi"], "arxiv_id": row["arxiv_id"],
                 "source": row["source"]}
        result = self.service.upload(filename or f"{file_stem(Candidate(row['title'], row['source'], doi=row['doi'], arxiv_id=row['arxiv_id']))}.pdf",
                                     data, category, known=known)
        relpath = result["paper"]["relpath"]
        if result["duplicate"]:
            self.store.set_status(cid, "in_library", relpath=relpath, detail=f"identical to {relpath}")
        else:
            self.store.set_status(cid, "filed", relpath=relpath, detail="supplied by hand")
        return {"candidate": self.store.candidate(cid), **result}

    def dismiss(self, cid: str) -> dict[str, Any]:
        """Mark a ``paywalled`` candidate ``dismissed``."""
        self._paywalled(cid)
        self.store.set_status(cid, "dismissed", detail="dismissed by hand")
        return {"candidate": self.store.candidate(cid)}

    def _paywalled(self, cid: str) -> dict[str, Any]:
        row = self.store.candidate(cid)
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "candidate not found")
        if row["status"] != "paywalled":
            raise ContractError(ErrorCode.CONFLICT, f"candidate is {row['status']}, not paywalled")
        return row


__all__ = ("Candidate", "Harvester", "HarvestStore", "PAYWALL_STATUSES", "SOURCES", "STATUSES", "arxiv_queries",
           "arxiv_searcher", "candidate_id", "clean_doi", "file_stem", "merge_candidates", "openalex_searcher",
           "pdf_downloader", "score_candidate", "topic_terms")
