"""Public F2W SDK: ``f2w.public`` (``app.public`` in a checkout). See docs/migration/public_api_spec.md.

Thin facades over :class:`~app.modules.application.F2WApplication` (T03). Only
the methods that exist today are implemented:

- :meth:`F2WClient.answer_from_knowledge` (and ``_sync``): the existing QA path,
  bypassing planning;
- :meth:`F2WClient.summarize_paper_terms`: the T19B deterministic paper summary
  (``lit_summarize_extracted_terms``), run through an authorizing executor;
- :meth:`LiteratureAgent.delegate`: a delegated literature task through the
  LangGraph supervisor;
- :meth:`F2WClient.submit_ingest`, :meth:`~F2WClient.get_job`, :meth:`~F2WClient.cancel_job`,
  :meth:`~F2WClient.plan_publication` and :meth:`~F2WClient.approve_plan`: forwarded unchanged
  to the shared :class:`~app.api.operations.F2WOperations` service the ``/v1`` routes call, so
  the same authorization applies as over HTTP (C2). They need ``operations=`` at construction;
- :meth:`F2WClient.graph_statistics`, :meth:`~F2WClient.compare_graphs`,
  :meth:`~F2WClient.paper_subgraph`, :meth:`~F2WClient.recent_entities`,
  :meth:`~F2WClient.audit_corpus`, :meth:`~F2WClient.run_knowledge_operation`,
  :meth:`~F2WClient.submit_knowledge_operation` and :meth:`~F2WClient.knowledge_job_result`
  (T19E): the five T19C Knowledge Operations, forwarded unchanged to the same
  ``F2WOperations`` (``/v1/operations/...``); the typed result is the agent Tool's.

The other methods of the specification raise ``NotImplementedError`` naming the
task that implements them.

Importing this module reads no environment or ``.env`` file, builds no
registry, model or client, and imports no torch, FAISS, PyMuPDF, OpenAI or
LangGraph code. Construction opens no network connection.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, FrozenSet, Mapping, Optional, Sequence, Union

from .modules.application import DelegatedOutcome, F2WApplication
from .modules.contracts import RequestContext, ToolResult
from .modules.security.authorization import DeploymentSettings

if TYPE_CHECKING:  # the operations service is imported only by callers that pass one
    from .api.operations import F2WOperations

__all__ = ("F2WClient", "LiteratureAgent", "create_literature_agent")


def _pending(method: str, task: str) -> NotImplementedError:
    return NotImplementedError(f"F2WClient.{method} is not implemented yet; it arrives with {task} "
                               "(docs/migration/public_api_spec.md)")


class F2WClient:
    """In-process F2W client over one :class:`F2WApplication`.

    Build it with :meth:`from_settings` (it then owns the application and closes
    it) or wrap an existing application. Two clients share no registry, executor,
    ledger or checkpointer.

    ``operations`` is the deployment's shared :class:`~app.api.operations.F2WOperations`
    (the object the ``/v1`` routes call). The job and publication methods forward to it;
    without it they raise ``ContractError(UNSUPPORTED_CAPABILITY)``.
    """

    def __init__(self, application: F2WApplication, *, owns_application: bool = False,
                 operations: Optional["F2WOperations"] = None) -> None:
        if not isinstance(application, F2WApplication):
            raise TypeError("application must be an F2WApplication")
        if operations is not None and operations.settings != application.settings:
            raise ValueError("operations and application must share the same DeploymentSettings")
        self._app = application
        self._owns = owns_application
        self._ops = operations
        self._summary: Optional[Any] = None  # (executor, tool), built on first use

    @classmethod
    def from_settings(
        cls,
        settings: DeploymentSettings,
        *,
        retrieval: Any,
        chat_model: Any = None,
        checkpointer: Any = None,
        operations: Optional["F2WOperations"] = None,
        **options: Any,
    ) -> "F2WClient":
        """Build a client from explicit settings (no ``.env`` or environment read).

        Args:
            settings: Validated startup ``DeploymentSettings``.
            retrieval: The retrieval service (``runtime_bridge.retrieval.LegacyRetrievalService``).
            chat_model: Optional LangChain chat model for delegated tasks.
            checkpointer: Optional LangGraph checkpointer for delegated threads.
            operations: Optional shared ``F2WOperations`` for the job and publication methods.
            **options: Further ``F2WApplication`` keyword arguments (limits, admission, ...).
        """
        app = F2WApplication(settings=settings, retrieval=retrieval, chat_model=chat_model,
                             checkpointer=checkpointer, **options)
        return cls(app, owns_application=True, operations=operations)

    @property
    def application(self) -> F2WApplication:
        """The wrapped application service."""
        return self._app

    @property
    def operations(self) -> Optional["F2WOperations"]:
        """The shared operations service, or ``None`` when none was given."""
        return self._ops

    def _operations(self, method: str) -> "F2WOperations":
        if self._ops is None:
            from .modules.contracts.errors import ContractError, ErrorCode

            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                f"F2WClient.{method} needs the deployment's F2WOperations "
                                "(F2WClient(..., operations=...))")
        return self._ops

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

    # -- jobs and publication: forwarded to the shared F2WOperations (same policy as /v1) -----

    def submit_ingest(self, ctx: RequestContext, request: Any) -> Any:
        """Submit an ``ingest`` job (``F2WOperations.submit_job``; ``POST /v1/jobs`` with kind ``ingest``).

        ``request`` is an ``OperationInputs`` or its dict form. Returns the ``JobHandle``.
        """
        return self._operations("submit_ingest").submit_job(ctx, "ingest", request)

    def get_job(self, ctx: RequestContext, job_id: str) -> Any:
        """The job's ``JobHandle`` (``F2WOperations.get_job``; ``GET /v1/jobs/{id}``)."""
        return self._operations("get_job").get_job(ctx, job_id)

    def cancel_job(self, ctx: RequestContext, job_id: str) -> Any:
        """Cancel the job (``F2WOperations.cancel_job``; ``POST /v1/jobs/{id}/cancel``)."""
        return self._operations("cancel_job").cancel_job(ctx, job_id)

    def plan_publication(self, ctx: RequestContext, snapshot_id: str, target: Any) -> Any:
        """Submit a publication job that plans ``snapshot_id`` for ``target`` (H5 gate applies).

        Forwards to ``F2WOperations.submit_publication`` (``POST /v1/publications``) and returns
        its ``JobHandle``. The job computes the ``ChangePlan``; read it with
        ``client.operations.current_plan(ctx, job_id)`` once the job awaits review.
        """
        return self._operations("plan_publication").submit_publication(ctx, snapshot_id, target)

    def approve_plan(self, ctx: RequestContext, plan: Union[Any, str], *, job_id: str,
                     approver_credential: Any = None, expires_at: Any = None) -> Any:
        """Approve the publication job's current plan (``F2WOperations.approve_publication``).

        ``plan`` is the ``ChangePlan`` the reviewer was shown, or its ``plan_sha256``; another
        current plan is ``STALE_APPROVAL``. On ALS ``approver_credential`` is the approver's own
        Tiled credential (fresh role check). Returns ``{"approval": ..., "job": ...}`` exactly
        like ``POST /v1/publications/{job_id}/approval``.
        """
        plan_sha256 = plan if isinstance(plan, str) else plan.plan_sha256
        return self._operations("approve_plan").approve_publication(
            ctx, job_id, plan_sha256, approver_credential=approver_credential, expires_at=expires_at)

    # -- knowledge operations (T19E): forwarded to the shared F2WOperations (same policy as /v1) -----

    def run_knowledge_operation(self, ctx: RequestContext, operation: str, args: Mapping[str, Any]) -> ToolResult:
        """Run one T19C operation by Tool name (``F2WOperations.run_knowledge_operation``).

        Returns the bounded ``ToolResult`` the agent Tool returns: ``status`` ``ok`` for a complete
        scan, ``partial`` for a stopped one; refusals raise ``ContractError``.
        """
        return self._operations("run_knowledge_operation").run_knowledge_operation(ctx, operation, args)

    def graph_statistics(self, ctx: RequestContext, graph_id: str) -> ToolResult:
        """``kg_compute_statistics`` (``GET /v1/operations/statistics``)."""
        return self._operations("graph_statistics").graph_statistics(ctx, graph_id)

    def compare_graphs(self, ctx: RequestContext, graph_ids: Sequence[str]) -> ToolResult:
        """``kg_compare_snapshots`` (``GET /v1/operations/compare``)."""
        return self._operations("compare_graphs").compare_graphs(ctx, graph_ids)

    def paper_subgraph(self, ctx: RequestContext, graph_id: str, paper: str, *, max_nodes: Optional[int] = None,
                       max_edges: Optional[int] = None) -> ToolResult:
        """``kg_paper_subgraph`` (``GET /v1/operations/paper-subgraph``)."""
        return self._operations("paper_subgraph").paper_subgraph(ctx, graph_id, paper, max_nodes=max_nodes,
                                                                 max_edges=max_edges)

    def recent_entities(self, ctx: RequestContext, graph_id: str, *, since: Optional[str] = None,
                        limit: Optional[int] = None) -> ToolResult:
        """``kg_recent_entities`` (``GET /v1/operations/recent``; Tiled graphs only)."""
        return self._operations("recent_entities").recent_entities(ctx, graph_id, since=since, limit=limit)

    def audit_corpus(self, ctx: RequestContext, corpus: str, *, sections: Optional[Sequence[str]] = None
                     ) -> ToolResult:
        """``curate_audit_corpus`` (``GET /v1/operations/corpus-audit``)."""
        return self._operations("audit_corpus").audit_corpus(ctx, corpus, sections=sections)

    def submit_knowledge_operation(self, ctx: RequestContext, operation: str, args: Mapping[str, Any], *,
                                   corpus_scope: Optional[Sequence[str]] = None) -> Any:
        """Submit a ``knowledge_operation`` job (``POST /v1/operations/jobs``; ``ops.run``). Returns the handle."""
        return self._operations("submit_knowledge_operation").submit_knowledge_operation(
            ctx, operation, args, corpus_scope=corpus_scope)

    def knowledge_job_result(self, ctx: RequestContext, job_id: str) -> Any:
        """Handle and bounded report of a ``knowledge_operation`` job (``GET /v1/operations/jobs/{id}``)."""
        return self._operations("knowledge_job_result").knowledge_job_result(ctx, job_id)

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
