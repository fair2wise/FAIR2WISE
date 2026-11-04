"""Caller identity port for ALS profiles: who is this user, according to the profile's Tiled.

The only identity authority on ``:als-dev``/``:als-prod`` is the profile's own Tiled
server (access_contract.md section 5). This module defines the narrow port the
resolver depends on, the verified result it returns, and an HTTP adapter for the
``whoami`` endpoint.

What the adapter relies on was read statically from the pinned ``tiled==0.2.18``
wheel (sha256 ``416079d4...7fa06``; owner decision O-T1). No server was contacted:

* ``tiled/server/router.py:279`` advertises ``authentication.links.whoami`` as
  ``{base_url}/auth/whoami``, i.e. ``{tiled}/api/v1/auth/whoami``.
* ``tiled/server/authentication.py:1641-1680``: ``GET /whoami`` returns
  ``schemas.Principal`` for the bearer token, ``null`` when no credential is
  present, and 401 for an expired token (``"Access token has expired. Refresh
  token."``, line 231) or a deleted principal (``"Principal no longer exists."``).
  Roles are re-read from the authn database on every call, so a role downgrade is
  visible at the next ``whoami``.
* ``tiled/server/schemas.py:330-452``: ``Principal{uuid, type: user|service,
  identities[{id, provider, latest_login}], roles[{name, scopes}], ...}``.
* ``tiled/authn_database/core.py:51`` and ``tiled/server/app.py:723``: the admin role
  is named ``"admin"``; ``authentication.tiled_admins`` grants it at server start.

Confirmed in T25C against application-owned disposable 0.2.18 servers
(``tests/security_live``): the admin gets roles ``["user", "admin"]`` and a plain user
``["user"]``; whoami without a credential is 200 ``null``; an expired access token is
401 ``"Access token has expired. Refresh token."``; a malformed, foreign-signed (another
server's) or unknown token is 401 ``"Could not validate credentials"``; a revoked or
unknown API key is 401 ``"Invalid API key"`` (0.2.18 does not tell revoked from
unknown); a service principal's API key resolves to ``type: "service"``; deleting the
admin role row is visible at the next whoami. Still open for the central servers'
build (D33): see the notes on :class:`TiledWhoamiAdapter`.

Importing this module has no side effects and does not import ``httpx`` or ``tiled``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import logging
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

#: Tiled role name that makes a principal an F2W admin (0.2.18 authn_database/core.py:51).
TILED_ADMIN_ROLE = "admin"

#: Path of the whoami endpoint under the Tiled base URL (0.2.18 router.py:279).
WHOAMI_PATH = "/api/v1/auth/whoami"
#: Server description (``library_version``, ``authentication``), 0.2.18 router.py:285.
ABOUT_PATH = "/api/v1/"
#: ``Authorization`` schemes 0.2.18 accepts for a principal (authentication.py:180-200, 668).
CREDENTIAL_SCHEMES = ("Bearer", "Apikey")


def normalize_tiled_target(url: str) -> str:
    """Return a canonical form of a Tiled base URL used to bind identity and grants.

    Lower-cases scheme and host, drops default ports, query, fragment and trailing
    slashes. ``https://Tiled.ALS.lbl.gov/`` and ``https://tiled.als.lbl.gov`` compare equal.

    Args:
        url: Base URL of a Tiled server.

    Returns:
        The normalized URL.

    Raises:
        ValueError: If ``url`` is not an absolute http(s) URL.
    """
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"not an absolute http(s) Tiled URL: {url!r}")
    host = parts.hostname.lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parts.path.rstrip("/"), "", ""))


@dataclass(frozen=True)
class TiledCredential:
    """A user's Tiled access token as held server-side by the F2W session store.

    The token is never logged, put in a ``RequestContext``, a prompt, a trace or an
    audit record. ``repr`` hides it.

    Attributes:
        access_token: Bearer access token issued by ``tiled_target``.
        tiled_target: Normalized Tiled base URL that issued the token. ``None`` only
            when the caller already bound the credential to this deployment.
        expires_at: Access-token expiry, if known to the session store.
        scheme: ``Bearer`` (browser session access token) or ``Apikey`` (a per-principal
            Tiled API key, D-T8).
    """

    access_token: str = field(repr=False)
    tiled_target: str | None = None
    expires_at: datetime | None = None
    scheme: str = "Bearer"

    def __post_init__(self) -> None:
        if self.scheme not in CREDENTIAL_SCHEMES:
            raise ValueError(f"unsupported Tiled credential scheme {self.scheme!r}")

    def fingerprint(self) -> str:
        """Return a non-reversible cache key for this credential."""
        return hashlib.sha256(self.access_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifiedIdentity:
    """The result of one live ``whoami`` against the profile's Tiled.

    Attributes:
        subject: Stable principal identifier (Tiled principal ``uuid``).
        issuer: Normalized base URL of the Tiled that answered.
        principal_type: ``"user"`` or ``"service"``.
        identity_provider: Provider of the first reported identity, if any.
        identity_ids: Identity ids (``identities[].id``) Tiled reported.
        roles: Role names Tiled reported.
        verified_at: When the check ran.
        credential_expires_at: Access-token expiry, if known.
        tiled_library_version: Server version, if the adapter learned it.
    """

    subject: str
    issuer: str
    principal_type: str
    roles: tuple[str, ...]
    verified_at: datetime
    identity_provider: str | None = None
    identity_ids: tuple[str, ...] = ()
    credential_expires_at: datetime | None = None
    tiled_library_version: str | None = None

    @property
    def is_admin(self) -> bool:
        """Return whether Tiled reported the admin role for this principal."""
        return TILED_ADMIN_ROLE in self.roles


class IdentityRejected(Exception):
    """The credential does not establish a signed-in user (maps to ``Unauthenticated``).

    Args:
        reason: Stable reason: ``missing``, ``expired``, ``revoked``, ``invalid``,
            ``wrong_issuer``, ``service_principal`` or ``writer_credential``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class IdentityUnavailable(Exception):
    """The identity authority could not answer (outage, 5xx, timeout, bad response).

    Never a reason to fall back to ``local_trusted``, another server or the writer.
    """


@runtime_checkable
class TiledIdentityPort(Protocol):
    """Live caller-identity check bound to exactly one Tiled server.

    Attributes:
        tiled_target: Normalized base URL this port asks; the only issuer it vouches for.
    """

    tiled_target: str

    def whoami(self, credential: TiledCredential, *, now: datetime) -> VerifiedIdentity:
        """Verify ``credential`` against ``tiled_target``.

        Args:
            credential: The user's credential; never the writer's.
            now: Time recorded as ``verified_at``.

        Returns:
            The verified identity.

        Raises:
            IdentityRejected: Missing, expired, revoked or otherwise invalid credential.
            IdentityUnavailable: The server could not be asked or answered unexpectedly.
        """
        ...


def identity_from_whoami(payload: object, *, issuer: str, now: datetime,
                         credential_expires_at: datetime | None = None,
                         tiled_library_version: str | None = None) -> VerifiedIdentity:
    """Map a tiled 0.2.18 ``whoami`` JSON body to a :class:`VerifiedIdentity`.

    Args:
        payload: Decoded JSON body (``schemas.Principal.model_dump()`` or ``None``).
        issuer: Normalized base URL that answered.
        now: Verification time.
        credential_expires_at: Known access-token expiry.
        tiled_library_version: Known server version.

    Returns:
        The verified identity.

    Raises:
        IdentityRejected: ``payload`` is ``null`` (no authenticated principal).
        IdentityUnavailable: ``payload`` does not have the 0.2.18 shape.
    """
    if payload is None:
        raise IdentityRejected("invalid")
    if not isinstance(payload, Mapping):
        raise IdentityUnavailable("whoami returned an unexpected body")
    subject = payload.get("uuid")
    principal_type = payload.get("type")
    roles = payload.get("roles", [])
    identities = payload.get("identities", [])
    if not isinstance(subject, str) or not subject or principal_type not in {"user", "service"}:
        raise IdentityUnavailable("whoami body lacks uuid/type")
    if not isinstance(roles, list) or not isinstance(identities, list):
        raise IdentityUnavailable("whoami roles/identities are not lists")
    role_names = tuple(r["name"] for r in roles if isinstance(r, Mapping) and isinstance(r.get("name"), str))
    ids = [i for i in identities if isinstance(i, Mapping)]
    return VerifiedIdentity(
        subject=subject,
        issuer=issuer,
        principal_type=principal_type,
        roles=role_names,
        verified_at=now,
        identity_provider=next((str(i["provider"]) for i in ids if i.get("provider")), None),
        identity_ids=tuple(str(i["id"]) for i in ids if i.get("id")),
        credential_expires_at=credential_expires_at,
        tiled_library_version=tiled_library_version,
    )


class TiledWhoamiAdapter:
    """``TiledIdentityPort`` over HTTP ``GET {tiled}/api/v1/auth/whoami`` (tiled 0.2.18).

    Args:
        tiled_target: Base URL of the profile's Tiled.
        http_client: An ``httpx.Client``-like object (``get(url, headers=..., timeout=...)``).
            Production passes a real client; unit tests use ``httpx.MockTransport``.
        timeout: Per-call timeout in seconds.

    Behaviour (verified against disposable 0.2.18 servers, T25C):

    * The credential is sent with its own scheme (``Bearer`` or ``Apikey``), only to
      ``tiled_target``; a credential bound to another server is refused before any call.
    * 401 maps to ``IdentityRejected``: ``expired`` (access token past ``exp``),
      ``revoked`` (principal deleted), otherwise ``invalid`` (unknown, malformed,
      signed by another server, revoked API key).
    * Anything else that is not 200 JSON, a transport error or a timeout is
      ``IdentityUnavailable``. That includes 404, which a single-user API key server
      returns because it has no auth routes.
    * ``library_version`` is read once from ``/api/v1/`` and attached to every identity
      (role-check evidence, C1). Failing to read it is not an identity failure.

    Gaps that remain for the central 0.2.19 servers (D33), not testable on 0.2.18 fixtures:

    * D-T3/D-T7: external (OIDC) providers. 0.2.18 maps them to a database principal
      (authentication.py:718-728), so ``uuid`` should stay stable, but no OIDC flow was run.
    * D-T6: 0.2.18 decodes its access tokens without a session lookup. A revoked session's
      access token passes whoami until it expires (default 15 min). F2W's sign-out drops
      the server-side session at once, and the refresh fails afterwards.
    * Role edits: 0.2.18 has no role-change API. A role removed in the authn database
      shows at the next whoami, but the access token's ``scp`` claim keeps the old
      scopes for Tiled's own checks until it expires.
    """

    def __init__(self, tiled_target: str, http_client: Any, *, timeout: float = 5.0) -> None:
        self.tiled_target = normalize_tiled_target(tiled_target)
        self._client = http_client
        self._timeout = timeout
        self._library_version: str | None = None

    def library_version(self) -> str | None:
        """Return the server's ``library_version`` (read once), or ``None`` if it cannot be read."""
        if self._library_version is None:
            try:
                response = self._client.get(self.tiled_target + ABOUT_PATH, headers={"Accept": "application/json"},
                                            timeout=self._timeout)
                body = response.json() if response.status_code == 200 else None
            except Exception:  # best effort: the version is evidence, not authority
                logger.debug("Tiled library_version unreadable", exc_info=True)
                return None
            version = body.get("library_version") if isinstance(body, Mapping) else None
            if isinstance(version, str) and version:
                self._library_version = version
        return self._library_version

    def whoami(self, credential: TiledCredential, *, now: datetime) -> VerifiedIdentity:
        """Ask the profile's Tiled who ``credential`` belongs to (see the port)."""
        if not credential.access_token:
            raise IdentityRejected("missing")
        if credential.tiled_target and normalize_tiled_target(credential.tiled_target) != self.tiled_target:
            raise IdentityRejected("wrong_issuer")
        headers = {"Authorization": f"{credential.scheme} {credential.access_token}",
                   "Accept": "application/json"}
        try:
            response = self._client.get(self.tiled_target + WHOAMI_PATH, headers=headers, timeout=self._timeout)
        except Exception as exc:  # transport error or timeout: never fall back
            raise IdentityUnavailable(type(exc).__name__) from None
        status = response.status_code
        if status == 401:
            detail = _detail(response).lower()
            if "expired" in detail:
                raise IdentityRejected("expired")
            if "no longer exists" in detail:
                raise IdentityRejected("revoked")
            raise IdentityRejected("invalid")
        if status != 200:
            raise IdentityUnavailable(f"whoami HTTP {status}")
        try:
            payload = response.json()
        except ValueError:
            raise IdentityUnavailable("whoami body is not JSON") from None
        return identity_from_whoami(payload, issuer=self.tiled_target, now=now,
                                    credential_expires_at=credential.expires_at,
                                    tiled_library_version=self.library_version())


def _detail(response: Any) -> str:
    """Return the FastAPI ``detail`` string of an error response, or ``""``."""
    try:
        body = response.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, Mapping) else None
    return detail if isinstance(detail, str) else ""
