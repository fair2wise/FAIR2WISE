"""The read-only ``answer_from_knowledge`` tool over the existing F2W QA path (task T03).

Name. ``answer_from_knowledge`` is the method name frozen in
``docs/migration/public_api_spec.md`` section 4 ("wraps the existing QA path
unchanged (T03)"). It is not ``retrieve_*``: the wrapped call does model
inference (the ``JUDGE_SYSTEM`` judge and the leeway synthesis) and returns an
answer, whereas the separate, planned ``query_knowledge`` is retrieval-only
with no model call.

Behavior. The tool calls :class:`~.retrieval.LegacyRetrievalService.query`,
which runs ``RetrievalAgent.query`` unchanged: KG/multi-KG retrieval, optional
source-document RAG, Materials Project and live-Tiled branches, the
no-evidence gate, ``JUDGE_SYSTEM``, leeway, citation tokens and limits. Nothing
is re-prompted, filtered or re-ranked here.

Result mapping (legacy verdict -> ``ToolResult``):

=====================================================  ==============  ======================
legacy verdict                                         status          error_code
=====================================================  ==============  ======================
``status=success, sufficient=True``                    ``OK``          -
``status=success, sufficient=False, no_evidence=False``  ``PARTIAL``   - (``outcome=insufficient``)
``status=success, no_evidence=True``                   ``EMPTY``       -
``status=judge_error`` (judge client/call failed)      ``UNAVAILABLE`` ``unavailable``
``status=context_error``                               ``ERROR``       ``internal``
other ``status``                                       ``ERROR``       ``unsupported_capability``
non-mapping verdict                                    ``ERROR``       ``internal``
``RuntimeError("No knowledge graphs loaded...")``      ``UNAVAILABLE`` ``unavailable``
``TimeoutError`` / ``ConnectionError``                 ``UNAVAILABLE`` ``unavailable``
any other exception                                    ``ERROR``       ``internal``
=====================================================  ==============  ======================

Denied (missing ``chat.ask``) and invalid arguments are refused by the
executor before the tool runs (``DENIED`` / ``INVALID``). Provider
unavailability is never ``EMPTY`` or ``OK``.

Legacy payload. The original verdict object (or the original exception) is
kept in a per-application :class:`LegacyResultLedger` under the result's
``provenance_id``, so the legacy adapter can return exactly what
``RetrievalAgent.query`` returned (or re-raise exactly what it raised) even
though ``ToolResult.data`` is bounded by the executor.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import re
import threading
from typing import Any, Awaitable, Callable, Mapping, Sequence
import uuid

from ..agent_runtime.executor import is_control_flow
from ..contracts import (
    ContractError,
    CostClass,
    Effect,
    ErrorCode,
    EvidenceRef,
    MatchType,
    SourceLocator,
    ToolResult,
    ToolSpec,
    ToolStatus,
)

TOOL_NAME = "answer_from_knowledge"

#: Citation tokens the existing QA path emits (f2w_llm_qa_baseline.json ``citation_formats``),
#: matched with the frontend's regex (ui/src/app/components/kgCitations.ts:6).
CITATION_TOKEN_RE = re.compile(r"\[(KG|PDF|OPS|MP):\s*([^\]]+?)\s*\]", re.IGNORECASE)

NO_GRAPHS_PREFIX = "No knowledge graphs loaded"

EVIDENCE_CONTRACT = (
    "OK/PARTIAL results carry one EvidenceRef per selected KG node, in retrieval order: "
    "evidence_id='kg:<graph_id>:<node_id>' (graph-scoped identity, never a URI smash), "
    "work_id='kg-node:<graph_id>:<node_id>', source_version='kg-graph:<graph_id>', empty locator, "
    "match_type=none (a node pointer, not a quote). data.citations lists the answer's citation "
    "tokens verbatim: [KG: <graph_id>: <name>] (retrieval_agent.py Allowed citations), "
    "[PDF: <file> p.<n>], [OPS: <file> §<heading>], [MP: <mp-id> <formula>]. "
    "EMPTY (no evidence) and failures carry no evidence."
)

_HISTORY_ITEM = {
    "type": "object",
    "properties": {"role": {"type": "string"}, "content": {"type": "string"}},
    "required": ["role", "content"],
}

ANSWER_FROM_KNOWLEDGE = ToolSpec(
    name=TOOL_NAME,
    version="1.0.0",
    domain="retrieval",
    description=(
        "Answer a question from the configured FAIR2WISE knowledge graphs with the existing F2W QA "
        "path: KG retrieval across the selected graphs, optional source-document RAG, the evidence "
        "gate, the F2W judge and contextual leeway. Returns the grounded answer with its exact "
        "citation tokens, or an explicit insufficient / no-evidence outcome."
    ),
    when_to_use=(
        "Any scientific or beamline question that should be answered from F2W's knowledge graphs or "
        "indexed sources. Pass the user's question verbatim; include recent history if the question "
        "depends on it."
    ),
    when_not_to_use=(
        "Questions about the active extracted paper, paper download or extraction, graph edits, "
        "publication, or small talk that needs no evidence. It never changes data."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string", "minLength": 1},
            "history": {"type": "array", "items": _HISTORY_ITEM},
            "source_rag": {"type": "boolean"},
            "use_live_tiled": {"type": "boolean"},
        },
        "required": ["question"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "outcome": {"enum": ["answered", "insufficient"]},
            "sufficient": {"type": "boolean"},
            "answer": {"type": ["string", "null"]},
            "citations": {"type": "array", "items": {"type": "string"}},
            "selected": {"type": "array", "items": {"type": "string"}},
            "graph_ids": {"type": "array", "items": {"type": "string"}},
            "selected_graph_ids": {"type": "array", "items": {"type": "string"}},
            "missing_topics": {"type": "array", "items": {"type": "string"}},
            "direct_evidence_count": {"type": "integer"},
            "legacy_status": {"type": "string"},
        },
        "required": ["outcome", "sufficient", "selected", "graph_ids"],
    },
    effects=frozenset({Effect.READ_CORPUS, Effect.MODEL_INFERENCE}),
    cost_class=CostClass.MODERATE,
    required_capabilities=frozenset({"chat.ask"}),
    evidence_contract=EVIDENCE_CONTRACT,
    idempotent=True,
)


# -- legacy payload ledger --------------------------------------------------------

_MISSING = object()


@dataclass(frozen=True)
class LegacyEntry:
    """What one tool invocation produced on the legacy side.

    Attributes:
        verdict: The object ``RetrievalAgent.query`` returned (``_MISSING`` if it raised).
        error: The exception it raised, if any.
    """

    verdict: Any = _MISSING
    error: BaseException | None = None


class LegacyResultLedger:
    """Bounded, thread-safe map ``provenance_id -> LegacyEntry`` for one application.

    Args:
        max_entries: Oldest entries are evicted beyond this many.
    """

    def __init__(self, max_entries: int = 256) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max = max_entries
        self._entries: OrderedDict[str, LegacyEntry] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def new_id() -> str:
        """A fresh provenance id for one invocation."""
        return f"f2w-read:{uuid.uuid4().hex}"

    def put(self, provenance_id: str, entry: LegacyEntry) -> None:
        """Record ``entry``; evicts the oldest entries beyond the bound."""
        with self._lock:
            self._entries[provenance_id] = entry
            self._entries.move_to_end(provenance_id)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def get(self, provenance_id: str | None, *, pop: bool = False) -> LegacyEntry | None:
        """Return (and optionally remove) the entry for ``provenance_id``."""
        if not provenance_id:
            return None
        with self._lock:
            return self._entries.pop(provenance_id, None) if pop else self._entries.get(provenance_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


@dataclass(frozen=True)
class ReadOutcome:
    """A typed tool result plus the legacy verdict it was mapped from.

    Attributes:
        result: The contracts ``ToolResult``.
        legacy_verdict: The unchanged ``RetrievalAgent.query`` return value, if the tool ran
            and returned.
        legacy_error: The unchanged exception, if the tool ran and the query raised.
    """

    result: ToolResult
    legacy_verdict: Any = _MISSING
    legacy_error: BaseException | None = None

    @property
    def ran(self) -> bool:
        """Whether the retrieval call actually happened (not refused by the executor)."""
        return self.legacy_verdict is not _MISSING or self.legacy_error is not None

    @property
    def evidence_ids(self) -> list[str]:
        """Evidence IDs in retrieval order."""
        return self.result.evidence_ids

    def to_legacy(self) -> Any:
        """Return exactly what the legacy call returned, or raise exactly what it raised.

        Raises:
            BaseException: The original retrieval exception.
            ContractError: When the executor refused the call before it ran (denied,
                invalid, budget), with the executor's error code.
        """
        if self.legacy_error is not None:
            raise self.legacy_error
        if self.legacy_verdict is not _MISSING:
            return self.legacy_verdict
        code = self.result.error_code or ErrorCode.INTERNAL
        message = "; ".join(self.result.warnings) or f"{self.result.tool} was not run ({code.value})"
        raise ContractError(code, message)


def outcome_from_ledger(result: ToolResult, ledger: LegacyResultLedger, *, pop: bool) -> ReadOutcome:
    """Pair ``result`` with its ledger entry (if the tool ran)."""
    entry = ledger.get(result.provenance_id, pop=pop)
    if entry is None:
        return ReadOutcome(result=result)
    return ReadOutcome(result=result, legacy_verdict=entry.verdict, legacy_error=entry.error)


# -- mapping ----------------------------------------------------------------------

def _strs(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _unique(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def selected_pairs(verdict: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(graph_id, node_id)`` for each selected node, in retrieval order.

    Uses ``selected_hits`` (graph-scoped) when present; otherwise ``selected`` with the
    single loaded graph id, or ``""`` when the graph is ambiguous or unknown.
    """
    hits = verdict.get("selected_hits")
    if isinstance(hits, (list, tuple)) and hits:
        pairs = []
        for hit in hits:
            if isinstance(hit, Mapping) and str(hit.get("id") or "").strip():
                pairs.append((str(hit.get("graph_id") or ""), str(hit["id"])))
        if pairs:
            return pairs
    graph_ids = _strs(verdict.get("graph_ids"))
    default_gid = graph_ids[0] if len(graph_ids) == 1 else ""
    return [(default_gid, node_id) for node_id in _strs(verdict.get("selected"))]


def evidence_id(graph_id: str, node_id: str) -> str:
    """Stable evidence id for a selected KG node."""
    return f"kg:{graph_id}:{node_id}" if graph_id else f"kg:{node_id}"


def kg_evidence(pairs: Sequence[tuple[str, str]]) -> tuple[EvidenceRef, ...]:
    """EvidenceRefs for selected KG nodes (pointers, not quotes)."""
    refs = []
    seen = set()
    for graph_id, node_id in pairs:
        eid = evidence_id(graph_id, node_id)
        if eid in seen:
            continue
        seen.add(eid)
        scope = graph_id or "unknown"
        refs.append(EvidenceRef(
            evidence_id=eid, work_id=f"kg-node:{scope}:{node_id}", source_version=f"kg-graph:{scope}",
            locator=SourceLocator(), match_type=MatchType.NONE,
        ))
    return tuple(refs)


def citation_tokens(answer: Any) -> list[str]:
    """Citation tokens in ``answer``, verbatim, in order of first appearance."""
    if not isinstance(answer, str):
        return []
    return _unique([m.group(0) for m in CITATION_TOKEN_RE.finditer(answer)])


def _fail(code: ErrorCode, message: str, *, provenance_id: str | None, retryable: bool = False,
          status: ToolStatus = ToolStatus.ERROR) -> ToolResult:
    return ToolResult(tool=TOOL_NAME, status=status, error_code=code, retryable=retryable,
                      warnings=(message[:300],), provenance_id=provenance_id)


def verdict_to_result(verdict: Any, *, provenance_id: str | None = None) -> ToolResult:
    """Map a legacy ``RetrievalAgent.query`` verdict to a ``ToolResult`` (see module table)."""
    if not isinstance(verdict, Mapping):
        return _fail(ErrorCode.INTERNAL, f"retrieval returned a malformed verdict ({type(verdict).__name__})",
                     provenance_id=provenance_id)
    status = str(verdict.get("status") or "")
    if status == "judge_error":
        return _fail(ErrorCode.UNAVAILABLE, "the QA judge model call failed or timed out",
                     provenance_id=provenance_id, retryable=True, status=ToolStatus.UNAVAILABLE)
    if status == "context_error":
        return _fail(ErrorCode.INTERNAL, "building the retrieval context failed", provenance_id=provenance_id)
    if status != "success":
        return _fail(ErrorCode.UNSUPPORTED_CAPABILITY, f"unrecognized retrieval status {status!r}",
                     provenance_id=provenance_id)
    if verdict.get("no_evidence"):
        return ToolResult(tool=TOOL_NAME, status=ToolStatus.EMPTY, provenance_id=provenance_id,
                          warnings=("no_evidence: the knowledge graphs hold no evidence for this question",))
    pairs = selected_pairs(verdict)
    sufficient = bool(verdict.get("sufficient"))
    answer = verdict.get("answer") if sufficient else None
    data: dict[str, Any] = {
        "outcome": "answered" if sufficient else "insufficient",
        "sufficient": sufficient,
        "answer": answer if isinstance(answer, str) else (None if answer is None else str(answer)),
        "citations": citation_tokens(answer),
        "selected": _strs(verdict.get("selected")),
        "graph_ids": _strs(verdict.get("graph_ids")),
        "selected_graph_ids": _unique([g for g, _ in pairs]),
        "missing_topics": _strs(verdict.get("missing_topics")),
        "direct_evidence_count": int(verdict.get("direct_evidence_count") or 0),
        "legacy_status": status,
    }
    return ToolResult(tool=TOOL_NAME, status=ToolStatus.OK if sufficient else ToolStatus.PARTIAL, data=data,
                      evidence=kg_evidence(pairs), provenance_id=provenance_id)


def exception_to_result(exc: BaseException, *, provenance_id: str | None = None) -> ToolResult:
    """Map an exception raised by the legacy query (see module table)."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return _fail(ErrorCode.UNAVAILABLE, f"retrieval dependency unavailable ({type(exc).__name__})",
                     provenance_id=provenance_id, retryable=True, status=ToolStatus.UNAVAILABLE)
    if isinstance(exc, RuntimeError) and str(exc).startswith(NO_GRAPHS_PREFIX):
        return _fail(ErrorCode.UNAVAILABLE, "no knowledge graph could be loaded", provenance_id=provenance_id,
                     status=ToolStatus.UNAVAILABLE)
    if isinstance(exc, ContractError):
        code = exc.code
        status = {ErrorCode.DENIED: ToolStatus.DENIED, ErrorCode.INVALID_INPUT: ToolStatus.INVALID,
                  ErrorCode.UNAVAILABLE: ToolStatus.UNAVAILABLE}.get(code, ToolStatus.ERROR)
        return _fail(code, str(exc), provenance_id=provenance_id, retryable=exc.retryable, status=status)
    return _fail(ErrorCode.INTERNAL, f"retrieval failed ({type(exc).__name__})", provenance_id=provenance_id)


# -- tool implementation ----------------------------------------------------------

def make_answer_tool(service: Any, ledger: LegacyResultLedger) -> Callable[[Any], Awaitable[ToolResult]]:
    """Return the async tool implementation bound to one retrieval service and ledger.

    The implementation holds no provider admission slot itself: the existing
    async clients already take ``cborg_limiter.async_slot`` (which delegates to
    the shared controller under ``F2W_SHARED_ADMISSION``), so taking another
    slot here would double-count one call.
    """

    async def answer_from_knowledge(inv: Any) -> ToolResult:
        args = inv.args
        provenance_id = ledger.new_id()
        try:
            verdict = await service.query(
                args["question"], history=args.get("history"), source_rag=args.get("source_rag"),
                use_live_tiled=args.get("use_live_tiled"),
            )
        except Exception as exc:  # noqa: BLE001 - mapped to a typed result; original kept for legacy
            if is_control_flow(exc):
                raise
            ledger.put(provenance_id, LegacyEntry(error=exc))
            return exception_to_result(exc, provenance_id=provenance_id)
        ledger.put(provenance_id, LegacyEntry(verdict=verdict))
        return verdict_to_result(verdict, provenance_id=provenance_id)

    answer_from_knowledge.service = service  # type: ignore[attr-defined] - for parity tests
    return answer_from_knowledge


__all__ = (
    "ANSWER_FROM_KNOWLEDGE", "CITATION_TOKEN_RE", "EVIDENCE_CONTRACT", "LegacyEntry", "LegacyResultLedger",
    "ReadOutcome", "TOOL_NAME", "citation_tokens", "evidence_id", "exception_to_result", "kg_evidence",
    "make_answer_tool", "outcome_from_ledger", "selected_pairs", "verdict_to_result",
)
