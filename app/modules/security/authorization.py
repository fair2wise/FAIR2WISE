"""Profile-specific access context and shared effect guards (task T25B).

Implements docs/migration/authentication/access_contract.md (approved at H1, H1-7):

* :class:`DeploymentSettings`: startup-only profile/mode/target selection (section 2).
* :func:`resolve_context`: the one resolver every entry point uses (sections 3-5).
* :func:`require` / :func:`requires`: the effect guard for services, Tools, SDK and
  workers (section 1.2). It raises before any effect.
* :data:`ROUTE_CAPABILITIES`: the per-route policy for the 34 inventoried routes (section 7).
* :class:`WorkerAuthority`: recorded worker context with ALS revalidation (section 11).
* :func:`create_approval` / :func:`verify_approval`: profile- and target-bound approvals.

Nothing here reads the environment, files or the network at import time. Authority
comes only from :class:`DeploymentSettings` and, on ALS, a live
:class:`~app.modules.security.tiled_identity.TiledIdentityPort` check. No request
field (header, cookie claim, body ``mode``/``is_admin``, hostname) is an input.
"""

from __future__ import annotations

import functools
import inspect
import threading
from dataclasses import dataclass, field
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple, TypeVar, Union

from ..contracts.changes import ApprovalRecord, ChangePlan, RoleCheckEvidence
from ..contracts.context import (
    ADMIN_CAPABILITIES,
    CAPABILITIES,
    CHAT_CAPABILITIES,
    LOCAL_OPERATOR,
    ActorKind,
    AuthorizationMode,
    DeploymentProfile,
    Principal,
    RequestContext,
)
from ..contracts.errors import ContractError, ErrorCode

from .tiled_identity import (
    IdentityRejected,
    IdentityUnavailable,
    TiledCredential,
    TiledIdentityPort,
    VerifiedIdentity,
    normalize_tiled_target,
)

Clock = Callable[[], datetime]
F = TypeVar("F", bound=Callable[..., Any])

#: Fixed Tiled target per ALS profile (docs/adr/tiled-deployment.md section 5, "Target fixity").
ALS_PROFILE_TARGETS: Mapping[DeploymentProfile, str] = {
    DeploymentProfile.ALS_DEV: "https://tiled-staging.als.lbl.gov",
    DeploymentProfile.ALS_PROD: "https://tiled.als.lbl.gov",
}
#: Managed :local Tiled (tiled-deployment ADR section 4).
LOCAL_DEFAULT_TILED_TARGET = "http://127.0.0.1:8765"
LOOPBACK_HOST = "127.0.0.1"
NETWORK_HOST = "0.0.0.0"
NETWORK_EXPOSURE_WARNING = (
    "every user who can reach this address has full application access, "
    "including publication to local Tiled."
)
DEFAULT_ROLE_CHECK_MAX_AGE = timedelta(seconds=60)
#: Upper bound for the role cache: tiled 0.2.18's default access-token lifetime
#: (server/settings.py:34). Revisit with D-T6.
MAX_ROLE_CHECK_MAX_AGE = timedelta(minutes=15)
#: Credentials this close to expiry are treated as expired (clock-skew tolerance).
CLOCK_SKEW_TOLERANCE = timedelta(seconds=30)

#: Request headers that claim trust. They are ignored (and may be logged), never read as authority.
TRUST_CLAIM_HEADERS = (
    "x-local-trusted", "x-f2w-mode", "x-f2w-authorization-mode", "x-f2w-is-admin", "x-f2w-profile",
    "x-forwarded-user", "x-remote-user", "x-tiled-roles",
)


# --- startup settings ----------------------------------------------------------

class StartupConfigError(ValueError):
    """The deployment configuration is refused; the app must not start (section 2, S1-S3)."""


def _mode_for(profile: DeploymentProfile) -> AuthorizationMode:
    """Return the only authorization mode valid for ``profile``."""
    if profile is DeploymentProfile.LOCAL:
        return AuthorizationMode.LOCAL_TRUSTED
    return AuthorizationMode.TILED_AUTHENTICATED


@dataclass(frozen=True)
class DeploymentSettings:
    """Immutable, startup-only access settings. No runtime API changes them (S4).

    Construct with plain strings; ``__post_init__`` validates and normalizes.

    Attributes:
        profile: ``local``, ``als-dev`` or ``als-prod``. Anything else is refused.
        writer_identity: Name/principal id of the configured Tiled writer (D27);
            recorded as ``executed_as``, never used to establish a caller's role.
        mode: Optional explicit mode; must equal the profile's mode.
        tiled_target: Profile Tiled base URL. ALS: must equal the fixed profile URL.
            Local: defaults to the managed loopback server; ``*.als.lbl.gov`` is refused.
        local_bind: ``loopback`` (default) or ``network``; ``network`` is ``:local`` only.
        role_check_max_age: Maximum age of a cached ALS role check.
        corpus_scope: Corpora this deployment may read or affect.
    """

    profile: Union[DeploymentProfile, str]
    writer_identity: str
    mode: Optional[Union[AuthorizationMode, str]] = None
    tiled_target: Optional[str] = None
    local_bind: str = "loopback"
    role_check_max_age: timedelta = DEFAULT_ROLE_CHECK_MAX_AGE
    corpus_scope: FrozenSet[str] = field(default_factory=frozenset)
    #: ``True`` only for settings built by :meth:`for_disposable_test_target` (T25C). Not an
    #: ``__init__`` argument, so no constructor call, ``from_mapping`` value or ``replace`` can set it.
    disposable_target: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        set_ = functools.partial(object.__setattr__, self)
        try:
            profile = DeploymentProfile(self.profile)
        except ValueError:
            raise StartupConfigError(f"config_rejected: unknown deployment profile {self.profile!r}") from None
        expected_mode = _mode_for(profile)
        if self.mode not in (None, ""):
            try:
                mode = AuthorizationMode(self.mode)
            except ValueError:
                raise StartupConfigError(f"config_rejected: unknown authorization mode {self.mode!r}") from None
            if mode is not expected_mode:
                raise StartupConfigError(f"config_rejected: {mode.value} not allowed on {profile.value}")
        set_("profile", profile)
        set_("mode", expected_mode)
        set_("tiled_target", self._validated_target(profile))
        if not self.writer_identity or self.writer_identity == LOCAL_OPERATOR:
            raise StartupConfigError("config_rejected: a technical writer identity other than local-operator "
                                     "is required")
        if self.local_bind not in ("loopback", "network"):
            raise StartupConfigError(f"config_rejected: unknown F2W_LOCAL_BIND {self.local_bind!r}")
        if self.local_bind == "network" and profile is not DeploymentProfile.LOCAL:
            raise StartupConfigError("config_rejected: F2W_LOCAL_BIND applies to the local profile only")
        if not (timedelta(0) < self.role_check_max_age <= MAX_ROLE_CHECK_MAX_AGE):
            raise StartupConfigError("config_rejected: F2W_ROLE_CHECK_MAX_AGE must be in (0, 900] seconds")
        set_("corpus_scope", frozenset(self.corpus_scope))

    def _validated_target(self, profile: DeploymentProfile) -> str:
        """Return the normalized Tiled target for ``profile`` or refuse startup."""
        try:
            given = normalize_tiled_target(self.tiled_target) if self.tiled_target else None
        except ValueError as exc:
            raise StartupConfigError(f"config_rejected: {exc}") from None
        if profile is DeploymentProfile.LOCAL:
            target = given or LOCAL_DEFAULT_TILED_TARGET
            host = target.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
            if host == "als.lbl.gov" or host.endswith(".als.lbl.gov"):
                raise StartupConfigError("config_rejected: the local profile cannot target a central ALS Tiled")
            return target
        fixed = ALS_PROFILE_TARGETS[profile]
        if given is not None and given != fixed:
            raise StartupConfigError(f"config_rejected: {profile.value} accepts only F2W_TILED_URL={fixed}/")
        return fixed

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "DeploymentSettings":
        """Build settings from trusted startup values (for example the image entrypoint env).

        The caller passes the mapping explicitly; this never reads ``os.environ`` or ``.env``.

        Args:
            values: ``F2W_DEPLOYMENT_PROFILE`` (required), ``F2W_AUTHORIZATION_MODE``,
                ``F2W_TILED_URL``, ``F2W_TILED_WRITER_IDENTITY`` (required),
                ``F2W_LOCAL_BIND``, ``F2W_ROLE_CHECK_MAX_AGE`` (seconds), ``F2W_CORPUS_SCOPE``
                (comma-separated).

        Returns:
            Validated settings.

        Raises:
            StartupConfigError: On any missing or rejected value.
        """
        profile = (values.get("F2W_DEPLOYMENT_PROFILE") or "").strip()
        if not profile:
            raise StartupConfigError("config_rejected: F2W_DEPLOYMENT_PROFILE is required")
        raw_age = (values.get("F2W_ROLE_CHECK_MAX_AGE") or "").strip()
        try:
            age = timedelta(seconds=float(raw_age)) if raw_age else DEFAULT_ROLE_CHECK_MAX_AGE
        except ValueError:
            raise StartupConfigError("config_rejected: F2W_ROLE_CHECK_MAX_AGE must be a number") from None
        corpora = frozenset(c.strip() for c in (values.get("F2W_CORPUS_SCOPE") or "").split(",") if c.strip())
        return cls(
            profile=profile,
            mode=(values.get("F2W_AUTHORIZATION_MODE") or "").strip() or None,
            tiled_target=(values.get("F2W_TILED_URL") or "").strip() or None,
            writer_identity=(values.get("F2W_TILED_WRITER_IDENTITY") or "").strip(),
            local_bind=(values.get("F2W_LOCAL_BIND") or "").strip() or "loopback",
            role_check_max_age=age,
            corpus_scope=corpora,
        )

    @classmethod
    def for_disposable_test_target(cls, profile: Union[DeploymentProfile, str], *, tiled_target: str,
                                   writer_identity: str, **kwargs: Any) -> "DeploymentSettings":
        """Test-only ALS settings bound to an application-owned disposable Tiled (T25C).

        Every ALS rule runs unchanged (profile, mode, identity, approvals, route policy);
        only ``tiled_target`` is replaced by a loopback fixture server instead of the
        profile's fixed URL. This is a Python-only entry point for test fixtures:
        :meth:`from_mapping` (the startup environment), ``F2W_*`` values and requests
        cannot reach it, and ``dataclasses.replace`` drops the override (the copy is
        re-validated and refused).

        Args:
            profile: ``als-dev`` or ``als-prod``.
            tiled_target: ``http://127.0.0.1:<port>`` of a disposable fixture server.
            writer_identity: Configured technical writer principal.
            **kwargs: Other :class:`DeploymentSettings` fields.

        Raises:
            StartupConfigError: Not an ALS profile, or the target is not an explicit
                loopback ``127.0.0.1`` port.
        """
        settings = cls(profile=profile, writer_identity=writer_identity, **kwargs)
        if settings.is_local:
            raise StartupConfigError("config_rejected: the disposable target override is for ALS profiles only")
        try:
            target = normalize_tiled_target(tiled_target)
        except ValueError as exc:
            raise StartupConfigError(f"config_rejected: {exc}") from None
        parts = target.split("://", 1)[1].split("/", 1)[0]
        host, _, port = parts.partition(":")
        if not target.startswith("http://") or host != LOOPBACK_HOST or not port.isdigit():
            raise StartupConfigError("config_rejected: a disposable target must be http://127.0.0.1:<port>")
        object.__setattr__(settings, "tiled_target", target)
        object.__setattr__(settings, "disposable_target", True)
        return settings

    @property
    def is_local(self) -> bool:
        """Return whether this is the trusted-local profile."""
        return self.mode is AuthorizationMode.LOCAL_TRUSTED

    @property
    def bind_host(self) -> str:
        """Host the published gateway port binds to (section 8, LOCAL-08)."""
        return NETWORK_HOST if self.local_bind == "network" else LOOPBACK_HOST

    @property
    def exposure_warning(self) -> Optional[str]:
        """Warning to log and show in ``/health`` and the UI when ``:local`` is network-exposed."""
        return NETWORK_EXPOSURE_WARNING if self.is_local and self.local_bind == "network" else None


# --- resolution ----------------------------------------------------------------

class Unauthenticated(Exception):
    """An ALS caller is not signed in: there is no RequestContext (C7). Maps to 401.

    Args:
        reason: ``missing``, ``expired``, ``revoked``, ``invalid``, ``wrong_issuer``,
            ``service_principal`` or ``writer_credential``.
    """

    code = "sign_in_required"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RoleCheckCache:
    """Bounded-age cache of ALS role checks, keyed by target and a credential hash.

    Args:
        max_age: Entries older than this are never returned.
        max_entries: Oldest entries are evicted beyond this size.
    """

    def __init__(self, max_age: timedelta, max_entries: int = 4096) -> None:
        self.max_age = max_age
        self.max_entries = max_entries
        self._entries: Dict[Tuple[str, str], VerifiedIdentity] = {}
        self._lock = threading.Lock()

    def get(self, target: str, credential: TiledCredential, now: datetime) -> Optional[VerifiedIdentity]:
        """Return a cached identity younger than ``max_age``, else ``None``."""
        key = (target, credential.fingerprint())
        with self._lock:
            hit = self._entries.get(key)
            if hit is None:
                return None
            if not (timedelta(0) <= now - hit.verified_at < self.max_age):
                del self._entries[key]
                return None
            return hit

    def put(self, target: str, credential: TiledCredential, identity: VerifiedIdentity) -> None:
        """Record a fresh check."""
        with self._lock:
            if len(self._entries) >= self.max_entries:
                del self._entries[next(iter(self._entries))]
            self._entries[(target, credential.fingerprint())] = identity

    def invalidate(self, target: str, credential: TiledCredential) -> None:
        """Forget the check for ``credential`` (sign-out, rejection)."""
        with self._lock:
            self._entries.pop((target, credential.fingerprint()), None)


def capabilities_for(identity: VerifiedIdentity) -> FrozenSet[str]:
    """Return the capability set for a verified ALS principal (section 6)."""
    return CAPABILITIES if identity.is_admin else CHAT_CAPABILITIES


def local_operator_context(settings: DeploymentSettings, *, request_id: str, session_id: Optional[str] = None,
                           actor_kind: ActorKind = ActorKind.INTERACTIVE,
                           run_id: Optional[str] = None) -> RequestContext:
    """Return the trusted-local context: local-operator with every capability (D34).

    No credential is read and no identity call is made (LOCAL-01, LOCAL-05).
    """
    if not settings.is_local:
        raise ContractError(ErrorCode.DENIED, "local-operator context is only valid on the local profile")
    return RequestContext(
        profile=settings.profile, mode=settings.mode, principal=Principal(subject=LOCAL_OPERATOR),
        capabilities=CAPABILITIES, corpus_scope=settings.corpus_scope, request_id=request_id,
        session_id=session_id, tiled_target=settings.tiled_target, actor_kind=actor_kind, run_id=run_id,
    )


def _als_context(settings: DeploymentSettings, identity: VerifiedIdentity, *, request_id: str,
                 session_id: Optional[str], actor_kind: ActorKind, run_id: Optional[str]) -> RequestContext:
    """Build an ALS context from a verified identity."""
    principal = Principal(
        subject=identity.subject, issuer=identity.issuer, is_admin=identity.is_admin,
        verified_at=identity.verified_at, identity_provider=identity.identity_provider,
        roles=identity.roles, credential_expires_at=identity.credential_expires_at,
    )
    return RequestContext(
        profile=settings.profile, mode=settings.mode, principal=principal,
        capabilities=capabilities_for(identity), corpus_scope=settings.corpus_scope, request_id=request_id,
        session_id=session_id, tiled_target=settings.tiled_target, actor_kind=actor_kind, run_id=run_id,
    )


def verify_identity(settings: DeploymentSettings, credential: Union[TiledCredential, str, None], *,
                    identity_port: Optional[TiledIdentityPort], clock: Clock,
                    role_cache: Optional[RoleCheckCache] = None, require_fresh: bool = True) -> VerifiedIdentity:
    """Verify an ALS user credential against the profile's own Tiled.

    Args:
        settings: Startup settings (must be an ALS profile).
        credential: The user's credential from the server-side session store.
        identity_port: Port bound to ``settings.tiled_target``.
        clock: Returns the current (timezone-aware) time.
        role_cache: Optional bounded-age cache; bypassed when ``require_fresh``.
        require_fresh: Re-run the role check unconditionally (protected boundary).

    Returns:
        The verified identity.

    Raises:
        Unauthenticated: Missing, expired, revoked, wrong-issuer, service or writer credential.
        ContractError: ``UNAVAILABLE`` when the identity authority cannot answer or is
            misconfigured. There is no fallback.
    """
    if settings.is_local:
        raise ContractError(ErrorCode.INTERNAL, "verify_identity is ALS only")
    if identity_port is None or getattr(identity_port, "tiled_target", None) != settings.tiled_target:
        raise ContractError(ErrorCode.UNAVAILABLE, "identity authority not configured for this profile")
    if isinstance(credential, str):
        credential = TiledCredential(access_token=credential, tiled_target=settings.tiled_target)
    if credential is None or not credential.access_token:
        raise Unauthenticated("missing")
    if credential.tiled_target and _safe_normalize(credential.tiled_target) != settings.tiled_target:
        raise Unauthenticated("wrong_issuer")
    now = clock()
    if credential.expires_at is not None and now + CLOCK_SKEW_TOLERANCE >= credential.expires_at:
        if role_cache is not None:
            role_cache.invalidate(settings.tiled_target, credential)
        raise Unauthenticated("expired")
    identity = None
    if role_cache is not None and not require_fresh:
        identity = role_cache.get(settings.tiled_target, credential, now)
    if identity is None:
        try:
            identity = identity_port.whoami(credential, now=now)
        except IdentityRejected as exc:
            if role_cache is not None:
                role_cache.invalidate(settings.tiled_target, credential)
            raise Unauthenticated(exc.reason) from None
        except IdentityUnavailable:
            raise ContractError(ErrorCode.UNAVAILABLE, "Tiled identity check unavailable; retry later") from None
        if identity.issuer != settings.tiled_target:
            raise Unauthenticated("wrong_issuer")
        if settings.writer_identity in (identity.subject, *identity.identity_ids):
            raise Unauthenticated("writer_credential")
        if identity.principal_type != "user":
            raise Unauthenticated("service_principal")
        if role_cache is not None:
            role_cache.put(settings.tiled_target, credential, identity)
    return identity


def resolve_context(settings: DeploymentSettings, request_credential: Union[TiledCredential, str, None], *,
                    request_id: str, session_id: Optional[str] = None,
                    actor_kind: ActorKind = ActorKind.INTERACTIVE,
                    identity_port: Optional[TiledIdentityPort] = None, clock: Clock,
                    role_cache: Optional[RoleCheckCache] = None, require_fresh: bool = False,
                    run_id: Optional[str] = None) -> RequestContext:
    """Resolve the acting context for one request, job step, Tool call or SDK call.

    On ``:local`` this always returns the local-operator with all capabilities and never
    reads ``request_credential`` or calls ``identity_port``. On ALS it verifies the
    credential against the profile's Tiled: admins get every capability, other users
    the chat group. Request-supplied trust claims are not parameters, so they cannot
    affect the result.

    Args:
        settings: Startup settings.
        request_credential: The caller's credential from the server-side session store.
        request_id: Server-generated correlation id.
        session_id: Chat session bound to this principal.
        actor_kind: What drives the request.
        identity_port: ALS identity port bound to ``settings.tiled_target``.
        clock: Returns the current time.
        role_cache: Optional bounded-age role cache.
        require_fresh: Bypass the cache (privileged boundary).
        run_id: Job/run binding.

    Returns:
        The request context.

    Raises:
        Unauthenticated: ALS caller not signed in (no context).
        ContractError: ``UNAVAILABLE`` on identity outage.
    """
    if settings.is_local:
        return local_operator_context(settings, request_id=request_id, session_id=session_id,
                                      actor_kind=actor_kind, run_id=run_id)
    identity = verify_identity(settings, request_credential, identity_port=identity_port, clock=clock,
                               role_cache=role_cache, require_fresh=require_fresh)
    return _als_context(settings, identity, request_id=request_id, session_id=session_id,
                        actor_kind=actor_kind, run_id=run_id)


def _safe_normalize(url: str) -> Optional[str]:
    """Normalize ``url`` or return ``None`` when it is not a Tiled URL."""
    try:
        return normalize_tiled_target(url)
    except ValueError:
        return None


# --- effect guard --------------------------------------------------------------

def _check_names(capabilities: Iterable[str]) -> FrozenSet[str]:
    """Return ``capabilities`` as a set, rejecting names outside the frozen vocabulary."""
    caps = frozenset(capabilities)
    unknown = caps - CAPABILITIES
    if unknown:
        raise ValueError(f"unknown capabilities: {sorted(unknown)}")
    return caps


def require(ctx: Optional[RequestContext], *capabilities: str) -> RequestContext:
    """Raise ``ContractError(DENIED)`` unless ``ctx`` holds every capability. Call before any effect.

    Args:
        ctx: The resolved request context.
        *capabilities: Required capability names.

    Returns:
        ``ctx``, for chaining.

    Raises:
        ContractError: ``DENIED`` when ``ctx`` is missing or lacks a capability.
        ValueError: A capability name is outside the frozen vocabulary (programming error).
    """
    needed = _check_names(capabilities)
    if not isinstance(ctx, RequestContext):
        raise ContractError(ErrorCode.DENIED, "no server-resolved request context")
    missing = needed - ctx.capabilities
    if missing:
        raise ContractError(ErrorCode.DENIED, f"missing capability: {', '.join(sorted(missing))}")
    return ctx


def require_any(ctx: Optional[RequestContext], *capabilities: str) -> RequestContext:
    """Raise ``ContractError(DENIED)`` unless ``ctx`` holds at least one of ``capabilities``."""
    needed = _check_names(capabilities)
    if not isinstance(ctx, RequestContext):
        raise ContractError(ErrorCode.DENIED, "no server-resolved request context")
    if needed and not needed & ctx.capabilities:
        raise ContractError(ErrorCode.DENIED, f"requires one of: {', '.join(sorted(needed))}")
    return ctx


def _find_context(args: Tuple[Any, ...], kwargs: Mapping[str, Any], name: str) -> Optional[RequestContext]:
    """Return the ``RequestContext`` passed as ``name=`` or positionally."""
    if isinstance(kwargs.get(name), RequestContext):
        return kwargs[name]
    return next((a for a in args if isinstance(a, RequestContext)), None)


def requires(*capabilities: str, ctx_arg: str = "ctx") -> Callable[[F], F]:
    """Decorate a service, Tool, SDK or worker function so it checks ``capabilities`` first.

    The wrapped function must receive its ``RequestContext`` as ``ctx_arg`` (keyword) or
    positionally. Sync and async functions are supported. The decorated function exposes
    ``required_capabilities`` for registries (``ToolSpec.required_capabilities``).

    Args:
        *capabilities: Required capability names.
        ctx_arg: Keyword name of the context argument.

    Returns:
        The decorator.
    """
    needed = _check_names(capabilities)

    def decorate(func: F) -> F:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                require(_find_context(args, kwargs, ctx_arg), *needed)
                return await func(*args, **kwargs)
            wrapper: Any = async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                require(_find_context(args, kwargs, ctx_arg), *needed)
                return func(*args, **kwargs)
            wrapper = sync_wrapper
        wrapper.required_capabilities = needed
        return wrapper  # type: ignore[return-value]

    return decorate


# --- per-route policy (section 7) ------------------------------------------------

AGENT_SURFACE = "app/modules/f2w_agent/api.py"
MODEL_API_SURFACE = "app/modules/kg_rag_api.py"
DEV_MODEL_API_SURFACE = "scripts/app/modules/kg_rag_api.py"
PAPER_FINDER_SURFACE = "scripts/paper_finder.py"


@dataclass(frozen=True)
class RoutePolicy:
    """Access policy for one inventoried route.

    Attributes:
        route_id: Contract id (R01..R34).
        surface: Source file of the app that serves it (f2w_http_routes.json ``file``).
        method: HTTP method.
        path: Route path as declared (FastAPI ``route.path``).
        capabilities: Capabilities needed to enter the handler (all required). Field- or
            owner-dependent extras are checked in the handler with the helpers below.
        public: Callable without sign-in (health only).
        on_local: Exposed in the ``:local`` image.
        on_als: Exposed in ALS images.
        note: Conditional rules from the contract.
    """

    route_id: str
    surface: str
    method: str
    path: str
    capabilities: FrozenSet[str]
    public: bool = False
    on_local: bool = True
    on_als: bool = True
    note: str = ""

    @property
    def needs_fresh_role_check(self) -> bool:
        """Return whether entry requires a privileged capability (re-check admin, section 5.3)."""
        return bool(self.capabilities & ADMIN_CAPABILITIES)

    def exposed_on(self, settings: DeploymentSettings) -> bool:
        """Return whether this route is served under ``settings``' profile."""
        return self.on_local if settings.is_local else self.on_als


def _p(route_id: str, surface: str, method: str, path: str, *caps: str, **kw: Any) -> RoutePolicy:
    """Build a :class:`RoutePolicy`, validating capability names."""
    return RoutePolicy(route_id, surface, method, path, _check_names(caps), **kw)


_A, _M, _D, _P = AGENT_SURFACE, MODEL_API_SURFACE, DEV_MODEL_API_SURFACE, PAPER_FINDER_SURFACE
_MODEL_API_NOTE = "ALS exposure depends on D-T8 (token auth for non-browser clients); not exposed until then"

#: Per-route policy for every route in docs/migration/companion/f2w_http_routes.json.
ROUTE_POLICIES: Tuple[RoutePolicy, ...] = (
    _p("R01", _A, "GET", "/health", "health.read", public=True,
       note="reduced projection unless health_projection(ctx) == 'full'"),
    _p("R02", _A, "GET", "/settings", "settings.personal",
       note="paths, tiled_uri, tiled_error need settings.global"),
    _p("R03", _A, "PUT", "/settings", "settings.personal",
       note="settings_update_plan (T25D): personal fields per principal; deployment-wide "
            "fields need settings.global (ignored for non-admins); tiled_uri writable on :local only"),
    _p("R04", _A, "POST", "/session/reset", "session.own", note="session.any for another principal's session"),
    _p("R05", _A, "DELETE", "/session/{session_id}", "session.own",
       note="session.any for another principal's session"),
    _p("R06", _A, "GET", "/graph", "chat.read_support"),
    _p("R07", _A, "POST", "/graph/nodes/search", "chat.read_support"),
    _p("R08", _A, "GET", "/graph/neighborhood/{node_id:path}", "chat.read_support"),
    _p("R09", _A, "GET", "/graph/node/{node_id}", "chat.read_support",
       note="json_graph_path query allowed for every reader (owner decision O-A5)"),
    _p("R10", _A, "PATCH", "/graph/node/{node_id}", "graph.edit"),
    _p("R11", _A, "POST", "/graph/upload", "graph.upload"),
    _p("R12", _A, "POST", "/chat", "chat.ask", note="no ingestion pending card unless chat_offers_ingestion"),
    _p("R13", _A, "POST", "/chat/action", "chat.decline_pending",
       note="decision=yes needs chat_action_capabilities(decision, kind)"),
    _p("R14", _A, "POST", "/publications/search", "chat.read_support"),
    _p("R15", _A, "POST", "/chat/stream", "chat.ask", note="as R12"),
    _p("R16", _A, "POST", "/chat/action/stream", "chat.decline_pending", note="as R13"),
    _p("R17", _A, "GET", "/traces/{turn_id}", "trace.read_own",
       note="trace.read_any for another principal's turn; always projected"),
    _p("R18", _A, "POST", "/telemetry/ui", "telemetry.write", note="actor from ctx, not payload"),
    _p("R19", _A, "POST", "/annotations", "annotation.write",
       note="annotator replaced by verified subject; any signed-in user on own turns, admins on any "
            "(owner decision O-A1)"),
    _p("R20", _M, "POST", "/api/chat", "chat.ask", on_als=False, note=_MODEL_API_NOTE),
    _p("R21", _M, "POST", "/v1/chat/completions", "chat.ask", on_als=False, note=_MODEL_API_NOTE),
    _p("R22", _M, "GET", "/api/tags", "chat.ask"),
    _p("R23", _M, "GET", "/api/version", "chat.ask"),
    _p("R24", _M, "GET", "/v1/models", "chat.ask"),
    _p("R25", _M, "GET", "/api/ps", "chat.ask"),
    _p("R26", _D, "POST", "/api/chat", on_local=False, on_als=False, note="developer script; not packaged"),
    _p("R27", _D, "POST", "/v1/chat/completions", on_local=False, on_als=False,
       note="developer script; not packaged"),
    _p("R28", _D, "GET", "/api/tags", on_local=False, on_als=False, note="developer script; not packaged"),
    _p("R29", _D, "GET", "/api/version", on_local=False, on_als=False, note="developer script; not packaged"),
    _p("R30", _D, "GET", "/v1/models", on_local=False, on_als=False, note="developer script; not packaged"),
    _p("R31", _D, "GET", "/api/ps", on_local=False, on_als=False, note="developer script; not packaged"),
    _p("R32", _P, "GET", "/", "ingest.acquire", on_als=False, note="loopback CLI operator tool only"),
    _p("R33", _P, "GET", "/detected", "ingest.acquire", on_als=False, note="loopback CLI operator tool only"),
    _p("R34", _P, "POST", "/action", "ingest.acquire", on_als=False, note="loopback CLI operator tool only"),
)

#: ``(surface, METHOD, path) -> RoutePolicy``.
ROUTE_CAPABILITIES: Mapping[Tuple[str, str, str], RoutePolicy] = {
    (p.surface, p.method, p.path): p for p in ROUTE_POLICIES
}


#: FastAPI's generated API reference pages, outside the 34-route inventory. Public on
#: every profile, including ALS (owner decision O-A8, 2026-10-12). They bypass route
#: dependencies, so they must expose no data beyond the API schema.
FRAMEWORK_ROUTE_POLICIES: Tuple[RoutePolicy, ...] = (
    _p("F01", _A, "GET", "/docs", "health.read", public=True, note="FastAPI Swagger UI (O-A8)"),
    _p("F02", _A, "GET", "/docs/oauth2-redirect", "health.read", public=True, note="FastAPI Swagger UI (O-A8)"),
    _p("F03", _A, "GET", "/redoc", "health.read", public=True, note="FastAPI ReDoc (O-A8)"),
    _p("F04", _A, "GET", "/openapi.json", "health.read", public=True, note="FastAPI OpenAPI schema (O-A8)"),
)

FRAMEWORK_ROUTE_CAPABILITIES: Mapping[Tuple[str, str, str], RoutePolicy] = {
    (p.surface, p.method, p.path): p for p in FRAMEWORK_ROUTE_POLICIES
}


#: ALS sign-in/sign-out handlers (access_contract.md section 7 "new" row; ``app/api/sessions.py``,
#: T25C). Mounted on ALS only; there is no local login (D34).
SESSION_ROUTE_POLICIES: Tuple[RoutePolicy, ...] = (
    _p("S01", _A, "GET", "/auth/providers", "auth.session", public=True, on_local=False,
       note="sign-in options generated from the profile Tiled's advertised providers"),
    _p("S02", _A, "POST", "/auth/sign-in", "auth.session", public=True, on_local=False,
       note="forwards to the profile Tiled's own provider; tokens stay server-side"),
    _p("S03", _A, "POST", "/auth/sign-out", "auth.session", public=True, on_local=False,
       note="drops the F2W session and revokes the Tiled session"),
)

SESSION_ROUTE_CAPABILITIES: Mapping[Tuple[str, str, str], RoutePolicy] = {
    (p.surface, p.method, p.path): p for p in SESSION_ROUTE_POLICIES
}


def route_capabilities(method: str, path: str, surface: str = AGENT_SURFACE) -> Optional[RoutePolicy]:
    """Return the policy for a route, or ``None`` (default-deny on ALS)."""
    key = (surface, method.upper(), path)
    return ROUTE_CAPABILITIES.get(key) or FRAMEWORK_ROUTE_CAPABILITIES.get(key) \
        or SESSION_ROUTE_CAPABILITIES.get(key)


#: PUT /settings fields a non-admin may change for their own session (LLM-01).
SETTINGS_PERSONAL_FIELDS = frozenset({"backend", "model", "provider"})
#: Not writable at runtime on ALS; ignored there with ``setting_not_writable``. On ``:local``
#: they are global settings (owner decision 2026-10-12: Tiled URL editable on local only).
SETTINGS_NOT_WRITABLE = frozenset({"tiled_uri"})


def settings_update_capabilities(
        fields: Iterable[str], *, local: bool = False
) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """Return ``(required capabilities, ignored fields)`` to change these fields server-wide.

    The legacy UI's PUT /settings uses :func:`settings_update_plan` since T25D (personal
    fields per principal on ALS); this function keeps the server-wide rule.

    Unknown fields count as global (fail closed). ``tiled_uri`` is ignored on ALS and is
    a global setting on ``:local`` (``local`` must come from startup settings, never
    from the request).
    """
    names = frozenset(fields)
    ignored = frozenset() if local else names & SETTINGS_NOT_WRITABLE
    caps = {"settings.personal"}
    if names - ignored - SETTINGS_PERSONAL_FIELDS:
        caps.add("settings.global")
    return frozenset(caps), ignored


#: T25D (owner Q4): fields stored per principal on ALS; each turn uses the caller's copy.
#: Classification and reasons: docs/migration/authentication/settings_fields.md.
SETTINGS_USER_FIELDS = frozenset({
    "backend", "model", "provider", "graph_source", "json_graph_path", "json_graph_paths",
    "selected_graph_ids", "kg_query_max_nodes", "kg_query_hops", "source_rag", "use_live_tiled",
})
#: T25D: fields that change the shared service; admin-only on ALS (``settings.global``).
#: ``tiled_uri`` is also in :data:`SETTINGS_NOT_WRITABLE` (never writable on ALS).
SETTINGS_DEPLOYMENT_FIELDS = frozenset({"workflow_mode", "extraction_mode", "targeted_max_pages", "tiled_uri"})


@dataclass(frozen=True)
class SettingsUpdatePlan:
    """How one PUT /settings body is applied on a profile (T25D).

    Attributes:
        capabilities: Capabilities the caller must hold for the fields that are applied.
        personal: Fields stored in the caller's personal settings (ALS) or applied (``:local``).
        deployment: Deployment-wide fields applied to the server runtime.
        ignored: Fields dropped with a ``setting_not_writable`` warning.
    """

    capabilities: FrozenSet[str]
    personal: FrozenSet[str]
    deployment: FrozenSet[str]
    ignored: FrozenSet[str]


def settings_update_plan(fields: Iterable[str], *, local: bool, admin: bool) -> SettingsUpdatePlan:
    """Split a PUT /settings body into personal, deployment-wide and ignored fields (T25D).

    On ``:local`` every field applies to the server runtime, as before (the operator's
    settings are the server's). On ALS, personal fields go to the caller's own copy;
    deployment-wide and unknown fields (fail closed) need ``settings.global`` and are
    ignored, not refused, for a non-admin (``admin`` is the caller's current
    ``settings.global``; the guard re-runs the role check before applying them);
    ``tiled_uri`` is ignored for everyone.
    """
    names = frozenset(fields)
    if local:
        caps, _ = settings_update_capabilities(names, local=True)
        return SettingsUpdatePlan(caps, names, frozenset(), frozenset())
    personal = names & SETTINGS_USER_FIELDS
    rest = names - personal
    ignored = rest & SETTINGS_NOT_WRITABLE
    deployment = rest - ignored
    if not admin:
        ignored, deployment = ignored | deployment, frozenset()
    caps = {"settings.personal"} | ({"settings.global"} if deployment else set())
    return SettingsUpdatePlan(frozenset(caps), personal, deployment, ignored)


def chat_action_capabilities(decision: str, kind: Optional[str]) -> FrozenSet[str]:
    """Return capabilities for POST /chat/action[/stream] (R13/R16).

    ``yes`` without a ``kind`` may resolve to either pending kind, so it needs both.
    """
    if decision == "no":
        return frozenset({"chat.decline_pending"})
    if decision != "yes":
        raise ContractError(ErrorCode.INVALID_INPUT, "decision must be yes or no")
    if kind == "download":
        return frozenset({"ingest.acquire"})
    if kind == "extraction":
        return frozenset({"ingest.extract"})
    return frozenset({"ingest.acquire", "ingest.extract"})


def graph_node_read_capabilities(json_graph_path: Optional[str]) -> FrozenSet[str]:
    """Return capabilities for GET /graph/node/{id} (R09).

    Reading a JSON graph is open to every local user and every signed-in ALS user,
    with or without ``json_graph_path`` (owner decision O-A5, 2026-10-12). Changing
    the server-wide default graph selection (PUT /settings ``json_graph_paths``)
    stays admin-only.
    """
    return frozenset({"chat.read_support"})


def require_owner_or(ctx: RequestContext, owner_subject: Optional[str], own: str, any_: str) -> RequestContext:
    """Require ``own`` for the caller's own resource, else ``any_`` (sessions R04/R05, traces R17).

    An unowned (legacy) resource counts as someone else's on ALS.
    """
    if ctx.mode is AuthorizationMode.TILED_AUTHENTICATED and owner_subject != ctx.principal.subject:
        return require(ctx, any_)
    return require(ctx, own)


def health_projection(ctx: Optional[RequestContext]) -> str:
    """Return ``"full"`` or ``"reduced"`` for GET /health (R01)."""
    return "full" if ctx is not None and ctx.has("settings.global") else "reduced"


def chat_offers_ingestion(ctx: RequestContext) -> bool:
    """Return whether chat may offer a download/extraction pending card (AUTH-03)."""
    return ctx.has("ingest.acquire") and ctx.has("ingest.extract")


# --- workers (section 11) ----------------------------------------------------------

def worker_context(ctx: RequestContext, *, run_id: str) -> RequestContext:
    """Return the recorded context a worker carries for ``run_id`` (``actor_kind=worker``)."""
    data = ctx.model_dump()
    data.update(actor_kind=ActorKind.WORKER, run_id=run_id)
    return RequestContext.model_validate(data)


class WorkerAuthority:
    """Recorded worker authority, checked at every protected boundary.

    Local workers need no refresh and make no identity calls. ALS workers re-verify
    the initiating subject's credential via the identity port at each boundary; a
    downgrade, expiry, sign-out or revocation stops new effects permanently (AUTH-07).
    Prior audit records are untouched.

    Args:
        ctx: The initiating context (converted to ``actor_kind=worker`` if needed).
        settings: Startup settings of the deployment running the worker.
        identity_port: ALS identity port.
        credential_provider: Returns the initiating subject's current credential from the
            server-side session store, or ``None`` once signed out.
        clock: Returns the current time.
    """

    def __init__(self, ctx: RequestContext, settings: DeploymentSettings, *,
                 identity_port: Optional[TiledIdentityPort] = None,
                 credential_provider: Optional[Callable[[], Optional[TiledCredential]]] = None,
                 clock: Clock) -> None:
        if ctx.profile is not settings.profile or ctx.tiled_target != settings.tiled_target:
            raise ContractError(ErrorCode.DENIED, "recorded context belongs to another deployment or target")
        if ctx.actor_kind is not ActorKind.WORKER:
            ctx = worker_context(ctx, run_id=ctx.run_id or ctx.request_id)
        self.ctx = ctx
        self._settings = settings
        self._port = identity_port
        self._credentials = credential_provider
        self._clock = clock
        self.stopped_reason: Optional[str] = None

    def check(self, *capabilities: str) -> RequestContext:
        """Re-authorize before an effect and return the current context.

        Raises:
            ContractError: ``DENIED`` (stopped: downgrade, expiry, revocation, sign-out)
                or ``UNAVAILABLE`` (identity outage; the job may retry later).
        """
        if self.stopped_reason is not None:
            raise ContractError(ErrorCode.DENIED, f"worker stopped: {self.stopped_reason}")
        if self._settings.is_local:
            return require(self.ctx, *capabilities)
        credential = self._credentials() if self._credentials is not None else None
        try:
            identity = verify_identity(self._settings, credential, identity_port=self._port,
                                       clock=self._clock, require_fresh=True)
        except Unauthenticated as exc:
            return self._stop(f"initiating credential {exc.reason}")
        if identity.subject != self.ctx.principal.subject:
            return self._stop("credential belongs to another subject")
        refreshed = _als_context(self._settings, identity, request_id=self.ctx.request_id,
                                 session_id=self.ctx.session_id, actor_kind=ActorKind.WORKER,
                                 run_id=self.ctx.run_id)
        self.ctx = refreshed
        try:
            return require(refreshed, *capabilities)
        except ContractError as exc:
            return self._stop(str(exc))

    def _stop(self, reason: str) -> RequestContext:
        """Stop the worker permanently and raise ``DENIED``."""
        self.stopped_reason = reason
        raise ContractError(ErrorCode.DENIED, f"worker stopped: {reason}")



CredentialLookup = Callable[[str], Optional[TiledCredential]]


class JobAuthority:
    """Default stage authority of a ``JobService``: one :class:`WorkerAuthority` per job run (AUD-01).

    ``JobService.register`` wires this for every kind that does not bring its own authority,
    so a stage's ``required_capability`` and every ``StageContext.require`` call re-authorize
    the *initiating* subject before the effect:

    * ``:local``: the local-operator worker context; no credential is read and no identity
      call is made (LOCAL-01, LOCAL-05).
    * ALS: the initiating subject's *current* credential comes from ``credential_lookup``
      (the server-side session store, by subject). A signed-out subject (``None``), a
      rejected credential, a different subject or a role downgrade stops the run before its
      next effect (AUTH-07). An identity outage raises ``UNAVAILABLE`` so the job may retry.

    Args:
        settings: Startup settings of the deployment running the worker.
        identity_port: ALS identity port (required on ALS).
        credential_lookup: ``subject -> current credential or None`` (required on ALS).
        clock: Returns the current time.

    Raises:
        StartupConfigError: An ALS deployment without an identity port or credential lookup.
    """

    def __init__(self, settings: DeploymentSettings, *, identity_port: Optional[TiledIdentityPort] = None,
                 credential_lookup: Optional[CredentialLookup] = None, clock: Optional[Clock] = None) -> None:
        if not settings.is_local:
            if identity_port is None:
                raise StartupConfigError("config_rejected: ALS job authority needs an identity port")
            if credential_lookup is None:
                raise StartupConfigError("config_rejected: ALS job authority needs a credential lookup "
                                         "(the server-side session store)")
        self.settings = settings
        self._port = identity_port
        self._lookup = credential_lookup
        self._clock: Clock = clock or (lambda: datetime.now(timezone.utc))

    def for_job(self, job: Any) -> Callable[[Any, Sequence[str]], RequestContext]:
        """The authority of one run of ``job`` (a stopped run never resumes effects)."""
        return _JobRunAuthority(self, job)

    def __call__(self, job: Any, capabilities: Sequence[str]) -> RequestContext:
        """One-off check (a runner that does not call :meth:`for_job`)."""
        return self.for_job(job)(job, capabilities)

    def _start(self, job: Any) -> WorkerAuthority:
        """Build the run's worker authority from the initiating subject's current credential."""
        settings = self.settings
        subject = str(getattr(job, "created_by", "") or "")
        run_id = str(getattr(job, "job_id", "") or uuid.uuid4().hex)
        request_id = uuid.uuid4().hex
        if settings.is_local:
            if subject != LOCAL_OPERATOR:
                raise ContractError(ErrorCode.DENIED, "worker stopped: job was not submitted by the local operator")
            ctx = local_operator_context(settings, request_id=request_id, actor_kind=ActorKind.WORKER, run_id=run_id)
            return WorkerAuthority(ctx, settings, clock=self._clock)
        assert self._lookup is not None  # checked at construction
        lookup = self._lookup
        credential = lookup(subject)
        if credential is None:
            raise ContractError(ErrorCode.DENIED, "worker stopped: initiating credential signed_out")
        try:
            identity = verify_identity(settings, credential, identity_port=self._port, clock=self._clock,
                                       require_fresh=True)
        except Unauthenticated as exc:
            raise ContractError(ErrorCode.DENIED, f"worker stopped: initiating credential {exc.reason}") from None
        if identity.subject != subject:
            raise ContractError(ErrorCode.DENIED, "worker stopped: credential belongs to another subject")
        ctx = _als_context(settings, identity, request_id=request_id, session_id=None,
                           actor_kind=ActorKind.WORKER, run_id=run_id)
        return WorkerAuthority(ctx, settings, identity_port=self._port,
                               credential_provider=lambda: lookup(subject), clock=self._clock)


class _JobRunAuthority:
    """Per-run state of :class:`JobAuthority`: the worker authority is built on first use."""

    def __init__(self, parent: JobAuthority, job: Any) -> None:
        self._parent = parent
        self._job_id = getattr(job, "job_id", None)
        self._worker: Optional[WorkerAuthority] = None
        self.stopped_reason: Optional[str] = None

    def __call__(self, job: Any, capabilities: Sequence[str]) -> RequestContext:
        if self.stopped_reason is not None:
            raise ContractError(ErrorCode.DENIED, f"worker stopped: {self.stopped_reason}")
        if getattr(job, "job_id", None) != self._job_id:
            raise ContractError(ErrorCode.DENIED, "worker stopped: authority belongs to another job")
        if self._worker is None:
            try:
                self._worker = self._parent._start(job)
            except ContractError as exc:
                if exc.code is ErrorCode.DENIED:
                    self.stopped_reason = str(exc).replace("worker stopped: ", "", 1)
                raise
        ctx = self._worker.check(*capabilities)
        return ctx


# --- approvals (section 11) ----------------------------------------------------------

def target_in_deployment(target: str, settings: DeploymentSettings) -> bool:
    """Return whether a plan/approval ``target`` lies on this deployment's Tiled."""
    base = settings.tiled_target
    try:
        parts = target.split("#", 1)
        normalized = normalize_tiled_target(parts[0])
    except ValueError:
        return False
    return normalized == base or normalized.startswith(base + "/")


def _check_binding(settings: DeploymentSettings, ctx: RequestContext, target: str) -> None:
    """Refuse when ``ctx`` or ``target`` belongs to another profile or Tiled target."""
    if ctx.profile is not settings.profile or ctx.mode is not settings.mode:
        raise ContractError(ErrorCode.DENIED, "context belongs to another deployment profile")
    if ctx.tiled_target is not None and ctx.tiled_target != settings.tiled_target:
        raise ContractError(ErrorCode.DENIED, "context is bound to another Tiled target")
    if not target_in_deployment(target, settings):
        raise ContractError(ErrorCode.DENIED, "target is not on this deployment's Tiled")


def create_approval(ctx: RequestContext, plan: ChangePlan, settings: DeploymentSettings, *, approval_id: str,
                    clock: Clock, identity_port: Optional[TiledIdentityPort] = None,
                    approver_credential: Optional[TiledCredential] = None,
                    requester: Optional[Principal] = None, operation_id: Optional[str] = None,
                    limits: Optional[Mapping[str, int]] = None, expires_at: Optional[datetime] = None,
                    capability: str = "curation.approve") -> ApprovalRecord:
    """Record an explicit human approval of ``plan`` by the caller in ``ctx``.

    Local: requested_by/approved_by are local-operator, ``executed_as`` is the writer,
    no issuer or role fields. ALS: the caller must be a verified admin after a fresh
    ``whoami`` with their own credential (never the writer's).

    Args:
        ctx: Context of the human approver (interactive or CLI; never a worker/delegate).
        plan: The exact plan being approved.
        settings: Startup settings.
        approval_id: Identifier of the new record.
        clock: Returns the current time.
        identity_port: ALS identity port.
        approver_credential: ALS approver's current credential.
        requester: Verified ALS requester; defaults to the approver.
        operation_id: Job/operation that requested the plan.
        limits: Numeric limits on the operation.
        expires_at: Approval expiry.
        capability: Capability that authorizes approving.

    Returns:
        The validated approval record.

    Raises:
        ContractError: ``DENIED`` (no capability, wrong actor, wrong target/profile, not
            admin) or ``UNAVAILABLE`` (identity outage).
        Unauthenticated: The ALS approver's credential is no longer valid.
    """
    if ctx.actor_kind not in (ActorKind.INTERACTIVE, ActorKind.CLI):
        raise ContractError(ErrorCode.DENIED, "approvals are made by a human, not a worker or delegate")
    require(ctx, capability)
    _check_binding(settings, ctx, plan.target)
    common: Dict[str, Any] = dict(
        approval_id=approval_id, profile=settings.profile, mode=settings.mode, plan_id=plan.plan_id,
        plan_sha256=plan.plan_sha256, target=plan.target, scope=plan.corpus_scope,
        executed_as=settings.writer_identity, operation_id=operation_id, limits=dict(limits or {}),
        expires_at=expires_at,
    )
    if settings.is_local:
        return ApprovalRecord(requested_by=LOCAL_OPERATOR, approved_by=LOCAL_OPERATOR,
                              approved_at=clock(), **common)
    identity = verify_identity(settings, approver_credential, identity_port=identity_port, clock=clock,
                               require_fresh=True)
    if identity.subject != ctx.principal.subject:
        raise ContractError(ErrorCode.DENIED, "approver credential belongs to another subject")
    if not identity.is_admin:
        raise ContractError(ErrorCode.DENIED, "approver is not a verified Tiled admin")
    requester = requester or ctx.principal
    if requester.is_local_operator() or requester.issuer != settings.tiled_target or not requester.verified_at:
        raise ContractError(ErrorCode.DENIED, "requester is not verified by this deployment's Tiled")
    evidence = RoleCheckEvidence(tiled_target=identity.issuer, roles=identity.roles,
                                 identity_provider=identity.identity_provider,
                                 tiled_library_version=identity.tiled_library_version)
    return ApprovalRecord(
        requested_by=requester.subject, approved_by=identity.subject, approver_issuer=identity.issuer,
        role_checked_at=identity.verified_at, approver_is_admin=True, role_check_evidence=evidence,
        requester_issuer=requester.issuer, approved_at=identity.verified_at, **common,
    )


def verify_approval(approval: ApprovalRecord, plan: ChangePlan, settings: DeploymentSettings, *,
                    at: datetime) -> ApprovalRecord:
    """Check an approval may authorize ``plan`` on this deployment before the import runs.

    Raises:
        ContractError: ``DENIED`` for another profile/target/issuer/writer (a local
            approval never authorizes an ALS target, LOCAL-10, AUTH-08); ``STALE_APPROVAL``
            when it does not cover this exact plan or has expired.
    """
    if approval.profile is not settings.profile or approval.mode is not settings.mode:
        raise ContractError(ErrorCode.DENIED, f"approval from profile {approval.profile.value} is not valid on "
                                              f"{settings.profile.value}")
    if not target_in_deployment(approval.target, settings):
        raise ContractError(ErrorCode.DENIED, "approval target is not on this deployment's Tiled")
    if not settings.is_local and approval.approver_issuer != settings.tiled_target:
        raise ContractError(ErrorCode.DENIED, "approval was verified by another Tiled server")
    if approval.executed_as != settings.writer_identity:
        raise ContractError(ErrorCode.DENIED, "approval names a different technical writer")
    if not approval.authorizes(plan, at):
        raise ContractError(ErrorCode.STALE_APPROVAL, "approval does not cover this plan, target or time")
    return approval
