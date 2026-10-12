"""Legacy UI/CLI adapter for ``F2W_RUNTIME=langgraph`` (task T03).

In langgraph mode ``AgentPipelineService._query_retrieval`` hands the ordinary
read/QA call to :class:`LegacyReadPath`, which runs it through an
:class:`~app.modules.application.F2WApplication` (authorizing executor and the
``answer_from_knowledge`` tool) and translates the result back with
``ReadOutcome.to_legacy()``: the exact verdict object the existing
``RetrievalAgent.query`` returned, or the exact exception it raised. Everything
after that call (graph_update, sufficiency branches, downloads, extraction,
``ChatResponse``/SSE construction) stays in ``api.py`` unchanged, so routes,
DTOs, statuses, SSE phases and citation tokens are byte-identical.

The retrieval service reads the pipeline's *current* agent on every call
(``pipeline.retrieval`` is rebuilt on settings changes) and its current Tiled URI.

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
from typing import Any, Callable, Dict, List, Mapping, Optional

from app.modules.contracts import ActorKind, RequestContext
from app.modules.security.authorization import DeploymentSettings

from .mode import RuntimeConfigError
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
    """

    def __init__(self, application: Any, *, context_factory: Callable[[], RequestContext]) -> None:
        self.application = application
        self._context_factory = context_factory

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
        outcome = await self.application.answer_from_knowledge(
            ctx, question, history=history, source_rag=source_rag, use_live_tiled=use_live_tiled)
        return outcome.to_legacy()


def build_legacy_read_path(pipeline: Any, environ: Optional[Mapping[str, str]] = None) -> LegacyReadPath:
    """Build the langgraph-mode read path for one ``AgentPipelineService`` instance."""
    from app.modules.application import F2WApplication

    settings = legacy_deployment_settings(environ)
    service = LegacyRetrievalService(
        agent_provider=lambda: pipeline.retrieval,
        tiled_uri_provider=lambda: getattr(getattr(pipeline, "runtime", None), "tiled_uri", None),
    )
    application = F2WApplication(settings=settings, retrieval=service, name="f2w-legacy-ui")

    def context() -> RequestContext:
        key = str(getattr(pipeline, "_active_session_key", "") or "")
        session_id = key if _SESSION_RE.match(key) else None
        return application.local_context(request_id=uuid.uuid4().hex, session_id=session_id,
                                         actor_kind=ActorKind.INTERACTIVE)

    return LegacyReadPath(application, context_factory=context)


__all__ = ("DEFAULT_WRITER_IDENTITY", "LegacyReadPath", "build_legacy_read_path", "legacy_deployment_settings")
