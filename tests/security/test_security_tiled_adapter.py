"""T25B: tiled 0.2.18 whoami adapter against httpx.MockTransport (no server is contacted).

Response bodies follow ``tiled/server/schemas.py:Principal`` and the error details in
``tiled/server/authentication.py`` of the pinned 0.2.18 wheel. Live confirmation is T25C.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.security.tiled_identity import (
    TILED_ADMIN_ROLE,
    IdentityRejected,
    IdentityUnavailable,
    TiledCredential,
    TiledIdentityPort,
    TiledWhoamiAdapter,
    normalize_tiled_target,
)
from security_fakes import PROD, STAGING, T0

PRINCIPAL = {
    "uuid": "5f0c6a8e9a7c4f0f8a1c2b3d4e5f6a7b",
    "type": "user",
    "identities": [{"id": "alice", "provider": "toy", "latest_login": "2026-10-12T11:00:00Z"}],
    "roles": [{"name": "admin", "scopes": ["read:metadata", "write:principals"]}],
    "api_keys": [],
    "sessions": [],
    "latest_activity": None,
}


def _adapter(handler, target=STAGING):
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    return TiledWhoamiAdapter(target, httpx.Client(transport=httpx.MockTransport(wrapped))), seen


def test_admin_principal_is_parsed_from_0_2_18_shape():
    adapter, seen = _adapter(lambda r: httpx.Response(200, json=PRINCIPAL))
    assert isinstance(adapter, TiledIdentityPort)
    ident = adapter.whoami(TiledCredential("tok"), now=T0)
    assert seen[0].url == STAGING + "/api/v1/auth/whoami" and seen[0].headers["authorization"] == "Bearer tok"
    assert ident.subject == PRINCIPAL["uuid"] and ident.issuer == STAGING and ident.is_admin
    assert ident.roles == (TILED_ADMIN_ROLE,) and ident.identity_provider == "toy"
    assert ident.identity_ids == ("alice",) and ident.verified_at == T0


def test_user_principal_is_not_admin():
    body = dict(PRINCIPAL, roles=[{"name": "user", "scopes": ["read:metadata"]}])
    adapter, _ = _adapter(lambda r: httpx.Response(200, json=body))
    assert not adapter.whoami(TiledCredential("tok"), now=T0).is_admin


@pytest.mark.parametrize("status,body,reason", [
    (401, {"detail": "Access token has expired. Refresh token."}, "expired"),
    (401, {"detail": "Principal no longer exists."}, "revoked"),
    (401, {"detail": "Could not validate credentials"}, "invalid"),
    (200, None, "invalid"),  # whoami answers JSON null when no principal is authenticated
])
def test_rejections(status, body, reason):
    content = b"null" if body is None else None
    adapter, _ = _adapter(lambda r: httpx.Response(status, content=content, headers={
        "content-type": "application/json"}) if content else httpx.Response(status, json=body))
    with pytest.raises(IdentityRejected) as info:
        adapter.whoami(TiledCredential("tok"), now=T0)
    assert info.value.reason == reason


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"detail": "boom"}), httpx.Response(503), httpx.Response(404),
    httpx.Response(200, content=b"not json"), httpx.Response(200, json={"uuid": "x"}),
    httpx.Response(200, json=[1, 2]),
])
def test_outages_and_bad_bodies_are_unavailable(response):
    adapter, _ = _adapter(lambda r: response)
    with pytest.raises(IdentityUnavailable):
        adapter.whoami(TiledCredential("tok"), now=T0)


def test_transport_errors_are_unavailable():
    def boom(request):
        raise httpx.ConnectTimeout("timeout", request=request)

    adapter, _ = _adapter(boom)
    with pytest.raises(IdentityUnavailable):
        adapter.whoami(TiledCredential("tok"), now=T0)


def test_missing_or_foreign_credential_never_sent():
    adapter, seen = _adapter(lambda r: httpx.Response(200, json=PRINCIPAL))
    with pytest.raises(IdentityRejected):
        adapter.whoami(TiledCredential(""), now=T0)
    with pytest.raises(IdentityRejected) as info:
        adapter.whoami(TiledCredential("tok", tiled_target=PROD), now=T0)
    assert info.value.reason == "wrong_issuer" and seen == []


def test_normalize_tiled_target():
    assert normalize_tiled_target("HTTPS://Tiled.ALS.lbl.gov:443/") == PROD
    assert normalize_tiled_target("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"
    for bad in ("", "tiled.als.lbl.gov", "ftp://x"):
        with pytest.raises(ValueError):
            normalize_tiled_target(bad)
