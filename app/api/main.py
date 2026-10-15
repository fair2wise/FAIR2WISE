"""F2W FastAPI app factory (task T14): the legacy UI API plus the versioned ``/v1`` routes.

:func:`create_api_app` builds the legacy app with
:func:`app.modules.f2w_agent.api.create_app` (unchanged signature, the 19 UI routes, the
ALS sign-in handlers, access control and disclosure projection) and adds:

* the ``/v1`` jobs, curation review, publication and status routes
  (``app/api/routes_*.py``), each declared in :mod:`app.api.policies`, all thin over one
  :class:`~app.api.operations.F2WOperations` that SDK, CLI and worker callers use directly;
* the browser-origin gate (:mod:`app.api.origin_gate`) and the request-body cap
  (:mod:`app.api.upload_limits`) on ``/v1/`` and ``/auth/``;
* the scope-change wiring: an ALS sign-out or a dropped admin role calls
  ``F2WOperations.notify_scope_change`` (``JobService.notify_scope_change``), which
  invalidates dependent publication approvals.

The legacy ``create_app`` / ``run_api`` / ``python -m app.modules.f2w_agent.cli api``
entrypoints are unchanged; ``... cli api --jobs-api`` serves this factory instead.

No job kind or real publication is registered by default (H3/H5). Importing this module
has no side effects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI

from ..modules.security.authorization import StartupConfigError
from .operations import F2WOperations, PublicationGate, build_operations
from .origin_gate import OriginGateMiddleware
from .upload_limits import DEFAULT_MAX_BODY_BYTES, MaxBodySizeMiddleware

#: Path prefixes covered by the origin gate and the body cap.
GUARDED_PREFIXES = ("/v1/", "/auth/")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_api_app(
    cfg: Any,
    *,
    cors_origins: Optional[List[str]] = None,
    deployment: Optional[Any] = None,
    startup_config: Optional[Dict[str, str]] = None,
    identity_port: Optional[Any] = None,
    credential_store: Optional[Any] = None,
    clock: Optional[Callable[[], datetime]] = None,
    operations: Optional[F2WOperations] = None,
    operations_root: Optional[Path] = None,
    publication_gate: Optional[PublicationGate] = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    sse_poll_seconds: float = 0.5,
    sse_max_seconds: float = 300.0,
) -> FastAPI:
    """Build the F2W API: the legacy UI routes plus ``/v1`` over the shared operations.

    Args:
        cfg: The launcher's ``CoordinatorConfig`` (as ``create_app``).
        cors_origins / deployment / startup_config / identity_port / credential_store / clock:
            Passed to ``create_app`` unchanged (profile selection, ALS identity, sessions).
        operations: Prebuilt operations bound to the same ``DeploymentSettings``; default
            :func:`~app.api.operations.build_operations` under ``operations_root``.
        operations_root: Directory of the default job, curation and audit stores
            (default ``<cfg.workdir>/operations``).
        publication_gate: H5 gate for the default operations (default: no target allowed).
        max_body_bytes: Request-body cap on ``/v1/`` and ``/auth/``.
        sse_poll_seconds / sse_max_seconds: Job event stream polling and maximum length.

    Raises:
        StartupConfigError: As ``create_app``, or ``operations`` bound to other settings.
    """
    from ..modules.f2w_agent.api import DEFAULT_CORS_ORIGINS, create_app
    from . import routes_jobs as jobs, routes_publications as publications, routes_review as review
    from . import routes_status as status

    app = create_app(cfg, cors_origins=cors_origins, deployment=deployment, startup_config=startup_config,
                     identity_port=identity_port, credential_store=credential_store, clock=clock)
    runtime = app.state.f2w_access
    settings = runtime.settings
    if operations is None:
        operations = build_operations(settings, operations_root or Path(cfg.workdir) / "operations",
                                      identity_port=identity_port, clock=clock or _utcnow,
                                      publication_gate=publication_gate)
    elif operations.settings != settings:
        raise StartupConfigError("config_rejected: operations are bound to another deployment profile or target")
    if operations.identity_port is None:
        operations.identity_port = identity_port
    app.state.f2w_operations = operations
    runtime.on_scope_change(operations.notify_scope_change)

    jobs.install(app, poll_seconds=sse_poll_seconds, max_stream_seconds=sse_max_seconds)
    review.install(app)
    publications.install(app)
    status.install(app)

    allowed = list(cors_origins or DEFAULT_CORS_ORIGINS)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=max_body_bytes, path_prefixes=GUARDED_PREFIXES)
    app.add_middleware(OriginGateMiddleware, allowed_origins=allowed, path_prefixes=GUARDED_PREFIXES)
    return app


def run_api(cfg: Any, *, host: str, port: int, cors_origins: Optional[List[str]] = None) -> None:
    """Serve :func:`create_api_app` with uvicorn (``cli api --jobs-api``)."""
    import uvicorn

    uvicorn.run(create_api_app(cfg, cors_origins=cors_origins), host=host, port=port)


__all__ = ("GUARDED_PREFIXES", "create_api_app", "run_api")
