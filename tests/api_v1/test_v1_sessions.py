"""T14 follow-up from T25C: cross-site protection of ``POST /auth/sign-in`` / ``/auth/sign-out``.

The handlers refuse a cross-site browser request (``Sec-Fetch-Site: cross-site`` or a
foreign ``Origin``) before any effect; same-origin and non-browser requests pass. Sign-out
names the verified subject to the access runtime's scope listeners. Tiled is an
``httpx.MockTransport``; nothing leaves the process.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import SESSION_COOKIE, AccessRuntime, install_access_control
from app.api.sessions import TiledSessionStore, install_session_routes
from app.modules.security.authorization import DeploymentSettings
from app.modules.security.tiled_identity import TiledWhoamiAdapter

TARGET = "http://127.0.0.1:59873"
T0 = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


class FakeTiled:
    def __init__(self) -> None:
        self.paths = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path == "/api/v1/":
            return httpx.Response(200, json={"library_version": "0.2.18", "authentication": {
                "providers": [{"provider": "toy", "mode": "internal"}]}})
        if path == "/api/v1/auth/provider/toy/token":
            return httpx.Response(200, json={"access_token": "access-1", "refresh_token": "refresh-1",
                                             "expires_in": 900, "token_type": "bearer"})
        if path == "/api/v1/auth/session/revoke":
            return httpx.Response(204)
        if path == "/api/v1/auth/whoami":
            return httpx.Response(200, json={"uuid": "u-1", "type": "user", "roles": [{"name": "admin"}],
                                             "identities": [{"id": "bob", "provider": "toy"}]})
        return httpx.Response(404)


@pytest.fixture
def world():
    tiled = FakeTiled()
    http = httpx.Client(transport=httpx.MockTransport(tiled.handler))
    settings = DeploymentSettings.for_disposable_test_target("als-dev", tiled_target=TARGET, writer_identity="w")
    store = TiledSessionStore(TARGET, http, clock=lambda: T0, cookie_path="/")
    app = FastAPI()
    runtime = AccessRuntime(settings, identity_port=TiledWhoamiAdapter(TARGET, http), credential_store=store,
                            clock=lambda: T0)
    install_access_control(app, runtime)
    install_session_routes(app, store, settings, allowed_origins=("https://ui.example.org",))
    changes = []
    runtime.on_scope_change(changes.append)
    return app, store, tiled, changes


def _signed_in(app, headers=None):
    client = TestClient(app, base_url="https://testserver", headers=headers, raise_server_exceptions=False)
    resp = client.post("/auth/sign-in", json={"provider": "toy", "username": "bob", "password": "pw"})
    assert resp.status_code == 200, resp.text
    return client, client.cookies.get(SESSION_COOKIE)


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"},
                                     {"Origin": "https://testserver.evil.example"}, {"Origin": "null"}])
def test_cross_site_sign_out_is_refused_and_the_session_survives(world, headers):
    app, store, tiled, changes = world
    client, token = _signed_in(app)
    resp = client.post("/auth/sign-out", headers=headers)
    assert resp.status_code == 403 and resp.json()["code"] == "cross_site_request"
    assert store.get(token) is not None and "/api/v1/auth/session/revoke" not in tiled.paths
    assert changes == []


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}])
def test_cross_site_sign_in_is_refused_before_contacting_tiled(world, headers):
    app, store, tiled, _ = world
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    resp = client.post("/auth/sign-in", headers=headers,
                       json={"provider": "toy", "username": "bob", "password": "pw"})
    assert resp.status_code == 403 and len(store) == 0 and tiled.paths == []
    assert SESSION_COOKIE not in resp.headers.get("set-cookie", "")


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://testserver", "Sec-Fetch-Site": "same-origin"},
                                     {"Origin": "https://ui.example.org"}])
def test_same_origin_allowlisted_and_non_browser_sign_out_work_and_notify(world, headers):
    app, store, tiled, changes = world
    client, token = _signed_in(app)
    resp = client.post("/auth/sign-out", headers=headers)
    assert resp.status_code == 200 and resp.json() == {"status": "signed_out"}
    assert store.get(token) is None and "/api/v1/auth/session/revoke" in tiled.paths
    assert changes == [{"subject": "u-1", "reason": "signed_out"}]


def test_sign_out_without_a_session_notifies_nobody(world):
    app, _store, _tiled, changes = world
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    assert client.post("/auth/sign-out").status_code == 200
    assert changes == []
