"""Read a paper by DOI: one source lookup, one acquisition path, one versioned parse (L-09).

``read_paper`` returns section *names* and counts; ``read_paper_section`` returns
one bounded section. Both are internal agent reads of private source material,
so each requires a :class:`~app.modules.security.content_access.SourceAccessScope`
granted by trusted host wiring (D19): acquiring/parsing needs ``PDF_BYTES``,
section text needs ``SECTION``. Nothing returned contains a store path; the
``source_version`` is the content hash used in evidence locators, not an access
token (ADR content-disclosure Q2).

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/literature/papers.py
(``read_paper``, ``read_paper_section``) (BSD-3-Clause).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..security.content_access import SourceAccessDenied, SourceAccessScope, SourceKind
from .acquire import PaperAcquirer
from .legacy import LegacyIndex, doi_key, doi_only
from .parse_service import ParseService
from .store import FileDocumentStore

DEFAULT_SECTION_CHARS = 6000


class NoSourceAvailable(ContractError):
    """No stored or open-access PDF exists for the DOI (a fact about the paper, not a fault)."""

    def __init__(self, doi: str, detail: str = "") -> None:
        super().__init__(ErrorCode.NOT_FOUND, f"no open-access PDF available for {doi}", retryable=False)
        self.detail = detail


class SectionNotFound(ContractError):
    """No section name matches; ``available`` lists the real names for a retry."""

    def __init__(self, section: str, available: List[str]) -> None:
        super().__init__(ErrorCode.NOT_FOUND, f"no section matching {section!r}", retryable=False)
        self.available = available


@dataclass
class PaperOverview:
    """What ``lit_read_paper`` reports: structure, not text."""

    doi: str
    section_names: List[str]
    num_pages: int
    num_figures: int
    num_tables: int
    parser: str
    parser_version: str
    source_version: str
    source_status: str
    figure_pages: List[int] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe form."""
        return asdict(self)


@dataclass
class SectionText:
    """One bounded section of a paper with its locator fields."""

    doi: str
    section: str
    text: str
    truncated: bool
    page: Optional[int]
    source_version: str
    parser: str

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe form."""
        return asdict(self)


class PaperReader:
    """Read papers through the shared store, acquirer and parse service.

    Args:
        store: Candidate store.
        index: Legacy index (old filenames, DOI aliases).
        parse_service: Versioned parser.
        acquirer: Optional OA acquirer; without it only stored papers can be read.
    """

    def __init__(
        self,
        store: FileDocumentStore,
        index: LegacyIndex,
        parse_service: ParseService,
        acquirer: Optional[PaperAcquirer] = None,
    ) -> None:
        self.store = store
        self.index = index
        self.parse_service = parse_service
        self.acquirer = acquirer

    @staticmethod
    def _require(scope: Any, kind: SourceKind, doi: str) -> None:
        if not isinstance(scope, SourceAccessScope):
            raise SourceAccessDenied("reading a paper needs a trusted source-access scope")
        scope.require(kind, work_id=doi_key(doi))

    def _source(self, ctx: RequestContext, doi: str, work: Optional[Mapping[str, Any]]) -> tuple:
        if self.acquirer is not None:
            result = self.acquirer.acquire(ctx, doi, work=work)
            if result.ok:
                return result.ref, result.status
            raise NoSourceAvailable(doi_only(doi), result.status)
        ref = self.index.find_by_doi(doi)
        if ref and self.store.verify(ctx, ref):
            return ref, "cached"
        raise NoSourceAvailable(doi_only(doi), "not stored")

    def _document(self, ctx: RequestContext, doi: str, work: Optional[Mapping[str, Any]]) -> tuple:
        if not doi_key(doi):
            raise ContractError(ErrorCode.INVALID_INPUT, "not a DOI", retryable=False)
        ref, status = self._source(ctx, doi, work)
        record = self.parse_service.parse(ctx, ref)
        return self.parse_service.load(ctx, record), status

    def read_paper(
        self, ctx: RequestContext, doi: str, *, scope: Any, work: Optional[Mapping[str, Any]] = None
    ) -> PaperOverview:
        """Acquire (if allowed and needed), parse, and list the paper's sections."""
        self._require(scope, SourceKind.PDF_BYTES, doi)
        document, status = self._document(ctx, doi, work)
        return PaperOverview(
            doi=doi_only(doi),
            section_names=list(document.section_names()),
            num_pages=document.num_pages,
            num_figures=len(document.figures),
            num_tables=len(document.tables),
            parser=document.parser,
            parser_version=document.parser_version,
            source_version=document.file_sha256,
            source_status=status,
            figure_pages=sorted({f.page for f in document.figures}),
        )

    def read_paper_section(
        self, ctx: RequestContext, doi: str, section: str, *, scope: Any, max_chars: int = DEFAULT_SECTION_CHARS
    ) -> SectionText:
        """One section, matched case-insensitively (exact name first, then substring), bounded."""
        self._require(scope, SourceKind.PDF_BYTES, doi)
        self._require(scope, SourceKind.SECTION, doi)
        document, _ = self._document(ctx, doi, None)
        needle = section.strip().lower()
        if not needle:
            raise ContractError(ErrorCode.INVALID_INPUT, "section name is empty", retryable=False)
        match = next((s for s in document.sections if s.name.lower() == needle), None)
        match = match or next((s for s in document.sections if needle in s.name.lower()), None)
        if match is None:
            raise SectionNotFound(section, list(document.section_names()))
        limit = max(1, int(max_chars))
        return SectionText(
            doi=doi_only(doi),
            section=match.name,
            text=match.text[:limit],
            truncated=len(match.text) > limit,
            page=match.page,
            source_version=document.file_sha256,
            parser=document.parser_key,
        )


__all__ = (
    "DEFAULT_SECTION_CHARS",
    "NoSourceAvailable",
    "PaperOverview",
    "PaperReader",
    "SectionNotFound",
    "SectionText",
)
