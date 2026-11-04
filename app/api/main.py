"""F2W FastAPI app factory (task T14): the legacy UI API plus the versioned ``/v1`` routes.

:func:`create_api_app` builds the legacy app with
:func:`app.modules.f2w_agent.api.create_app` (unchanged signature, the 19 UI routes, the
ALS sign-in handlers, access control and disclosure projection) and adds:

* the ``/v1`` jobs, curation review, publication and status routes
  (``app/api/routes_*.py``), each declared in :mod:`app.api.policies`, all thin over one
  :class:`~app.api.operations.F2WOperations` that SDK, CLI and worker callers use directly;
* the ``/v1/validation/judge-model`` routes behind the settings panel's judge dropdown
  (owner O-U2; :mod:`app.modules.validation_settings`);
* the T31 routes (owner decision B5): ``/v1/curation/cost-control``,
  ``/v1/curation/cases/bulk-review``, ``/v1/vocabulary/quality`` and ``/v1/figures/scales``
  (``app/api/routes_review.py``, ``app/api/routes_records.py``), all over ``F2WOperations``;
* on the ``:local`` profile only, the T32 folder routes (owner decision B4) ``/v1/folders/roots``,
  ``/list`` and ``/index`` over roots fixed by ``F2W_FOLDER_ROOTS`` (:mod:`app.api.folders`); an ALS
  profile does not register them, so they answer 404 there;
* on the ``:local`` profile only, the T35 library routes ``/v1/library`` (status, papers, upload, verify) over the
  mounted ``F2W_LIBRARY_DIR`` (:mod:`app.modules.library.service`), and the real ``library_verify`` job handler;
* the ``/v1/operations`` routes (T19E) over the deployment's
  :class:`~app.modules.operations.knowledge.KnowledgeOperations` (default
  ``KnowledgeOperations.from_deployment``: the JSON graphs under ``storage/kg`` and the
  corpora under ``storage/terminology`` of the working directory, as the legacy UI lists
  them, plus the deployment's Tiled graphs read-only on every profile, ``:local`` included,
  owner decision D3.17), plus the ``knowledge_operation`` job kind (``ops.run``) registered
  on the shared operations when no trusted wiring did so;
* the browser-origin gate (:mod:`app.api.origin_gate`) and the request-body cap
  (:mod:`app.api.upload_limits`) on ``/v1/`` and ``/auth/`` (``create_app`` itself gates the
  legacy routes and caps their bodies at 128 MiB, AUD-04);
* the scope-change wiring: an ALS sign-out or a dropped admin role calls
  ``F2WOperations.notify_scope_change`` (``JobService.notify_scope_change``), which
  invalidates dependent publication approvals.
* with ``model_api=True`` (the image entrypoint ``run_api``), the model-compatible API
  (:func:`app.modules.kg_rag_api.create_fastapi_app`, routes R20-R25) mounted at
  :data:`~app.modules.deployment.gateway.MODEL_API_MOUNT` over the same deployment settings,
  identity port and server-side session store, so the ALS session cookie signs a caller in
  to both (owner decision B7, O-A9, 2026-10-30). The gateway forwards
  ``/fair2wise/model-api/<path>`` as ``/model-api/<path>``; :class:`PublicRootPath` gives the
  mounted app ``root_path=/fair2wise/model-api`` so its ``/docs`` and OpenAPI ``servers``
  name the public path. Non-browser clients have no sign-in yet (D-T8).

The legacy ``create_app`` / ``run_api`` / ``python -m app.modules.f2w_agent.cli api``
entrypoints are unchanged; ``... cli api --jobs-api`` serves this factory instead.

No job kind or real publication is registered by default (H3/H5). Importing this module
has no side effects.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from ..modules.deployment.gateway import MODEL_API_MOUNT, layout_from_env
from ..modules.security.authorization import StartupConfigError
from .auth import undeclared_routes, warn_if_exposed
from .operations import F2WOperations, PublicationGate, build_operations
from .origin_gate import OriginGateMiddleware
from .upload_limits import DEFAULT_MAX_BODY_BYTES, MaxBodySizeMiddleware

if TYPE_CHECKING:
    from ..modules.f2w_agent.coordinator import CoordinatorConfig
    from ..modules.graph.legacy_save import SaveTarget
    from ..modules.library.service import LibraryService
    from ..modules.operations.knowledge import KnowledgeOperations
    from ..modules.security.authorization import DeploymentSettings
    from ..modules.security.tiled_identity import TiledIdentityPort
    from ..modules.validation_settings import JudgeModelService
    from .auth import CredentialStore
    from .folders import FolderRoots
    from .post_extraction import PostExtractionPublisher

logger = logging.getLogger(__name__)

#: Path prefixes covered by the origin gate and the body cap.
GUARDED_PREFIXES = ("/v1/", "/auth/")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PublicRootPath:
    """ASGI shim for a sub-application published under its own public path.

    Starlette's ``Mount`` gives a mounted app ``root_path = <parent root_path> + <mount path>``
    (``/fair2wise/api/model-api`` behind the gateway), which is not where the gateway publishes
    it (``/fair2wise/model-api``). This rewrites ``root_path``, ``app_root_path``, ``path`` and
    ``raw_path`` so routing inside the app is unchanged and every generated URL (FastAPI
    ``/docs`` -> ``openapi.json``, OpenAPI ``servers``, redirects) carries ``public_root``.
    Nothing is read from request headers: the public root is trusted startup configuration.

    Attributes:
        app: The wrapped application (``undeclared_routes`` inspects it through this attribute).
        public_root: Rooted public path without a trailing slash, for example ``/fair2wise/model-api``.
    """

    def __init__(self, app: ASGIApp, public_root: str) -> None:
        """Wrap ``app`` for ``public_root``.

        Raises:
            StartupConfigError: If ``public_root`` is not rooted or ends with a slash.
        """
        if not public_root.startswith("/") or public_root.endswith("/"):
            raise StartupConfigError(f"config_rejected: public root must be /rooted without a trailing slash, "
                                     f"got {public_root!r}")
        self.app = app
        self.public_root = public_root

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Rewrite the path keys of HTTP and WebSocket scopes, then call the wrapped app; other scopes pass through."""
        if scope["type"] in ("http", "websocket"):
            mounted_root = scope.get("root_path", "")
            path = scope["path"]
            rest = path[len(mounted_root):] if mounted_root and path.startswith(mounted_root) else path
            raw = scope.get("raw_path")
            raw_root = mounted_root.encode("ascii", "surrogateescape")
            if raw is not None and raw.startswith(raw_root):
                raw = raw[len(raw_root):]
            scope = dict(scope, root_path=self.public_root, app_root_path=self.public_root,
                         path=self.public_root + rest)
            if raw is not None:
                scope["raw_path"] = self.public_root.encode("ascii") + raw
        await self.app(scope, receive, send)


def _install_kg_library(app: FastAPI, cfg: CoordinatorConfig, judge_settings: JudgeModelService | None) -> None:
    """T38: KG bundle routes (``:local`` only); the model catalog is the judge setting's (one provider cache)."""
    from . import routes_library_kgs
    from ..modules.library.bundles import BUNDLES_DIRNAME, BundleStore
    from ..modules.library.kg_service import KgLibrary

    store = BundleStore(Path(cfg.workdir) / BUNDLES_DIRNAME)
    # The estimate resolves paper ids through the T35 catalog service (built just before on :local).
    routes_library_kgs.install(app, KgLibrary(store, catalog=getattr(judge_settings, "catalog", None),
                                              papers=getattr(app.state, "f2w_library", None)))


def create_api_app(
    cfg: Any,  # noqa: ANN401  # Any: the coordinator config gains tiled_save/extraction_publisher attributes at wiring time
    *,
    cors_origins: list[str] | None = None,
    deployment: DeploymentSettings | None = None,
    startup_config: dict[str, str] | None = None,
    identity_port: TiledIdentityPort | None = None,
    credential_store: CredentialStore | None = None,
    clock: Callable[[], datetime] | None = None,
    operations: F2WOperations | None = None,
    operations_root: Path | None = None,
    publication_gate: PublicationGate | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    sse_poll_seconds: float = 0.5,
    sse_max_seconds: float = 300.0,
    judge_settings: JudgeModelService | None = None,
    knowledge_operations: KnowledgeOperations | None = None,
    model_api: bool = False,
    model_api_root_path: str | None = None,
    folder_roots: FolderRoots | None = None,
    library: LibraryService | None = None,
    tiled_save: SaveTarget | None = None,
    extraction_publisher: PostExtractionPublisher | None = None,
) -> FastAPI:
    """Build the F2W API: the legacy UI routes plus ``/v1`` over the shared operations.

    Args:
        cfg: The launcher's ``CoordinatorConfig`` (as ``create_app``).
        cors_origins: Passed to ``create_app`` unchanged (CORS allowlist).
        deployment: Passed to ``create_app`` unchanged (profile selection).
        startup_config: Passed to ``create_app`` unchanged (startup configuration mapping); also the
            source of ``F2W_PUBLIC_PREFIX`` and of the knowledge-operations configuration.
        identity_port: Passed to ``create_app`` unchanged (ALS identity); also set on ``operations``
            when it has none.
        credential_store: Passed to ``create_app`` unchanged (sessions).
        clock: Passed to ``create_app`` unchanged; also the clock of the default operations
            (default: the current UTC time).
        operations: Prebuilt operations bound to the same ``DeploymentSettings``; default
            :func:`~app.api.operations.build_operations` under ``operations_root``.
        operations_root: Directory of the default job, curation and audit stores
            (default ``<cfg.workdir>/operations``).
        publication_gate: H5 gate for the default operations (default: no target allowed).
        max_body_bytes: Request-body cap on ``/v1/`` and ``/auth/``.
        sse_poll_seconds: Polling interval of the job event stream, in seconds.
        sse_max_seconds: Maximum length of one job event stream, in seconds.
        judge_settings: Validation judge-model service (O-U2); default a
            :class:`~app.modules.validation_settings.JudgeModelService` over
            ``<workdir>/validation/judge_model.json`` and the live CBORG model list.
        knowledge_operations: ``KnowledgeOperations`` for ``/v1/operations`` (T19E); default
            ``KnowledgeOperations.from_deployment(settings, startup_config or os.environ, Path.cwd())``
            (JSON graphs plus the deployment's Tiled graphs, D3.17) unless ``operations`` already
            carries one.
        model_api: Mount the model-compatible API (``kg_rag_api.create_fastapi_app`` over
            ``cfg.graph``, ``cfg.backend`` and ``cfg.model``) at ``MODEL_API_MOUNT`` with this
            app's settings, identity port and session store (owner decision B7). Off by default
            for library and test callers; ``run_api`` (the image entrypoint) turns it on.
        model_api_root_path: Public root the mounted model API advertises (``/fair2wise/model-api``
            behind the gateway). Default from ``F2W_PUBLIC_PREFIX`` in ``startup_config`` (or the
            process environment), as ``PublicLayout.model_api_root_path``.
        folder_roots: :class:`~app.api.folders.FolderRoots` for the ``/v1/folders`` routes (T32).
        library: :class:`~app.modules.library.service.LibraryService` for the ``/v1/library`` routes (T35); built from
            ``F2W_LIBRARY_DIR`` when ``None`` (``:local`` only).
            Default: parsed from ``F2W_FOLDER_ROOTS`` in ``startup_config`` (or the process
            environment). The routes are registered on the ``:local`` profile only; on an ALS
            profile they are absent (404) and a configured value is ignored with a warning.
        tiled_save: A :class:`~app.modules.graph.legacy_save.SaveTarget` (T18D), attached to the legacy service (the
            ``create_app`` signature is pinned): the UI Save becomes a journaled multi-write on Tiled with no Splash call.
        extraction_publisher: A :class:`~app.api.post_extraction.PostExtractionPublisher` (T18E): after an approved
            extraction the legacy ``splash_reimport_*`` phases publish ``v<N+1>`` to Tiled under the profile's approval
            policy. Without one the phases report ``skipped``; there is no Splash wipe.

    Raises:
        StartupConfigError: As ``create_app``, ``operations`` bound to other settings,
            ``knowledge_operations`` given beside ``operations`` that already carry one, or
            ``model_api`` without a JSON graph in ``cfg.graph``.
    """
    from . import routes_capabilities as capabilities
    from . import routes_folders as folders
    from . import routes_jobs as jobs
    from . import routes_library as library_routes
    from . import routes_library_harvest
    from . import routes_operations as knowledge_routes
    from . import routes_publications as publications
    from . import routes_records as records
    from . import routes_review as review
    from . import routes_status as status
    from . import routes_validation as validation
    from ..modules.f2w_agent.api import create_app, effective_cors_origins
    from ..modules.library import register_library_jobs
    from ..modules.operations.knowledge import OPERATION_JOB_KIND, KnowledgeOperations, knowledge_job_context
    from ..modules.validation_settings import JudgeModelService, JudgeModelStore, judge_settings_path
    from .folders import FOLDER_ROOTS_ENV, FolderRoots

    if extraction_publisher is not None:
        cfg.extraction_publisher = extraction_publisher
    if tiled_save is not None:
        cfg.tiled_save = tiled_save     # read by the service before it builds its session graph (no Splash call)
    app = create_app(cfg, cors_origins=cors_origins, deployment=deployment, startup_config=startup_config,
                     identity_port=identity_port, credential_store=credential_store, clock=clock)
    runtime = app.state.f2w_access
    settings = runtime.settings
    if operations is None:
        operations = build_operations(settings, operations_root or Path(cfg.workdir) / "operations",
                                      identity_port=identity_port, clock=clock or _utcnow,
                                      publication_gate=publication_gate,
                                      credential_lookup=runtime.credential_lookup())
    elif operations.settings != settings:
        raise StartupConfigError("config_rejected: operations are bound to another deployment profile or target")
    if operations.identity_port is None:
        operations.identity_port = identity_port
    if knowledge_operations is not None and operations.knowledge is not None:
        raise StartupConfigError("config_rejected: knowledge_operations given but operations already carry one")
    if operations.knowledge is None:
        operations.knowledge = knowledge_operations or KnowledgeOperations.from_deployment(
            settings, os.environ if startup_config is None else startup_config, Path.cwd())
    if not operations.jobs.has_kind(OPERATION_JOB_KIND):
        # The worker re-resolves the initiating subject with the same lookup the job authority uses (AUD-01).
        lookup = runtime.credential_lookup() or getattr(operations.jobs.authority, "credential_lookup", None)
        operations.register_knowledge_jobs(knowledge_job_context(
            settings, identity_port=operations.identity_port, credential_lookup=lookup, clock=clock or _utcnow))
    # T34: the library kinds (local operator only; nothing is registered on ALS, so submissions are refused).
    # T35: the real library_verify handler runs over the mounted folder (:local only; ALS builds no service).
    library_handlers_: Any = None
    if settings.is_local:
        from ..modules.library.handlers import library_handlers
        from ..modules.library.service import LibraryService

        library_service = library if library is not None else LibraryService.from_startup(
            os.environ if startup_config is None else startup_config, Path(cfg.workdir), settings)
        app.state.f2w_library = library_service
        from ..modules.library.bundles import BUNDLES_DIRNAME, BundleStore
        from ..modules.library.extraction import LibraryExtractor

        startup_env = os.environ if startup_config is None else startup_config
        from ..modules.library.harvest import Harvester, HarvestStore

        app.state.f2w_harvest = Harvester(library_service, HarvestStore(Path(cfg.workdir) / "library" / "harvest.sqlite"))
        library_handlers_ = library_handlers(library_service, extractor=LibraryExtractor(
            library_service, BundleStore(Path(cfg.workdir) / BUNDLES_DIRNAME), env=startup_env),
            harvester=app.state.f2w_harvest)
    register_library_jobs(operations.jobs, settings, handlers=library_handlers_)
    app.state.f2w_operations = operations
    runtime.on_scope_change(operations.notify_scope_change)

    jobs.install(app, poll_seconds=sse_poll_seconds, max_stream_seconds=sse_max_seconds)
    review.install(app)
    publications.install(app)
    status.install(app)
    capabilities.install(app)
    if judge_settings is None:
        judge_settings = JudgeModelService(JudgeModelStore(judge_settings_path(Path(cfg.workdir))))
    app.state.f2w_judge_settings = judge_settings
    validation.install(app)
    knowledge_routes.install(app)
    records.install(app)

    startup = os.environ if startup_config is None else startup_config
    if settings.is_local:
        # T32 (B4): folder browse/index over roots fixed in configuration, on the local profile only.
        if operations.folders is None:
            operations.folders = folder_roots if folder_roots is not None else FolderRoots.from_startup(startup)
        folders.install(app)
        library_routes.install(app)  # T35 (A8): library catalog/upload/verify, local profile only
        routes_library_harvest.install(app)  # T45 (A8): Harvester runs and the paywalled queue
        _install_kg_library(app, cfg, judge_settings)
    elif folder_roots is not None or (startup.get(FOLDER_ROOTS_ENV) or "").strip():
        logger.warning("%s is ignored on %s: folder browsing is a local-profile feature (owner decision B4)",
                       FOLDER_ROOTS_ENV, settings.profile)

    if model_api:
        from ..modules.kg_rag_api import create_fastapi_app as create_model_api_app

        if not cfg.graph:
            raise StartupConfigError("config_rejected: the model-compatible API needs a JSON graph (cfg.graph)")
        if model_api_root_path is None:
            model_api_root_path = layout_from_env(os.environ if startup_config is None else startup_config
                                                  ).model_api_root_path
        # The legacy app's body cap (128 MiB) and browser-origin gate wrap every path of this app,
        # the mount included (AUD-04); the gateway applies the same client_max_body_size.
        # The missing-node JSONL goes under the session workdir (writable in the images); the app root is read-only.
        gaps_dir = Path(cfg.workdir) / "knowledge_gaps" if getattr(cfg, "workdir", None) else None
        model_app = create_model_api_app(str(cfg.graph), backend=cfg.backend, model=cfg.model, deployment=settings,
                                         identity_port=runtime.identity_port,
                                         credential_store=runtime.credential_store, clock=clock, gaps_dir=gaps_dir)
        app.mount(MODEL_API_MOUNT, PublicRootPath(model_app, model_api_root_path), name="model-api")
        app.state.f2w_model_api = model_app

    allowed = effective_cors_origins(cors_origins, is_local=settings.is_local)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=max_body_bytes, path_prefixes=GUARDED_PREFIXES)
    app.add_middleware(OriginGateMiddleware, allowed_origins=allowed, path_prefixes=GUARDED_PREFIXES)
    missing = undeclared_routes(app)
    if missing:  # DEP-19 at startup: every mounted route carries a declared policy (fail closed)
        raise StartupConfigError(f"config_rejected: routes without a declared access policy: {', '.join(missing)}")
    return app


def run_api(cfg: object, *, host: str, port: int, cors_origins: list[str] | None = None,
            root_path: str | None = None) -> None:
    """Serve :func:`create_api_app` with uvicorn (``cli api --jobs-api``).

    ``root_path`` is the ASGI root path behind the ``/fair2wise`` gateway (T24C, C6). ``None``
    reads it from the trusted startup setting ``F2W_PUBLIC_PREFIX`` exactly as the legacy
    ``f2w_agent.api.run_api`` does (``/fair2wise`` -> ``/fair2wise/api``; unset -> none). Routes
    stay rooted; only generated URLs (``/docs`` -> ``openapi.json``) carry the prefix.

    The model-compatible API is mounted (``model_api=True``, owner decision B7) and advertises
    ``PublicLayout.model_api_root_path`` from the same ``F2W_PUBLIC_PREFIX`` setting, whatever
    ``root_path`` says (``/fair2wise/model-api`` in the images, ``/model-api`` unprefixed).

    Deployment settings come from the process environment (``F2W_DEPLOYMENT_PROFILE`` and the
    other trusted startup values, profile ``local`` when unset). On an ALS profile the Tiled
    identity port and the server-side session store are built here with
    :func:`app.api.sessions.als_identity_wiring` (cookie ``Path`` = the public prefix), so the
    ``api`` service of the ALS images starts with sign-in wired (register D3.21).
    """
    import uvicorn

    from ..modules.deployment.gateway import uvicorn_options
    from .legacy_ui import deployment_settings_from

    layout = layout_from_env(os.environ)
    if root_path is None:
        options = uvicorn_options(layout)
    else:
        options = {"root_path": root_path} if root_path else {}
    settings = deployment_settings_from(os.environ)
    identity_port = credential_store = None
    if not settings.is_local:
        from .sessions import als_identity_wiring

        identity_port, credential_store = als_identity_wiring(settings, cookie_path=layout.prefix or "/")
    app = create_api_app(cfg, cors_origins=cors_origins, deployment=settings, identity_port=identity_port,
                         credential_store=credential_store, model_api=True,
                         model_api_root_path=layout.model_api_root_path)
    warn_if_exposed(app, host)  # AUD-16: :local beyond loopback serves raw traces to every caller
    uvicorn.run(app, host=host, port=port, **options)


__all__ = ("GUARDED_PREFIXES", "PublicRootPath", "create_api_app", "run_api")
