"""Application-scoped handle on the existing F2W retrieval/QA path (task T03).

:class:`LegacyRetrievalService` is the one object both call styles use: the
direct read call (``F2WApplication.answer_from_knowledge``) and the delegated
LangGraph task reach the same instance through the ``answer_from_knowledge``
tool. It does not reimplement retrieval. It calls the existing
``RetrievalAgent.query`` exactly as ``AgentPipelineService._query_retrieval``
does today (live-Tiled setter first, then ``query`` with the same
``TypeError`` fallbacks), and returns the agent's verdict object unchanged.

The agent comes from one of:

- ``agent``: an existing instance (tests, or a caller that already owns one);
- ``agent_provider``: a zero-argument callable returning the *current* agent,
  used by the legacy UI service, which rebuilds its agent on settings changes;
- ``settings`` (+ optional ``factory``): built lazily, once per service, by
  :func:`build_retrieval_agent`. Graphs load on the first query, inside the
  agent, never at import.

``RetrievalAgent`` itself loads no global graph or model at import; graphs are
per instance and loaded on first use (``_ensure_graphs``), so no initialization
had to move.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..f2w_agent.retrieval_agent import RetrievalAgent


@dataclass(frozen=True)
class RetrievalSettings:
    """Constructor arguments for one ``RetrievalAgent``; ``None`` keeps its default.

    Attributes:
        graph_files: JSON KG paths (first is primary); empty uses the agent default.
        graph_source: ``"json"`` or ``"splash"``.
        backend: LLM backend for the judge/leeway calls (``"cborg"``/``"ollama"``).
        model: Model name, resolved by the caller through the existing precedence.
        kg_query_hops: Neighborhood hops (clamped by the agent).
        kg_query_max_nodes: Node cap (clamped by the agent).
        source_rag: Source-document RAG default.
        live_tiled: Live Tiled lookup default.
        tiled_uri: Tiled URI for live lookup.
    """

    graph_files: tuple[str, ...] = ()
    graph_source: str = "json"
    backend: str | None = None
    model: str | None = None
    kg_query_hops: int | None = None
    kg_query_max_nodes: int | None = None
    source_rag: bool | None = None
    live_tiled: bool | None = None
    tiled_uri: str | None = None


def build_retrieval_agent(settings: RetrievalSettings) -> RetrievalAgent:
    """Construct an existing ``RetrievalAgent`` from ``settings`` (imported lazily)."""
    from ..f2w_agent.retrieval_agent import RetrievalAgent

    kwargs: dict[str, Any] = {
        "graph_files": list(settings.graph_files) or None,
        "graph_source": settings.graph_source,
        "backend": settings.backend,
        "model": settings.model,
        "source_rag": settings.source_rag,
        "live_tiled": settings.live_tiled,
        "tiled_uri": settings.tiled_uri,
    }
    if settings.kg_query_hops is not None:
        kwargs["kg_query_hops"] = settings.kg_query_hops
    if settings.kg_query_max_nodes is not None:
        kwargs["kg_query_max_nodes"] = settings.kg_query_max_nodes
    return RetrievalAgent(**kwargs)


class LegacyRetrievalService:
    """The existing QA/retrieval path for one application instance.

    Args:
        agent: An existing retrieval agent.
        agent_provider: Callable returning the current agent (re-read per call).
        settings: Settings for a lazily built agent (with ``factory``).
        factory: Builds the agent from ``settings``; default :func:`build_retrieval_agent`.
        tiled_uri_provider: Returns the Tiled URI passed to ``set_live_tiled``;
            default the agent's own ``_tiled_uri`` (or ``settings.tiled_uri``).

    Raises:
        ValueError: Unless exactly one of ``agent``, ``agent_provider`` or ``settings`` is given.
    """

    def __init__(
        self,
        agent: object = None,
        *,
        agent_provider: Callable[[], Any] | None = None,
        settings: RetrievalSettings | None = None,
        factory: Callable[[RetrievalSettings], Any] = build_retrieval_agent,
        tiled_uri_provider: Callable[[], str | None] | None = None,
    ) -> None:
        given = [x is not None for x in (agent, agent_provider, settings)]
        if sum(given) != 1:
            raise ValueError("pass exactly one of agent, agent_provider or settings")
        self._agent = agent
        self._provider = agent_provider
        self._settings = settings
        self._factory = factory
        self._tiled_uri_provider = tiled_uri_provider
        self._lock = threading.Lock()
        self.calls = 0

    @property
    def agent(self) -> Any:  # noqa: ANN401  # Any: provider-injected retrieval agent (tests pass fakes; the donor agent is untyped)
        """The retrieval agent the next query uses (built on first access from settings)."""
        if self._provider is not None:
            return self._provider()
        if self._agent is None:
            with self._lock:
                if self._agent is None:
                    if self._settings is None:  # constructor guarantees one of agent/provider/settings
                        raise RuntimeError("retrieval agent has no settings to build from")
                    self._agent = self._factory(self._settings)
        return self._agent

    def _tiled_uri(self, agent: object) -> str | None:
        if self._tiled_uri_provider is not None:
            return self._tiled_uri_provider()
        if self._settings is not None and self._settings.tiled_uri is not None:
            return self._settings.tiled_uri
        return getattr(agent, "_tiled_uri", None)

    async def query(
        self,
        question: str,
        *,
        history: Sequence[dict[str, str]] | None = None,
        source_rag: bool | None = None,
        use_live_tiled: bool | None = None,
    ) -> Any:  # noqa: ANN401  # Any: legacy verdict (untyped donor structure)
        """Run the existing retrieval/QA query and return its verdict object unchanged.

        Mirrors ``AgentPipelineService._query_retrieval`` (api.py): when a
        live-Tiled choice is given it is applied through ``set_live_tiled`` with
        the configured URI, then ``query`` is awaited with history and
        ``source_rag``, falling back to the older signatures on ``TypeError``.
        Exceptions propagate unchanged.
        """
        self.calls += 1
        agent = self.agent
        if use_live_tiled is not None:
            setter = getattr(agent, "set_live_tiled", None)
            if callable(setter):
                setter(bool(use_live_tiled), uri=self._tiled_uri(agent))
        history_arg: list[dict[str, str]] | None = list(history) if history is not None else None
        query = agent.query
        try:
            return await query(question, history=history_arg, source_rag=source_rag)
        except TypeError:
            try:
                return await query(question, history=history_arg)
            except TypeError:
                return await query(question)


__all__ = ("LegacyRetrievalService", "RetrievalSettings", "build_retrieval_agent")
