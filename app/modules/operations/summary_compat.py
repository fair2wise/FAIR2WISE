"""Legacy UI compatibility adapter for ``lit_summarize_extracted_terms`` (task T19B).

The legacy UI reaches the summary at two places in ``f2w_agent/api.py``, both
calling ``summarize_extracted_terms`` with server state only:

- ``_maybe_report_active_extraction`` (status ``extraction_reported``):
  ``(coord.session_terms, coord.extraction_manifest, active_paper.filename)``;
- ``_resume_extraction`` post-extraction re-check (``rounds[].extraction_report``):
  the same three plus ``max_terms=6``, ``query=question``, the retrieval
  verdict's ``missing_topics`` and the re-check subgraph's node labels.

:meth:`LegacySummaryPath.summarize_extracted_terms` is a drop-in for that
function with the same signature. It pins the server-side paper scope
(:func:`~.papers.pinned_paper_scope`), runs the Tool through an authorizing
``ToolExecutor`` (capability, schema, audit), and returns the exact dictionary
the wrapped function returned (or re-raises the exact exception it raised), so
``ChatResponse``/SSE output is byte-identical. ``api.py`` is not changed by
T19B; wiring the adapter in behind ``F2W_RUNTIME=langgraph`` is an integrator
step (see the T19B report).

For model-driven calls (a supervisor choosing the Tool), no scope is pinned
and :func:`session_paper_resolver` resolves the paper from the pipeline's
active session instead. Either way, no path, filename or paper id is ever a
model argument.

Context. Like the T03 read adapter this runs only on the ``:local`` profile
(local-operator, D34); ALS profiles refuse startup until per-request identity
is wired (T18B).
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import re
from typing import Any, cast
import uuid

from ..contracts import ActorKind, ContractError, ErrorCode, RequestContext, ToolStatus
from .papers import MAX_TERMS_CAP, TOOL_NAME, PaperScope, PaperScopeResolver, PaperSummaryTool, pinned_paper_scope

logger = logging.getLogger(__name__)

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _session_id(pipeline: object) -> str | None:
    key = str(getattr(pipeline, "_active_session_key", "") or "")
    return key if _SESSION_RE.match(key) else None


def session_paper_resolver(pipeline: Any) -> PaperScopeResolver:
    """Resolve the active extracted paper from an ``AgentPipelineService``'s session state.

    Uses exactly what ``api.py`` passes: ``pipeline.coord.session_terms``,
    ``pipeline.coord.extraction_manifest`` and the active session's
    ``workflow.snapshot()["active_paper"]["filename"]``. The request must
    belong to the pipeline's active session (``ctx.session_id`` equals the
    active key, ``None`` for the unnamed legacy session).

    Raises (from the returned resolver):
        ContractError: ``DENIED`` when the request's session is not the active one.
    """

    def resolve(ctx: RequestContext) -> PaperScope | None:
        if ctx.session_id != _session_id(pipeline):
            raise ContractError(ErrorCode.DENIED, "the request does not belong to the active chat session")
        paper = (pipeline.workflow.snapshot() or {}).get("active_paper")
        filename = str(paper.get("filename") or "") if isinstance(paper, dict) else ""
        if not filename:
            return None
        paper_id = str(cast(dict[str, Any], paper).get("paper_id") or "") or None
        return PaperScope(str(pipeline.coord.session_terms), str(pipeline.coord.extraction_manifest), filename,
                          paper_id=paper_id)

    return resolve


class LegacySummaryPath:
    """Serves the legacy summary call through the Tool.

    Args:
        executor: A ``ToolExecutor`` whose registry holds ``tool``'s registration.
        tool: The registered :class:`~.papers.PaperSummaryTool` (its ledger holds legacy payloads).
        context_factory: Returns the server-built ``RequestContext`` for one call.
    """

    def __init__(self, executor: Any, tool: PaperSummaryTool, *,
                 context_factory: Callable[[], RequestContext]) -> None:
        if executor.registry.get(TOOL_NAME) is None:
            raise ValueError(f"the executor's registry does not hold {TOOL_NAME}")
        self.executor = executor
        self.tool = tool
        self._context_factory = context_factory
        self.fallbacks = 0

    @staticmethod
    def _args(max_terms: int, query: str, missing_topics: list[str] | None,
              relevant_node_names: list[str] | None) -> dict[str, Any]:
        args: dict[str, Any] = {"max_terms": max_terms}
        if query:
            args["query"] = query
        if missing_topics is not None:
            args["missing_topics"] = list(missing_topics)
        if relevant_node_names is not None:
            args["relevant_node_names"] = list(relevant_node_names)
        return args

    def summarize_extracted_terms(
        self,
        terms_path: str,
        manifest_path: str,
        filename: str,
        *,
        max_terms: int = MAX_TERMS_CAP,
        query: str = "",
        missing_topics: list[str] | None = None,
        relevant_node_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Same contract as ``paper_evidence_agent.summarize_extracted_terms``.

        Arguments outside the Tool's input bounds (``max_terms`` > 16, a query over
        16000 characters, more than 100 missing topics or 2000 node names, an item
        over 1000 characters) are refused by the executor as ``INVALID``; the legacy
        UI must not change behavior because of that, so those calls run the wrapped
        function directly (same result) and are counted in ``fallbacks`` and logged.

        Raises:
            Exception: Exactly what the wrapped function raised.
            ContractError: When the executor refused the call for any other reason.
        """
        scope = PaperScope(str(terms_path), str(manifest_path), str(filename))
        args = self._args(max_terms, query, missing_topics, relevant_node_names)
        with pinned_paper_scope(scope):
            result = self.executor.execute(self._context_factory(), TOOL_NAME, args)
        entry = self.tool.ledger.pop(result.provenance_id)
        if entry is not None and entry.error is not None:
            raise entry.error
        if entry is not None and entry.ran:
            return cast(dict[str, Any], entry.legacy)
        if result.status is ToolStatus.INVALID:
            from ..f2w_agent.paper_evidence_agent import summarize_extracted_terms

            self.fallbacks += 1
            logger.warning("%s: arguments outside the tool bounds; ran the legacy function directly (%s)",
                           TOOL_NAME, "; ".join(result.warnings))
            return summarize_extracted_terms(terms_path, manifest_path, filename, max_terms=max_terms,
                                             query=query, missing_topics=missing_topics,
                                             relevant_node_names=relevant_node_names)
        code = result.error_code or ErrorCode.INTERNAL
        raise ContractError(code, "; ".join(result.warnings) or f"{TOOL_NAME} was not run ({code.value})")


def build_legacy_summary_path(pipeline: Any, environ: Any | None = None) -> LegacySummaryPath:
    """Build the summary adapter for one ``AgentPipelineService`` (``:local`` only).

    The registry comes from the runtime bridge bootstrap (``answer_from_knowledge``
    plus this Tool and the ``summarize_literature`` skill), the same shape an
    ``F2WApplication`` gets when it passes ``paper_summary``.

    Raises:
        RuntimeConfigError: On an ALS profile.
    """
    from ..agent_runtime.executor import RuntimeLimits, ToolExecutor
    from ..agent_runtime.policies import InMemoryEventSink
    from ..llm_admission import ProviderAdmission
    from ..runtime_bridge.bootstrap import build_read_registry, build_read_skills
    from ..runtime_bridge.legacy_adapter import legacy_deployment_settings
    from ..runtime_bridge.qa_tool import LegacyResultLedger
    from ..runtime_bridge.retrieval import LegacyRetrievalService
    from ..security.authorization import local_operator_context

    settings = legacy_deployment_settings(environ)
    tool = PaperSummaryTool(session_paper_resolver(pipeline))
    service = LegacyRetrievalService(
        agent_provider=lambda: pipeline.retrieval,
        tiled_uri_provider=lambda: getattr(getattr(pipeline, "runtime", None), "tiled_uri", None),
    )
    registry = build_read_registry(service, LegacyResultLedger(), name="f2w-legacy-ui-summary", paper_summary=tool)
    build_read_skills(registry)  # verifies skill coverage of the new tool
    executor = ToolExecutor(registry, event_sink=InMemoryEventSink(), admission=ProviderAdmission(),
                            limits=RuntimeLimits(max_retries=0))

    def context() -> RequestContext:
        return local_operator_context(settings, request_id=uuid.uuid4().hex, session_id=_session_id(pipeline),
                                      actor_kind=ActorKind.INTERACTIVE)

    return LegacySummaryPath(executor, tool, context_factory=context)


__all__ = ("LegacySummaryPath", "build_legacy_summary_path", "session_paper_resolver")
