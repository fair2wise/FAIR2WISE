"""T25B: FastAPI dependency and error mapping, proven on small test apps (no real routes, no Tiled)."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import pytest
from fastapi import Body, Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.auth import (
    SESSION_COOKIE,
    AccessRuntime,
    install_access_control,
    optional_request_context,
    request_context,
    undeclared_routes,
)
from app.modules.contracts.context import RequestContext
from app.modules.security.authorization import (
    AGENT_SURFACE,
    MODEL_API_SURFACE,
    NETWORK_EXPOSURE_WARNING,
    PAPER_FINDER_SURFACE,
    ROUTE_POLICIES,
    chat_action_capabilities,
    chat_offers_ingestion,
    health_projection,
    require,
    require_owner_or,
    settings_update_capabilities,
)
from app.modules.security.tiled_identity import TiledCredential
from security_fakes import (
    PROD,
    STAGING,
    FakeClock,
    FakeTiledIdentityPort,
    ForbiddenIdentityPort,
    InMemoryCredentialStore,
    admin_user,
    local_settings,
    plain_user,
    staging_settings,
)

#: FastAPI's built-in /docs, /redoc and /openapi.json bypass dependencies; undeclared_routes reports them.
NO_DOCS = {"docs_url": None, "redoc_url": None, "openapi_url": None}
TRACE_OWNERS = {"t-own": "uuid-user", "t-other": "uuid-someone"}
FORGED_HEADERS = {
    "X-Local-Trusted": "1", "X-F2W-Mode": "local_trusted", "X-F2W-Is-Admin": "true", "X-Tiled-Roles": "admin",
    "X-Forwarded-User": "admin", "Host": "127.0.0.1", "Authorization": "Bearer forged-admin",
}


class Harness:
    """A representative test app plus its fakes."""

    def __init__(self, settings, port=None, store=None, clock=None):
        self.effects: List[str] = []
        self.writer_writes: List[str] = []  # the technical writer must never be used by chat (AUTH-11)
        self.port = port
        self.store = store or InMemoryCredentialStore()
        self.clock = clock or FakeClock()
        self.app = _representative_app(self)
        install_access_control(self.app, AccessRuntime(settings, identity_port=port, credential_store=self.store,
                                                       clock=self.clock))
        self.client = TestClient(self.app)

    def sign_in(self, cookie: str, token: str, target: str = STAGING) -> Dict[str, str]:
        self.store.sessions[cookie] = TiledCredential(token, tiled_target=target)
        return {SESSION_COOKIE: cookie}

    def call(self, method: str, path: str, cookies=None, headers=None, json=None):
        self.client.cookies.clear()
        for k, v in (cookies or {}).items():
            self.client.cookies.set(k, v)
        return self.client.request(method, path, headers=headers, json=json)


def _representative_app(h: Harness) -> FastAPI:
    app = FastAPI(**NO_DOCS)

    @app.get("/health")
    def health(ctx: Optional[RequestContext] = Depends(optional_request_context)):
        return {"status": "ok", "projection": health_projection(ctx)}

    @app.get("/settings")
    def get_settings(ctx: RequestContext = Depends(request_context)):
        return {"global_fields_visible": ctx.has("settings.global")}

    @app.put("/settings")
    def put_settings(body: Dict[str, Any] = Body(...), ctx: RequestContext = Depends(request_context)):
        caps, ignored = settings_update_capabilities(body)
        require(ctx, *caps)
        h.effects.append("settings")
        return {"warnings": [{"code": "setting_not_writable", "field": f} for f in sorted(ignored)]}

    @app.patch("/graph/node/{node_id}")
    def patch_node(node_id: str, body: Dict[str, Any] = Body(default={}),
                   ctx: RequestContext = Depends(request_context)):
        h.effects.append(f"patch:{node_id}")
        return {"ok": True}

    @app.post("/graph/upload")
    def upload(ctx: RequestContext = Depends(request_context)):
        h.effects.append("upload")
        return {"ok": True}

    @app.post("/chat")
    def chat(body: Dict[str, Any] = Body(default={}), ctx: RequestContext = Depends(request_context)):
        h.effects.append("chat")
        return {"subject": ctx.principal.subject, "offers_ingestion": chat_offers_ingestion(ctx)}

    @app.post("/chat/action")
    def chat_action(body: Dict[str, Any] = Body(...), ctx: RequestContext = Depends(request_context)):
        require(ctx, *chat_action_capabilities(body.get("decision"), body.get("kind")))
        h.effects.append(f"action:{body.get('decision')}")
        return {"ok": True}

    @app.get("/traces/{turn_id}")
    def trace(turn_id: str, ctx: RequestContext = Depends(request_context)):
        require_owner_or(ctx, TRACE_OWNERS.get(turn_id), "trace.read_own", "trace.read_any")
        return {"turn_id": turn_id, "projected": True}

    @app.post("/telemetry/ui")
    def telemetry(body: Dict[str, Any] = Body(default={}), ctx: RequestContext = Depends(request_context)):
        h.effects.append(f"telemetry:{ctx.principal.subject}")  # actor from ctx, never the payload
        return {"ok": True}

    @app.get("/undeclared")
    def undeclared(ctx: RequestContext = Depends(request_context)):
        h.effects.append("undeclared")
        return {"ok": True}

    return app


def _als(**kw):
    port = FakeTiledIdentityPort(tokens={"admin-tok": admin_user(), "user-tok": plain_user()})
    h = Harness(staging_settings(), port=port, **kw)
    return h, h.sign_in("c-admin", "admin-tok"), h.sign_in("c-user", "user-tok")


REQUESTS = [
    ("GET", "/settings", None), ("PUT", "/settings", {"model": "m"}),
    ("PUT", "/settings", {"json_graph_paths": ["x.json"]}), ("PATCH", "/graph/node/n1", {"label": "x"}),
    ("POST", "/graph/upload", None), ("POST", "/chat", {"message": "hi"}),
    ("POST", "/chat/action", {"decision": "yes", "kind": "download"}),
    ("POST", "/chat/action", {"decision": "no"}), ("GET", "/traces/t-other", None),
    ("POST", "/telemetry/ui", {"actor": "admin"}),
]


# --- :local (LOCAL-01, LOCAL-02, LOCAL-05, LOCAL-06 shape) ---------------------------

@pytest.mark.parametrize("cookies,headers", [
    (None, None), ({SESSION_COOKIE: "expired"}, {"Authorization": "Bearer expired"}),
    ({SESSION_COOKIE: "non-admin"}, FORGED_HEADERS),
])
def test_local_everything_allowed_without_sign_in_and_without_identity_calls(cookies, headers):
    port = ForbiddenIdentityPort()
    h = Harness(local_settings(), port=port)
    for method, path, body in REQUESTS + [("GET", "/health", None), ("GET", "/undeclared", None)]:
        r = h.call(method, path, cookies=cookies, headers=headers, json=body)
        assert r.status_code == 200, (method, path, r.text)
        assert "sign_in" not in r.text
    assert h.call("GET", "/health").json()["projection"] == "full"
    assert h.call("POST", "/chat", json={}).json() == {"subject": "local-operator", "offers_ingestion": True}
    assert port.calls == 0


def _generic_app(surface: str) -> FastAPI:
    app = FastAPI(**NO_DOCS)
    for policy in ROUTE_POLICIES:
        if policy.surface != surface:
            continue

        def endpoint(ctx: RequestContext = Depends(request_context), _rid=policy.route_id):
            return {"route": _rid, "subject": ctx.principal.subject}

        app.add_api_route(policy.path, endpoint, methods=[policy.method], name=policy.route_id)
    return app


@pytest.mark.parametrize("surface", [AGENT_SURFACE, MODEL_API_SURFACE, PAPER_FINDER_SURFACE])
def test_local_every_exposed_inventoried_route_works_without_sign_in(surface):
    """LOCAL-01 over every route the :local image serves, with the identity port forbidden."""
    port = ForbiddenIdentityPort()
    app = _generic_app(surface)
    install_access_control(app, AccessRuntime(local_settings(), identity_port=port, surface=surface))
    assert undeclared_routes(app) == []
    client = TestClient(app)
    served = [p for p in ROUTE_POLICIES if p.surface == surface and p.on_local]
    assert served
    for policy in served:
        path = re.sub(r"\{[^}]+\}", "n1", policy.path)
        r = client.request(policy.method, path, headers={"Authorization": "Bearer expired"})
        assert r.status_code == 200 and r.json()["subject"] == "local-operator", (policy.route_id, r.text)
    assert port.calls == 0


def test_local_network_exposure_is_explicit_and_warned(caplog):
    """LOCAL-08."""
    with caplog.at_level(logging.WARNING, logger="app.api.auth"):
        Harness(local_settings(local_bind="network"), port=ForbiddenIdentityPort())
    assert NETWORK_EXPOSURE_WARNING in caplog.text


# --- ALS (AUTH-01..08) -----------------------------------------------------------------

def test_als_anonymous_gets_only_reduced_health():
    """AUTH-01."""
    h, _, _ = _als()
    assert h.call("GET", "/health").json() == {"status": "ok", "projection": "reduced"}
    for method, path, body in REQUESTS + [("GET", "/undeclared", None)]:
        r = h.call(method, path, json=body)
        assert r.status_code == 401 and r.json()["code"] == "sign_in_required", (method, path)
    assert h.effects == [] and h.port.calls == []


def test_als_user_is_chat_only_and_denied_before_effects():
    """AUTH-02, AUTH-03, AUTH-04."""
    h, _, user = _als()
    expect = {
        ("GET", "/settings"): 200, ("PATCH", "/graph/node/n1"): 403, ("POST", "/graph/upload"): 403,
        ("POST", "/chat"): 200, ("GET", "/traces/t-other"): 403, ("POST", "/telemetry/ui"): 200,
    }
    for (method, path), status in expect.items():
        assert h.call(method, path, cookies=user, json={}).status_code == status, (method, path)
    assert h.call("GET", "/traces/t-own", cookies=user).status_code == 200
    assert h.call("PUT", "/settings", cookies=user, json={"model": "m"}).status_code == 200
    r = h.call("PUT", "/settings", cookies=user, json={"json_graph_paths": ["x"]})
    assert r.status_code == 403 and r.json()["code"] == "denied"
    assert h.call("POST", "/chat/action", cookies=user, json={"decision": "no"}).status_code == 200
    assert h.call("POST", "/chat/action", cookies=user, json={"decision": "yes", "kind": "download"}).status_code \
        == 403
    assert h.call("POST", "/chat", cookies=user, json={}).json()["offers_ingestion"] is False
    assert h.call("GET", "/health", cookies=user).json()["projection"] == "reduced"
    assert h.call("GET", "/settings", cookies=user).json() == {"global_fields_visible": False}
    assert not [e for e in h.effects if e.startswith(("patch", "upload", "action:yes"))]
    assert "settings" in h.effects and "telemetry:uuid-user" in h.effects


def test_als_admin_can_do_privileged_work_with_fresh_role_checks():
    """AUTH-05 (route level); admin entry to privileged routes re-checks Tiled each time."""
    h, admin, _ = _als()
    for method, path, body in REQUESTS:
        assert h.call(method, path, cookies=admin, json=body).status_code == 200, (method, path)
    assert h.call("GET", "/health", cookies=admin).json()["projection"] == "full"
    before = len(h.port.calls)
    h.call("PATCH", "/graph/node/n1", cookies=admin, json={})
    h.call("PATCH", "/graph/node/n2", cookies=admin, json={})
    assert len(h.port.calls) == before + 2


def test_forged_trust_claims_never_elevate():
    """AUTH-06 / LOCAL-07: headers, Host, body fields and bearer tokens are ignored."""
    h, _, user = _als()
    forged_body = {"mode": "local_trusted", "is_admin": True, "profile": "local", "roles": ["admin"]}
    assert h.call("PATCH", "/graph/node/n1", headers=FORGED_HEADERS, json=forged_body).status_code == 401
    r = h.call("PATCH", "/graph/node/n1", cookies=user, headers=FORGED_HEADERS, json=forged_body)
    assert r.status_code == 403
    assert h.call("POST", "/chat", cookies=user, headers=FORGED_HEADERS, json=forged_body).json()["subject"] \
        == "uuid-user"
    assert not [e for e in h.effects if e.startswith("patch")]


def test_identity_outage_fails_closed_without_local_trust():
    """LOCAL-07 / section 12."""
    h, admin, user = _als()
    assert h.call("POST", "/chat", cookies=user, json={}).status_code == 200  # verified, now cached
    h.port.outage = True
    r = h.call("PATCH", "/graph/node/n1", cookies=admin, json={})
    assert r.status_code == 503 and r.json()["code"] == "unavailable" and r.headers["retry-after"]
    assert h.call("POST", "/chat", cookies=user, json={}).status_code == 200  # within role-cache age
    h.clock.advance(61)
    assert h.call("POST", "/chat", cookies=user, json={}).status_code == 503
    assert h.call("GET", "/health").json()["projection"] == "reduced"
    assert not [e for e in h.effects if e.startswith("patch")]


def test_staging_session_is_not_accepted_with_another_issuer():
    """AUTH-08."""
    h, _, _ = _als()
    cookies = h.sign_in("c-prod", "admin-tok", target=PROD)
    r = h.call("PATCH", "/graph/node/n1", cookies=cookies, json={})
    assert r.status_code == 401 and h.effects == []


def test_als_revoked_or_expired_session_is_sign_in_required():
    """AUTH-07 at the route boundary."""
    h, admin, _ = _als()
    assert h.call("PATCH", "/graph/node/n1", cookies=admin, json={}).status_code == 200
    h.port.rejected["admin-tok"] = "revoked"
    assert h.call("PATCH", "/graph/node/n2", cookies=admin, json={}).status_code == 401
    assert h.effects == ["patch:n1"]


def test_als_default_deny_and_not_exposed_routes():
    h, admin, _ = _als()
    assert h.call("GET", "/undeclared", cookies=admin).status_code == 403
    assert undeclared_routes(h.app) == ["GET /undeclared"]
    app = _generic_app(MODEL_API_SURFACE)
    port = FakeTiledIdentityPort(tokens={"admin-tok": admin_user()})
    store = InMemoryCredentialStore()
    store.sessions["c"] = TiledCredential("admin-tok", tiled_target=STAGING)
    install_access_control(app, AccessRuntime(staging_settings(), identity_port=port, credential_store=store,
                                              surface=MODEL_API_SURFACE))
    client = TestClient(app, cookies={SESSION_COOKIE: "c"})
    assert client.post("/api/chat").status_code == 403  # R20: not exposed on ALS until D-T8
    assert client.get("/api/tags").status_code == 200


def test_builtin_docs_routes_are_reported_as_undeclared():
    """FastAPI's docs/openapi routes skip dependencies, so the route audit must flag them."""
    app = FastAPI()
    install_access_control(app, AccessRuntime(staging_settings()))
    assert set(undeclared_routes(app)) >= {"GET /openapi.json", "GET /docs", "GET /redoc"}


def test_chat_recording_never_uses_the_writer():
    """AUTH-11 (guard layer): chat/telemetry resolve with whoami only; no writer call."""
    h, _, user = _als()
    h.call("POST", "/chat", cookies=user, json={})
    h.call("POST", "/telemetry/ui", cookies=user, json={})
    assert h.writer_writes == [] and len(h.port.calls) == 1  # one whoami, then the role cache
