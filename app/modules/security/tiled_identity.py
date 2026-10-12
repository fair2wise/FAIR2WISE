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

Still to confirm in T25C (against a disposable authenticated Tiled, and on the
central servers' build; D-T1..D-T9): see ``TODO(T25C)`` notes below.

Importing this module has no side effects and does not import ``httpx`` or ``tiled``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Protocol, Tuple, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

#: Tiled role name that makes a principal an F2W admin (0.2.18 authn_database/core.py:51).
TILED_ADMIN_ROLE = "admin"

#: Path of the whoami endpoint under the Tiled base URL (0.2.18 router.py:279).
WHOAMI_PATH = "/api/v1/auth/whoami"


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
    """

    access_token: str = field(repr=False)
    tiled_target: Optional[str] = None
    expires_at: Optional[datetime] = None

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
    roles: Tuple[str, ...]
    verified_at: datetime
    identity_provider: Optional[str] = None
    identity_ids: Tuple[str, ...] = ()
    credential_expires_at: Optional[datetime] = None
    tiled_library_version: Optional[str] = None

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


def identity_from_whoami(payload: Any, *, issuer: str, now: datetime,
                         credential_expires_at: Optional[datetime] = None,
                         tiled_library_version: Optional[str] = None) -> VerifiedIdentity:
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
    """``TiledIdentityPort`` over HTTP ``GET {tiled}/api/v1/auth/whoami`` (tiled 0.2.18 shape).

    Args:
        tiled_target: Base URL of the profile's Tiled.
        http_client: An ``httpx.Client``-like object (``get(url, headers=..., timeout=...)``).
            Injected so tests use ``httpx.MockTransport`` and never reach a server.
        timeout: Per-call timeout in seconds.

    TODO(T25C): confirm against a disposable authenticated tiled 0.2.18 and the central
    servers' build (reported 0.2.19, D33):
      * D-T3: ``uuid`` is stable across sign-ins for both password and external (OIDC)
        providers (0.2.18 resolves OIDC tokens to a DB principal, authentication.py:718-728).
      * D-T4: central ``tiled_admins`` map to a role named ``admin`` in ``whoami.roles``.
      * D-T5/D-T6: access-token lifetime (0.2.18 default 15 min, server/settings.py:34);
        0.2.18 decodes Tiled-minted JWTs without a session lookup, so a revoked session's
        access token stays valid until it expires. That bounds F2W_ROLE_CHECK_MAX_AGE.
      * D-T8: per-user API keys (``Authorization: Apikey``) for non-browser clients; this
        adapter only sends ``Authorization: Bearer``.
      * Whether ``about.library_version`` should be attached (C1) per check.
    """

    def __init__(self, tiled_target: str, http_client: Any, *, timeout: float = 5.0) -> None:
        self.tiled_target = normalize_tiled_target(tiled_target)
        self._client = http_client
        self._timeout = timeout

    def whoami(self, credential: TiledCredential, *, now: datetime) -> VerifiedIdentity:
        """Ask the profile's Tiled who ``credential`` belongs to (see the port)."""
        if not credential.access_token:
            raise IdentityRejected("missing")
        if credential.tiled_target and normalize_tiled_target(credential.tiled_target) != self.tiled_target:
            raise IdentityRejected("wrong_issuer")
        headers = {"Authorization": f"Bearer {credential.access_token}", "Accept": "application/json"}
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
                                    credential_expires_at=credential.expires_at)


def _detail(response: Any) -> str:
    """Return the FastAPI ``detail`` string of an error response, or ``""``."""
    try:
        body = response.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, Mapping) else None
    return detail if isinstance(detail, str) else ""
