"""Server-side ALS session store and the sign-in/sign-out handlers (task T25C).

Implements access_contract.md sections 5.1, 5.4 and 5.5, minimally and delegated to the
profile's own Tiled:

* **No identity provider in F2W.** Sign-in forwards the user's form to the profile
  Tiled's own ``internal`` (password) provider, ``POST {tiled}/api/v1/auth/provider/
  {provider}/token``. F2W never stores, logs or checks a password. External (OIDC)
  providers are listed but refused with ``unsupported_capability`` until the gateway
  redirect flow exists (D-T7, T24C).
* **Tokens stay server-side.** The Tiled access and refresh tokens are kept in memory,
  keyed by the SHA-256 of an opaque random session id. The browser gets only that id,
  in an HttpOnly, Secure, SameSite=Strict cookie (:data:`~app.api.auth.SESSION_COOKIE`)
  scoped to ``/fair2wise``. Tokens never enter a response, a log, a ``RequestContext``
  or an audit record.
* **Refresh.** :meth:`TiledSessionStore.get` refreshes an access token that is about to
  expire through ``/api/v1/auth/session/refresh``. A refused refresh drops the session
  (the caller is signed out); an unreachable Tiled is ``UNAVAILABLE``, never a reason to
  keep using an expired token.
* **Sign-out** drops the F2W session first, then revokes the Tiled session
  (``/api/v1/auth/session/revoke``). On 0.2.18 the revoked session's access token is
  still accepted by Tiled until it expires (D-T6), but F2W no longer holds it.
* **No proxy.** Only these three handlers talk to Tiled's auth router; nothing else is
  forwarded.
* **Cross-site requests (T14).** ``POST /auth/sign-in`` and ``POST /auth/sign-out`` refuse a
  cross-site browser request before any effect (:func:`app.api.origin_gate.check_browser_origin`:
  ``Sec-Fetch-Site: cross-site`` or an ``Origin`` that is neither the request's own host nor
  in ``allowed_origins``). Together with the ``SameSite=Strict`` cookie this blocks login
  CSRF and forced sign-out from other sites, including sibling ``*.lbl.gov`` hosts that
  ``SameSite`` treats as same-site. A session-bound CSRF token (access contract section 9)
  needs a sign-in page that sends it; the frozen UI has none, so it waits for the gateway
  sign-in UI (T24C). See docs/migration/api/README.md section 5.
* **Scope change (T14).** Sign-out tells the app's access runtime which verified subject
  signed out, so dependent publication approvals are invalidated
  (``JobService.notify_scope_change``).
* **Restart signs everyone out.** Sessions are in memory (one process); a persistent or
  shared store is a deployment item (T24).

The store binds to exactly one Tiled target and is the ``credential_store`` that
:func:`app.api.auth.request_context` reads. Roles are never decided here: every request
still goes through the live whoami role check in ``resolve_context``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import logging
import secrets
import threading
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.contracts.json_types import Untyped
from ..modules.security.authorization import CLOCK_SKEW_TOLERANCE, DeploymentSettings, StartupConfigError
from ..modules.security.tiled_identity import (
    ABOUT_PATH,
    TiledCredential,
    TiledWhoamiAdapter,
    normalize_tiled_target,
)
from .auth import SESSION_COOKIE, AccessRuntime, current_subject
from .origin_gate import check_browser_origin

if TYPE_CHECKING:  # annotation only; als_identity_wiring imports httpx when it builds a client
    import httpx

logger = logging.getLogger(__name__)

#: 0.2.18 auth router paths under the Tiled base URL (``tiled/server/authentication.py``).
TOKEN_PATH = "/api/v1/auth/provider/{provider}/token"
REFRESH_PATH = "/api/v1/auth/session/refresh"
REVOKE_PATH = "/api/v1/auth/session/revoke"
#: Cookie path of the ALS gateway (``/fair2wise``, AUTH-09).
DEFAULT_COOKIE_PATH = "/fair2wise"
DEFAULT_MAX_SESSIONS = 10_000


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SignInFailed(Exception):
    """The profile Tiled refused the sign-in (wrong user name or password). Maps to 401."""

    code = "sign_in_failed"


@dataclass
class _Session:
    """One signed-in browser session. Never serialized; ``repr`` hides the tokens."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: datetime | None
    created_at: datetime
    #: Verified subject of the session once a request resolved it (``bind_subject``); workers
    #: look the session up by it (``credential_for_subject``). Never taken from request data.
    subject: str | None = None


class TiledSessionStore:
    """In-memory server-side session store bound to one Tiled (``CredentialStore``).

    Sessions live only as long as the process; a restart signs everyone out.

    Args:
        tiled_target: Base URL of the profile's Tiled; the only server this store talks to.
        http_client: ``httpx.Client``-like object with ``get``/``post``.
        clock: Returns the current UTC time.
        timeout: Per-call timeout in seconds.
        cookie_path: Cookie ``Path`` (the gateway prefix).
        cookie_secure: Send the cookie over HTTPS only.
        max_sessions: Oldest sessions are dropped beyond this many.
    """

    def __init__(self, tiled_target: str, http_client: object, *, clock: Callable[[], datetime] = _utcnow,
                 timeout: float = 10.0, cookie_path: str = DEFAULT_COOKIE_PATH, cookie_secure: bool = True,
                 max_sessions: int = DEFAULT_MAX_SESSIONS) -> None:
        """Bind the Tiled target (normalized) and the HTTP client; the arguments are described on the class."""
        self.tiled_target = normalize_tiled_target(tiled_target)
        self.cookie_path = cookie_path
        self.cookie_secure = cookie_secure
        self._client = http_client
        self._clock = clock
        self._timeout = timeout
        self._max = max_sessions
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"TiledSessionStore({self.tiled_target!r}, sessions={len(self._sessions)})"

    @staticmethod
    def _key(session_token: str) -> str:
        return hashlib.sha256(session_token.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ Tiled calls

    def _call(self, method: str, path: str, **kwargs: object) -> Untyped:
        """Call the bound Tiled; transport failures are ``UNAVAILABLE``."""
        try:
            return getattr(self._client, method)(self.tiled_target + path, timeout=self._timeout, **kwargs)
        except Exception as exc:
            raise ContractError(ErrorCode.UNAVAILABLE, f"Tiled sign-in service unavailable ({type(exc).__name__})",
                                retryable=True) from None

    def providers(self) -> list[dict[str, str]]:
        """Return the advertised providers as ``[{"provider", "mode"}]`` (sign-in options)."""
        resp = self._call("get", ABOUT_PATH, headers={"Accept": "application/json"})
        try:
            about = resp.json() if resp.status_code == 200 else None
        except ValueError:
            about = None
        authn = about.get("authentication") if isinstance(about, Mapping) else None
        if not isinstance(authn, Mapping):
            raise ContractError(ErrorCode.UNAVAILABLE, "Tiled did not describe its authentication", retryable=True)
        out = []
        for item in authn.get("providers") or []:
            if isinstance(item, Mapping) and isinstance(item.get("provider"), str):
                out.append({"provider": item["provider"], "mode": str(item.get("mode") or "")})
        return out

    def _tokens(self, resp: httpx.Response) -> tuple[str, str | None, datetime | None]:
        """Read ``access_token``/``refresh_token``/``expires_in`` from a Tiled token response."""
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, Mapping) or not isinstance(body.get("access_token"), str):
            raise ContractError(ErrorCode.UNAVAILABLE, "Tiled returned no access token", retryable=True)
        expires_in = body.get("expires_in")
        expires_at = None
        if isinstance(expires_in, (int, float)):
            expires_at = self._clock() + timedelta(seconds=float(expires_in))
        refresh = body.get("refresh_token") if isinstance(body.get("refresh_token"), str) else None
        return body["access_token"], refresh, expires_at

    # ------------------------------------------------------------------ store API

    def sign_in(self, provider: str, username: str, password: str) -> str:
        """Sign in through the profile Tiled's password provider and return a new session id.

        Raises:
            SignInFailed: Tiled refused the user name or password.
            ContractError: ``INVALID_INPUT`` (unknown provider), ``UNSUPPORTED_CAPABILITY``
                (external provider) or ``UNAVAILABLE``.
        """
        advertised = {p["provider"]: p["mode"] for p in self.providers()}
        if provider not in advertised:
            raise ContractError(ErrorCode.INVALID_INPUT, "unknown sign-in provider")
        if advertised[provider] != "internal":
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                "external sign-in providers need the gateway redirect flow (not available yet)")
        resp = self._call("post", TOKEN_PATH.format(provider=provider),
                          data={"username": username, "password": password},
                          headers={"Accept": "application/json"})
        if resp.status_code == 401:
            raise SignInFailed("sign in failed")
        if resp.status_code != 200:
            raise ContractError(ErrorCode.UNAVAILABLE, f"Tiled sign-in answered HTTP {resp.status_code}",
                                retryable=True)
        access, refresh, expires_at = self._tokens(resp)
        token = secrets.token_urlsafe(32)
        with self._lock:
            while len(self._sessions) >= self._max:
                del self._sessions[next(iter(self._sessions))]
            self._sessions[self._key(token)] = _Session(access, refresh, expires_at, self._clock())
        return token

    def get(self, session_token: str) -> TiledCredential | None:
        """Return the session's current credential, refreshing it when about to expire.

        Returns ``None`` for an unknown or signed-out session, or when Tiled refuses the
        refresh (the session is dropped).

        Raises:
            ContractError: ``UNAVAILABLE`` when a needed refresh cannot reach Tiled.
        """
        if not session_token:
            return None
        key = self._key(session_token)
        with self._lock:
            sess = self._sessions.get(key)
        if sess is None:
            return None
        if sess.expires_at is not None and self._clock() + CLOCK_SKEW_TOLERANCE >= sess.expires_at:
            sess = self._refresh(key, sess)
            if sess is None:
                return None
        return TiledCredential(sess.access_token, tiled_target=self.tiled_target, expires_at=sess.expires_at)

    def _refresh(self, key: str, sess: _Session) -> _Session | None:
        if not sess.refresh_token:
            self.drop(key)
            return None
        resp = self._call("post", REFRESH_PATH, json={"refresh_token": sess.refresh_token},
                          headers={"Accept": "application/json"})
        if resp.status_code in (401, 403, 409):
            self.drop(key)
            return None
        if resp.status_code != 200:
            raise ContractError(ErrorCode.UNAVAILABLE, f"Tiled refresh answered HTTP {resp.status_code}",
                                retryable=True)
        access, refresh, expires_at = self._tokens(resp)
        new = _Session(access, refresh or sess.refresh_token, expires_at, sess.created_at, sess.subject)
        with self._lock:
            if key not in self._sessions:  # signed out meanwhile
                return None
            self._sessions[key] = new
        return new

    def bind_subject(self, session_token: str, subject: str) -> None:
        """Record the verified ``subject`` of a session (called by the server after ``resolve_context``)."""
        if not session_token or not subject:
            return
        key = self._key(session_token)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is not None and sess.subject != subject:
                self._sessions[key] = _Session(sess.access_token, sess.refresh_token, sess.expires_at,
                                               sess.created_at, subject)

    def credential_for_subject(self, subject: str) -> TiledCredential | None:
        """The current credential of ``subject``'s newest live session, or ``None`` once signed out.

        This is the worker's ``credential_lookup`` (``JobAuthority``): a job keeps its authority
        only while its initiating subject still has a session in this store.

        Raises:
            ContractError: ``UNAVAILABLE`` when a needed refresh cannot reach Tiled.
        """
        if not subject:
            return None
        with self._lock:
            matches = [(key, sess) for key, sess in self._sessions.items() if sess.subject == subject]
        for key, sess in sorted(matches, key=lambda item: item[1].created_at, reverse=True):
            if sess.expires_at is not None and self._clock() + CLOCK_SKEW_TOLERANCE >= sess.expires_at:
                refreshed = self._refresh(key, sess)
                if refreshed is None:
                    continue
                sess = refreshed
            return TiledCredential(sess.access_token, tiled_target=self.tiled_target, expires_at=sess.expires_at)
        return None

    def drop(self, key: str) -> _Session | None:
        """Forget a session by its store key."""
        with self._lock:
            return self._sessions.pop(key, None)

    def sign_out(self, session_token: str) -> bool:
        """Drop the session at once, then revoke the Tiled session (best effort).

        Returns:
            Whether a session existed.
        """
        sess = self.drop(self._key(session_token)) if session_token else None
        if sess is None:
            return False
        if sess.refresh_token:
            try:
                resp = self._call("post", REVOKE_PATH, json={"refresh_token": sess.refresh_token})
                if resp.status_code not in (200, 204):
                    logger.warning("Tiled session revoke answered HTTP %s", resp.status_code)
            except ContractError:
                logger.warning("Tiled session revoke failed; the F2W session is already gone")
        return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


# --------------------------------------------------------------------------- handlers


class SignInRequest(BaseModel):
    """Body of ``POST /auth/sign-in``. The password is forwarded to Tiled and never kept."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=500)
    password: str = Field(min_length=1, max_length=4096, repr=False)


def install_session_routes(app: FastAPI, store: TiledSessionStore, settings: DeploymentSettings, *,
                           allowed_origins: Iterable[str] = ()) -> FastAPI:
    """Mount ``GET /auth/providers``, ``POST /auth/sign-in`` and ``POST /auth/sign-out`` (ALS only).

    ``allowed_origins`` are browser origins accepted besides the request's own host (ALS is
    same-origin under ``/fair2wise``, so the default accepts only the gateway's own origin).

    Raises:
        StartupConfigError: On ``:local`` (no local login, D34) or when ``store`` is bound
            to another Tiled than ``settings``.
    """
    if settings.is_local:
        raise StartupConfigError("config_rejected: there is no sign-in on the local profile")
    if store.tiled_target != settings.tiled_target:
        raise StartupConfigError(f"config_rejected: session store is not bound to {settings.tiled_target}")

    origins = tuple(allowed_origins)

    def _set_cookie(response: JSONResponse, token: str) -> None:
        response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=store.cookie_secure, samesite="strict",
                            path=store.cookie_path)

    @app.get("/auth/providers")
    async def auth_providers() -> dict[str, Any]:
        """Sign-in options, generated from the profile Tiled's advertised providers."""
        return {"providers": store.providers()}

    @app.post("/auth/sign-in")
    async def auth_sign_in(body: SignInRequest, request: Request) -> JSONResponse:
        """Sign in through the profile Tiled; set the opaque session cookie."""
        refused = check_browser_origin(request, allowed_origins=origins)
        if refused is not None:
            return refused
        previous = request.cookies.get(SESSION_COOKIE)
        if previous:
            store.sign_out(previous)  # never reuse an id across sign-ins (fixation)
        try:
            token = store.sign_in(body.provider, body.username, body.password)
        except SignInFailed:
            return JSONResponse({"code": SignInFailed.code, "message": "sign in failed"}, status_code=401)
        response = JSONResponse({"status": "signed_in"})
        _set_cookie(response, token)
        return response

    @app.post("/auth/sign-out")
    async def auth_sign_out(request: Request) -> JSONResponse:
        """Drop the F2W session and revoke the Tiled session; invalidate dependent approvals."""
        refused = check_browser_origin(request, allowed_origins=origins)
        if refused is not None:
            return refused
        subject = current_subject(request)
        had_session = store.sign_out(request.cookies.get(SESSION_COOKIE) or "")
        runtime = getattr(request.app.state, "f2w_access", None)
        if had_session and subject and isinstance(runtime, AccessRuntime):
            runtime.notify_scope_change({"subject": subject, "reason": "signed_out"})
        response = JSONResponse({"status": "signed_out"})
        response.delete_cookie(SESSION_COOKIE, path=store.cookie_path, secure=store.cookie_secure,
                               httponly=True, samesite="strict")
        return response

    return app


def als_identity_wiring(settings: DeploymentSettings, http_client: httpx.Client | None = None, *, timeout: float = 5.0,
                        **store_kwargs: Any) -> tuple[TiledWhoamiAdapter, TiledSessionStore]:  # noqa: ANN401  # Any: forwarded keyword arguments, passed through unchanged
    """Return the identity port and session store for an ALS deployment, both bound to its Tiled.

    Pass both to ``create_app(identity_port=..., credential_store=...)``. ``http_client``
    defaults to an ``httpx.Client`` that does not follow redirects.
    """
    if settings.is_local:
        raise StartupConfigError("config_rejected: the local profile has no user identity port")
    if http_client is None:
        import httpx

        http_client = httpx.Client(follow_redirects=False, timeout=timeout)
    target = settings.tiled_target
    if target is None:  # the ALS profiles fix the target; ``is_local`` was refused above
        raise StartupConfigError("config_rejected: the profile names no Tiled target for identity")
    port = TiledWhoamiAdapter(target, http_client, timeout=timeout)
    store = TiledSessionStore(target, http_client, timeout=timeout, **store_kwargs)
    return port, store


__all__ = ("SignInFailed", "SignInRequest", "TiledSessionStore", "als_identity_wiring", "install_session_routes")
