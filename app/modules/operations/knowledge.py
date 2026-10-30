"""One bundle of the T19C Knowledge Operations for every access surface (task T19E).

:class:`KnowledgeOperations` holds the four graph Tools (:class:`~.graph.GraphOperationsTool`)
and, optionally, the corpus audit (:class:`~.corpus.CorpusAuditTool`), and offers them to:

* the agent runtime: :meth:`KnowledgeOperations.registrations` and :meth:`~KnowledgeOperations.skills`
  feed ``runtime_bridge.bootstrap.build_read_registry(knowledge=...)`` the way T19B's
  ``paper_summary`` and T05's ``literature`` opt in; the supervisor then sees
  ``kg_compute_statistics``, ``kg_compare_snapshots``, ``kg_paper_subgraph``,
  ``kg_recent_entities`` and ``curate_audit_corpus`` as ordinary read Tools;
* the shared service (:class:`app.api.operations.F2WOperations`), the ``/v1/operations`` routes,
  the SDK (:class:`app.public.F2WClient`) and the operations CLI: :meth:`~KnowledgeOperations.result`
  returns exactly the bounded :class:`~app.modules.contracts.ToolResult` the agent Tool returns
  (same ``data``, same ``status``: ``PARTIAL`` for a stopped scan, never ``OK``);
* the job runner: :attr:`~KnowledgeOperations.dispatch` is the :data:`~.reports.OperationFn`
  ``reports.register_operation_jobs`` wraps as the ``knowledge_operation`` job kind.

There is one implementation per operation (T19C); this module adds no metric, no
population rule and no model call. Reads need ``chat.ask`` (T19C contract); a scan is
bounded by the suites' ``max_records`` and the request deadline; the D19 guard
(:func:`~.reports.check_no_source_material`) runs inside every report.

**Graph selection.** :class:`DynamicJsonGraphs` is a JSON GraphPort whose graph list is
re-read from a provider on every call, so the agent follows the caller's current graph
selection (T25D: the legacy UI passes the active runtime's JSON graph files) and the API
follows the deployment's ``storage/kg`` directory. A graph outside the current list is
``NOT_FOUND``; graphs are never merged.

Importing this module has no side effects.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ..contracts import ContractError, ErrorCode, RequestContext, ToolResult, ToolSpec
from ..security.authorization import require
from .corpus import AUDIT_LITERATURE_CORPUS_SKILL, CorpusAuditTool, CorpusResolver, CorpusSource
from .graph import ANALYZE_KNOWLEDGE_GRAPH_SKILL, GraphOperationsTool
from .reports import (
    OPERATION_JOB_KIND,
    OperationFn,
    OperationReport,
    ScanControl,
    operation_dispatch,
    report_to_result,
    store_report_artifact,
)

#: Capability every Knowledge Operation read needs (T19C contract).
READ_CAPABILITY = "chat.ask"

GraphFilesProvider = Callable[[], Sequence[Union[str, Path]]]


class DynamicJsonGraphs:
    """JSON GraphPort over whatever graph files ``provider`` returns right now.

    Args:
        provider: Returns the current JSON graph paths (the caller's selection, or a
            directory listing). Called on every :meth:`graph_ids` and :meth:`snapshot`;
            the backend is rebuilt only when the list changes, so loaded snapshots are
            reused while the selection is stable.
    """

    def __init__(self, provider: GraphFilesProvider) -> None:
        self._provider = provider
        self._files: Tuple[str, ...] = ()
        self._backend: Any = None
        self._lock = threading.Lock()

    def _current(self) -> Any:
        from ..graph.backends.json import JsonGraphBackend

        files = tuple(str(Path(p)) for p in self._provider() if p)
        with self._lock:
            if self._backend is None or files != self._files:
                self._backend = JsonGraphBackend(list(files))
                self._files = files
            return self._backend

    def capabilities(self) -> Any:
        """The JSON backend's capabilities (complete listing, snapshot-isolated)."""
        return self._current().capabilities()

    def graph_ids(self) -> List[str]:
        """Graph ids of the current selection."""
        return self._current().graph_ids()

    def snapshot(self, ctx: RequestContext, graph_id: str) -> Any:
        """The loaded snapshot of ``graph_id`` (scope-checked; ``NOT_FOUND`` outside the selection)."""
        return self._current().snapshot(ctx, graph_id)

    def reload(self) -> None:
        """Drop the loaded snapshots; the next call reads the files again."""
        with self._lock:
            self._backend = None
            self._files = ()


def storage_graph_files(root: Union[str, Path]) -> List[str]:
    """``<root>/storage/kg/*.json``, sorted by name (the legacy ``list_storage_kg_json_files`` rule)."""
    kg_dir = Path(root) / "storage" / "kg"
    if not kg_dir.is_dir():
        return []
    return [str(p) for p in sorted(kg_dir.glob("*.json"), key=lambda p: p.name.lower())]


def directory_corpus_resolver(directory: Union[str, Path]) -> CorpusResolver:
    """Resolve a corpus name to ``<directory>/<name>.json`` (the extracted-terms files).

    Names are file stems (``extracted_terms_xray_papers_cborg_chat``); nothing outside
    ``directory`` can be named (no separators pass the Tool's name pattern, and the
    resolved path must stay inside the directory). A non-empty ``ctx.corpus_scope`` is
    honoured as ``mapping_corpus_resolver`` does (``DENIED`` outside it).
    """
    base = Path(directory)

    def resolve(ctx: RequestContext, name: str) -> Optional[CorpusSource]:
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        path = (base / f"{name}.json")
        try:
            path.resolve().relative_to(base.resolve())
        except ValueError:
            return None
        if not path.is_file():
            return None
        if ctx.corpus_scope and name not in ctx.corpus_scope:
            raise ContractError(ErrorCode.DENIED, f"corpus {name!r} is outside the request scope")
        return CorpusSource(name, str(path))

    return resolve


class KnowledgeOperations:
    """The T19C operations bound to one graph port and one corpus resolver.

    Args:
        graph: The graph suite (``GraphOperationsTool`` over the deployment's GraphPort).
        corpus: The corpus audit suite, or ``None`` when no corpus is configured.
        artifact_store: Optional ``DocumentStore`` for the private full report of a direct call
            (jobs pass their own through ``register_operation_jobs``).
    """

    def __init__(self, graph: GraphOperationsTool, corpus: Optional[CorpusAuditTool] = None, *,
                 artifact_store: Any = None) -> None:
        if not isinstance(graph, GraphOperationsTool):
            raise TypeError("graph must be an operations.graph.GraphOperationsTool")
        if corpus is not None and not isinstance(corpus, CorpusAuditTool):
            raise TypeError("corpus must be an operations.corpus.CorpusAuditTool or None")
        self.graph = graph
        self.corpus = corpus
        self.artifact_store = artifact_store
        suites: List[Any] = [graph] + ([corpus] if corpus is not None else [])
        self._dispatch: OperationFn = operation_dispatch(*suites)
        self._names: Tuple[str, ...] = tuple(n for s in suites for n in s.operation_names())

    # -- construction helpers -----------------------------------------------------------------

    @classmethod
    def from_storage(cls, root: Union[str, Path], *, artifact_store: Any = None,
                     graph_max_records: int = 500_000) -> "KnowledgeOperations":
        """Operations over ``<root>/storage/kg`` (graphs) and ``<root>/storage/terminology`` (corpora).

        The graph list is re-read on every call, so a graph added to ``storage/kg`` while the
        server runs is visible without a restart (the legacy UI lists the directory the same way).
        """
        root = Path(root)
        graph = GraphOperationsTool(DynamicJsonGraphs(lambda: storage_graph_files(root)),
                                    max_records=graph_max_records)
        corpus = CorpusAuditTool(directory_corpus_resolver(root / "storage" / "terminology"))
        return cls(graph, corpus, artifact_store=artifact_store)

    @classmethod
    def from_graph_files(cls, provider: GraphFilesProvider, *, corpus: Optional[CorpusAuditTool] = None,
                         artifact_store: Any = None, graph_max_records: int = 500_000) -> "KnowledgeOperations":
        """Operations following a caller-supplied graph selection (T25D: the active runtime's files)."""
        graph = GraphOperationsTool(DynamicJsonGraphs(provider), max_records=graph_max_records)
        return cls(graph, corpus, artifact_store=artifact_store)

    # -- agent runtime -------------------------------------------------------------------------

    def operation_names(self) -> Tuple[str, ...]:
        """Tool names this bundle serves."""
        return self._names

    def specs(self) -> List[ToolSpec]:
        """The Tool specs (graph suite first, then the corpus audit)."""
        out = list(self.graph.specs)
        if self.corpus is not None:
            out.append(self.corpus.spec)
        return out

    def registrations(self) -> List[Tuple[ToolSpec, Callable[[Any], ToolResult]]]:
        """``(spec, fn)`` pairs for ``build_registry(extra=...)``."""
        out = list(self.graph.registrations())
        if self.corpus is not None:
            out.append(self.corpus.registration())
        return out

    def skills(self) -> List[Any]:
        """The skills covering the registered Tools."""
        out = [ANALYZE_KNOWLEDGE_GRAPH_SKILL]
        if self.corpus is not None:
            out.append(AUDIT_LITERATURE_CORPUS_SKILL)
        return out

    # -- shared service, routes, SDK, CLI --------------------------------------------------------

    @property
    def dispatch(self) -> OperationFn:
        """``(ctx, operation, args, control) -> OperationReport`` for the job runner."""
        return self._dispatch

    def compute(self, ctx: RequestContext, operation: str, args: Mapping[str, Any],
                control: Optional[ScanControl] = None) -> OperationReport:
        """Run one operation by Tool name and return its full report.

        Raises:
            ContractError: ``DENIED`` without ``chat.ask`` or outside the scope; ``INVALID_INPUT``
                for an unknown operation or bad arguments; ``NOT_FOUND`` for an unconfigured graph
                or corpus; ``UNAVAILABLE`` / ``UNSUPPORTED_CAPABILITY`` from the backend.
        """
        require(ctx, READ_CAPABILITY)
        self.validate(operation, args)
        return self._dispatch(ctx, operation, dict(args), control)

    def result(self, ctx: RequestContext, operation: str, args: Mapping[str, Any], *,
               control: Optional[ScanControl] = None, artifact_store: Any = None) -> ToolResult:
        """The bounded typed result of one operation: what the agent Tool returns for the same call.

        ``status`` is ``OK`` for a complete scan and ``PARTIAL`` for a stopped one; refusals and
        failures raise ``ContractError`` (the routes map them to HTTP codes, the CLI to exit 2).
        """
        report = self.compute(ctx, operation, args, control)
        store = artifact_store if artifact_store is not None else self.artifact_store
        ref = store_report_artifact(store, ctx, report)
        return report_to_result(operation, report, artifact_refs=(ref,) if ref else ())

    def validate(self, operation: str, args: Mapping[str, Any]) -> None:
        """Schema-check ``args`` as the executor does (the direct surfaces have no executor).

        Raises:
            ContractError: ``INVALID_INPUT`` for an unknown operation or arguments outside the Tool's schema.
        """
        from ..agent_runtime.executor import _validate as validate_schema

        spec = next((s for s in self.specs() if s.name == operation), None)
        if spec is None:
            raise ContractError(ErrorCode.INVALID_INPUT, f"unknown operation {operation!r}")
        problem = validate_schema(spec.input_schema, dict(args))
        if problem:
            raise ContractError(ErrorCode.INVALID_INPUT, f"invalid arguments for {operation}: {problem}")


def knowledge_job_context(settings: Any, *, identity_port: Any = None,
                          credential_lookup: Optional[Callable[[str], Any]] = None,
                          clock: Optional[Callable[[], Any]] = None) -> Callable[[Any], RequestContext]:
    """``context_for_job`` for :func:`~.reports.register_operation_jobs` (trusted wiring).

    On ``:local`` the worker carries the local operator. On ALS it re-resolves the initiating
    subject's credential through ``credential_lookup(job.created_by)`` with a fresh role check;
    a missing or revoked credential is ``DENIED`` (the job fails closed, AUD-01).
    """
    from datetime import datetime, timezone

    from ..contracts.context import ActorKind
    from ..security.authorization import local_operator_context, resolve_context, worker_context

    def context_for_job(job: Any) -> RequestContext:
        if settings.is_local:
            return local_operator_context(settings, request_id=job.job_id, actor_kind=ActorKind.WORKER,
                                          run_id=job.job_id)
        credential = credential_lookup(job.created_by) if credential_lookup is not None else None
        if credential is None:
            raise ContractError(ErrorCode.DENIED, "the initiating subject has no live credential")
        ctx = resolve_context(settings, credential, request_id=job.job_id, identity_port=identity_port,
                              clock=clock or (lambda: datetime.now(timezone.utc)), require_fresh=True)
        return worker_context(ctx, run_id=job.job_id)

    return context_for_job


def job_params(operation: str, args: Mapping[str, Any]) -> Dict[str, Any]:
    """``inputs.params`` of a :data:`~.reports.OPERATION_JOB_KIND` job."""
    return {"operation": operation, "args": dict(args)}


def job_scope(args: Mapping[str, Any]) -> Tuple[str, ...]:
    """The corpora an operation job touches, derived from its arguments (graph ids, corpus name).

    This is the job's ``corpus_scope``: ``JobService`` checks it against the caller's scope on
    ALS exactly as ``graph.ports.require_scope`` checks each graph read (T19C).
    """
    names: List[str] = []
    for key in ("graph_id", "corpus"):
        value = args.get(key)
        if isinstance(value, str) and value and value not in names:
            names.append(value)
    for value in args.get("graph_ids") or []:
        if isinstance(value, str) and value and value not in names:
            names.append(value)
    return tuple(names)


__all__: Tuple[str, ...] = (
    "DynamicJsonGraphs", "KnowledgeOperations", "OPERATION_JOB_KIND", "READ_CAPABILITY",
    "directory_corpus_resolver", "job_params", "job_scope", "knowledge_job_context", "storage_graph_files",
)
