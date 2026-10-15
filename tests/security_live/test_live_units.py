"""T25C units that need no server: the disposable target override, the session store, the adapter.

``httpx.MockTransport`` stands in for Tiled here; the same behaviour is exercised against
real disposable servers in the other ``test_live_*`` modules (``F2W_TILED_PYTHON``).
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import SESSION_COOKIE
from app.api.sessions import TiledSessionStore, als_identity_wiring, install_session_routes
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.security.authorization import (
    ALS_PROFILE_TARGETS, SESSION_ROUTE_POLICIES, DeploymentSettings, StartupConfigError, route_capabilities,
)
from app.modules.contracts.context import DeploymentProfile
from app.modules.security.tiled_identity import (
    IdentityUnavailable, TiledCredential, TiledWhoamiAdapter,
)

TARGET = "http://127.0.0.1:59123"
T0 = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ disposable target override


def test_override_keeps_the_als_profile_and_binds_the_loopback_target():
    s = DeploymentSettings.for_disposable_test_target("als-dev", tiled_target=TARGET + "/", writer_identity="w")
    assert s.profile is DeploymentProfile.ALS_DEV and not s.is_local
    assert s.tiled_target == TARGET and s.disposable_target is True
    assert DeploymentSettings(profile="als-dev", writer_identity="w").disposable_target is False


@pytest.mark.parametrize("target", [
    ALS_PROFILE_TARGETS[DeploymentProfile.ALS_DEV], "https://127.0.0.1:59123", "http://localhost:59123",
    "http://10.1.2.3:59123", "http://127.0.0.1", "ftp://127.0.0.1:1", "not a url",
])
def test_override_accepts_only_explicit_loopback_http_ports(target):
    with pytest.raises(StartupConfigError):
        DeploymentSettings.for_disposable_test_target("als-prod", tiled_target=target, writer_identity="w")


def test_override_is_unreachable_from_startup_config_constructor_and_replace():
    with pytest.raises(StartupConfigError):
        DeploymentSettings.for_disposable_test_target("local", tiled_target=TARGET, writer_identity="w")
    with pytest.raises(StartupConfigError):  # the startup environment cannot select it
        DeploymentSettings.from_mapping({"F2W_DEPLOYMENT_PROFILE": "als-dev", "F2W_TILED_URL": TARGET,
                                         "F2W_TILED_WRITER_IDENTITY": "w"})
    with pytest.raises(TypeError):
        DeploymentSettings(profile="als-dev", writer_identity="w", disposable_target=True)
    s = DeploymentSettings.for_disposable_test_target("als-dev", tiled_target=TARGET, writer_identity="w")
    with pytest.raises(StartupConfigError):  # a copy is re-validated and refused
        dataclasses.replace(s)


def test_sign_in_routes_are_declared_public_and_als_only():
    for policy in SESSION_ROUTE_POLICIES:
        assert policy.public and not policy.on_local and policy.on_als
        assert route_capabilities(policy.method, policy.path) is policy


# ------------------------------------------------------------------ a scripted Tiled


class ScriptedTiled:
    """Minimal 0.2.18 auth router over MockTransport; records every request."""

    def __init__(self) -> None:
        self.requests = []
        self.mode = "internal"
        self.refresh_status = 200
        self.down = False
        self.n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("down", request=request)
        path = request.url.path
        if path == "/api/v1/":
            return httpx.Response(200, json={"library_version": "0.2.18", "authentication": {
                "providers": [{"provider": "toy", "mode": self.mode}]}})
        if path == "/api/v1/auth/provider/toy/token":
            form = dict(x.split("=") for x in request.content.decode().split("&"))
            if form.get("password") != "pw":
                return httpx.Response(401, json={"detail": "Incorrect username or password"})
            return self._tokens()
        if path == "/api/v1/auth/session/refresh":
            if self.refresh_status != 200:
                return httpx.Response(self.refresh_status, json={"detail": "Session has expired."})
            return self._tokens()
        if path == "/api/v1/auth/session/revoke":
            return httpx.Response(204)
        if path == "/api/v1/auth/whoami":
            return httpx.Response(200, json={"uuid": "u-1", "type": "user", "roles": [{"name": "user"}],
                                             "identities": [{"id": "bob", "provider": "toy"}]})
        return httpx.Response(404)

    def _tokens(self) -> httpx.Response:
        self.n += 1
        return httpx.Response(200, json={"access_token": f"access-{self.n}", "refresh_token": f"refresh-{self.n}",
                                         "expires_in": 900, "token_type": "bearer"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def tiled():
    return ScriptedTiled()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tiled, clock):
    return TiledSessionStore(TARGET, tiled.client(), clock=clock)


# ------------------------------------------------------------------ session store


def test_sign_in_forwards_to_the_profile_provider_and_returns_an_opaque_id(store, tiled):
    token = store.sign_in("toy", "bob", "pw")
    assert "access" not in token and len(token) >= 40
    login = [r for r in tiled.requests if r.url.path.endswith("/token")][0]
    assert str(login.url).startswith(TARGET + "/api/v1/auth/provider/toy/token")
    cred = store.get(token)
    assert cred.access_token == "access-1" and cred.tiled_target == TARGET and cred.scheme == "Bearer"
    assert "access-1" not in repr(store) and "access-1" not in repr(store._sessions)


def test_wrong_password_unknown_and_external_providers(store, tiled):
    from app.api.sessions import SignInFailed

    with pytest.raises(SignInFailed):
        store.sign_in("toy", "bob", "wrong")
    with pytest.raises(ContractError) as err:
        store.sign_in("other", "bob", "pw")
    assert err.value.code is ErrorCode.INVALID_INPUT
    tiled.mode = "external"
    with pytest.raises(ContractError) as err:
        store.sign_in("toy", "bob", "pw")
    assert err.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert len(store) == 0


def test_refresh_before_expiry_and_refused_refresh_signs_out(store, tiled, clock):
    token = store.sign_in("toy", "bob", "pw")
    clock.now += timedelta(seconds=880)  # within the skew tolerance of the 900 s lifetime
    assert store.get(token).access_token == "access-2"
    clock.now += timedelta(seconds=900)
    tiled.refresh_status = 401
    assert store.get(token) is None and len(store) == 0


def test_refresh_outage_is_unavailable_not_a_stale_token(store, tiled, clock):
    token = store.sign_in("toy", "bob", "pw")
    clock.now += timedelta(seconds=1000)
    tiled.down = True
    with pytest.raises(ContractError) as err:
        store.get(token)
    assert err.value.code is ErrorCode.UNAVAILABLE
    assert len(store) == 1  # kept: the user is not signed out by an outage


def test_sign_out_drops_first_then_revokes(store, tiled):
    token = store.sign_in("toy", "bob", "pw")
    tiled.down = True  # even if Tiled cannot be reached, the F2W session is gone
    assert store.sign_out(token) is True and store.get(token) is None
    tiled.down = False
    token = store.sign_in("toy", "bob", "pw")
    store.sign_out(token)
    revoke = [r for r in tiled.requests if r.url.path.endswith("/session/revoke")][-1]
    assert json.loads(revoke.content) == {"refresh_token": "refresh-2"}
    assert store.sign_out("unknown") is False


# ------------------------------------------------------------------ handlers


def _app(store, settings=None):
    settings = settings or DeploymentSettings.for_disposable_test_target("als-dev", tiled_target=TARGET,
                                                                         writer_identity="w")
    app = FastAPI()
    install_session_routes(app, store, settings)
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)


def test_handlers_set_a_hardened_cookie_and_never_return_tokens(store):
    client = _app(store)
    resp = client.post("/auth/sign-in", json={"provider": "toy", "username": "bob", "password": "pw"})
    assert resp.status_code == 200 and "access-1" not in resp.text and "refresh-1" not in resp.text
    cookie = resp.headers["set-cookie"].lower()
    for flag in ("httponly", "secure", "samesite=strict", "path=/fair2wise"):
        assert flag in cookie
    assert "access-1" not in cookie
    bad = client.post("/auth/sign-in", json={"provider": "toy", "username": "bob", "password": "x"})
    assert bad.status_code == 401 and bad.json()["code"] == "sign_in_failed"
    extra = client.post("/auth/sign-in", json={"provider": "toy", "username": "b", "password": "pw", "x": 1})
    assert extra.status_code == 422
    assert client.post("/auth/sign-out").json() == {"status": "signed_out"}


def test_handlers_refuse_local_and_a_foreign_store(tiled):
    with pytest.raises(StartupConfigError):
        _app(TiledSessionStore(TARGET, tiled.client()), DeploymentSettings(profile="local", writer_identity="w"))
    with pytest.raises(StartupConfigError):
        _app(TiledSessionStore("http://127.0.0.1:1", tiled.client()))
    with pytest.raises(StartupConfigError):
        als_identity_wiring(DeploymentSettings(profile="local", writer_identity="w"), tiled.client())


def test_create_app_mounts_sign_in_only_with_a_tiled_session_store(monkeypatch, tmp_path, tiled):
    """The legacy UI app gains /auth/* on ALS with a TiledSessionStore; :local never does."""
    from pathlib import Path

    import _legacy_ui_harness as H
    from _wiring_support import make_cfg
    from app.api.auth import undeclared_routes
    from app.modules.f2w_agent import api as api_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", Path(__file__).resolve().parents[2])
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    settings = DeploymentSettings.for_disposable_test_target("als-dev", tiled_target=TARGET, writer_identity="w")
    port, store = als_identity_wiring(settings, tiled.client(), cookie_path="/")
    app = api_mod.create_app(make_cfg(ctx), deployment=settings, identity_port=port, credential_store=store)
    assert {"/auth/sign-in", "/auth/sign-out", "/auth/providers"} <= {r.path for r in app.routes}
    assert undeclared_routes(app) == []
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    assert client.get("/settings").status_code == 401
    assert client.post("/auth/sign-in", json={"provider": "toy", "username": "bob", "password": "pw"}) \
        .status_code == 200
    assert client.get("/settings").status_code == 200 and SESSION_COOKIE in client.cookies
    local = api_mod.create_app(make_cfg(ctx), deployment=DeploymentSettings(profile="local", writer_identity="w"))
    assert "/auth/sign-in" not in {r.path for r in local.routes}


# ------------------------------------------------------------------ adapter


def test_adapter_sends_the_credential_scheme_and_attaches_the_version(tiled):
    adapter = TiledWhoamiAdapter(TARGET, tiled.client())
    identity = adapter.whoami(TiledCredential("k", tiled_target=TARGET, scheme="Apikey"), now=T0)
    whoami = [r for r in tiled.requests if r.url.path.endswith("/whoami")][-1]
    assert whoami.headers["authorization"] == "Apikey k"
    assert identity.tiled_library_version == "0.2.18"


def test_adapter_404_is_unavailable_and_bad_scheme_is_refused():
    def handler(request):
        return httpx.Response(404, json={"detail": "Not Found"})

    adapter = TiledWhoamiAdapter(TARGET, httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(IdentityUnavailable):  # a single-user API key server has no auth routes
        adapter.whoami(TiledCredential("k", tiled_target=TARGET), now=T0)
    with pytest.raises(ValueError):
        TiledCredential("k", scheme="Basic")
