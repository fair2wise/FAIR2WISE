"""The six verified ``lit_*`` tools as F2W ToolSpecs over F2W-owned services (L-01).

Same tool names and arguments as the donor, typed results instead of JSON
strings, and the distinctions the donor collapsed kept apart:

* ``EMPTY`` means the provider answered with nothing; ``UNAVAILABLE`` means it
  could not answer (with ``retryable`` and, for a spent quota, the resume time);
* search/citation/related results are external candidates
  (``record_kind="external_search_result"``), never ingested Works, and these
  tools do not download;
* the two read tools acquire only with ``ingest.acquire`` and read source text
  only through a trusted :class:`SourceAccessScope` supplied by host wiring.
  Their output is agent-private: user-facing traces must go through the
  disclosure projection (D19), which this module does not bypass.

Registration is explicit: :meth:`LiteratureToolset.registrations` returns
``(ToolSpec, fn)`` pairs for ``build_registry(extra=...)``; nothing registers at import.

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/literature/tools.py
(``lit_search_papers``, ``lit_get_papers_by_doi``, ``lit_find_citing_papers``,
``lit_find_related_papers``, ``lit_read_paper``, ``lit_read_paper_section``,
``build_lit_tools``) (BSD-3-Clause).
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any, Callable, Sequence
import uuid

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..contracts.tools import CostClass, Effect, ToolResult, ToolSpec, ToolStatus
from ..security.content_access import ReadPurpose, SourceAccessAuthority, SourceAccessScope, SourceKind
from .openalex import EXTERNAL_RESULT, OpenAlexClient, OpenAlexQuotaPaused, ProviderUnavailable
from .parse_service import ParseFailed
from .reading import PaperReader, SectionNotFound
from .skill import LIT_TOOL_NAMES, LITERATURE_RESEARCH_SKILL

logger = logging.getLogger(__name__)

TOOL_VERSION = "1.0.0"
MAX_PAPERS = 25
_READ = frozenset({"chat.ask"})
_PAPERS_OUT = {
    "type": "object",
    "properties": {"count": {"type": "integer"}, "papers": {"type": "array"}, "record_kind": {"type": "string"}},
    "required": ["count", "papers"],
}
_DOI = {"type": "string", "minLength": 4, "maxLength": 300}
_MAX = {"type": "integer", "minimum": 1, "maximum": MAX_PAPERS}


def _spec(
    name: str,
    description: str,
    when: str,
    when_not: str,
    props: dict[str, Any],
    required: Sequence[str],
    *,
    effects: frozenset,
    cost: CostClass,
    output: dict[str, Any],
    evidence: str = "none",
    idempotent: bool = True,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        version=TOOL_VERSION,
        domain="literature",
        description=description,
        when_to_use=when,
        when_not_to_use=when_not,
        input_schema={
            "type": "object",
            "properties": props,
            "required": list(required),
            "additionalProperties": False,
        },
        output_schema=output,
        effects=effects,
        cost_class=cost,
        required_capabilities=_READ,
        evidence_contract=evidence,
        idempotent=idempotent,
    )


LIT_SEARCH_PAPERS = _spec(
    "lit_search_papers",
    "Search the published literature (OpenAlex) by topic, technique and material.",
    "Find what has been published on a material or measurement; results are external candidates with DOIs.",
    "Answering from F2W's knowledge graphs; results are not ingested papers and are not evidence.",
    {
        "query": {"type": "string", "minLength": 1, "maxLength": 500},
        "technique": {"type": "string", "maxLength": 100},
        "material": {"type": "string", "maxLength": 100},
        "year_from": {"type": "integer", "minimum": 0, "maximum": 3000},
        "year_to": {"type": "integer", "minimum": 0, "maximum": 3000},
        "max_results": _MAX,
    },
    ["query"],
    effects=frozenset({Effect.NETWORK}),
    cost=CostClass.CHEAP,
    output=_PAPERS_OUT,
)
LIT_GET_PAPERS_BY_DOI = _spec(
    "lit_get_papers_by_doi",
    "Look up specific papers by DOI (up to 10, comma-separated).",
    "A DOI is already known from a citation, a user or a graph entity.",
    "Searching by topic; use lit_search_papers.",
    {"dois": {"type": "string", "minLength": 4, "maxLength": 3000}},
    ["dois"],
    effects=frozenset({Effect.NETWORK}),
    cost=CostClass.FREE,
    output=_PAPERS_OUT,
)
LIT_FIND_CITING_PAPERS = _spec(
    "lit_find_citing_papers",
    "Find later papers that cite a given OpenAlex work, most-cited first.",
    "Following the citation graph forward from a work returned by lit_search_papers.",
    "Finding related work that never cited it; use lit_find_related_papers.",
    {"openalex_id": {"type": "string", "pattern": r"^(https://openalex\.org/)?W\d{3,}$"}, "max_results": _MAX},
    ["openalex_id"],
    effects=frozenset({Effect.NETWORK}),
    cost=CostClass.CHEAP,
    output=_PAPERS_OUT,
)
LIT_FIND_RELATED_PAPERS = _spec(
    "lit_find_related_papers",
    "Find semantically related papers (Semantic Scholar recommendations).",
    "Surfacing work on the same problem whether or not it cites the seed paper.",
    "Exhaustive citation analysis.",
    {
        "paper_id": {"type": "string", "minLength": 4, "maxLength": 300},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    ["paper_id"],
    effects=frozenset({Effect.NETWORK}),
    cost=CostClass.CHEAP,
    output=_PAPERS_OUT,
)
LIT_READ_PAPER = _spec(
    "lit_read_paper",
    "List an open-access paper's sections, figures and tables (structure, not text).",
    "Before reading a section of a paper by DOI.",
    "Bulk reading or exporting a paper.",
    {"doi": _DOI},
    ["doi"],
    effects=frozenset({Effect.READ_PRIVATE_SOURCE, Effect.NETWORK, Effect.WRITE_STAGED}),
    cost=CostClass.MODERATE,
    output={
        "type": "object",
        "properties": {"doi": {"type": "string"}, "section_names": {"type": "array"}},
        "required": ["doi", "section_names"],
    },
)
LIT_READ_PAPER_SECTION = _spec(
    "lit_read_paper_section",
    "Read one bounded section of a paper (matched case-insensitively by name).",
    "After lit_read_paper, to read e.g. 'results' or 'experimental'.",
    "Reproducing a paper's full text; a section is bounded and agent-private.",
    {"doi": _DOI, "section": {"type": "string", "minLength": 1, "maxLength": 200}},
    ["doi", "section"],
    effects=frozenset({Effect.READ_PRIVATE_SOURCE, Effect.NETWORK, Effect.WRITE_STAGED}),
    cost=CostClass.MODERATE,
    output={
        "type": "object",
        "properties": {
            "doi": {"type": "string"},
            "section": {"type": "string"},
            "text": {"type": "string"},
            "truncated": {"type": "boolean"},
        },
        "required": ["doi", "section", "text", "truncated"],
    },
    evidence="source section with page and source_version locator",
)

LIT_TOOL_SPECS: tuple[ToolSpec, ...] = (
    LIT_SEARCH_PAPERS,
    LIT_GET_PAPERS_BY_DOI,
    LIT_FIND_CITING_PAPERS,
    LIT_FIND_RELATED_PAPERS,
    LIT_READ_PAPER,
    LIT_READ_PAPER_SECTION,
)

assert tuple(spec.name for spec in LIT_TOOL_SPECS) == LIT_TOOL_NAMES

#: ``(ctx) -> SourceAccessScope`` built by trusted host wiring.
ScopeProvider = Callable[[RequestContext], SourceAccessScope]


def authority_scope_provider(
    authority: SourceAccessAuthority, *, ttl: timedelta = timedelta(minutes=2)
) -> ScopeProvider:
    """A scope provider issuing a short-lived paper-evidence grant per call from ``authority``."""

    def provide(ctx: RequestContext) -> SourceAccessScope:
        grant = authority.issue(
            ctx, purpose=ReadPurpose.PAPER_EVIDENCE, kinds=(SourceKind.PDF_BYTES, SourceKind.SECTION), ttl=ttl
        )
        return authority.scope(ctx, grant)

    return provide


def _failure(tool: str, exc: Exception) -> ToolResult:
    provenance = f"lit:{uuid.uuid4().hex}"
    if isinstance(exc, OpenAlexQuotaPaused):
        return ToolResult(
            tool=tool,
            status=ToolStatus.UNAVAILABLE,
            error_code=ErrorCode.UNAVAILABLE,
            retryable=True,
            provenance_id=provenance,
            warnings=(f"openalex quota paused; do not retry before {exc.resume_at_iso or 'resume'}",),
        )
    if isinstance(exc, ProviderUnavailable):
        return ToolResult(
            tool=tool,
            status=ToolStatus.UNAVAILABLE,
            error_code=ErrorCode.UNAVAILABLE,
            retryable=True,
            provenance_id=provenance,
            warnings=(f"{exc.provider} unavailable ({exc.reason})",),
        )
    if isinstance(exc, SectionNotFound):
        return ToolResult(
            tool=tool,
            status=ToolStatus.ERROR,
            error_code=ErrorCode.NOT_FOUND,
            provenance_id=provenance,
            data={"available_sections": exc.available[:100]},
            warnings=(str(exc),),
        )
    if isinstance(exc, ContractError):
        code = exc.code
        status = {
            ErrorCode.DENIED: ToolStatus.DENIED,
            ErrorCode.UNAUTHENTICATED: ToolStatus.DENIED,
            ErrorCode.INVALID_INPUT: ToolStatus.INVALID,
            ErrorCode.UNAVAILABLE: ToolStatus.UNAVAILABLE,
        }.get(code, ToolStatus.ERROR)
        message = "source access denied" if code is ErrorCode.DENIED else str(exc)
        return ToolResult(
            tool=tool,
            status=status,
            error_code=code,
            retryable=exc.retryable,
            provenance_id=provenance,
            warnings=(message,),
        )
    logger.warning("lit tool %s failed: %s", tool, type(exc).__name__)
    return ToolResult(
        tool=tool,
        status=ToolStatus.ERROR,
        error_code=ErrorCode.INTERNAL,
        provenance_id=provenance,
        warnings=(f"{tool} failed ({type(exc).__name__})",),
    )


def _papers(tool: str, papers: list[dict[str, Any]], **extra: Any) -> ToolResult:
    if not papers:
        return ToolResult(tool=tool, status=ToolStatus.EMPTY, warnings=tuple(extra.pop("warnings", ())))
    warnings = tuple(extra.pop("warnings", ()))
    return ToolResult(
        tool=tool,
        status=ToolStatus.PARTIAL if warnings else ToolStatus.OK,
        warnings=warnings,
        data={"count": len(papers), "papers": papers, "record_kind": EXTERNAL_RESULT, **extra},
    )


class LiteratureToolset:
    """The six ``lit_*`` tools bound to one OpenAlex client and one paper reader.

    Args:
        client: Discovery client.
        reader: Paper reader (store + acquirer + parse service); ``None`` disables
            the two read tools' work (they report ``UNAVAILABLE``).
        scope_provider: Trusted source-access scope per request (see
            :func:`authority_scope_provider`); ``None`` makes read tools ``DENIED``.
    """

    specs = LIT_TOOL_SPECS
    skill = LITERATURE_RESEARCH_SKILL

    def __init__(
        self,
        client: OpenAlexClient,
        reader: PaperReader | None = None,
        *,
        scope_provider: ScopeProvider | None = None,
    ) -> None:
        self.client = client
        self.reader = reader
        self.scope_provider = scope_provider

    def registrations(self) -> tuple[tuple[ToolSpec, Callable[[Any], ToolResult]], ...]:
        """``(spec, fn)`` pairs for ``build_registry(extra=...)``."""
        fns = (
            self.search_papers,
            self.get_papers_by_doi,
            self.find_citing_papers,
            self.find_related_papers,
            self.read_paper,
            self.read_paper_section,
        )
        return tuple(zip(self.specs, fns))

    def search_papers(self, inv: Any) -> ToolResult:
        """``lit_search_papers``."""
        a = inv.args
        try:
            found = self.client.search_papers(
                a["query"],
                technique=a.get("technique", ""),
                material=a.get("material", ""),
                year_from=a.get("year_from", 0),
                year_to=a.get("year_to", 0),
                max_results=min(int(a.get("max_results", 10)), MAX_PAPERS),
            )
        except Exception as exc:  # noqa: BLE001 - typed result
            return _failure(LIT_SEARCH_PAPERS.name, exc)
        return _papers(LIT_SEARCH_PAPERS.name, found)

    def get_papers_by_doi(self, inv: Any) -> ToolResult:
        """``lit_get_papers_by_doi``: unknown DOIs are reported, unanswered ones flagged."""
        wanted = [d.strip() for d in inv.args["dois"].split(",") if d.strip()]
        try:
            lookup = self.client.lookup_dois(wanted)
        except Exception as exc:  # noqa: BLE001
            return _failure(LIT_GET_PAPERS_BY_DOI.name, exc)
        if lookup.unavailable and not lookup.papers and not lookup.not_found:
            return _failure(LIT_GET_PAPERS_BY_DOI.name, ProviderUnavailable("openalex", "unreachable"))
        warnings = [f"openalex did not answer for {len(lookup.unavailable)} DOI(s)"] if lookup.unavailable else []
        return _papers(
            LIT_GET_PAPERS_BY_DOI.name,
            lookup.papers,
            requested=len(wanted),
            not_found=lookup.not_found,
            unavailable=lookup.unavailable,
            warnings=warnings,
        )

    def find_citing_papers(self, inv: Any) -> ToolResult:
        """``lit_find_citing_papers``."""
        try:
            found = self.client.find_citing_papers(
                inv.args["openalex_id"], max_results=min(int(inv.args.get("max_results", 10)), MAX_PAPERS)
            )
        except Exception as exc:  # noqa: BLE001
            return _failure(LIT_FIND_CITING_PAPERS.name, exc)
        return _papers(LIT_FIND_CITING_PAPERS.name, found)

    def find_related_papers(self, inv: Any) -> ToolResult:
        """``lit_find_related_papers``."""
        try:
            found = self.client.find_related_papers(
                inv.args["paper_id"], max_results=min(int(inv.args.get("max_results", 10)), 20)
            )
        except Exception as exc:  # noqa: BLE001
            return _failure(LIT_FIND_RELATED_PAPERS.name, exc)
        return _papers(LIT_FIND_RELATED_PAPERS.name, found)

    def _scope(self, ctx: RequestContext) -> SourceAccessScope:
        if self.scope_provider is None:
            raise ContractError(ErrorCode.DENIED, "no trusted source-access scope is configured", retryable=False)
        return self.scope_provider(ctx)

    def _unavailable_reader(self, tool: str) -> ToolResult:
        return ToolResult(
            tool=tool,
            status=ToolStatus.UNAVAILABLE,
            error_code=ErrorCode.UNAVAILABLE,
            warnings=("paper reading is not configured",),
        )

    def read_paper(self, inv: Any) -> ToolResult:
        """``lit_read_paper``: section names and counts, never text."""
        if self.reader is None:
            return self._unavailable_reader(LIT_READ_PAPER.name)
        try:
            overview = self.reader.read_paper(inv.ctx, inv.args["doi"], scope=self._scope(inv.ctx))
        except (ContractError, ParseFailed) as exc:
            return _failure(LIT_READ_PAPER.name, exc)
        except Exception as exc:  # noqa: BLE001
            return _failure(LIT_READ_PAPER.name, exc)
        data = overview.as_dict()
        data["hint"] = "Call lit_read_paper_section for the text of one section."
        return ToolResult(
            tool=LIT_READ_PAPER.name, status=ToolStatus.OK, data=data, provenance_id=f"lit:{uuid.uuid4().hex}"
        )

    def read_paper_section(self, inv: Any) -> ToolResult:
        """``lit_read_paper_section``: one bounded section with page and source version."""
        if self.reader is None:
            return self._unavailable_reader(LIT_READ_PAPER_SECTION.name)
        try:
            section = self.reader.read_paper_section(
                inv.ctx, inv.args["doi"], inv.args["section"], scope=self._scope(inv.ctx)
            )
        except Exception as exc:  # noqa: BLE001
            return _failure(LIT_READ_PAPER_SECTION.name, exc)
        return ToolResult(
            tool=LIT_READ_PAPER_SECTION.name,
            status=ToolStatus.OK,
            data=section.as_dict(),
            provenance_id=f"lit:{uuid.uuid4().hex}",
        )


__all__ = (
    "LITERATURE_RESEARCH_SKILL",
    "LIT_FIND_CITING_PAPERS",
    "LIT_FIND_RELATED_PAPERS",
    "LIT_GET_PAPERS_BY_DOI",
    "LIT_READ_PAPER",
    "LIT_READ_PAPER_SECTION",
    "LIT_SEARCH_PAPERS",
    "LIT_TOOL_SPECS",
    "LiteratureToolset",
    "MAX_PAPERS",
    "ScopeProvider",
    "TOOL_VERSION",
    "authority_scope_provider",
)
