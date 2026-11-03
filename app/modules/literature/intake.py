"""Local PDF intake: vet a file, establish its identity, and stage its bytes in the candidate store.

Four identity facts are kept apart (plan T05, L-05/L-06):

* **asserted DOI** - what a caller or filename *claims* (never trusted alone);
* **verified DOI** - a DOI whose registry title appears in this PDF's own first
  pages, or which folds to the filename *and* appears in the text;
* **canonical Work** - the bibliographic record (OpenAlex id) the verified DOI
  resolves to; a preprint and its journal version are *candidate* versions of
  one Work, proposed for review and never merged here;
* **source version** - the content address of these exact bytes in the store.

Order is cheapest-refusal first (donor ``prepare_local_pdf``): containment and
size, magic bytes, content hash (duplicate), text, DOI candidates, and only then
the registry. Nothing here ingests or extracts; a ``ready`` report means "these
bytes are in the candidate store under a verified identity".

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/paper_intake.py (``IntakeReport``,
``extract_doi_candidates``, ``filename_doi_candidates``, ``title_matches_pdf``,
``resolve_by_filename``, ``resolve_asserted``, ``resolve_verified``,
``prepare_local_pdf``) (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Any

from ..contracts.context import RequestContext
from .legacy import LegacyIndex, doi_key, doi_only, looks_like_pdf
from .licence import LicenceInfo
from .licence import collect as collect_licence
from .licence import licence_status as _licence_status
from .openalex import is_quota_paused
from .resolver_batch import title_similarity
from .store import AssetKind, FileDocumentStore, StoreError, sha256_of

#: Leading pages searched for a DOI and title-checked (a references section would add other papers' DOIs).
DOI_SEARCH_PAGES = 2
#: Smallest file accepted as a paper (donor cache floor).
MIN_PDF_BYTES = 1000

_DOI_IN_TEXT = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>,;)\]}]+")
_DOI_TRAILING = ".,;:)]}>'\"-"

#: Closed set of outcomes. ``ready`` is the only ingestable one.
STATUSES: tuple[str, ...] = (
    "ready",
    "skipped:not_pdf",
    "skipped:too_small",
    "skipped:too_large",
    "skipped:not_allowed",
    "skipped:no_text",
    "skipped:no_doi",
    "skipped:doi_unresolved",
    "skipped:openalex_unreachable",
    "skipped:openalex_quota_paused",
    "skipped:title_mismatch",
    "skipped:filename_mismatch",
    "skipped:duplicate",
    "failed:store",
)
INGESTABLE = frozenset({"ready"})
#: Outcomes that mean "ask again later"; never checkpointed as settled.
RETRYABLE = frozenset({"skipped:openalex_unreachable", "skipped:openalex_quota_paused"})

#: ``fetch(path, params) -> dict`` (an :meth:`OpenAlexClient.fetch`).
Fetch = Callable[..., dict[str, Any]]
#: ``extract(path, pages) -> str`` first-pages text plus document metadata.
TextExtractor = Callable[[Path, int], str]


@dataclass
class WorkIdentity:
    """The identity facts of one intake, kept separate (see module docstring)."""

    asserted_doi: str = ""
    pdf_doi_candidates: list[str] = field(default_factory=list)
    verified_doi: str = ""
    verification: str = ""          # title_match | filename_match | asserted_registry_only
    canonical_work_id: str = ""     # OpenAlex work id of the bibliographic record, when resolved
    source_ref: str = ""            # sha256:<hex> of these bytes
    source_version: str = "unknown"  # preprint | journal | unknown
    prior_source_refs: list[str] = field(default_factory=list)
    doi_mismatch: bool = False      # the asserted DOI differs from every DOI printed in the PDF

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form."""
        return asdict(self)


@dataclass
class IntakeReport:
    """What happened to one file. ``status`` is from :data:`STATUSES`; ``detail`` is prose only."""

    source: str
    status: str
    detail: str = ""
    doi: str = ""
    title: str = ""
    sha256: str = ""
    duplicate_of: list[str] = field(default_factory=list)
    candidates_tried: list[str] = field(default_factory=list)
    identity_asserted: bool = False
    identity: WorkIdentity = field(default_factory=WorkIdentity)
    work: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    #: B2 licence and open-access facts; every record carries one (explicit ``unknown`` when nothing is known).
    licence: LicenceInfo = field(default_factory=LicenceInfo)

    @property
    def licence_status(self) -> str:
        """``licensed`` | ``notice_only`` | ``unknown`` (see :mod:`.licence`)."""
        return _licence_status(self.licence)

    @property
    def ok(self) -> bool:
        """Whether the file is ready for ingest."""
        return self.status in INGESTABLE

    @property
    def retryable(self) -> bool:
        """Whether a later run may succeed (registry outage or quota)."""
        return self.status in RETRYABLE

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form; the source filename only, never a store path."""
        data = asdict(self)
        data["source"] = Path(self.source).name
        data["licence"] = self.licence.as_dict()
        return data


def pdf_first_pages_text(path: Path, pages: int = DOI_SEARCH_PAGES) -> str:
    """First ``pages`` pages of text plus document metadata; ``""`` on any failure.

    Read with :func:`app.modules.literature.pdf_reader.open_pdf` (pypdfium2). Pass a different
    ``text_extractor`` to :func:`prepare_local_pdf` to read the PDF another way.
    """
    from .pdf_reader import open_pdf

    chunks: list[str] = []
    try:
        with open_pdf(path) as doc:
            if getattr(doc, "needs_pass", False):
                return ""
            chunks.extend(str(v) for v in (doc.metadata or {}).values() if v)
            for index in range(min(pages, doc.page_count)):
                chunks.append(doc.load_page(index).get_text())
    except Exception:  # noqa: BLE001 - an unreadable PDF is a report, not a crash
        return ""
    return "\n".join(chunks)


def extract_doi_candidates(text: str, *, limit: int = 5) -> list[str]:
    """Distinct DOIs in ``text`` in first-occurrence order; a stripped form first, the raw form after."""
    seen: list[str] = []
    for match in _DOI_IN_TEXT.finditer(text or ""):
        raw = match.group(0)
        for form in (raw.rstrip(_DOI_TRAILING), raw):
            candidate = doi_only(form)
            if candidate and candidate not in seen:
                seen.append(candidate)
        if len(seen) >= limit:
            break
    return seen[:limit]


def filename_doi_candidates(filename: str, *, limit: int = 6) -> list[str]:
    """DOIs a filename implies; candidates only, each still verified.

    Patterns: F2W's own stems (``10.1021_acs.macromol.0c01782.pdf`` from
    ``DownloadAgent``/``harvest_rsoxs``, where the first ``_`` after the registrant
    is the ``/``), OSA/Optica journal names (donor) and arXiv ids (donor).
    """
    name = Path(filename).name
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    out: list[str] = []
    f2w = re.match(r"^(10\.\d{4,9})_(.+)$", stem)
    if f2w:
        out.append(f"{f2w.group(1)}/{f2w.group(2)}")
    m = re.match(r"^(ao|josa|josaa|oe|ol|optica)-(\d+)-(\d+)-(\d+)", name, re.IGNORECASE)
    if m:
        journal = {"ao": "AO", "josa": "JOSA", "josaa": "JOSAA", "oe": "OE", "ol": "OL",
                   "optica": "OPTICA"}[m.group(1).lower()]
        volume, page = m.group(2), int(m.group(4))
        out.append(f"10.1364/{journal}.{volume}.{page:06d}")
        out.append(f"10.1364/{journal.lower()}.{volume}.{page:06d}")
    arxiv = re.search(r"(?<!\d)(\d{4}\.\d{4,5})(?!\d)", name)
    if arxiv:
        out.append(f"10.48550/arXiv.{arxiv.group(1)}")
        out.append(f"10.48550/arxiv.{arxiv.group(1)}")
    unique: list[str] = []
    for doi in out:
        if doi not in unique:
            unique.append(doi)
    return unique[:limit]


def title_matches_pdf(title: str, text: str, *, threshold: float = 0.6) -> bool:
    """Whether ``title`` plausibly names the document ``text`` came from (content words >= 4 chars).

    A title with no long words cannot be checked and returns ``False``:
    unverifiable is not verified.
    """
    words = {w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(w) >= 4}
    if not words:
        return False
    haystack = (text or "").lower()
    return sum(1 for w in words if w in haystack) / len(words) >= threshold


def _fold(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def resolve_by_filename(candidates: list[str], filename: str) -> tuple[dict[str, Any] | None, str, list[str]]:
    """Offline verification: a candidate found in the text that also folds to the filename."""
    stem = _fold(Path(filename).stem)
    tried: list[str] = []
    if not stem:
        return None, "skipped:filename_mismatch", tried
    for doi in candidates:
        tried.append(doi)
        if _fold(doi) == stem:
            return {"doi": doi, "metadata_source": "pdf_only"}, "", tried
    return None, "skipped:filename_mismatch", tried


def _ask(fetch: Fetch, doi: str) -> Mapping[str, Any]:
    """One registry lookup; ``None`` becomes ``{}`` but an ``OpenAlexNoData`` keeps its reason."""
    payload = fetch("works", {"filter": f"doi:{doi}"})
    return {} if payload is None else payload


def _registry_answer(payload: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """``("", results)`` for an answer, else the closed-set status for the non-answer."""
    if is_quota_paused(payload):
        return "skipped:openalex_quota_paused", []
    if not payload:
        return "skipped:openalex_unreachable", []
    return "", list(payload.get("results") or [])


def resolve_asserted(doi: str, *, fetch: Fetch) -> tuple[dict[str, Any] | None, str, list[str]]:
    """Resolve a caller-asserted DOI without the title check (an SI can fail it legitimately).

    Still shape-checked offline and still refused when the registry has no such record.
    """
    normalised = doi_only(doi)
    if not normalised or not _DOI_IN_TEXT.fullmatch(normalised):
        return None, "skipped:no_doi", []
    status, results = _registry_answer(_ask(fetch, normalised))
    if status:
        return None, status, [normalised]
    if not results:
        return None, "skipped:doi_unresolved", [normalised]
    return results[0], "", [normalised]


def resolve_verified(candidates: list[str], pdf_text: str, *,
                     fetch: Fetch) -> tuple[dict[str, Any] | None, str, list[str]]:
    """The first candidate whose registry title appears in ``pdf_text``.

    A registry outage or quota pause wins over "unresolved"/"mismatch" so a
    retryable cause is never reported as a permanent one.
    """
    outage = ""
    saw_resolved = False
    tried: list[str] = []
    for doi in candidates:
        tried.append(doi)
        status, results = _registry_answer(_ask(fetch, doi))
        if status:
            outage = outage if outage == "skipped:openalex_quota_paused" else status
            if status == "skipped:openalex_quota_paused":
                break
            continue
        if not results:
            continue
        saw_resolved = True
        work = results[0]
        if title_matches_pdf(str(work.get("title") or ""), pdf_text):
            return work, "", tried
    if outage:
        return None, outage, tried
    return None, ("skipped:title_mismatch" if saw_resolved else "skipped:doi_unresolved"), tried


def is_preprint_doi(doi: str) -> bool:
    """Whether ``doi`` is an arXiv-registered (preprint) DOI."""
    key = doi_key(doi)
    return key.startswith("10.48550/arxiv.")


def version_pair_candidate(left: Mapping[str, Any], right: Mapping[str, Any], *,
                           threshold: float = 0.8) -> dict[str, Any] | None:
    """Propose that a preprint and a journal record are versions of one Work, for review.

    Returns ``None`` unless exactly one side is a preprint DOI and the titles
    agree. Never merges: identity changes need H3 review (B23 keeps its
    ``collapse_near_duplicates`` as the baseline operator path).
    """
    ldoi, rdoi = str(left.get("doi") or ""), str(right.get("doi") or "")
    if not doi_key(ldoi) or not doi_key(rdoi) or doi_key(ldoi) == doi_key(rdoi):
        return None
    if is_preprint_doi(ldoi) == is_preprint_doi(rdoi):
        return None
    similarity = title_similarity(str(left.get("title") or ""), str(right.get("title") or ""))
    if similarity < threshold:
        return None
    preprint, journal = (left, right) if is_preprint_doi(ldoi) else (right, left)
    return {"relation": "candidate_version_of", "preprint_doi": doi_key(preprint.get("doi")),
            "journal_doi": doi_key(journal.get("doi")), "title_similarity": round(similarity, 3),
            "requires_review": True}


def _detail(status: str, tried: list[str]) -> str:
    return {
        "skipped:openalex_unreachable": f"the registry did not answer for {tried!r}; retryable",
        "skipped:openalex_quota_paused": "the registry allowance is spent; retry after the breaker resumes",
        "skipped:doi_unresolved": f"the registry answered and has no record for {tried!r}",
        "skipped:filename_mismatch": f"none of {tried!r} folds to this file's name (offline check)",
        "skipped:title_mismatch": (f"resolved {tried!r} but none of their titles appear in this PDF; "
                                   "the signature of a cited work's or the journal's own DOI"),
        "skipped:no_doi": "no DOI in the first pages, the metadata or the filename",
    }.get(status, status)


def prepare_local_pdf(
    ctx: RequestContext,
    path: Path,
    *,
    store: FileDocumentStore,
    index: LegacyIndex | None = None,
    fetch: Fetch | None = None,
    text_extractor: TextExtractor = pdf_first_pages_text,
    apply: bool = False,
    force: bool = False,
    verify: str = "openalex",
    doi: str = "",
    max_bytes: int = 256 * 1024 * 1024,
    allowed: Callable[[Path], Path] | None = None,
    crossref: Mapping[str, Any] | None = None,
) -> IntakeReport:
    """Vet one local PDF and, with ``apply``, put its bytes in the candidate store.

    Args:
        ctx: Request context.
        path: The local file.
        store: Candidate store that receives the bytes.
        index: Legacy index receiving the verified DOI alias (optional).
        fetch: Registry lookup (``OpenAlexClient.fetch``); required unless
            ``verify="filename"`` and no ``doi`` is asserted.
        text_extractor: First-pages text extractor (pluggable; pypdfium2 default).
        apply: Store bytes and record the alias. ``False`` writes nothing.
        force: Proceed although the store already holds these bytes.
        verify: ``"openalex"`` (title check) or ``"filename"`` (offline).
        doi: A caller-asserted DOI (waives the title check; recorded as such).
        max_bytes: Size limit (giant uploads are refused before hashing).
        allowed: Containment check returning the resolved path or raising
            ``ValueError`` (see :class:`.fetch.LocalIntakePolicy`).
        crossref: An already-fetched Crossref work message whose licence array is recorded
            (no lookup happens here).

    Returns:
        An :class:`IntakeReport`; never raises for a bad input file.
    """
    report = IntakeReport(source=str(path), status="ready")
    report.identity.asserted_doi = doi_only(doi)
    if verify not in {"openalex", "filename"}:
        raise ValueError(f"verify must be 'openalex' or 'filename', not {verify!r}")
    try:
        path = allowed(Path(path)) if allowed is not None else Path(path)
    except ValueError as exc:
        report.status, report.detail = "skipped:not_allowed", str(exc)
        return report
    try:
        size = path.stat().st_size
    except OSError as exc:
        report.status, report.detail = "skipped:not_pdf", f"unreadable: {type(exc).__name__}"
        return report
    if size > max_bytes:
        report.status, report.detail = "skipped:too_large", f"{size} bytes exceeds the {max_bytes}-byte limit"
        return report
    if not looks_like_pdf(path):
        report.status, report.detail = "skipped:not_pdf", "not a PDF by magic bytes"
        return report
    if size <= MIN_PDF_BYTES:
        report.status, report.detail = "skipped:too_small", f"{size} bytes is at or below {MIN_PDF_BYTES}"
        return report
    report.sha256 = sha256_of(path)
    report.identity.source_ref = f"sha256:{report.sha256}" if report.sha256 else ""
    if report.identity.source_ref and store.exists(ctx, report.identity.source_ref):
        report.duplicate_of = index.names_for(report.identity.source_ref) if index else []
        report.duplicate_of = report.duplicate_of or [report.identity.source_ref]
        if not force:
            report.status = "skipped:duplicate"
            report.detail = "these bytes are already in the candidate store; use force to re-run intake"
            return report
    text = text_extractor(path, DOI_SEARCH_PAGES)
    report.licence = collect_licence(None, pdf_text=text)  # the PDF's own notice, even if identity later fails
    report.identity.pdf_doi_candidates = extract_doi_candidates(text)
    if len((text or "").strip()) < 50 and not doi:
        report.status, report.detail = "skipped:no_text", "no extractable text in the first pages (OCR first)"
        return report

    if doi:
        if fetch is None:
            raise ValueError("an asserted DOI still needs a registry fetch")
        work, status, tried = resolve_asserted(doi, fetch=fetch)
        report.identity_asserted = True
        verification = "asserted_registry_only"
        printed = {doi_key(c) for c in report.identity.pdf_doi_candidates}
        if printed and doi_key(doi) not in printed:
            report.identity.doi_mismatch = True
            report.warnings.append("asserted DOI differs from every DOI printed in the PDF")
    else:
        candidates = report.identity.pdf_doi_candidates or filename_doi_candidates(path.name)
        if not candidates:
            report.status, report.detail = "skipped:no_doi", _detail("skipped:no_doi", [])
            return report
        if verify == "filename":
            in_text = report.identity.pdf_doi_candidates + [
                c for c in filename_doi_candidates(path.name) if doi_key(c) in text.lower()]
            # Two independent sources: the DOI must be in the text AND fold to the filename.
            work, status, tried = resolve_by_filename(in_text, path.name)
            verification = "filename_match"
        else:
            if fetch is None:
                raise ValueError("verify='openalex' needs a registry fetch")
            work, status, tried = resolve_verified(candidates, text, fetch=fetch)
            verification = "title_match"
    report.candidates_tried = list(tried)
    if work is None:
        report.status = status
        report.doi = tried[0] if tried else ""
        report.detail = _detail(status, tried)
        return report

    report.work = dict(work)
    report.licence = collect_licence(work, crossref=crossref, pdf_text=text)
    report.title = str(work.get("title") or "")
    # The DOI spelling comes from the registry, not the PDF (case differs by publisher).
    report.doi = doi_only(str(work.get("doi") or "")) or tried[0]
    identity = report.identity
    identity.verified_doi = "" if report.identity_asserted else report.doi
    identity.verification = verification
    identity.canonical_work_id = str(work.get("id") or "").replace("https://openalex.org/", "")
    identity.source_version = "preprint" if is_preprint_doi(report.doi) else "journal"
    if index is not None:
        identity.prior_source_refs = [r for r in index.refs_for_doi(report.doi) if r != identity.source_ref]
        if identity.prior_source_refs:
            report.warnings.append("another source version of this DOI is already stored; both are kept")
    if not apply:
        report.detail = f"would store {Path(path).name} for {report.doi}"
        return report
    try:
        asset = store.put_file(ctx, path, media_type="application/pdf", max_bytes=max_bytes, metadata={
            "kind": AssetKind.SOURCE_PDF.value, "doi": report.doi, "intake": verification,
            "original_name": Path(path).name[:200],
            "licence_status": report.licence_status, "licence": report.licence.license,
            "oa_status": report.licence.oa_status})
    except (OSError, StoreError) as exc:
        report.status, report.detail = "failed:store", f"could not store the file: {type(exc).__name__}"
        return report
    if asset.ref != identity.source_ref:  # the file changed between hash and copy
        report.status, report.detail = "failed:store", "file changed while it was being stored"
        return report
    if index is not None:
        index.add_doi_alias(report.doi, asset.ref)
    report.detail = f"stored as a candidate source for {report.doi}"
    return report


__all__ = (
    "DOI_SEARCH_PAGES", "INGESTABLE", "IntakeReport", "MIN_PDF_BYTES", "RETRYABLE", "STATUSES", "WorkIdentity",
    "extract_doi_candidates", "filename_doi_candidates", "is_preprint_doi", "prepare_local_pdf",
    "pdf_first_pages_text", "resolve_asserted", "resolve_by_filename", "resolve_verified",
    "title_matches_pdf", "version_pair_candidate",
)
