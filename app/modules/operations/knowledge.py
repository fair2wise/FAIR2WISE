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

**Tiled graphs on every profile (owner decision D3.17, O-X4, 2026-10-30).**
:class:`CompositeGraphPort` serves the JSON graphs *and* the deployment's Tiled graph
backend behind one GraphPort: ``graph_ids()`` lists the JSON ids followed by the graph ids
discovered in Tiled (the ``f2w_graph_id`` marker of owned literature entities, see
:mod:`app.modules.graph.identity_adapter`), each id is routed to the backend that holds it,
and ``capabilities(graph_id)`` is the routed backend's. Discovery is a paged GraphQL scan
cached for :data:`TILED_DISCOVERY_TTL` seconds and refreshed by ``reload()``. On a
collision the JSON graph wins and the Tiled copy is shadowed (a warning names it): T08B
publishes a JSON graph to Tiled under the same graph id, so a collision is the same graph
in two backends and the snapshot-isolated JSON file is the exact one; no prefix scheme is
introduced. An unreachable or unconfigured Tiled never breaks the JSON graphs: its ids are
absent, and the reason is recorded once (:attr:`CompositeGraphPort.warnings`, plus one log
line). :meth:`KnowledgeOperations.from_deployment` builds this bundle from the trusted
startup settings: the managed loopback server on ``:local`` (writer key read from its
secret file per request, never held or logged) or the fixed external target on ALS
(writer secret file per request). Reads only: no mutation is reachable through the
operations (the Tiled adapter refuses every write).

Importing this module has no side effects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, cast
from urllib.parse import urlsplit

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
#: Seconds a Tiled graph-id discovery stays valid before the next ``graph_ids()`` re-scans.
TILED_DISCOVERY_TTL = 60.0
#: Entities one discovery scan reads at most; beyond it the listing is partial (warned once).
TILED_DISCOVERY_MAX_SCAN = 100_000

_LOG = logging.getLogger(__name__)
#: Discovery reads only what ``literature_entity_key`` needs (owner marker, graph id, URI).
_DISCOVER_Q = "query($l: Int!, $o: Int!) { entities(limit: $l, offset: $o) { uri properties } }"

GraphFilesProvider = Callable[[], Sequence[str | Path]]
#: Returns the Tiled GraphPort to read, or ``None`` when no server is reachable right now.
TiledBackendFactory = Callable[[], Any | None]


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
        self._files: tuple[str, ...] = ()
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

    def graph_ids(self) -> list[str]:
        """Graph ids of the current selection."""
        return cast(list[str], self._current().graph_ids())  # Any: backend is created lazily

    def snapshot(self, ctx: RequestContext, graph_id: str) -> Any:
        """The loaded snapshot of ``graph_id`` (scope-checked; ``NOT_FOUND`` outside the selection)."""
        return self._current().snapshot(ctx, graph_id)

    def reload(self) -> None:
        """Drop the loaded snapshots; the next call reads the files again."""
        with self._lock:
            self._backend = None
            self._files = ()


class CompositeGraphPort:
    """One GraphPort over the JSON graphs and the deployment's Tiled graph backend (D3.17).

    Args:
        json_port: The JSON GraphPort (:class:`DynamicJsonGraphs` or a ``JsonGraphBackend``).
        tiled: A Tiled GraphPort (``TiledGraphBackend``), a :data:`TiledBackendFactory` that
            resolves one on demand (``None`` while no server is reachable), or ``None`` for a
            JSON-only bundle. A factory is re-run after a failed read and after
            :meth:`reload`, so a managed server started later is picked up.
        ttl: Seconds the discovered Tiled graph ids stay cached.
        max_scan: Entities one discovery scan reads at most.
        clock: Monotonic clock (tests inject one).

    Routing: a graph id in the JSON list is read from JSON; otherwise one discovered in
    Tiled is read from Tiled; anything else is ``NOT_FOUND``. A Tiled id equal to a JSON id
    is shadowed by the JSON graph (warned once; see the module docstring).
    """

    def __init__(self, json_port: Any, tiled: Any | TiledBackendFactory | None = None, *,
                 ttl: float = TILED_DISCOVERY_TTL, max_scan: int = TILED_DISCOVERY_MAX_SCAN,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._json = json_port
        if tiled is None:
            self._factory: TiledBackendFactory | None = None
        elif callable(tiled) and not hasattr(tiled, "capabilities"):
            self._factory = tiled
        else:
            fixed = tiled
            self._factory = lambda: fixed
        self._ttl = float(ttl)
        self._max_scan = max(1, int(max_scan))
        self._clock = clock
        self._lock = threading.RLock()
        self._backend: Any = None
        self._ids: tuple[str, ...] = ()
        self._expires: float | None = None
        self._warnings: list[str] = []

    # -- diagnostics ----------------------------------------------------------------------------

    @property
    def warnings(self) -> tuple[str, ...]:
        """Reasons recorded (once each) for Tiled graphs being absent or partially listed."""
        return tuple(self._warnings)

    @property
    def json_port(self) -> Any:
        """The JSON GraphPort."""
        return self._json

    def record_warning(self, message: str) -> None:
        """Record ``message`` once (and log it once); repeats are dropped."""
        with self._lock:
            if message in self._warnings:
                return
            self._warnings.append(message)
        _LOG.warning("knowledge operations: %s", message)

    # -- Tiled side -------------------------------------------------------------------------------

    def tiled_backend(self) -> Any | None:
        """The Tiled GraphPort, resolving it through the factory when needed (``None`` if absent)."""
        with self._lock:
            if self._backend is not None or self._factory is None:
                return self._backend
            try:
                backend = self._factory()
            except Exception as exc:  # noqa: BLE001 - a Tiled problem never breaks the JSON graphs
                from ..agent_runtime.executor import is_control_flow

                if is_control_flow(exc):
                    raise
                self.record_warning(f"Tiled graph backend unavailable ({type(exc).__name__}): {exc}")
                return None
            if backend is None:
                self.record_warning("no Tiled graph server is reachable; Tiled graphs are not listed")
            self._backend = backend
            return backend

    def _discover(self) -> list[str]:
        backend = self.tiled_backend()
        if backend is None:
            return []
        from ..graph.identity_adapter import literature_entity_key

        page = max(1, int(getattr(backend, "page_size", 500)))
        found: list[str] = []
        offset = 0
        try:
            while True:
                limit = min(page, self._max_scan - offset)
                if limit <= 0:
                    self.record_warning(f"Tiled graph discovery stopped after {self._max_scan} entities; "
                                        "graphs beyond that point are not listed")
                    break
                rows = backend.client.query(_DISCOVER_Q, {"l": limit, "o": offset}).get("entities") or []
                for row in rows:
                    key = literature_entity_key(row)
                    if key is not None and key[0] not in found:
                        found.append(key[0])
                if len(rows) < limit:
                    break
                offset += len(rows)
        except ContractError as exc:
            self.record_warning(f"Tiled graphs are not listed ({exc.code.value}): {exc}")
            self._backend = None  # re-resolve next time (a managed server may have moved)
            return []
        return sorted(found)

    def tiled_graph_ids(self) -> list[str]:
        """Graph ids discovered in Tiled (cached for ``ttl`` seconds; empty when Tiled is absent)."""
        with self._lock:
            now = self._clock()
            if self._expires is None or now >= self._expires:
                self._ids = tuple(self._discover())
                self._expires = now + self._ttl
            return list(self._ids)

    # -- GraphPort ---------------------------------------------------------------------------------

    def graph_ids(self) -> list[str]:
        """JSON graph ids, then the Tiled graph ids not shadowed by a JSON graph."""
        json_ids = list(self._json.graph_ids())
        tiled_ids = self.tiled_graph_ids()
        shadowed = [g for g in tiled_ids if g in json_ids]
        if shadowed:
            self.record_warning(f"Tiled graphs {shadowed} are shadowed by JSON graphs of the same id "
                                "(JSON is read; the Tiled copy is not listed)")
        return json_ids + [g for g in tiled_ids if g not in json_ids]

    def resolve(self, graph_id: str) -> Any:
        """The backend holding ``graph_id`` (JSON first).

        Raises:
            ContractError: ``NOT_FOUND`` when neither backend lists the graph.
        """
        if graph_id in self._json.graph_ids():
            return self._json
        if graph_id in self.tiled_graph_ids():
            backend = self.tiled_backend()
            if backend is not None:
                return backend
        raise ContractError(ErrorCode.NOT_FOUND, f"graph {graph_id!r} is not configured")

    def capabilities(self, graph_id: str | None = None) -> Any:
        """Capabilities of the backend serving ``graph_id`` (the JSON backend's when no id is given)."""
        if graph_id is None:
            return self._json.capabilities()
        return self.resolve(graph_id).capabilities()

    def reload(self) -> None:
        """Drop loaded JSON snapshots, the cached Tiled ids and backend, and the recorded warnings."""
        with self._lock:
            if hasattr(self._json, "reload"):
                self._json.reload()
            self._backend = None
            self._ids = ()
            self._expires = None
            self._warnings = []


def _same_target(url: str, target: str | None) -> bool:
    if not target:
        return False
    a, b = urlsplit(url), urlsplit(target)
    return (a.scheme.lower(), (a.hostname or "").lower(), a.port, a.path.rstrip("/")) == \
        (b.scheme.lower(), (b.hostname or "").lower(), b.port, b.path.rstrip("/"))


def tiled_backend_factory(tiled_settings: Any, **backend_kw: Any) -> TiledBackendFactory:
    """A :data:`TiledBackendFactory` for the deployment's Tiled target (read-only adapter).

    * ``managed`` (``:local`` and test fixtures): :func:`~app.modules.deployment.tiled_lifecycle.find_managed`
      locates the running server by its manifest each time the factory runs; the writer key
      is read from the managed secret file per request by ``TiledGraphClient.from_managed``.
      No running server: ``None`` (the composite lists JSON graphs only until the next refresh).
    * ``external`` (ALS): ``TiledGraphClient.from_external`` reads the writer secret file per request.

    Credentials never pass through this module and are never logged.
    """
    from ..graph.backends.tiled import TiledGraphBackend
    from ..graph.backends.tiled_client import TiledGraphClient

    if tiled_settings.mode == "external":
        external = tiled_settings.external

        def from_external() -> Any | None:
            return TiledGraphBackend(TiledGraphClient.from_external(external), **backend_kw)

        return from_external
    managed = tiled_settings.managed

    def from_managed() -> Any | None:
        from ..deployment.tiled_lifecycle import find_managed

        server = find_managed(managed)
        if server is None:
            return None
        return TiledGraphBackend(TiledGraphClient.from_managed(server), **backend_kw)

    return from_managed


def resolve_tiled_settings(settings: Any, environ: Any = None) -> tuple[Any | None, str | None]:
    """``(TiledSettings, None)`` for the deployment, or ``(None, reason)`` when Tiled is not usable.

    Args:
        settings: The ``DeploymentSettings`` (its profile is authoritative).
        environ: The trusted startup environment (``os.environ`` when ``None``), or a
            ready ``TiledSettings``.

    An external target that is not the deployment's ``tiled_target`` (for example a
    disposable loopback rehearsal target on an ALS test profile) is refused, so the
    operations never contact a Tiled host the deployment is not bound to. A configuration
    error is a reason, not an exception: the deployment preflight already fails closed on
    it, and the operations keep serving the JSON graphs.
    """
    from ..deployment.tiled_config import TiledConfigError, TiledSettings, load_tiled_settings

    profile = getattr(getattr(settings, "profile", None), "value", None) or str(getattr(settings, "profile", ""))
    if isinstance(environ, TiledSettings):
        tiled = environ
        if tiled.profile != profile:
            return None, f"Tiled settings are for profile {tiled.profile!r}, the deployment runs {profile!r}"
    else:
        env: dict[str, str] = {k: str(v) for k, v in (os.environ if environ is None else environ).items()}
        env["F2W_DEPLOYMENT_PROFILE"] = profile
        try:
            tiled = load_tiled_settings(env)
        except TiledConfigError as exc:
            return None, f"Tiled is not configured for the operations: {exc}"
    if tiled.mode == "external":
        if tiled.external is None:
            return None, "external Tiled mode without a target"
        if not _same_target(tiled.external.url, getattr(settings, "tiled_target", None)):
            return None, (f"Tiled target {tiled.external.url} is not the deployment's "
                          f"{getattr(settings, 'tiled_target', None)}; Tiled graphs are not listed")
    elif tiled.managed is None:
        return None, "managed Tiled mode without a server configuration"
    return tiled, None


def storage_graph_files(root: str | Path) -> list[str]:
    """``<root>/storage/kg/*.json``, sorted by name (the legacy ``list_storage_kg_json_files`` rule)."""
    kg_dir = Path(root) / "storage" / "kg"
    if not kg_dir.is_dir():
        return []
    return [str(p) for p in sorted(kg_dir.glob("*.json"), key=lambda p: p.name.lower())]


def directory_corpus_resolver(directory: str | Path) -> CorpusResolver:
    """Resolve a corpus name to ``<directory>/<name>.json`` (the extracted-terms files).

    Names are file stems (``extracted_terms_xray_papers_cborg_chat``); nothing outside
    ``directory`` can be named (no separators pass the Tool's name pattern, and the
    resolved path must stay inside the directory). A non-empty ``ctx.corpus_scope`` is
    honoured as ``mapping_corpus_resolver`` does (``DENIED`` outside it).
    """
    base = Path(directory)

    def resolve(ctx: RequestContext, name: str) -> CorpusSource | None:
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

    def __init__(self, graph: GraphOperationsTool, corpus: CorpusAuditTool | None = None, *,
                 artifact_store: Any = None) -> None:
        if not isinstance(graph, GraphOperationsTool):
            raise TypeError("graph must be an operations.graph.GraphOperationsTool")
        if corpus is not None and not isinstance(corpus, CorpusAuditTool):
            raise TypeError("corpus must be an operations.corpus.CorpusAuditTool or None")
        self.graph = graph
        self.corpus = corpus
        self.artifact_store = artifact_store
        suites: list[Any] = [graph] + ([corpus] if corpus is not None else [])
        self._dispatch: OperationFn = operation_dispatch(*suites)
        self._names: tuple[str, ...] = tuple(n for s in suites for n in s.operation_names())

    # -- construction helpers -----------------------------------------------------------------

    @classmethod
    def from_storage(cls, root: str | Path, *, artifact_store: Any = None,
                     graph_max_records: int = 500_000) -> KnowledgeOperations:
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
    def from_deployment(cls, settings: Any, environ: Any = None, root: str | Path | None = None, *,
                        artifact_store: Any = None, graph_max_records: int = 500_000,
                        discovery_ttl: float = TILED_DISCOVERY_TTL) -> KnowledgeOperations:
        """Operations over ``<root>/storage/kg`` *and* the deployment's Tiled graphs (D3.17).

        Args:
            settings: The ``DeploymentSettings`` of the process (profile, Tiled target).
            environ: The trusted startup environment (``os.environ`` when ``None``) read once
                here through :func:`~app.modules.deployment.tiled_config.load_tiled_settings`,
                or a ready ``TiledSettings``.
            root: Project root (``Path.cwd()`` when ``None``) for ``storage/kg`` and ``storage/terminology``.
            artifact_store: Optional ``DocumentStore`` for the private full report of a direct call.
            graph_max_records: Record cap passed to the graph operations as ``max_records``.
            discovery_ttl: Seconds the discovered Tiled graph ids stay cached.

        On ``:local`` the managed loopback server is located by its manifest and read with the
        writer key from its secret file (per request); on ALS the fixed external target is read
        with the writer secret file. When Tiled is not configured, not running or bound to
        another target, the bundle serves the JSON graphs and records the reason once
        (``operations.graph.port.warnings``). Nothing is contacted at construction time.
        """
        root = Path(root) if root is not None else Path.cwd()
        tiled, reason = resolve_tiled_settings(settings, environ)
        port = CompositeGraphPort(DynamicJsonGraphs(lambda: storage_graph_files(root)),
                                  tiled_backend_factory(tiled) if tiled is not None else None, ttl=discovery_ttl)
        if reason:
            port.record_warning(reason)
        graph = GraphOperationsTool(port, max_records=graph_max_records)
        corpus = CorpusAuditTool(directory_corpus_resolver(root / "storage" / "terminology"))
        return cls(graph, corpus, artifact_store=artifact_store)

    @classmethod
    def from_graph_files(cls, provider: GraphFilesProvider, *, corpus: CorpusAuditTool | None = None,
                         artifact_store: Any = None, graph_max_records: int = 500_000,
                         tiled: Any | TiledBackendFactory | None = None,
                         discovery_ttl: float = TILED_DISCOVERY_TTL) -> KnowledgeOperations:
        """Operations following a caller-supplied graph selection (T25D: the active runtime's files).

        ``tiled`` (a Tiled GraphPort or a :data:`TiledBackendFactory`) adds the deployment's Tiled
        graphs beside the selection through a :class:`CompositeGraphPort`; ``None`` keeps JSON only.
        """
        port: Any = DynamicJsonGraphs(provider)
        if tiled is not None:
            port = CompositeGraphPort(port, tiled, ttl=discovery_ttl)
        graph = GraphOperationsTool(port, max_records=graph_max_records)
        return cls(graph, corpus, artifact_store=artifact_store)

    # -- agent runtime -------------------------------------------------------------------------

    def operation_names(self) -> tuple[str, ...]:
        """Tool names this bundle serves."""
        return self._names

    def specs(self) -> list[ToolSpec]:
        """The Tool specs (graph suite first, then the corpus audit)."""
        out = list(self.graph.specs)
        if self.corpus is not None:
            out.append(self.corpus.spec)
        return out

    def registrations(self) -> list[tuple[ToolSpec, Callable[[Any], ToolResult]]]:
        """``(spec, fn)`` pairs for ``build_registry(extra=...)``."""
        out = list(self.graph.registrations())
        if self.corpus is not None:
            out.append(self.corpus.registration())
        return out

    def skills(self) -> list[Any]:
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
                control: ScanControl | None = None) -> OperationReport:
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
               control: ScanControl | None = None, artifact_store: Any = None) -> ToolResult:
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
                          credential_lookup: Callable[[str], Any] | None = None,
                          clock: Callable[[], Any] | None = None) -> Callable[[Any], RequestContext]:
    """``context_for_job`` for :func:`~.reports.register_operation_jobs` (trusted wiring).

    On ``:local`` the worker carries the local operator. On ALS it re-resolves the initiating
    subject's credential through ``credential_lookup(job.created_by)`` with a fresh role check;
    a missing or revoked credential is ``DENIED`` (the job fails closed, AUD-01).
    """
    from datetime import datetime

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
                              clock=clock or (lambda: datetime.now(UTC)), require_fresh=True)
        return worker_context(ctx, run_id=job.job_id)

    return context_for_job


def job_params(operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """``inputs.params`` of a :data:`~.reports.OPERATION_JOB_KIND` job."""
    return {"operation": operation, "args": dict(args)}


def job_scope(args: Mapping[str, Any]) -> tuple[str, ...]:
    """The corpora an operation job touches, derived from its arguments (graph ids, corpus name).

    This is the job's ``corpus_scope``: ``JobService`` checks it against the caller's scope on
    ALS exactly as ``graph.ports.require_scope`` checks each graph read (T19C).
    """
    names: list[str] = []
    for key in ("graph_id", "corpus"):
        value = args.get(key)
        if isinstance(value, str) and value and value not in names:
            names.append(value)
    for value in args.get("graph_ids") or []:
        if isinstance(value, str) and value and value not in names:
            names.append(value)
    return tuple(names)


__all__: tuple[str, ...] = (
    "CompositeGraphPort", "DynamicJsonGraphs", "KnowledgeOperations", "OPERATION_JOB_KIND", "READ_CAPABILITY",
    "TILED_DISCOVERY_MAX_SCAN", "TILED_DISCOVERY_TTL", "TiledBackendFactory", "directory_corpus_resolver",
    "job_params", "job_scope", "knowledge_job_context", "resolve_tiled_settings", "storage_graph_files",
    "tiled_backend_factory",
)
