"""Legacy UI/CLI adapter for ``F2W_RUNTIME=langgraph`` (tasks T03, T03B).

In langgraph mode ``AgentPipelineService._query_retrieval`` hands the ordinary
read/QA call to :class:`LegacyReadPath`, which runs it through an
:class:`~app.modules.application.F2WApplication` (authorizing executor and the
``answer_from_knowledge`` tool) and translates the result back with
``ReadOutcome.to_legacy()``: the exact verdict object the existing
``RetrievalAgent.query`` returned, or the exact exception it raised. Everything
after that call (graph_update, sufficiency branches, downloads, extraction,
``ChatResponse``/SSE construction) stays in ``api.py`` unchanged, so routes,
DTOs, statuses, SSE phases and citation tokens are byte-identical.

Route (T03B, ``F2W_QA_ROUTE``, read once at startup; see :mod:`.qa_route`): by
default (``supervisor``) the call is planned by the literature supervisor
(``F2WApplication.answer_via_supervisor``: one planning model call under shared
admission, traced as ``supervisor-plan``, tool arguments pinned to the UI's);
``direct`` keeps the T03 direct call. Both return the same ``ReadOutcome``.

The retrieval service reads the pipeline's *current* agent on every call
(``pipeline.retrieval`` is rebuilt on settings changes) and its current Tiled URI.

Knowledge Operations (T19E). The application also registers the five T19C read Tools
(``kg_compute_statistics``, ``kg_compare_snapshots``, ``kg_paper_subgraph``,
``kg_recent_entities``, ``curate_audit_corpus``) over the pipeline's *current* JSON graph
selection (:func:`active_json_graph_files`: the active runtime's graph files, which T25D
sets per user for the turn) and the ``storage/terminology`` corpora. The supervisor may
choose them for a delegated task; the legacy UI QA turn keeps its contract (the
``answer_from_knowledge`` verdict is the answer, with the documented fallback when the
planner called no read tool), so no UI DTO changes here.

Context. The legacy UI API has no per-request identity wiring yet (that is
T18B). This adapter therefore runs only on the ``:local`` profile, where every
caller is the local-operator (D34), which is the legacy server's existing trust
posture. ``F2W_DEPLOYMENT_PROFILE`` defaults to ``local`` when unset; any ALS
profile refuses startup in langgraph mode instead of acting without a verified
principal.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from ..contracts import ActorKind, RequestContext
from ..security.authorization import DeploymentSettings

from .mode import RuntimeConfigError
from .qa_route import QaRoute, TracedPlanningModel, pipeline_planning_model, qa_route
from .retrieval import LegacyRetrievalService

#: Placeholder writer identity for the read-only adapter; never used to write or to
#: establish a caller's role (DeploymentSettings requires one).
DEFAULT_WRITER_IDENTITY = "f2w-writer"

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def legacy_deployment_settings(environ: Optional[Mapping[str, str]] = None) -> DeploymentSettings:
    """Startup settings for the legacy service in langgraph mode.

    Raises:
        RuntimeConfigError: On an ALS profile (no verified per-request principal yet).
        StartupConfigError: On any value ``DeploymentSettings`` rejects.
    """
    env = os.environ if environ is None else environ
    keys = ("F2W_DEPLOYMENT_PROFILE", "F2W_AUTHORIZATION_MODE", "F2W_TILED_URL", "F2W_TILED_WRITER_IDENTITY",
            "F2W_LOCAL_BIND", "F2W_ROLE_CHECK_MAX_AGE", "F2W_CORPUS_SCOPE")
    values: Dict[str, str] = {k: str(env.get(k) or "") for k in keys}
    values["F2W_DEPLOYMENT_PROFILE"] = values["F2W_DEPLOYMENT_PROFILE"].strip() or "local"
    values["F2W_TILED_WRITER_IDENTITY"] = values["F2W_TILED_WRITER_IDENTITY"].strip() or DEFAULT_WRITER_IDENTITY
    settings = DeploymentSettings.from_mapping(values)
    if not settings.is_local:
        raise RuntimeConfigError(
            "config_rejected: F2W_RUNTIME=langgraph on the legacy UI API needs the local profile until "
            "per-request ALS identity is wired (T18B)")
    return settings


class LegacyReadPath:
    """Runs the legacy read/QA call through the application service.

    Args:
        application: The ``F2WApplication`` whose retrieval service wraps the pipeline's agent.
        context_factory: Returns the server-built ``RequestContext`` for one call.
        route: ``QaRoute``; ``SUPERVISOR`` needs an application built with a chat model.
    """

    def __init__(self, application: Any, *, context_factory: Callable[[], RequestContext],
                 route: QaRoute = QaRoute.DIRECT) -> None:
        if route is QaRoute.SUPERVISOR and getattr(application, "supervisor", None) is None:
            raise ValueError("the supervisor QA route needs an application with a chat model")
        self.application = application
        self._context_factory = context_factory
        self.route = route
        self.last_supervised: Any = None

    async def query(
        self,
        question: str,
        *,
        history: Optional[List[Dict[str, str]]] = None,
        source_rag: Optional[bool] = None,
        use_live_tiled: Optional[bool] = None,
    ) -> Any:
        """Same contract as ``AgentPipelineService._query_retrieval``'s agent call: returns the
        legacy verdict unchanged or raises the original exception."""
        ctx = self._context_factory()
        if self.route is QaRoute.SUPERVISOR:
            supervised = await self.application.answer_via_supervisor(
                ctx, question, history=history, source_rag=source_rag, use_live_tiled=use_live_tiled)
            self.last_supervised = supervised
            return supervised.read.to_legacy()
        outcome = await self.application.answer_from_knowledge(
            ctx, question, history=history, source_rag=source_rag, use_live_tiled=use_live_tiled)
        return outcome.to_legacy()


#: Planning-model provider factory, ``(pipeline) -> (() -> chat model)``. Tests replace it with a fake.
planning_model_provider: Callable[[Any], Callable[[], Any]] = pipeline_planning_model


def active_json_graph_files(pipeline: Any) -> List[str]:
    """The JSON graph files of ``pipeline``'s active runtime (T25D per-user selection), existing ones only.

    Empty when the active graph source is not ``json`` (Splash/Tiled mode has no JSON snapshot
    to scan) or when the pipeline exposes no runtime.
    """
    runtime = getattr(pipeline, "runtime", None)
    if runtime is None or str(getattr(runtime, "graph_source", "json") or "json") != "json":
        return []
    paths_fn = getattr(pipeline, "_runtime_json_paths", None)
    raw = list(paths_fn() if callable(paths_fn) else (getattr(runtime, "json_graph_paths", None) or []))
    out: List[str] = []
    for path in raw:
        candidate = Path(str(path))
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        if candidate.is_file() and str(candidate) not in out:
            out.append(str(candidate))
    return out


def build_knowledge_operations(pipeline: Any) -> Any:
    """``KnowledgeOperations`` following ``pipeline``'s active graph selection and ``storage/terminology``."""
    from ..operations.corpus import CorpusAuditTool
    from ..operations.knowledge import KnowledgeOperations, directory_corpus_resolver

    corpus = CorpusAuditTool(directory_corpus_resolver(Path.cwd() / "storage" / "terminology"))
    return KnowledgeOperations.from_graph_files(lambda: active_json_graph_files(pipeline), corpus=corpus)


def build_legacy_read_path(pipeline: Any, environ: Optional[Mapping[str, str]] = None) -> LegacyReadPath:
    """Build the langgraph-mode read path for one ``AgentPipelineService`` instance.

    Raises:
        RuntimeConfigError: Non-local profile or invalid ``F2W_QA_ROUTE``.
    """
    from ..agent_runtime.executor import RuntimeLimits
    from ..application import F2WApplication

    settings = legacy_deployment_settings(environ)
    route = qa_route(environ)
    service = LegacyRetrievalService(
        agent_provider=lambda: pipeline.retrieval,
        tiled_uri_provider=lambda: getattr(getattr(pipeline, "runtime", None), "tiled_uri", None),
    )
    chat_model = None
    if route is QaRoute.SUPERVISOR:
        chat_model = TracedPlanningModel(planning_model_provider(pipeline))
    backend = str(getattr(getattr(pipeline, "runtime", None), "backend", None) or "cborg")
    # One planning call per turn: the tool verdict is the answer, so no relay call is made.
    limits = RuntimeLimits(max_retries=0, max_model_calls=1)
    application = F2WApplication(settings=settings, retrieval=service, chat_model=chat_model, limits=limits,
                                 provider=backend, name="f2w-legacy-ui",
                                 knowledge=build_knowledge_operations(pipeline))

    def context() -> RequestContext:
        key = str(getattr(pipeline, "_active_session_key", "") or "")
        session_id = key if _SESSION_RE.match(key) else None
        return application.local_context(request_id=uuid.uuid4().hex, session_id=session_id,
                                         actor_kind=ActorKind.INTERACTIVE)

    return LegacyReadPath(application, context_factory=context, route=route)


__all__ = ("DEFAULT_WRITER_IDENTITY", "LegacyReadPath", "active_json_graph_files", "build_knowledge_operations",
           "build_legacy_read_path", "legacy_deployment_settings")
