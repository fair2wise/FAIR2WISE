"""Helpers for the T25C live identity tests against application-owned disposable Tiled 0.2.18.

Every server here is started by ``tests/fixtures/tiled_server.py`` on an ephemeral
``127.0.0.1`` port with generated credentials. Nothing here reads a central URL, a key
from the environment or a ``.env``. :func:`loopback_only` replaces the socket layer so a
test can reach only 127.0.0.1 (and never the developer's own Tiled ports 8011/8765).

Fixture-side manipulations of a disposable server (these are test setup, not F2W code):

* :func:`mint_expired_token` re-signs a real access token with an ``exp`` in the past,
  using the instance's own HS256 key from its 0600 secrets file, so the *server* decides
  it is expired (0.2.18 has no setting for a short token lifetime in the fixture config).
* :func:`drop_role` deletes a role row in the instance's authn database. 0.2.18 has no
  role-change API (the auth router has none); this simulates a role downgrade the way
  an operator would (edit the authn database), visible at the next whoami.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from app.modules.security.tiled_identity import TiledCredential, VerifiedIdentity

#: Captured at import, before any test harness patches the socket layer.
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
#: Tiled servers the developer runs on this machine; tests must never touch them.
FORBIDDEN_PORTS = frozenset({8011, 8765})


class LiveNetworkBlocked(RuntimeError):
    """A live test tried to reach something other than a disposable loopback server."""


def _check(address: Any) -> None:
    host, port = (address[0], address[1]) if isinstance(address, tuple) and len(address) >= 2 else (None, None)
    if host not in ("127.0.0.1",) or port in FORBIDDEN_PORTS:
        raise LiveNetworkBlocked(f"live identity tests reach only disposable 127.0.0.1 servers, not {address!r}")


def loopback_only(monkeypatch: Any) -> None:
    """Allow sockets to 127.0.0.1 (except ports 8011/8765) and refuse everything else."""

    def connect(self: socket.socket, address: Any, *args: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address)
        return _REAL_CONNECT(self, address, *args)

    def connect_ex(self: socket.socket, address: Any, *args: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address)
        return _REAL_CONNECT_EX(self, address, *args)

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        _check(address)
        return _REAL_CREATE_CONNECTION(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OffsetClock:
    """Real UTC time plus an adjustable offset (to age F2W's own view of token expiry)."""

    def __init__(self) -> None:
        self.offset = timedelta(0)

    def __call__(self) -> datetime:
        return utcnow() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += timedelta(seconds=seconds)


class CountingPort:
    """Wraps a real identity port and records every whoami (LOCAL-05 zero-call checks)."""

    def __init__(self, port: Any) -> None:
        self._port = port
        self.tiled_target = port.tiled_target
        self.calls: List[str] = []

    def whoami(self, credential: TiledCredential, *, now: datetime) -> VerifiedIdentity:
        self.calls.append(credential.fingerprint()[:12])
        return self._port.whoami(credential, now=now)


# --------------------------------------------------------------------------- disposable server setup

def bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def access_token(inst: Any, user: str) -> str:
    """A real password-login access token for ``user`` on ``inst``."""
    return inst.login(user)["access_token"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint_expired_token(inst: Any, token: str, *, seconds_ago: int = 120) -> str:
    """Return ``token`` re-signed by ``inst``'s own key with ``exp`` in the past."""
    head, body, _sig = token.split(".")
    claims = json.loads(_unb64(body))
    claims["exp"] = int(time.time()) - seconds_ago
    key = json.loads(inst.config.authn_secrets_file.read_text(encoding="utf-8"))["secret_keys"][0]
    new_body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(key.encode(), f"{head}.{new_body}".encode(), hashlib.sha256).digest()
    return f"{head}.{new_body}.{_b64(sig)}"


def drop_role(inst: Any, identity_id: str, role: str = "admin") -> int:
    """Remove ``role`` from the principal with identity ``identity_id``; return rows removed."""
    db = sqlite3.connect(str(inst.config.authn_db_path))
    try:
        (pid,) = db.execute("select principal_id from identities where id = ? and provider = ?",
                            (identity_id, inst.provider)).fetchone()
        (rid,) = db.execute("select id from roles where name = ?", (role,)).fetchone()
        cur = db.execute("delete from principal_role_association where principal_id = ? and role_id = ?",
                         (pid, rid))
        db.commit()
        return cur.rowcount
    finally:
        db.close()


@dataclass(frozen=True)
class ServiceAccount:
    """A Tiled service principal with one API key (the F2W technical writer, D27)."""

    uuid: str
    key: str
    first_eight: str


def create_service_account(inst: Any, *, role: str = "admin", expires_in: int = 3600) -> ServiceAccount:
    """Create a service principal with ``role`` and an API key, using the instance admin."""
    admin = bearer(access_token(inst, "admin"))
    svc = requests.post(f"{inst.url}/api/v1/auth/principal", params={"role": role}, headers=admin, timeout=10)
    svc.raise_for_status()
    uuid = svc.json()["uuid"]
    issued = requests.post(f"{inst.url}/api/v1/auth/principal/{uuid}/apikey",
                           json={"expires_in": expires_in, "scopes": ["inherit"]}, headers=admin, timeout=10)
    issued.raise_for_status()
    body = issued.json()
    return ServiceAccount(uuid=uuid, key=body["secret"], first_eight=body["first_eight"])


def revoke_service_key(inst: Any, account: ServiceAccount) -> None:
    """Revoke ``account``'s API key with the admin API (204)."""
    resp = requests.delete(f"{inst.url}/api/v1/auth/principal/{account.uuid}/apikey",
                           params={"first_eight": account.first_eight},
                           headers=bearer(access_token(inst, "admin")), timeout=10)
    assert resp.status_code == 204, resp.text


def user_api_key(inst: Any, user: str) -> Tuple[str, str]:
    """Create a per-user API key (D-T8); return ``(secret, user access token)``."""
    token = access_token(inst, user)
    resp = requests.post(f"{inst.url}/api/v1/auth/apikey", json={"expires_in": 600, "scopes": ["inherit"]},
                         headers=bearer(token), timeout=10)
    resp.raise_for_status()
    return resp.json()["secret"], token


def revoke_user_api_key(inst: Any, secret: str, token: str) -> None:
    resp = requests.delete(f"{inst.url}/api/v1/auth/apikey", params={"first_eight": secret[:8]},
                           headers=bearer(token), timeout=10)
    assert resp.status_code == 204, resp.text


def principal_uuid(inst: Any, user: str) -> str:
    """The Tiled principal uuid of ``user`` (the F2W subject)."""
    resp = requests.get(f"{inst.url}/api/v1/auth/whoami", headers=bearer(access_token(inst, user)), timeout=10)
    resp.raise_for_status()
    return resp.json()["uuid"]


def entity_count(inst: Any) -> int:
    """Number of graph entities, read as the instance admin."""
    body = inst.graphql("{ entities(limit: 100000) { id } }", credential=access_token(inst, "admin"),
                        scheme="Bearer")
    return len(body["entities"])


class RecordingSession(requests.Session):
    """``requests.Session`` that records the ``Authorization`` header of every request."""

    def __init__(self) -> None:
        super().__init__()
        self.authorizations: List[Optional[str]] = []

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        headers = kwargs.get("headers") or {}
        self.authorizations.append(headers.get("Authorization"))
        return super().request(method, url, *args, **kwargs)


def all_tokens_in(values: Iterable[Optional[str]], *secrets: str) -> bool:
    """Whether any of ``secrets`` appears in ``values``."""
    return any(s and v and s in v for v in values for s in secrets)
