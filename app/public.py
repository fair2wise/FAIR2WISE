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
  ``F2WOperations`` (``/v1/operations/...``); the typed result is the agent Tool's;
- :meth:`F2WClient.cost_control`, :meth:`~F2WClient.set_cost_control`, :meth:`~F2WClient.bulk_review`,
  :meth:`~F2WClient.vocabulary_quality`, :meth:`~F2WClient.figure_scales`,
  :meth:`~F2WClient.figure_scale`, :meth:`~F2WClient.record_figure_scale` (T31) and
  :meth:`~F2WClient.folder_roots`, :meth:`~F2WClient.list_folder`, :meth:`~F2WClient.folder_index`
  (T32, ``:local`` only): forwarded unchanged to the same ``F2WOperations`` as the matching
  ``/v1`` routes;
- :meth:`F2WClient.search_literature`, :meth:`~F2WClient.resolve_work`,
  :meth:`~F2WClient.read_paper` and :meth:`~F2WClient.read_section` (T15 over T05): the
  ``lit_*`` tools of the application's ``LiteratureToolset`` run through the same
  authorizing executor the supervisor uses (capability, schema and deadline checks first;
  refusals come back as failed ``ToolResult``). They need ``literature=`` at construction;
- :meth:`F2WClient.get_evidence` and :meth:`~F2WClient.evidence_bundle` (T15 over T06): candidate
  evidence records of the application's evidence source, read with the T19C read capability
  (``chat.ask``); the bundle is the read-only :class:`~app.modules.evidence.bundle.LiteratureEvidenceBundle`.
  They need ``evidence=`` at construction;
- :meth:`F2WClient.publish_plan` (T15 over T10): hands an existing approval to the publication job
  (``F2WOperations.apply_approval``); the import runs in the job and its receipt is read from
  ``job_view``.

Results of the literature and evidence methods are agent-private typed results (D19): a
consumer showing them to a person first passes them through :meth:`F2WClient.disclose`, the
same ``project_api_response`` projection the ``/v1`` routes apply.

``query_knowledge`` and ``trace_provenance`` raise ``NotImplementedError``: their backing
(the K-12 ``kg_find_entities`` / ``kg_get_lineage`` literature tools of plan section 10) is
not ported and no addenda task schedules it yet (docs/migration/public_api_spec.md section 4).

Importing this module reads no environment or ``.env`` file, builds no
registry, model or client, and imports no torch, FAISS, Docling, pypdfium2, OpenAI or
LangGraph code. Construction opens no network connection.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .modules.application import DelegatedOutcome, F2WApplication
from .modules.contracts import RequestContext, ToolResult
from .modules.contracts.json_types import JsonValue
from .modules.security.authorization import DeploymentSettings

if TYPE_CHECKING:  # the operations service is imported only by callers that pass one
    from datetime import datetime

    from .api.operations import F2WOperations
    from .modules.agent_runtime.chat_model import ChatModel
    from .modules.agent_runtime.executor import ToolExecutor
    from .modules.contracts.changes import ApprovalRecord, ChangePlan
    from .modules.contracts.jobs import JobHandle
    from .modules.evidence.bundle import EvidenceSource, LiteratureEvidenceBundle
    from .modules.jobs.keys import OperationInputs
    from .modules.operations.papers import PaperSummaryTool
    from .modules.runtime_bridge.qa_tool import ReadOutcome
    from .modules.security.tiled_identity import TiledCredential

__all__ = ("F2WClient", "LiteratureAgent", "create_literature_agent")


def _pending(method: str, backing: str) -> NotImplementedError:
    return NotImplementedError(f"F2WClient.{method} is not implemented yet; it needs {backing} "
                               "(docs/migration/public_api_spec.md section 4)")


#: Plan section 10 skills whose donor tools (K-12) no merged task ported; no addenda task schedules them.
_K12_QUERY = ("the K-12 query_knowledge_graph tools (kg_find_entities, kg_get_summary, kg_read_paper), "
              "which T08 did not port and no plan_addenda task (A6/A7) schedules")
_K12_TRACE = ("the K-12 trace_provenance tools (kg_get_lineage, kg_get_connections) plus an execution-provenance "
              "lookup by provenance_id, which no merged or scheduled task (plan_addenda A6/A7) delivers")


class F2WClient:
    """In-process F2W client over one :class:`F2WApplication`.

    Build it with :meth:`from_settings` (it then owns the application and closes
    it) or wrap an existing application. Two clients share no registry, executor,
    ledger or checkpointer.

    ``operations`` is the deployment's shared :class:`~app.api.operations.F2WOperations`
    (the object the ``/v1`` routes call). The job and publication methods forward to it;
    without it they raise ``ContractError(UNSUPPORTED_CAPABILITY)``. Likewise the literature
    methods need the application's ``literature`` toolset and the evidence methods its
    ``evidence`` source (``F2WClient.from_settings(..., literature=..., evidence=...)``).
    """

    def __init__(self, application: F2WApplication, *, owns_application: bool = False,
                 operations: F2WOperations | None = None) -> None:
        """Wrap ``application``; ``owns_application`` makes :meth:`close` close it.

        Raises:
            TypeError: If ``application`` is not an ``F2WApplication``.
            ValueError: If ``operations`` is bound to different ``DeploymentSettings``.
        """
        if not isinstance(application, F2WApplication):
            raise TypeError("application must be an F2WApplication")
        if operations is not None and operations.settings != application.settings:
            raise ValueError("operations and application must share the same DeploymentSettings")
        self._app = application
        self._owns = owns_application
        self._ops = operations
        self._summary: Any | None = None  # (executor, tool), built on first use
        self._projector: Any | None = None  # DisclosureProjector, built on first use

    @classmethod
    def from_settings(
        cls,
        settings: DeploymentSettings,
        *,
        retrieval: object,
        chat_model: ChatModel | None = None,
        checkpointer: object = None,
        operations: F2WOperations | None = None,
        **options: Any,  # noqa: ANN401  # Any: forwarded verbatim to F2WApplication
    ) -> F2WClient:
        """Build a client from explicit settings (no ``.env`` or environment read).

        Args:
            settings: Validated startup ``DeploymentSettings``.
            retrieval: The retrieval service (``runtime_bridge.retrieval.LegacyRetrievalService``).
            chat_model: Optional LangChain chat model for delegated tasks.
            checkpointer: Optional LangGraph checkpointer for delegated threads.
            operations: Optional shared ``F2WOperations`` for the job and publication methods.
            **options: Further ``F2WApplication`` keyword arguments (``literature``, ``evidence``,
                ``knowledge``, limits, admission, ...).
        """
        app = F2WApplication(settings=settings, retrieval=retrieval, chat_model=chat_model,
                             checkpointer=checkpointer, **options)
        return cls(app, owns_application=True, operations=operations)

    @property
    def application(self) -> F2WApplication:
        """The wrapped application service."""
        return self._app

    @property
    def operations(self) -> F2WOperations | None:
        """The shared operations service, or ``None`` when none was given."""
        return self._ops

    def _operations(self, method: str) -> F2WOperations:
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

    def __enter__(self) -> F2WClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- request context ---------------------------------------------------------------

    def context_for(self, credential: TiledCredential | str | None = None, **kw: Any,  # noqa: ANN401  # Any: forwarded verbatim to F2WApplication.context
                    ) -> RequestContext:
        """Resolve a request context with the profile rules (local-operator on ``:local``)."""
        return self._app.context(credential, **kw)

    # -- implemented methods ---------------------------------------------------------------

    async def answer_from_knowledge(self, ctx: RequestContext, question: str,
                                    **kw: Any) -> ReadOutcome:  # noqa: ANN401  # Any: forwarded verbatim to F2WApplication.answer_from_knowledge
        """Existing F2W QA answer (``ReadOutcome``; ``.to_legacy()`` gives the legacy verdict)."""
        return await self._app.answer_from_knowledge(ctx, question, **kw)

    def answer_from_knowledge_sync(self, ctx: RequestContext, question: str,
                                   **kw: Any) -> ReadOutcome:  # noqa: ANN401  # Any: forwarded verbatim to F2WApplication.answer_from_knowledge_sync
        """Synchronous :meth:`answer_from_knowledge`; raises inside a running event loop."""
        return self._app.answer_from_knowledge_sync(ctx, question, **kw)

    def _summary_executor(self) -> tuple[ToolExecutor, PaperSummaryTool]:
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
        scope: object,
        *,
        max_terms: int | None = None,
        query: str = "",
        missing_topics: Sequence[str] | None = None,
        relevant_node_names: Sequence[str] | None = None,
    ) -> ToolResult:
        """Deterministic summary of one extracted paper (T19B ``lit_summarize_extracted_terms``).

        ``scope`` is an ``operations.papers.PaperScope`` chosen by trusted caller code; it is
        pinned for this call and never exposed as a model argument. Capability, schema and
        bound checks run first; refusals come back as a failed ``ToolResult``.
        """
        from .modules.operations.papers import TOOL_NAME, PaperScope, pinned_paper_scope

        if not isinstance(scope, PaperScope):
            raise TypeError("scope must be an operations.papers.PaperScope")
        args: dict[str, Any] = {}
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

    def submit_ingest(self, ctx: RequestContext, request: OperationInputs | Mapping[str, Any]) -> JobHandle:
        """Submit an ``ingest`` job (``F2WOperations.submit_job``; ``POST /v1/jobs`` with kind ``ingest``).

        ``request`` is an ``OperationInputs`` or its dict form. Returns the ``JobHandle``.
        """
        return self._operations("submit_ingest").submit_job(ctx, "ingest", request)

    def get_job(self, ctx: RequestContext, job_id: str) -> JobHandle:
        """The job's ``JobHandle`` (``F2WOperations.get_job``; ``GET /v1/jobs/{id}``)."""
        return self._operations("get_job").get_job(ctx, job_id)

    def cancel_job(self, ctx: RequestContext, job_id: str) -> JobHandle:
        """Cancel the job (``F2WOperations.cancel_job``; ``POST /v1/jobs/{id}/cancel``)."""
        return self._operations("cancel_job").cancel_job(ctx, job_id)

    def plan_publication(self, ctx: RequestContext, snapshot_id: str, target: str) -> JobHandle:
        """Submit a publication job that plans ``snapshot_id`` for ``target`` (H5 gate applies).

        Forwards to ``F2WOperations.submit_publication`` (``POST /v1/publications``) and returns
        its ``JobHandle``. The job computes the ``ChangePlan``; read it with
        ``client.operations.current_plan(ctx, job_id)`` once the job awaits review.
        """
        return self._operations("plan_publication").submit_publication(ctx, snapshot_id, target)

    def approve_plan(self, ctx: RequestContext, plan: ChangePlan | str, *, job_id: str,
                     approver_credential: TiledCredential | None = None,
                     expires_at: datetime | None = None) -> dict[str, Any]:
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

    def paper_subgraph(self, ctx: RequestContext, graph_id: str, paper: str, *, max_nodes: int | None = None,
                       max_edges: int | None = None) -> ToolResult:
        """``kg_paper_subgraph`` (``GET /v1/operations/paper-subgraph``)."""
        return self._operations("paper_subgraph").paper_subgraph(ctx, graph_id, paper, max_nodes=max_nodes,
                                                                 max_edges=max_edges)

    def recent_entities(self, ctx: RequestContext, graph_id: str, *, since: str | None = None,
                        limit: int | None = None) -> ToolResult:
        """``kg_recent_entities`` (``GET /v1/operations/recent``; Tiled graphs only)."""
        return self._operations("recent_entities").recent_entities(ctx, graph_id, since=since, limit=limit)

    def audit_corpus(self, ctx: RequestContext, corpus: str, *, sections: Sequence[str] | None = None
                     ) -> ToolResult:
        """``curate_audit_corpus`` (``GET /v1/operations/corpus-audit``)."""
        return self._operations("audit_corpus").audit_corpus(ctx, corpus, sections=sections)

    def submit_knowledge_operation(self, ctx: RequestContext, operation: str, args: Mapping[str, Any], *,
                                   corpus_scope: Sequence[str] | None = None) -> JobHandle:
        """Submit a ``knowledge_operation`` job (``POST /v1/operations/jobs``; ``ops.run``). Returns the handle."""
        return self._operations("submit_knowledge_operation").submit_knowledge_operation(
            ctx, operation, args, corpus_scope=corpus_scope)

    def knowledge_job_result(self, ctx: RequestContext, job_id: str) -> dict[str, Any]:
        """Handle and bounded report of a ``knowledge_operation`` job (``GET /v1/operations/jobs/{id}``)."""
        return self._operations("knowledge_job_result").knowledge_job_result(ctx, job_id)

    # -- T31/T32: cost control, bulk review, vocabulary quality, figure scales, folders (same policy as /v1) --

    def cost_control(self, ctx: RequestContext) -> dict[str, Any]:
        """The remote-cost switch and cap with the ledger spend (``GET /v1/curation/cost-control``)."""
        return self._operations("cost_control").cost_control(ctx)

    def set_cost_control(self, ctx: RequestContext, *, enabled: bool, budget_cap_usd: float | None) -> dict[str, Any]:
        """Set the remote-cost switch and cap (``PUT /v1/curation/cost-control``; human, audited)."""
        return self._operations("set_cost_control").set_cost_control(ctx, enabled=enabled,
                                                                     budget_cap_usd=budget_cap_usd)

    def bulk_review(self, ctx: RequestContext, decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """Review several cases at once (``POST /v1/curation/cases/bulk-review``); one result per case."""
        return self._operations("bulk_review").bulk_review(ctx, decisions)

    def vocabulary_quality(self, ctx: RequestContext, *, limit: int = 30) -> dict[str, Any]:
        """Newest-first vocabulary-quality snapshots (``GET /v1/vocabulary/quality``)."""
        return self._operations("vocabulary_quality").vocabulary_quality(ctx, limit=limit)

    def figure_scales(self, ctx: RequestContext, *, corpus: str | None = None, work_id: str | None = None,
                      figure_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Human-read figure-scale records in the caller's scope (``GET /v1/figures/scales``)."""
        return self._operations("figure_scales").list_figure_scales(ctx, corpus=corpus, work_id=work_id,
                                                                    figure_id=figure_id, limit=limit)

    def figure_scale(self, ctx: RequestContext, record_id: str) -> dict[str, Any]:
        """One figure-scale record (``GET /v1/figures/scales/{id}``)."""
        return self._operations("figure_scale").get_figure_scale(ctx, record_id)

    def record_figure_scale(self, ctx: RequestContext, **fields: Any,  # noqa: ANN401  # Any: forwarded verbatim to F2WOperations.record_figure_scale
                            ) -> dict[str, Any]:
        """Record a human-read scale (``POST /v1/figures/scales``; human, ``curation.approve``, audited).

        ``fields`` are those of ``F2WOperations.record_figure_scale``: ``corpus``, ``work_id``,
        ``figure_id``, ``panel_index``, ``nm_per_px`` and the optional bar lengths, source and note.
        """
        return self._operations("record_figure_scale").record_figure_scale(ctx, **fields)

    def folder_roots(self, ctx: RequestContext) -> dict[str, Any]:
        """The configured folder root labels (``GET /v1/folders/roots``; ``:local`` only)."""
        return self._operations("folder_roots").folder_roots(ctx)

    def list_folder(self, ctx: RequestContext, root: str, path: str | None = None, *, limit: int = 1000) -> dict[str, Any]:
        """Entries of one folder under a configured root (``GET /v1/folders/list``; ``:local`` only)."""
        return self._operations("list_folder").list_folder(ctx, root, path, limit=limit)

    def folder_index(self, ctx: RequestContext, root: str, path: str | None = None, *, limit: int = 500) -> dict[str, Any]:
        """Bounded document inventory under one folder (``GET /v1/folders/index``; ``:local`` only)."""
        return self._operations("folder_index").folder_index(ctx, root, path, limit=limit)

    # -- literature (T15 over the T05 lit_* tools, through the application's executor) ----------------

    def _literature_tool(self, method: str, name: str) -> str:
        if self._app.literature is None or name not in self._app.registry:
            from .modules.contracts.errors import ContractError, ErrorCode

            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                f"F2WClient.{method} needs the deployment's LiteratureToolset "
                                "(F2WClient.from_settings(..., literature=...))")
        return name

    def _run_tool(self, ctx: RequestContext, name: str, args: Mapping[str, Any]) -> ToolResult:
        """Run one registered tool through this application's authorizing executor (blocking)."""
        return self._app.executor.execute(ctx, name, dict(args))

    def search_literature(self, ctx: RequestContext, query: str, limit: int = 20, *, technique: str = "",
                          material: str = "", year_from: int | None = None, year_to: int | None = None,
                          ) -> ToolResult:
        """``lit_search_papers``: external candidates (DOIs), never ingested Works, never evidence.

        ``limit`` above the tool's bound (25) is refused as ``INVALID``; ``EMPTY`` means the provider
        answered with nothing, ``UNAVAILABLE`` that it could not answer (``retryable``).
        """
        name = self._literature_tool("search_literature", "lit_search_papers")
        args: dict[str, Any] = {"query": query, "max_results": limit}
        if technique:
            args["technique"] = technique
        if material:
            args["material"] = material
        if year_from is not None:
            args["year_from"] = year_from
        if year_to is not None:
            args["year_to"] = year_to
        return self._run_tool(ctx, name, args)

    def resolve_work(self, ctx: RequestContext, identifier: str) -> ToolResult:
        """Resolve a DOI or arXiv identifier to its OpenAlex work record (``lit_get_papers_by_doi``).

        ``identifier`` may be a bare DOI, a ``doi:``/``https://doi.org/`` form, ``arXiv:<id>`` or an
        arXiv URL (resolved through its DataCite DOI ``10.48550/arXiv.<id>``). ``EMPTY`` means the
        provider knows no such work; ``INVALID`` an unrecognised identifier. OpenAlex ``W`` ids and
        titles are not resolvable through the ``lit_*`` tools (use :meth:`search_literature`).
        """
        from .modules.literature.identifiers import UnrecognisedIdentifier, classify_identifier
        from .modules.literature.legacy import doi_key, doi_only

        name = self._literature_tool("resolve_work", "lit_get_papers_by_doi")
        if doi_key(identifier):  # the reader's DOI rule (``10.<prefix>/<suffix>``, URL or ``doi:`` forms)
            parsed = {"kind": "doi", "value": doi_only(identifier)}
        else:
            try:
                parsed = classify_identifier(identifier)
            except UnrecognisedIdentifier:
                parsed = {"kind": "unknown", "value": identifier}
        if parsed["kind"] == "doi":
            doi = parsed["value"]
        elif parsed["kind"] == "arxiv":
            doi = f"10.48550/arXiv.{parsed['value']}"
        else:
            from .modules.contracts.errors import ErrorCode
            from .modules.contracts.tools import ToolStatus

            reason = ("OpenAlex work ids are not resolvable through the lit_* tools"
                      if parsed["kind"] == "openalex" else "not a DOI or arXiv identifier")
            return ToolResult(tool=name, status=ToolStatus.INVALID, error_code=ErrorCode.INVALID_INPUT,
                              warnings=(f"{reason}; pass a DOI or use search_literature",))
        return self._run_tool(ctx, name, {"dois": doi})

    def read_paper(self, ctx: RequestContext, work_id: str) -> ToolResult:
        """``lit_read_paper``: section names and counts of a paper by DOI, never text.

        The read needs the toolset's trusted source-access scope (``DENIED`` without one) and
        acquisition needs ``ingest.acquire``. The result is agent-private (D19): pass it through
        :meth:`disclose` before showing it to a person.
        """
        return self._run_tool(ctx, self._literature_tool("read_paper", "lit_read_paper"), {"doi": work_id})

    def read_section(self, ctx: RequestContext, work_id: str, section: str) -> ToolResult:
        """``lit_read_paper_section``: one bounded section with its page and ``source_version`` locator.

        ``NOT_FOUND`` carries ``available_sections``. Agent-private (D19), as :meth:`read_paper`.
        """
        return self._run_tool(ctx, self._literature_tool("read_section", "lit_read_paper_section"),
                              {"doi": work_id, "section": section})

    # -- evidence (T15 over the T06 candidate records) ------------------------------------------------

    def _evidence_source(self, method: str) -> EvidenceSource:
        source = self._app.evidence
        if source is None or not hasattr(source, "evidence") or not hasattr(source, "quantities"):
            from .modules.contracts.errors import ContractError, ErrorCode

            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                f"F2WClient.{method} needs the deployment's candidate evidence source "
                                "(F2WClient.from_settings(..., evidence=InMemoryEvidenceSink-like))")
        return source

    def get_evidence(self, ctx: RequestContext, evidence_ids: Sequence[str]) -> ToolResult:
        """The ``EvidenceRef`` of each candidate evidence id (T19C read capability ``chat.ask``).

        ``OK`` when every id is known, ``PARTIAL`` when some are missing (named in ``warnings`` and
        ``data["missing"]``), ``EMPTY`` when none is. ``evidence`` holds the refs in request order;
        ``data["records"]`` the full candidate records. Quotes inside are agent-private (D19).
        """
        from .modules.contracts.tools import ToolStatus
        from .modules.security.authorization import require

        source = self._evidence_source("get_evidence")
        require(ctx, "chat.ask")
        wanted = [str(e) for e in evidence_ids]
        found = [source.evidence[e] for e in wanted if e in source.evidence]
        missing = [e for e in wanted if e not in source.evidence]
        tool = "evidence_lookup"
        if not found:
            return ToolResult(tool=tool, status=ToolStatus.EMPTY,
                              warnings=tuple(f"unknown evidence id {e}" for e in missing))
        return ToolResult(
            tool=tool, status=ToolStatus.PARTIAL if missing else ToolStatus.OK,
            data={"requested": len(wanted), "found": len(found), "missing": missing,
                  "records": [r.to_dict() for r in found], "status": "candidate"},
            evidence=tuple(r.ref for r in found),
            warnings=tuple(f"unknown evidence id {e}" for e in missing),
        )

    def evidence_bundle(self, ctx: RequestContext, work_id: str, *, include_quotes: bool = False) -> LiteratureEvidenceBundle:
        """The read-only literature-evidence bundle of one Work (T15).

        Returns a :class:`~app.modules.evidence.bundle.LiteratureEvidenceBundle` (JSON through
        ``to_json``/``from_json``): source version and checksum, reported quantities with raw and
        normalized units, evidence ids with locators, and caveats. Quotes are included only with
        ``include_quotes=True`` and stay agent-private (D19). An unknown Work gives an empty bundle
        (``bundle.empty``), never an error: absence of candidates is a fact about the records.
        """
        from .modules.evidence.bundle import build_bundle
        from .modules.security.authorization import require

        source = self._evidence_source("evidence_bundle")
        require(ctx, "chat.ask")
        return build_bundle(work_id, source.evidence, source.quantities, include_quotes=include_quotes)

    # -- disclosure (D19) ------------------------------------------------------------------------------

    def disclose(self, payload: object) -> JsonValue:
        """Project a result for a person the way the ``/v1`` routes do (``project_api_response``).

        ``payload`` may be a ``ToolResult``, a bundle or any JSON-ready value; Pydantic models are
        dumped first. Private keys (source text, snippets, paths, secrets) are dropped, absolute
        paths become opaque ids and strings are clipped to the B10 bound. No quote is confirmed
        verbatim here (that needs the turn's retrieved regions, which the SDK does not hold).
        """
        if self._projector is None:
            from .modules.security.disclosure import DisclosureProjector

            self._projector = DisclosureProjector()
        body = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        return self._projector.project_api_response(body)

    # -- publication: apply an existing approval -------------------------------------------------------

    def publish_plan(self, ctx: RequestContext, plan: ChangePlan, approval: ApprovalRecord) -> JobHandle:
        """Hand ``approval`` (a ``create_approval`` record naming the job) to the publication job.

        Forwards to ``F2WOperations.apply_approval``: ``approval.authorizes(plan)`` is checked, then the
        job re-checks the human caller, ``curation.approve``, scope and staleness and is re-queued. The
        graph write happens in the job; read its ``ImportReceipt`` id from
        ``client.operations.job_view(ctx, handle.job_id)`` once the job has succeeded.
        """
        return self._operations("publish_plan").apply_approval(ctx, plan, approval)

    # -- specified, not implemented yet ------------------------------------------------------

    def query_knowledge(self, ctx: RequestContext, question: str, graphs: Sequence[str] = ()) -> ToolResult:
        """Direct graph retrieval without a planning model: pending (see :data:`_K12_QUERY`).

        ``answer_from_knowledge`` is the existing direct read (it runs the QA judge model);
        the T19C operations (:meth:`paper_subgraph`, ...) are the graph reads available today.
        """
        raise _pending("query_knowledge", _K12_QUERY)

    def trace_provenance(self, ctx: RequestContext, record_id: str) -> ToolResult:
        """Evidence versus execution provenance of one record: pending (see :data:`_K12_TRACE`).

        :meth:`get_evidence` answers the evidence side for candidate evidence ids today.
        """
        raise _pending("trace_provenance", _K12_TRACE)


class LiteratureAgent:
    """Delegated literature tasks through the client's LangGraph supervisor."""

    def __init__(self, client: F2WClient, *, owns_client: bool = False) -> None:
        """Wrap ``client``; ``owns_client`` makes :meth:`close` close it.

        Raises:
            ValueError: If the client's application has no supervisor.
        """
        if client.application.supervisor is None:
            raise ValueError("the client's application has no supervisor; build it with a chat_model")
        self.client = client
        self._owns = owns_client

    def close(self) -> None:
        """Close the client if this agent's factory built it."""
        if self._owns:
            self.client.close()

    def __enter__(self) -> LiteratureAgent:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    async def delegate(
        self, ctx: RequestContext, task: str, *, thread_id: str, scope: frozenset[str] | None = None,
    ) -> DelegatedOutcome:
        """Run one delegated task on ``thread_id`` (``F2WApplication.delegate``)."""
        return await self.client.application.delegate(ctx, task, thread_id=thread_id, scope=scope)


def create_literature_agent(
    *,
    client: F2WClient | None = None,
    settings: DeploymentSettings | None = None,
    retrieval: object = None,
    chat_model: ChatModel | None = None,
    checkpointer: object = None,
    sync_model: object = None,
    async_model: object = None,
    **options: Any,  # noqa: ANN401  # Any: forwarded F2WApplication keyword arguments
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
