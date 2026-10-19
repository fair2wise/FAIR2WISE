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
* :meth:`AccessRuntime.notify_scope_change` tells registered listeners (T14 wires
  ``JobService.notify_scope_change``) that a caller's credential or role changed: an ALS
  sign-out or a verified admin who is no longer admin. Dependent approvals are then
  invalidated; nothing falls back to a server credential.

Route policies come from ``ROUTE_CAPABILITIES`` and, for the T14 ``/v1`` routes,
:data:`app.api.policies.API_ROUTE_CAPABILITIES` (:func:`lookup_policy`).

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
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol

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
from .policies import API_SURFACE, api_route_policy

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
    ErrorCode.WRITER_UNAUTHORIZED: 503,  # deployment writer problem, not the caller's (no sign-in)
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
    #: Called with ``{"subject", "reason"}`` when an ALS caller signs out or loses admin (T14).
    scope_listeners: List[Callable[[Mapping[str, Any]], None]] = field(default_factory=list)
    #: Last verified admin flag per ALS subject (bounded), to detect a role downgrade.
    _admin_seen: Dict[str, bool] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.role_cache is None:
            self.role_cache = RoleCheckCache(self.settings.role_check_max_age)

    def on_scope_change(self, listener: Callable[[Mapping[str, Any]], None]) -> None:
        """Register ``listener`` for credential/role changes (``JobService.notify_scope_change``)."""
        self.scope_listeners.append(listener)

    def notify_scope_change(self, change: Mapping[str, Any]) -> None:
        """Run every listener; a failing listener is logged and does not stop the others."""
        for listener in list(self.scope_listeners):
            try:
                listener(dict(change))
            except Exception:  # noqa: BLE001 - one listener must not hide the change from the rest
                logger.exception("scope-change listener failed")

    def observe(self, ctx: RequestContext) -> None:
        """Record a verified ALS caller's admin flag; notify listeners when it was dropped."""
        if self.settings.is_local:
            return
        subject = ctx.principal.subject
        now_admin = bool(ctx.principal.is_admin)
        was_admin = self._admin_seen.get(subject)
        if len(self._admin_seen) >= 10_000 and subject not in self._admin_seen:
            self._admin_seen.pop(next(iter(self._admin_seen)))
        self._admin_seen[subject] = now_admin
        if was_admin and not now_admin:
            logger.warning("verified admin role dropped for a subject; notifying scope listeners")
            self.notify_scope_change({"subject": subject, "reason": "role_downgraded"})


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


def lookup_policy(method: str, path: str, surface: str) -> Optional[RoutePolicy]:
    """Return the declared policy of a route on ``surface``, or ``None`` (default-deny on ALS).

    The T14 ``/v1`` routes are declared once for the app factory (``API_SURFACE``); they
    are served by the legacy UI surface it extends, so both surfaces resolve them.
    """
    policy = route_capabilities(method, path, surface)
    if policy is None and surface in (AGENT_SURFACE, API_SURFACE):
        policy = api_route_policy(method, path)
    return policy


def undeclared_routes(app: FastAPI, surface: Optional[str] = None) -> List[str]:
    """Return ``"METHOD path"`` for app routes without a declared policy (DEP-19)."""
    surface = surface or app.state.f2w_access.surface
    missing = []
    for route in app.routes:
        if not isinstance(getattr(route, "path", None), str):
            # A mount or lazily included router hides its routes from this check: fail closed.
            missing.append(f"UNINSPECTABLE {type(route).__name__}")
            continue
        for method in sorted(getattr(route, "methods", None) or ()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            if lookup_policy(method, route.path, surface) is None:
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
    return lookup_policy(request.method, path, runtime.surface)


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
    runtime.observe(ctx)
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


def session_credential(request: Request) -> Optional[TiledCredential]:
    """Return the ALS caller's own credential from the server-side session store (never sent out).

    ``create_approval`` re-verifies the approver with it (fresh whoami with the approver's
    credential, never the writer's). ``None`` on ``:local`` and for a signed-out caller.
    """
    runtime = _runtime(request)
    if runtime.settings.is_local or runtime.credential_store is None:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    return runtime.credential_store.get(token) if token else None


def current_subject(request: Request) -> Optional[str]:
    """Return the verified ALS subject of the request's session, or ``None`` (best effort).

    Used by sign-out to name the subject whose approvals depend on the session. Uses the
    bounded-age role cache; any failure (signed out, outage) yields ``None``.
    """
    runtime = getattr(request.app.state, "f2w_access", None)
    if not isinstance(runtime, AccessRuntime) or runtime.settings.is_local:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    if not token or runtime.credential_store is None:
        return None
    try:
        credential = runtime.credential_store.get(token)
        ctx = resolve_context(runtime.settings, credential, request_id=uuid.uuid4().hex,
                              identity_port=runtime.identity_port, clock=runtime.clock,
                              role_cache=runtime.role_cache)
    except (Unauthenticated, ContractError):
        return None
    return ctx.principal.subject


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
    shown = status < 500 or code in (ErrorCode.UNAVAILABLE, ErrorCode.UNSUPPORTED_CAPABILITY,
                                     ErrorCode.WRITER_UNAUTHORIZED)
    message = str(exc) if shown else "internal error"
    headers = {"Retry-After": "5"} if code is ErrorCode.UNAVAILABLE else None
    return JSONResponse({"code": code.value, "message": message, "retryable": retryable},
                        status_code=status, headers=headers)
