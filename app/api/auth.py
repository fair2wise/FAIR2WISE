"""FastAPI wiring for the shared access policy (task T25B).

* :func:`install_access_control` attaches an :class:`AccessRuntime` to an app and
  registers the error mapping: ``Unauthenticated`` -> 401 ``sign_in_required``,
  ``DENIED`` -> 403 ``denied``, ``UNAVAILABLE`` -> 503 ``unavailable``.
* :func:`request_context` is the route dependency. It looks up the matched route in
  ``ROUTE_CAPABILITIES``, resolves the context with
  :func:`~app.modules.security.authorization.resolve_context` and checks the route's
  entry capabilities before the handler runs.
* :func:`optional_request_context` is for public routes (``GET /health``): it yields
  ``None`` for a signed-out ALS caller instead of 401.

On ``:local`` no cookie, header or token is read and the response is never a sign-in
prompt. On ALS the only credential source is the server-side session store keyed by the
opaque ``f2w_session`` cookie; Tiled tokens never reach the browser. Request headers that
claim trust (``X-Local-Trusted`` and similar) are logged and ignored.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..modules.contracts.context import RequestContext
from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.security.authorization import (
    AGENT_SURFACE,
    TRUST_CLAIM_HEADERS,
    DeploymentSettings,
    RoleCheckCache,
    RoutePolicy,
    Unauthenticated,
    require,
    resolve_context,
    route_capabilities,
)
from ..modules.security.tiled_identity import TiledCredential, TiledIdentityPort

logger = logging.getLogger(__name__)

#: Opaque, HttpOnly session cookie scoped to /fair2wise (access_contract.md section 5.1).
SESSION_COOKIE = "f2w_session"

_STATUS: Dict[ErrorCode, int] = {
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.DENIED: 403,
    ErrorCode.UNAVAILABLE: 503,
    ErrorCode.UNSUPPORTED_CAPABILITY: 501,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.STALE_APPROVAL: 409,
    ErrorCode.BUDGET_EXHAUSTED: 429,
    ErrorCode.CANCELLED: 409,
    ErrorCode.INTERNAL: 500,
}


class CredentialStore(Protocol):
    """Server-side session store: opaque session id -> the user's Tiled credential."""

    def get(self, session_token: str) -> Optional[TiledCredential]:
        """Return the credential for ``session_token``, or ``None`` if signed out/unknown."""
        ...


def _utcnow() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


@dataclass
class AccessRuntime:
    """Per-app access wiring, built once at startup from trusted configuration.

    Attributes:
        settings: Validated startup settings.
        identity_port: ALS identity port (unused and may be ``None`` on ``:local``).
        credential_store: ALS server-side session store.
        surface: Which inventoried app this is (``ROUTE_CAPABILITIES`` key).
        clock: Returns the current time.
        role_cache: Bounded-age role-check cache.
    """

    settings: DeploymentSettings
    identity_port: Optional[TiledIdentityPort] = None
    credential_store: Optional[CredentialStore] = None
    surface: str = AGENT_SURFACE
    clock: Callable[[], datetime] = _utcnow
    role_cache: Optional[RoleCheckCache] = field(default=None)

    def __post_init__(self) -> None:
        if self.role_cache is None:
            self.role_cache = RoleCheckCache(self.settings.role_check_max_age)


def install_access_control(app: FastAPI, runtime: AccessRuntime) -> FastAPI:
    """Attach ``runtime`` to ``app`` and register the access error handlers.

    Args:
        app: The FastAPI application.
        runtime: Access wiring built from trusted startup configuration.

    Returns:
        ``app``.
    """
    app.state.f2w_access = runtime
    app.add_exception_handler(Unauthenticated, _unauthenticated_handler)
    app.add_exception_handler(ContractError, _contract_error_handler)
    if runtime.settings.exposure_warning:
        logger.warning("F2W local network exposure: %s", runtime.settings.exposure_warning)
    if runtime.settings.disposable_target:
        logger.warning("F2W %s is bound to a disposable test Tiled at %s (test fixture override)",
                       runtime.settings.profile.value, runtime.settings.tiled_target)
    return app


def undeclared_routes(app: FastAPI, surface: Optional[str] = None) -> List[str]:
    """Return ``"METHOD path"`` for app routes missing from ``ROUTE_CAPABILITIES`` (DEP-19)."""
    surface = surface or app.state.f2w_access.surface
    missing = []
    for route in app.routes:
        for method in sorted(getattr(route, "methods", None) or ()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            if route_capabilities(method, route.path, surface) is None:
                missing.append(f"{method} {route.path}")
    return missing


def _runtime(request: Request) -> AccessRuntime:
    """Return the app's access runtime or fail closed."""
    runtime = getattr(request.app.state, "f2w_access", None)
    if not isinstance(runtime, AccessRuntime):
        raise ContractError(ErrorCode.INTERNAL, "access control is not installed")
    return runtime


def _policy(request: Request, runtime: AccessRuntime) -> Optional[RoutePolicy]:
    """Return the declared policy for the matched route, if any."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return None
    return route_capabilities(request.method, path, runtime.surface)


def _log_trust_claims(request: Request) -> None:
    """Log (never honour) request headers that claim trust (AUTH-06, LOCAL-07)."""
    claimed = [h for h in TRUST_CLAIM_HEADERS if h in request.headers]
    if claimed:
        logger.warning("ignored request trust claims: %s", ", ".join(claimed))


def _resolve(request: Request, policy: Optional[RoutePolicy], *, fresh: bool = False) -> RequestContext:
    """Resolve the context for ``request`` and check the route's entry capabilities.

    ``fresh`` forces a new ALS role check (privileged boundary inside a handler).
    """
    runtime = _runtime(request)
    settings = runtime.settings
    _log_trust_claims(request)
    if policy is not None and not policy.exposed_on(settings):
        raise ContractError(ErrorCode.DENIED, "route is not exposed on this deployment profile")
    credential: Optional[TiledCredential] = None
    if not settings.is_local:
        token = request.cookies.get(SESSION_COOKIE)
        if token and runtime.credential_store is not None:
            credential = runtime.credential_store.get(token)
    ctx = resolve_context(
        settings, credential, request_id=uuid.uuid4().hex, identity_port=runtime.identity_port,
        clock=runtime.clock, role_cache=runtime.role_cache,
        require_fresh=fresh or bool(policy and policy.needs_fresh_role_check),
    )
    if policy is None:
        if not settings.is_local:
            raise ContractError(ErrorCode.DENIED, "route has no declared access policy")
        return ctx
    return require(ctx, *policy.capabilities)


async def request_context(request: Request) -> RequestContext:
    """FastAPI dependency: the server-resolved, route-checked ``RequestContext``.

    Raises:
        Unauthenticated: ALS caller not signed in (401 ``sign_in_required``).
        ContractError: ``DENIED`` (403) or ``UNAVAILABLE`` (503).
    """
    return _resolve(request, _policy(request, _runtime(request)))


def fresh_request_context(request: Request) -> RequestContext:
    """Re-resolve the caller with an unconditional ALS role check (section 5.3).

    Handlers call this before a conditional privileged effect whose route entry used
    the cached role check (for example ``/chat/action decision=yes``). On ``:local``
    it returns the local-operator context without any identity call.
    """
    return _resolve(request, _policy(request, _runtime(request)), fresh=True)


async def optional_request_context(request: Request) -> Optional[RequestContext]:
    """FastAPI dependency for public routes: ``None`` for a signed-out ALS caller.

    On a non-public route this behaves exactly like :func:`request_context`.
    """
    policy = _policy(request, _runtime(request))
    try:
        return _resolve(request, policy)
    except Unauthenticated:
        if policy is not None and policy.public:
            return None
        raise
    except ContractError as exc:
        if policy is not None and policy.public and exc.code is ErrorCode.UNAVAILABLE:
            return None
        raise


async def _unauthenticated_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map ``Unauthenticated`` to 401 ``sign_in_required`` (never on ``:local``)."""
    runtime = getattr(request.app.state, "f2w_access", None)
    if isinstance(runtime, AccessRuntime) and runtime.settings.is_local:
        # Unreachable by construction: :local never resolves to Unauthenticated (D34).
        return JSONResponse({"code": ErrorCode.INTERNAL.value, "message": "internal error"}, status_code=500)
    return JSONResponse({"code": Unauthenticated.code, "message": "sign in required"}, status_code=401)


async def _contract_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map a ``ContractError`` to its HTTP status with a stable ``code``."""
    code = exc.code if isinstance(exc, ContractError) else ErrorCode.INTERNAL
    retryable = getattr(exc, "retryable", False)
    status = _STATUS.get(code, 500)
    message = str(exc) if status < 500 or code is ErrorCode.UNAVAILABLE else "internal error"
    headers = {"Retry-After": "5"} if code is ErrorCode.UNAVAILABLE else None
    return JSONResponse({"code": code.value, "message": message, "retryable": retryable},
                        status_code=status, headers=headers)
