"""Public F2W SDK: ``f2w.public`` (``app.public`` in a checkout). See docs/migration/public_api_spec.md.

Thin facades over :class:`~app.modules.application.F2WApplication` (T03). Only
the methods that exist today are implemented:

- :meth:`F2WClient.answer_from_knowledge` (and ``_sync``): the existing QA path,
  bypassing planning;
- :meth:`F2WClient.summarize_paper_terms`: the T19B deterministic paper summary
  (``lit_summarize_extracted_terms``), run through an authorizing executor;
- :meth:`LiteratureAgent.delegate`: a delegated literature task through the
  LangGraph supervisor.

The other methods of the specification raise ``NotImplementedError`` naming the
task that implements them.

Importing this module reads no environment or ``.env`` file, builds no
registry, model or client, and imports no torch, FAISS, PyMuPDF, OpenAI or
LangGraph code. Construction opens no network connection.
"""
from __future__ import annotations

from typing import Any, FrozenSet, Mapping, Optional, Sequence

from .modules.application import DelegatedOutcome, F2WApplication
from .modules.contracts import RequestContext, ToolResult
from .modules.security.authorization import DeploymentSettings

__all__ = ("F2WClient", "LiteratureAgent", "create_literature_agent")


def _pending(method: str, task: str) -> NotImplementedError:
    return NotImplementedError(f"F2WClient.{method} is not implemented yet; it arrives with {task} "
                               "(docs/migration/public_api_spec.md)")


class F2WClient:
    """In-process F2W client over one :class:`F2WApplication`.

    Build it with :meth:`from_settings` (it then owns the application and closes
    it) or wrap an existing application. Two clients share no registry, executor,
    ledger or checkpointer.
    """

    def __init__(self, application: F2WApplication, *, owns_application: bool = False) -> None:
        if not isinstance(application, F2WApplication):
            raise TypeError("application must be an F2WApplication")
        self._app = application
        self._owns = owns_application
        self._summary: Optional[Any] = None  # (executor, tool), built on first use

    @classmethod
    def from_settings(
        cls,
        settings: DeploymentSettings,
        *,
        retrieval: Any,
        chat_model: Any = None,
        checkpointer: Any = None,
        **options: Any,
    ) -> "F2WClient":
        """Build a client from explicit settings (no ``.env`` or environment read).

        Args:
            settings: Validated startup ``DeploymentSettings``.
            retrieval: The retrieval service (``runtime_bridge.retrieval.LegacyRetrievalService``).
            chat_model: Optional LangChain chat model for delegated tasks.
            checkpointer: Optional LangGraph checkpointer for delegated threads.
            **options: Further ``F2WApplication`` keyword arguments (limits, admission, ...).
        """
        app = F2WApplication(settings=settings, retrieval=retrieval, chat_model=chat_model,
                             checkpointer=checkpointer, **options)
        return cls(app, owns_application=True)

    @property
    def application(self) -> F2WApplication:
        """The wrapped application service."""
        return self._app

    # -- lifecycle -------------------------------------------------------------------

    def close(self) -> None:
        """Close the application if this client built it."""
        if self._owns:
            self._app.close()

    def __enter__(self) -> "F2WClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- request context ---------------------------------------------------------------

    def context_for(self, credential: Any = None, **kw: Any) -> RequestContext:
        """Resolve a request context with the profile rules (local-operator on ``:local``)."""
        return self._app.context(credential, **kw)

    # -- implemented methods ---------------------------------------------------------------

    async def answer_from_knowledge(self, ctx: RequestContext, question: str, **kw: Any) -> Any:
        """Existing F2W QA answer (``ReadOutcome``; ``.to_legacy()`` gives the legacy verdict)."""
        return await self._app.answer_from_knowledge(ctx, question, **kw)

    def answer_from_knowledge_sync(self, ctx: RequestContext, question: str, **kw: Any) -> Any:
        """Synchronous :meth:`answer_from_knowledge`; raises inside a running event loop."""
        return self._app.answer_from_knowledge_sync(ctx, question, **kw)

    def _summary_executor(self) -> Any:
        if self._summary is None:
            from .modules.agent_runtime.executor import ToolExecutor
            from .modules.operations.papers import PaperSummaryTool
            from .modules.tools.bootstrap import build_registry

            tool = PaperSummaryTool()
            registry = build_registry(None, include=(), extra=[tool.registration()],
                                      name=f"{self._app.registry.name}-summary")
            app = self._app
            executor = ToolExecutor(registry, event_sink=app.event_sink, admission=app.admission,
                                    limits=app.limits, clock=app.clock)
            self._summary = (executor, tool)
        return self._summary

    async def summarize_paper_terms(
        self,
        ctx: RequestContext,
        scope: Any,
        *,
        max_terms: Optional[int] = None,
        query: str = "",
        missing_topics: Optional[Sequence[str]] = None,
        relevant_node_names: Optional[Sequence[str]] = None,
    ) -> ToolResult:
        """Deterministic summary of one extracted paper (T19B ``lit_summarize_extracted_terms``).

        ``scope`` is an ``operations.papers.PaperScope`` chosen by trusted caller code; it is
        pinned for this call and never exposed as a model argument. Capability, schema and
        bound checks run first; refusals come back as a failed ``ToolResult``.
        """
        from .modules.operations.papers import TOOL_NAME, PaperScope, pinned_paper_scope

        if not isinstance(scope, PaperScope):
            raise TypeError("scope must be an operations.papers.PaperScope")
        args: dict = {}
        if max_terms is not None:
            args["max_terms"] = max_terms
        if query:
            args["query"] = query
        if missing_topics is not None:
            args["missing_topics"] = list(missing_topics)
        if relevant_node_names is not None:
            args["relevant_node_names"] = list(relevant_node_names)
        executor, tool = self._summary_executor()
        with pinned_paper_scope(scope):
            result = await executor.aexecute(ctx, TOOL_NAME, args)
        tool.ledger.pop(result.provenance_id)  # the client returns the typed result only
        return result

    # -- specified, not implemented yet ------------------------------------------------------

    def search_literature(self, ctx: RequestContext, query: str, limit: int = 20) -> ToolResult:
        raise _pending("search_literature", "T05")

    def resolve_work(self, ctx: RequestContext, identifier: str) -> ToolResult:
        raise _pending("resolve_work", "T05")

    def read_paper(self, ctx: RequestContext, work_id: str) -> ToolResult:
        raise _pending("read_paper", "T05")

    def read_section(self, ctx: RequestContext, work_id: str, section: str) -> ToolResult:
        raise _pending("read_section", "T05")

    def get_evidence(self, ctx: RequestContext, evidence_ids: Sequence[str]) -> ToolResult:
        raise _pending("get_evidence", "T06")

    def query_knowledge(self, ctx: RequestContext, question: str, graphs: Sequence[str] = ()) -> ToolResult:
        raise _pending("query_knowledge", "T08")

    def trace_provenance(self, ctx: RequestContext, record_id: str) -> ToolResult:
        raise _pending("trace_provenance", "T08")

    def submit_ingest(self, ctx: RequestContext, request: Any) -> Any:
        raise _pending("submit_ingest", "T10")

    def get_job(self, ctx: RequestContext, job_id: str) -> Any:
        raise _pending("get_job", "T10")

    def cancel_job(self, ctx: RequestContext, job_id: str) -> Any:
        raise _pending("cancel_job", "T10")

    def plan_publication(self, ctx: RequestContext, snapshot_id: str, target: Any) -> Any:
        raise _pending("plan_publication", "T10")

    def approve_plan(self, ctx: RequestContext, plan: Any) -> Any:
        raise _pending("approve_plan", "T10")

    def publish_plan(self, ctx: RequestContext, plan: Any, approval: Any) -> Any:
        raise _pending("publish_plan", "T08B")


class LiteratureAgent:
    """Delegated literature tasks through the client's LangGraph supervisor."""

    def __init__(self, client: F2WClient, *, owns_client: bool = False) -> None:
        if client.application.supervisor is None:
            raise ValueError("the client's application has no supervisor; build it with a chat_model")
        self.client = client
        self._owns = owns_client

    def close(self) -> None:
        """Close the client if this agent's factory built it."""
        if self._owns:
            self.client.close()

    def __enter__(self) -> "LiteratureAgent":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    async def delegate(
        self, ctx: RequestContext, task: str, *, thread_id: str, scope: Optional[FrozenSet[str]] = None,
    ) -> DelegatedOutcome:
        """Run one delegated task on ``thread_id`` (``F2WApplication.delegate``)."""
        return await self.client.application.delegate(ctx, task, thread_id=thread_id, scope=scope)


def create_literature_agent(
    *,
    client: Optional[F2WClient] = None,
    settings: Optional[DeploymentSettings] = None,
    retrieval: Any = None,
    chat_model: Any = None,
    checkpointer: Any = None,
    sync_model: Any = None,
    async_model: Any = None,
    **options: Mapping[str, Any],
) -> LiteratureAgent:
    """Return a :class:`LiteratureAgent` over ``client`` or over a new client.

    Today the supervisor takes an injected LangChain ``chat_model``; the factory never
    chooses a provider or model. Pass either ``client`` (built with a chat model) or
    ``settings`` + ``retrieval`` + ``chat_model`` (+ optional ``checkpointer``).

    Raises:
        NotImplementedError: ``sync_model``/``async_model`` (F2W ``ChatClient`` instances, spec
            section 2) need a ChatClient-to-LangChain bridge that arrives with T14/T15.
        ValueError: Neither a usable client nor settings, retrieval and chat_model were given.
    """
    if sync_model is not None or async_model is not None:
        raise NotImplementedError("create_literature_agent(sync_model=..., async_model=...) is not implemented "
                                  "yet; it arrives with T14/T15. Pass a LangChain chat_model instead.")
    if client is not None:
        if settings is not None or retrieval is not None or chat_model is not None or checkpointer is not None:
            raise ValueError("pass either client or settings/retrieval/chat_model/checkpointer, not both")
        return LiteratureAgent(client)
    if settings is None or retrieval is None or chat_model is None:
        raise ValueError("create_literature_agent needs client, or settings, retrieval and chat_model")
    client = F2WClient.from_settings(settings, retrieval=retrieval, chat_model=chat_model,
                                     checkpointer=checkpointer, **options)
    return LiteratureAgent(client, owns_client=True)
