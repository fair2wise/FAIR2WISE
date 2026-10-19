"""Read-only GraphQL client for Tiled Graph 0.2.18 (T08A; catalog K-04, adapted).

Differences from the donor ``GraphQLClient`` (all required by K-04's notes):

* **No best-effort ``None``.** The donor returned ``None`` on an unreachable endpoint, and
  every read then looked like "no data". Here a transport failure, timeout or 5xx is
  ``ContractError(UNAVAILABLE)`` (retryable); a rejected credential is ``DENIED``; a
  GraphQL error is raised, never swallowed. An unavailable graph is never empty science.
* **Target validated by deployment config.** The URL goes through
  ``deployment.tiled_config.validate_external_url`` for the trusted startup profile. An
  ALS host is never rewritten to localhost (``tiled_graph.coerce_local_tiled_uri`` is not
  used); a local/test profile pointed at an ALS host fails.
* **Credentials from runtime config only.** :meth:`from_external` reads the writer secret
  file named by ``ExternalTiledConfig``; :meth:`from_managed` uses the managed server's
  generated key. ``os.environ`` (``TILED_API_KEY``/``TILED_URI``) is never read, and there
  is no process-wide client/credential cache: one client per configured target.
* **Read-only.** Mutation documents are refused; publication is T08B.

Adapted from als-computing/forge_rsoxs_analysis src/forge/kg/client.py (BSD-3-Clause).
"""

from __future__ import annotations

import re
from functools import partial
from typing import Any, Callable, Dict, Mapping, Optional

import requests

from app.modules.deployment.tiled_config import (
    ExternalTiledConfig,
    TiledConfigError,
    read_secret_file,
    validate_external_url,
)

from ..ports import ContractError, ErrorCode

GRAPHQL_PATH = "/api/graphql"
_MUTATION_RE = re.compile(r"^\s*(#[^\n]*\n\s*)*mutation\b", re.IGNORECASE)


class TiledCredentialRejected(ContractError):
    """Tiled answered HTTP 401/403 to the configured credential.

    Still ``DENIED`` for readers; the importer maps it to ``WRITER_UNAUTHORIZED`` (the credential
    there is always the runtime writer).
    """

    def __init__(self, message: str, status: int) -> None:
        super().__init__(ErrorCode.DENIED, message, retryable=False)
        self.status = status


def graphql_endpoint_for(base_url: str) -> str:
    """GraphQL endpoint for a normalized Tiled base URL."""
    return base_url.rstrip("/") + GRAPHQL_PATH


class TiledGraphClient:
    """GraphQL reads against one validated Tiled target.

    Args:
        base_url: Tiled base URL; validated for ``profile``.
        profile: Trusted deployment profile (``local``, ``test``, ``als-dev``, ``als-prod``).
        credential: Service credential, or a zero-argument callable returning it (read
            lazily from the runtime secret); ``None`` sends no ``Authorization``.
        scheme: ``Apikey`` or ``Bearer``.
        timeout: Per-request timeout in seconds.
        verify: ``requests`` TLS verify value.
        session: Optional ``requests.Session``-compatible object.
    """

    def __init__(self, base_url: str, *, profile: str,
                 credential: Optional[Any] = None, scheme: str = "Apikey",
                 timeout: float = 10.0, verify: Any = True, session: Any = None) -> None:
        try:
            self.base_url = validate_external_url(profile, base_url)
        except TiledConfigError as exc:
            raise ContractError(ErrorCode.INVALID_INPUT, str(exc)) from None
        if scheme not in ("Apikey", "Bearer"):
            raise ContractError(ErrorCode.INVALID_INPUT, f"unsupported credential scheme {scheme!r}")
        self.profile = profile
        self.endpoint = graphql_endpoint_for(self.base_url)
        self._credential = credential
        self._scheme = scheme
        self._timeout = timeout
        self._verify = verify
        self._session = session or requests.Session()

    def __repr__(self) -> str:  # never show the credential
        return f"TiledGraphClient({self.base_url!r}, profile={self.profile!r})"

    @classmethod
    def from_external(cls, cfg: ExternalTiledConfig, *, session: Any = None) -> "TiledGraphClient":
        """Client for an external (connect-only) target; the secret file is read per request."""
        secret_file = cfg.writer_secret_file
        provider: Optional[Callable[[], str]] = None
        if secret_file is not None:
            provider = partial(read_secret_file, secret_file)
        return cls(cfg.url, profile=cfg.profile, credential=provider, scheme=cfg.writer_scheme,
                   timeout=cfg.timeout, verify=str(cfg.ca_bundle) if cfg.ca_bundle else True, session=session)

    @classmethod
    def from_managed(cls, server: Any, *, session: Any = None) -> "TiledGraphClient":
        """Client for a running managed server (``tiled_lifecycle.ManagedServer``)."""
        from app.modules.deployment import tiled_lifecycle

        cfg = server.config
        return cls(server.url, profile=cfg.profile,
                   credential=lambda: tiled_lifecycle.writer_credential(cfg), session=session)

    def _headers(self) -> Dict[str, str]:
        try:
            cred = self._credential() if callable(self._credential) else self._credential
        except TiledConfigError as exc:  # missing/unreadable runtime secret: graph unavailable, not empty
            raise ContractError(ErrorCode.UNAVAILABLE, f"Tiled graph credential unavailable: {exc}") from None
        return {"Authorization": f"{self._scheme} {cred}"} if cred else {}

    def query(self, document: str, variables: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Run a read query and return ``data``.

        Raises:
            ContractError: ``INVALID_INPUT`` for a mutation document or a rejected query
                (for example the depth limit of 10); ``UNAVAILABLE`` for transport, timeout,
                5xx or a non-JSON body; ``DENIED`` for 401/403 or "Not permitted";
                ``INTERNAL`` for any other GraphQL error.
        """
        if _MUTATION_RE.match(document):
            raise ContractError(ErrorCode.INVALID_INPUT, "the literature read adapter does not send mutations")
        try:
            resp = self._session.post(self.endpoint, json={"query": document, "variables": dict(variables or {})},
                                      headers=self._headers(), timeout=self._timeout, verify=self._verify)
        except requests.RequestException as exc:
            raise ContractError(ErrorCode.UNAVAILABLE,
                                f"Tiled graph at {self.base_url} is unavailable ({type(exc).__name__})") from None
        if resp.status_code in (401, 403):
            raise TiledCredentialRejected(f"Tiled graph at {self.base_url} rejected the credential "
                                          f"(HTTP {resp.status_code})", resp.status_code)
        if resp.status_code >= 500 or resp.status_code == 404:
            raise ContractError(ErrorCode.UNAVAILABLE,
                                f"Tiled graph at {self.base_url} answered HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            raise ContractError(ErrorCode.UNAVAILABLE,
                                f"Tiled graph at {self.base_url} sent a non-JSON body") from None
        if resp.status_code >= 400 and not (isinstance(body, dict) and body.get("errors")):
            raise ContractError(ErrorCode.INTERNAL, f"Tiled graph answered HTTP {resp.status_code}")
        errors = body.get("errors") if isinstance(body, dict) else None
        if errors:
            message = str((errors[0] or {}).get("message", ""))[:500]
            if "Not permitted" in message:
                raise ContractError(ErrorCode.DENIED, message)
            if "maximum operation depth" in message or "Cannot query field" in message \
                    or "Unknown argument" in message:
                raise ContractError(ErrorCode.INVALID_INPUT, message)
            raise ContractError(ErrorCode.INTERNAL, f"Tiled graph query failed: {message}")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise ContractError(ErrorCode.UNAVAILABLE, "Tiled graph returned no data object")
        return data
