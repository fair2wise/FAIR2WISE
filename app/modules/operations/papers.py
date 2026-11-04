"""Paper summaries as a Knowledge Operation (task T19B, inventory item OP01).

``lit_summarize_extracted_terms`` wraps the existing deterministic
``paper_evidence_agent.summarize_extracted_terms`` (QA-44, lines 122-292)
**unchanged**: the same arguments reach it, it applies its own page
eligibility and citation format, and the text it returns is passed through
verbatim. Nothing here re-ranks, re-filters, re-words or re-computes eligible
pages. In particular the D-10 discrepancy is preserved: the wrapped function
does not call ``_eligible_pages``; for ``extraction_state=full`` without a
page list it bounds eligibility by the pages the terms themselves cite.

No model call. The wrapped function reads two local JSON files and applies
regular expressions; it constructs no chat client and does no network I/O
(``tests/operations`` runs it with every client constructor and socket
blocked). The Tool therefore declares ``read_corpus`` only and cost ``free``.

What is counted. The counting unit is the *raw extracted term record* for ONE
PDF filename (not canonical entities, not Works; duplicate labels count
separately). The population is every record in the session terms file whose
``source_papers`` contains that filename and that has at least one page on the
paper's manifest-eligible pages. The scan over that population is always
complete; only the *display* is capped (``max_terms``, default 16, at most
16 through the Tool; at most ``min(max_terms, 3)`` when a query matches no
record). A summary whose list is shorter than ``term_count`` is reported as
``completeness="partial"`` with the reason, never as exhaustive. In
query-scoped mode the legacy "N additional terms omitted" line counts only
omitted *matching* records; ``omitted_term_count`` carries the full number.

Paper selection. The Tool's input schema has no path, filename or paper id:
the paper is resolved server-side, either from a scope pinned by trusted
server code for one call (:func:`pinned_paper_scope`, used by the legacy UI
adapter with the values ``api.py`` already passes) or from an injected
resolver over the caller's session state (``summary_compat.session_paper_resolver``:
the session's ``active_paper.filename`` and the coordinator's session terms
and manifest files, the same sources ``api.py`` uses).

Bounds. ``ToolResult.data`` serialises to at most :data:`MAX_RESULT_CHARS`
characters (below the executor's default 8000-character inline bound): whole
summary lines are dropped from the end, and ``summary_truncated`` set, if the
definitions are long. At most 16 term lines, 16 citations and 16 evidence
references are returned. The untruncated legacy dictionary is kept only in a
per-instance bounded ledger for the legacy adapter.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import threading
from typing import Any, Final, Literal
import uuid

from pydantic import BaseModel, ConfigDict

from ..agent_runtime.executor import is_control_flow
from ..agent_runtime.skills import Skill
from ..contracts import (
    ContractError,
    CostClass,
    Effect,
    ErrorCode,
    EvidenceRef,
    MatchType,
    RequestContext,
    SourceLocator,
    ToolResult,
    ToolSpec,
    ToolStatus,
)
from ..runtime_bridge.qa_tool import CITATION_TOKEN_RE

logger = logging.getLogger(__name__)

TOOL_NAME: Final = "lit_summarize_extracted_terms"
OPERATION_VERSION = "1.0.0"

#: Largest ``max_terms`` the Tool accepts: the legacy default (api.py report flow).
MAX_TERMS_CAP = 16
#: Bound on ``len(json.dumps(ToolResult.data))``; below ``RuntimeLimits.max_output_chars`` (8000).
MAX_RESULT_CHARS = 7000
COUNTING_UNIT = "raw_extracted_term_record"
POPULATION = (
    "Extracted-term records in the session terms file whose source_papers contains this PDF filename and "
    "that cite at least one manifest-eligible page of it (eligibility exactly as summarize_extracted_terms "
    "implements it). A compact term summary, not an abstract of the paper."
)

_OMITTED_RE = re.compile(r"^- (\d+) additional terms omitted from this compact summary\.$")
_PDF_TOKEN_RE = re.compile(r"\[PDF:\s*([^]]+?)\s+p\.(\d+)\]")

_STR_ITEMS = {"type": "string", "maxLength": 1000}

LIT_SUMMARIZE_EXTRACTED_TERMS = ToolSpec(
    name=TOOL_NAME,
    version=OPERATION_VERSION,
    domain="operations",
    description=(
        "Deterministic, page-cited compact summary of the terms extracted from the session's active paper "
        "(no model call). Returns the summary text with its [PDF: file p.N] citations, the full count of "
        "page-grounded term records, how many are listed, and whether the list is complete or partial."
    ),
    when_to_use=(
        "The user asks what terms or concepts were extracted from the paper that was just downloaded and "
        "extracted in this session, or a recap of that extraction. Pass the user's question as query only "
        "when the summary should rank terms by relevance to it."
    ),
    when_not_to_use=(
        "Questions answered from the knowledge graphs, questions about a paper's content beyond its "
        "extracted terms, other papers, or corpus-wide counts. It cannot select a paper or file: it always "
        "summarizes the session's active extracted paper. It never changes data."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "max_terms": {"type": "integer", "minimum": 1, "maximum": MAX_TERMS_CAP},
            "query": {"type": "string", "maxLength": 16000},
            "missing_topics": {"type": "array", "items": _STR_ITEMS, "maxItems": 100},
            "relevant_node_names": {"type": "array", "items": _STR_ITEMS, "maxItems": 2000},
        },
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "operation": {"const": TOOL_NAME},
            "operation_version": {"type": "string"},
            "outcome": {"const": "summarized"},
            "legacy_status": {"type": "string"},
            "source_paper": {"type": "string"},
            "paper_id": {"type": ["string", "null"]},
            "extraction_state": {"type": ["string", "null"]},
            "counting_unit": {"const": COUNTING_UNIT},
            "population": {"type": "string"},
            "term_count": {"type": "integer", "minimum": 0},
            "relevant_term_count": {"type": "integer", "minimum": 0},
            "displayed_term_count": {"type": "integer", "minimum": 0, "maximum": MAX_TERMS_CAP},
            "omitted_term_count": {"type": "integer", "minimum": 0},
            "legacy_omitted_note_count": {"type": "integer", "minimum": 0},
            "relevance_mode": {"enum": ["all_terms", "matched", "query_targeted_pages"]},
            "max_terms": {"type": "integer"},
            "scan_complete": {"type": "boolean"},
            "completeness": {"enum": ["complete", "partial"]},
            "completeness_reason": {"type": ["string", "null"]},
            "summary_text": {"type": "string"},
            "summary_truncated": {"type": "boolean"},
            "summary_chars": {"type": "integer", "minimum": 0},
            "citations": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_TERMS_CAP},
            "used_pages": {"type": "array", "items": {"type": "integer"}, "maxItems": MAX_TERMS_CAP},
        },
        "required": ["operation", "outcome", "source_paper", "counting_unit", "term_count",
                     "displayed_term_count", "omitted_term_count", "completeness", "summary_text", "citations"],
        "additionalProperties": False,
    },
    effects=frozenset({Effect.READ_CORPUS}),
    cost_class=CostClass.FREE,
    required_capabilities=frozenset({"chat.ask"}),
    evidence_contract=(
        "OK results carry one EvidenceRef per [PDF: <file> p.<n>] token in summary_text, in order: "
        "evidence_id='pdf:<file>:p<n>', work_id='pdf-file:<file>' (the PDF filename key, not a canonical "
        "Work), source_version='f2w-extracted-terms:<file>', locator.page=<n>, match_type=none, citation=the "
        "token verbatim. Insufficient (EMPTY) and failed results carry none."
    ),
    idempotent=True,
)

SUMMARIZE_LITERATURE_SKILL = Skill(
    name="summarize_literature",
    description=(
        "Summarize what was extracted from the session's active paper as a deterministic, page-cited term "
        "list with explicit counts and completeness."
    ),
    when_to_use=(
        "The user asks for the terms or a recap of the paper extracted in this session. Relay the summary "
        "text and its citation tokens unchanged; when completeness is partial, say the list is a preview of "
        "term_count records, never that it is all of them."
    ),
    tools=(TOOL_NAME,),
    required_capabilities=frozenset({"chat.ask"}),
)


# -- paper scope ---------------------------------------------------------------------

@dataclass(frozen=True)
class PaperScope:
    """The one paper a summary is computed over, resolved by server code only.

    Attributes:
        terms_path: Session extracted-terms JSON (``Coordinator.session_terms``).
        manifest_path: Extraction manifest JSON (``Coordinator.extraction_manifest``).
        filename: PDF filename key in the manifest and in ``source_papers``.
        paper_id: The session's ``active_paper.paper_id``, when known.
    """

    terms_path: str
    manifest_path: str
    filename: str
    paper_id: str | None = None


#: Resolves the paper for one request; ``None`` means the session has no active extracted paper.
PaperScopeResolver = Callable[[RequestContext], PaperScope | None]

_PINNED: ContextVar[PaperScope | None] = ContextVar("f2w_pinned_paper_scope", default=None)


@contextmanager
def pinned_paper_scope(scope: PaperScope) -> Iterator[PaperScope]:
    """Pin ``scope`` for tool calls made in this context (trusted server code only).

    The legacy adapter uses it to hand the Tool the exact files and filename
    ``api.py`` passes today, without exposing them as model arguments.
    ``asyncio.to_thread`` copies the context, so the executor's worker thread sees it.
    """
    if not isinstance(scope, PaperScope):
        raise TypeError("scope must be a PaperScope")
    token = _PINNED.set(scope)
    try:
        yield scope
    finally:
        _PINNED.reset(token)


# -- typed result --------------------------------------------------------------------

class PaperTermSummary(BaseModel):
    """Typed, bounded result of one summary over one paper.

    ``summary_text`` is the wrapped function's ``answer`` verbatim unless
    ``summary_truncated``. Counts come from the wrapped function's own fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Literal["lit_summarize_extracted_terms"] = TOOL_NAME
    operation_version: str = OPERATION_VERSION
    outcome: Literal["summarized", "insufficient", "unreadable"]
    legacy_status: str
    source_paper: str
    paper_id: str | None = None
    extraction_state: str | None = None
    counting_unit: str = COUNTING_UNIT
    population: str = POPULATION
    term_count: int = 0
    relevant_term_count: int = 0
    displayed_term_count: int = 0
    omitted_term_count: int = 0
    legacy_omitted_note_count: int = 0
    relevance_mode: str | None = None
    max_terms: int = MAX_TERMS_CAP
    scan_complete: bool = False
    completeness: Literal["complete", "partial"] = "partial"
    completeness_reason: str | None = None
    summary_text: str = ""
    summary_truncated: bool = False
    summary_chars: int = 0
    citations: tuple[str, ...] = ()
    used_pages: tuple[int, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_data(self) -> dict[str, Any]:
        """``ToolResult.data`` payload (warnings travel on the ToolResult instead)."""
        data = self.model_dump(mode="json", exclude={"warnings"})
        data["citations"] = list(self.citations)
        data["used_pages"] = list(self.used_pages)
        return data


@dataclass(frozen=True)
class SummaryOutcome:
    """A typed summary plus the unchanged dictionary ``summarize_extracted_terms`` returned."""

    summary: PaperTermSummary
    legacy: dict[str, Any]


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _legacy_omitted_note(answer: str) -> int:
    lines = answer.splitlines()
    match = _OMITTED_RE.match(lines[-1]) if lines else None
    return int(match.group(1)) if match else 0


def _completeness(mode: str, total: int, relevant: int, shown: int, max_terms: int) -> str | None:
    if shown >= total:
        return None
    if mode == "matched":
        capped = f", capped at max_terms={max_terms}" if relevant > shown else ""
        return (f"query-scoped: only records matching the query are listed ({shown} listed; {relevant} of "
                f"{total} matched{capped})")
    if mode == "query_targeted_pages":
        return (f"query-scoped with no matching record: at most min(max_terms, 3)={min(max_terms, 3)} records "
                f"are listed ({shown} of {total})")
    return f"display capped at max_terms={max_terms}: {shown} of {total} records listed"


def _extraction_state(scope: PaperScope) -> str | None:
    from ..f2w_agent.paper_evidence_agent import _manifest_entry

    try:
        value = _manifest_entry(Path(scope.manifest_path), scope.filename).get("extraction_state")
    except (OSError, ValueError) as exc:
        logger.debug("extraction manifest unreadable: %s", type(exc).__name__)
        return None
    return str(value) if value is not None else None


def _fit(summary: PaperTermSummary, limit: int) -> PaperTermSummary:
    """Drop whole trailing summary lines until ``to_data`` serialises within ``limit``."""
    def size(item: PaperTermSummary) -> int:
        return len(json.dumps(item.to_data(), sort_keys=True, default=str))

    if size(summary) <= limit:
        return summary
    lines = summary.summary_text.splitlines()
    kept = len(lines)
    candidate = summary
    while kept > 1:
        kept -= 1
        text = "\n".join(lines[:kept])
        candidate = summary.model_copy(update={"summary_text": text, "summary_truncated": True,
                                               "citations": tuple(citation_tokens(text))})
        if size(candidate) <= limit:
            break
    if size(candidate) > limit:
        candidate = summary.model_copy(update={"summary_text": "", "summary_truncated": True, "citations": ()})
    note = (f"summary_text truncated to {kept if candidate.summary_text else 0} of {len(lines)} lines to stay "
            f"within the {limit}-character result bound; counts are unaffected")
    return candidate.model_copy(update={"warnings": (*candidate.warnings, note)})


def citation_tokens(text: str) -> list[str]:
    """Citation tokens in ``text`` verbatim, first appearance order (the frontend's regex)."""
    out: list[str] = []
    for match in CITATION_TOKEN_RE.finditer(text or ""):
        if match.group(0) not in out:
            out.append(match.group(0))
    return out


def summarize_paper_terms(
    scope: PaperScope,
    *,
    max_terms: int = MAX_TERMS_CAP,
    query: str = "",
    missing_topics: Sequence[str] | None = None,
    relevant_node_names: Sequence[str] | None = None,
    result_chars: int = MAX_RESULT_CHARS,
) -> SummaryOutcome:
    """Run the existing ``summarize_extracted_terms`` over ``scope`` and type its result.

    Arguments are passed through unchanged (the defaults equal the wrapped
    function's). Exceptions the wrapped function raises propagate unchanged.
    """
    from ..f2w_agent.paper_evidence_agent import summarize_extracted_terms

    legacy = summarize_extracted_terms(
        scope.terms_path, scope.manifest_path, scope.filename, max_terms=max_terms, query=query,
        missing_topics=None if missing_topics is None else list(missing_topics),
        relevant_node_names=None if relevant_node_names is None else list(relevant_node_names),
    )
    status = str(legacy.get("status") or "")
    answer = str(legacy.get("answer") or "")
    base: dict[str, Any] = {"legacy_status": status, "source_paper": scope.filename, "paper_id": scope.paper_id,
                            "max_terms": max(1, int(max_terms))}
    if status != "success":
        outcome: Literal["summarized", "insufficient", "unreadable"] = (
            "unreadable" if status == "manifest_error" else "insufficient")
        return SummaryOutcome(PaperTermSummary(outcome=outcome, scan_complete=outcome == "insufficient",
                                               summary_text=answer, summary_chars=len(answer), **base), legacy)
    total = _int(legacy.get("term_count"))
    relevant = _int(legacy.get("relevant_term_count"))
    shown = _int(legacy.get("displayed_term_count"))
    mode = str(legacy.get("relevance_mode") or "")
    reason = _completeness(mode, total, relevant, shown, base["max_terms"])
    note = _legacy_omitted_note(answer)
    warnings: list[str] = []
    if reason:
        warnings.append(f"partial display: {shown} of {total} page-grounded term records are listed; "
                        f"term_count is the full count for this paper")
    if note != total - shown:
        warnings.append(f"the summary's 'additional terms omitted' line says {note}; {total - shown} records "
                        f"in total are not listed (the line counts only query-matched records)")
    summary = PaperTermSummary(
        outcome="summarized", extraction_state=_extraction_state(scope), term_count=total,
        relevant_term_count=relevant, displayed_term_count=shown, omitted_term_count=max(0, total - shown),
        legacy_omitted_note_count=note, relevance_mode=mode or None, scan_complete=True,
        completeness="partial" if reason else "complete", completeness_reason=reason, summary_text=answer,
        summary_chars=len(answer), citations=tuple(citation_tokens(answer)),
        used_pages=tuple(_int(p) for p in legacy.get("used_pages") or []), warnings=tuple(warnings), **base,
    )
    return SummaryOutcome(_fit(summary, result_chars), legacy)


# -- Tool ----------------------------------------------------------------------------

_MISSING = object()


@dataclass(frozen=True)
class LedgerEntry:
    """What one invocation produced on the legacy side (dict returned, or exception raised)."""

    legacy: Any = _MISSING
    error: BaseException | None = None

    @property
    def ran(self) -> bool:
        """Whether ``summarize_extracted_terms`` was actually called."""
        return self.legacy is not _MISSING or self.error is not None


class SummaryLedger:
    """Bounded, thread-safe ``provenance_id -> LedgerEntry`` map for one Tool instance."""

    def __init__(self, max_entries: int = 64) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max = max_entries
        self._entries: OrderedDict[str, LedgerEntry] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, provenance_id: str, entry: LedgerEntry) -> None:
        """Record ``entry``, evicting the oldest beyond the bound."""
        with self._lock:
            self._entries[provenance_id] = entry
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def pop(self, provenance_id: str | None) -> LedgerEntry | None:
        """Remove and return the entry for ``provenance_id``."""
        if not provenance_id:
            return None
        with self._lock:
            return self._entries.pop(provenance_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _evidence(summary: PaperTermSummary) -> tuple[EvidenceRef, ...]:
    refs = []
    for token in summary.citations:
        match = _PDF_TOKEN_RE.fullmatch(token)
        if not match:
            continue
        name, page = match.group(1).strip(), int(match.group(2))
        refs.append(EvidenceRef(
            evidence_id=f"pdf:{name}:p{page}", work_id=f"pdf-file:{name}",
            source_version=f"f2w-extracted-terms:{name}", locator=SourceLocator(page=page if page >= 1 else None),
            match_type=MatchType.NONE, citation=token,
        ))
    return tuple(refs)


def summary_to_result(summary: PaperTermSummary, *, provenance_id: str | None = None) -> ToolResult:
    """Map a typed summary to a ``ToolResult``.

    ``summarized`` -> OK with data; ``insufficient`` (no page-grounded record) ->
    EMPTY; ``unreadable`` (terms/manifest missing, corrupt, or no manifest entry)
    -> ERROR/``not_found``. The unreadable message names server files, so it is
    not forwarded.
    """
    if summary.outcome == "summarized":
        return ToolResult(tool=TOOL_NAME, status=ToolStatus.OK, data=summary.to_data(),
                          evidence=_evidence(summary), warnings=summary.warnings, provenance_id=provenance_id)
    if summary.outcome == "insufficient":
        return ToolResult(tool=TOOL_NAME, status=ToolStatus.EMPTY, provenance_id=provenance_id,
                          warnings=("insufficient: no page-grounded extracted terms for the active paper on its "
                                    "eligible extracted pages",))
    return ToolResult(tool=TOOL_NAME, status=ToolStatus.ERROR, error_code=ErrorCode.NOT_FOUND,
                      provenance_id=provenance_id,
                      warnings=("the active paper's extracted terms or extraction manifest could not be read",))


class PaperSummaryTool:
    """``lit_summarize_extracted_terms`` bound to one paper resolver and one ledger.

    Args:
        resolver: Server-side resolver for the request's active paper; consulted
            only when no scope is pinned (:func:`pinned_paper_scope`).
        ledger_size: Bound on retained legacy payloads.
    """

    spec = LIT_SUMMARIZE_EXTRACTED_TERMS
    skill = SUMMARIZE_LITERATURE_SKILL

    def __init__(self, resolver: PaperScopeResolver | None = None, *, ledger_size: int = 64) -> None:
        self._resolver = resolver
        self.ledger = SummaryLedger(ledger_size)

    def registration(self) -> tuple[ToolSpec, Callable[[Any], ToolResult]]:
        """``(spec, fn)`` for ``build_registry(extra=...)``."""
        return self.spec, self.run

    def resolve(self, ctx: RequestContext) -> PaperScope | None:
        """The pinned scope, else the resolver's answer for ``ctx``."""
        pinned = _PINNED.get()
        if pinned is not None:
            return pinned
        return self._resolver(ctx) if self._resolver is not None else None

    def run(self, inv: Any) -> ToolResult:
        """Executor entry point: ``fn(invocation)``; arguments are already schema-validated."""
        scope = self.resolve(inv.ctx)
        if scope is None:
            raise ContractError(ErrorCode.NOT_FOUND, "this session has no active extracted paper")
        args: Mapping[str, Any] = inv.args
        provenance_id = f"f2w-op:{uuid.uuid4().hex}"
        try:
            outcome = summarize_paper_terms(
                scope, max_terms=args.get("max_terms", MAX_TERMS_CAP), query=args.get("query", ""),
                missing_topics=args.get("missing_topics"), relevant_node_names=args.get("relevant_node_names"),
            )
        except Exception as exc:  # noqa: BLE001 - typed result; original kept for the legacy adapter
            logger.warning("summarizing extracted terms failed", exc_info=True)
            if is_control_flow(exc):
                raise
            self.ledger.put(provenance_id, LedgerEntry(error=exc))
            return ToolResult(tool=TOOL_NAME, status=ToolStatus.ERROR, error_code=ErrorCode.INTERNAL,
                              provenance_id=provenance_id,
                              warnings=(f"summarizing extracted terms failed ({type(exc).__name__})",))
        self.ledger.put(provenance_id, LedgerEntry(legacy=outcome.legacy))
        return summary_to_result(outcome.summary, provenance_id=provenance_id)


__all__: tuple[str, ...] = (
    "COUNTING_UNIT", "LIT_SUMMARIZE_EXTRACTED_TERMS", "LedgerEntry", "MAX_RESULT_CHARS", "MAX_TERMS_CAP",
    "OPERATION_VERSION", "POPULATION", "PaperScope", "PaperScopeResolver", "PaperSummaryTool", "PaperTermSummary",
    "SUMMARIZE_LITERATURE_SKILL", "SummaryLedger", "SummaryOutcome", "TOOL_NAME", "citation_tokens",
    "pinned_paper_scope", "summarize_paper_terms", "summary_to_result",
)
