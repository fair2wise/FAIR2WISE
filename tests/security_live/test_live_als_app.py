"""T25C item 2: the real legacy UI app on ALS, bound to a disposable Tiled, with real sign-in.

``create_app`` runs with ``DeploymentSettings.for_disposable_test_target("als-dev", ...)``,
the real :class:`~app.modules.security.tiled_identity.TiledWhoamiAdapter` and the real
:class:`~app.api.sessions.TiledSessionStore`. Users sign in through ``POST /auth/sign-in``
with the server's generated passwords; the browser side (``TestClient``) only ever holds
the opaque ``f2w_session`` cookie. Models, providers and Splash are the T18A fakes; the only
reachable network is the disposable 127.0.0.1 server.

Binary cases: AUTH-01 (anonymous), AUTH-02 (reader chat surface), AUTH-03/AUTH-04 (reader
denied before effects), AUTH-05 (admin), AUTH-06 (forged headers), AUTH-07 (refresh,
sign-out, downgrade, outage), AUTH-09 (sign-in round trip, no token in the browser),
AUTH-10 (FORGE delegate), AUTH-11 (no Tiled writes from chat), LOCAL-07, LOCAL-10, DEP-19.
"""
from __future__ import annotations

import json
import time

import pytest

from live_apps import build_live_als
from live_support import access_token, bearer, drop_role, entity_count, principal_uuid
from tiled_server import tiled_python

from _wiring_support import FORGED_HEADERS, UPLOAD

from app.api.auth import SESSION_COOKIE, undeclared_routes
from app.api.legacy_ui import NO_INGESTION_ANSWER, SETTING_WARNING_HEADER
from app.modules.contracts.context import CHAT_CAPABILITIES, ActorKind
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.security.authorization import (
    DeploymentSettings, create_approval, local_operator_context, resolve_context, verify_approval,
)
from app.modules.security.tiled_identity import TiledCredential
from security_fakes import make_plan

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")

ASK = "What is Term A?"
INSUFFICIENT = "Explain the insufficient synthetic topic"
#: One request per agent route (as tests/api_wiring/test_wiring_als_guards.py ROUTE_CALLS).
ROUTE_CALLS = [
    ("GET", "/settings", {}), ("PUT", "/settings", {"json": {"backend": "ollama"}}),
    ("POST", "/session/reset", {"json": {"session_id": "s-x"}}), ("DELETE", "/session/s-x", {}),
    ("GET", "/graph", {}), ("POST", "/graph/nodes/search", {"json": {"query": "resonant", "limit": 3}}),
    ("GET", "/graph/neighborhood/beamline:ESAF-contract", {}), ("GET", "/graph/node/matkg:TermA", {}),
    ("PATCH", "/graph/node/matkg:TermA", {"json": {"description": "edit"}}),
    ("POST", "/graph/upload", {"json": UPLOAD}),
    ("POST", "/chat", {"json": {"message": ASK, "session_id": "s-x", "client_turn_id": "t-x1"}}),
    ("POST", "/chat/action", {"json": {"decision": "no", "session_id": "s-x"}}),
    ("POST", "/publications/search", {"json": {"query": "resonant"}}),
    ("POST", "/chat/stream", {"json": {"message": ASK, "session_id": "s-x", "client_turn_id": "t-x2"}}),
    ("POST", "/chat/action/stream", {"json": {"decision": "no", "session_id": "s-x"}}),
    ("GET", "/traces/t-x1", {}), ("POST", "/telemetry/ui", {"json": {"events": [{"type": "citation_click"}]}}),
    ("POST", "/annotations", {"json": {"turn_id": "t-x1", "verdict": "pass"}}),
]
PRIVILEGED = [
    ("PATCH", "/graph/node/matkg:TermA", {"json": {"description": "user edit"}}),
    ("POST", "/graph/upload", {"json": UPLOAD}),
    ("POST", "/chat/action", {"json": {"decision": "yes", "kind": "download", "candidate_index": 0,
                                       "session_id": "s-pend"}}),
    ("POST", "/chat/action", {"json": {"decision": "yes", "kind": "extraction", "session_id": "s-pend"}}),
    ("POST", "/chat/action/stream", {"json": {"decision": "yes", "kind": "download", "candidate_index": 0,
                                              "session_id": "s-pend"}}),
]


@pytest.fixture
def als(monkeypatch, tmp_path, live_auth):
    return build_live_als(monkeypatch, tmp_path, live_auth)


def _effects(als):
    uploads = als.workdir / "uploads"
    return dict(als.ctx.calls), sorted(p.name for p in uploads.glob("*")) if uploads.exists() else []


# ------------------------------------------------------------------ anonymous (AUTH-01, AUTH-09, DEP-19)


def test_every_route_is_declared_including_sign_in(als):
    assert undeclared_routes(als.app) == []
    paths = {r.path for r in als.app.routes}
    assert {"/auth/providers", "/auth/sign-in", "/auth/sign-out"} <= paths


@pytest.mark.parametrize("headers", [None, FORGED_HEADERS], ids=["plain", "forged"])
def test_anonymous_gets_only_health_docs_and_sign_in(als, headers):
    anon = als.browser(headers)
    startup = dict(als.ctx.calls)  # what create_app itself loaded
    for method, path, kwargs in ROUTE_CALLS:
        resp = anon.request(method, path, **kwargs)
        assert resp.status_code == 401 and resp.json()["code"] == "sign_in_required", (method, path)
    assert anon.get("/health").json() == {"status": "ok"}
    for path in ("/docs", "/openapi.json"):
        assert anon.get(path).status_code == 200
    providers = anon.get("/auth/providers").json()["providers"]
    assert providers == [{"provider": als.inst.provider, "mode": "internal"}]
    assert als.ctx.calls == startup and als.port.calls == []  # no model call, no read, no identity call


def test_a_tiled_token_sent_by_the_browser_is_not_a_credential(als):
    """Only the server-side session counts; a raw Tiled bearer token or forged cookie does nothing."""
    token = access_token(als.inst, "admin")
    for client in (als.browser(bearer(token)), als.browser()):
        if SESSION_COOKIE not in client.cookies:
            client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/settings").status_code == 401
    assert als.port.calls == []


def test_sign_in_round_trip_keeps_tokens_server_side(als):
    browser = als.browser()
    bad = browser.post("/auth/sign-in", json={"provider": als.inst.provider, "username": "reader",
                                              "password": "wrong"})
    assert bad.status_code == 401 and bad.json()["code"] == "sign_in_failed"
    assert SESSION_COOKIE not in browser.cookies and len(als.store) == 0
    unknown = browser.post("/auth/sign-in", json={"provider": "nope", "username": "reader", "password": "x"})
    assert unknown.status_code == 422
    resp = browser.post("/auth/sign-in", json={"provider": als.inst.provider, "username": "reader",
                                               "password": als.inst.passwords["reader"]})
    assert resp.status_code == 200 and resp.json() == {"status": "signed_in"}
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "secure" in set_cookie and "samesite=strict" in set_cookie
    cookie = browser.cookies[SESSION_COOKIE]
    assert len(als.store) == 1 and "." not in cookie  # opaque id, not a JWT
    assert als.inst.passwords["reader"] not in resp.text
    assert browser.get("/settings").status_code == 200
    # Signing in again replaces the session id (no fixation) and leaves one session.
    again = browser.post("/auth/sign-in", json={"provider": als.inst.provider, "username": "reader",
                                                "password": als.inst.passwords["reader"]})
    assert again.status_code == 200 and browser.cookies[SESSION_COOKIE] != cookie and len(als.store) == 1


# ------------------------------------------------------------------ reader (AUTH-02, AUTH-03, AUTH-04, AUTH-06)


def test_reader_chat_surface_works(als):
    reader = als.client("reader")
    reader_uuid = principal_uuid(als.inst, "reader")
    calls = [
        ("GET", "/settings", {}), ("POST", "/session/reset", {"json": {"session_id": "s-r"}}),
        ("GET", "/graph", {}), ("POST", "/graph/nodes/search", {"json": {"query": "resonant", "limit": 3}}),
        ("GET", "/graph/node/matkg:TermA", {}),
        ("POST", "/chat", {"json": {"message": ASK, "session_id": "s-r", "client_turn_id": "t-r1"}}),
        ("POST", "/chat/action", {"json": {"decision": "no", "session_id": "s-r"}}),
        ("POST", "/publications/search", {"json": {"query": "resonant"}}),
        ("POST", "/chat/stream", {"json": {"message": ASK, "session_id": "s-r", "client_turn_id": "t-r2"}}),
        ("GET", "/traces/t-r1", {}),
        ("POST", "/telemetry/ui", {"json": {"events": [{"type": "citation_click", "turn_id": "t-r1"}]}}),
        ("POST", "/annotations", {"json": {"turn_id": "t-r1", "verdict": "pass", "annotator": "admin"}}),
        ("DELETE", "/session/s-r", {}),
    ]
    for method, path, kwargs in calls:
        resp = reader.request(method, path, **kwargs)
        assert resp.status_code == 200, (method, path, resp.status_code, resp.text[:300])
    lines = (als.ctx.workspace / "traces" / "annotations.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["annotator"] for x in lines] == [reader_uuid]
    assert reader.get("/health").json() == {"status": "ok"}


def test_reader_personal_settings_are_their_own(als):
    reader, admin = als.client("reader"), als.client("admin")
    before = admin.get("/settings").json()["backend"]
    resp = reader.put("/settings", json={"backend": "ollama", "kg_query_hops": 1})
    assert resp.status_code == 200 and resp.json()["backend"] == "ollama"
    assert reader.get("/settings").json()["kg_query_hops"] == 1
    assert admin.get("/settings").json()["backend"] == before  # T25D: per principal


def test_reader_cannot_change_deployment_wide_settings(als):
    reader, admin = als.client("reader"), als.client("admin")
    before = admin.get("/settings").json()
    resp = reader.put("/settings", json={"extraction_mode": "full", "targeted_max_pages": 99})
    assert resp.status_code == 200
    assert resp.headers[SETTING_WARNING_HEADER] == "setting_not_writable=extraction_mode,targeted_max_pages"
    after = admin.get("/settings").json()
    for name in ("extraction_mode", "targeted_max_pages"):
        assert after[name] == before[name]


def test_reader_reads_json_graphs_by_id(als):
    up = als.client("admin").post("/graph/upload", json=UPLOAD).json()
    node = als.client("reader").get("/graph/node/matkg:U1", params={"json_graph_path": up["graph_path"]})
    assert node.status_code == 200 and node.json()["id"] == "matkg:U1"  # O-A5


def test_reader_gets_no_ingestion_offer(als):
    body = als.client("reader").post("/chat", json={"message": INSUFFICIENT, "session_id": "s-pend",
                                                    "client_turn_id": "t-pend"}).json()
    assert body["pending"] is None and body["answer"] == NO_INGESTION_ANSWER


@pytest.mark.parametrize("headers", [None, FORGED_HEADERS], ids=["plain", "forged"])
@pytest.mark.parametrize("method,path,kwargs", PRIVILEGED, ids=[f"{m} {p} {i}" for i, (m, p, _k)
                                                                in enumerate(PRIVILEGED)])
def test_reader_privileged_work_is_denied_before_effects(als, method, path, kwargs, headers):
    reader = als.sign_in("reader", headers=headers)
    reader.post("/chat", json={"message": INSUFFICIENT, "session_id": "s-pend", "client_turn_id": "t-p"})
    before = _effects(als)
    resp = reader.request(method, path, **kwargs)
    assert resp.status_code == 403 and resp.json()["code"] == "denied", resp.text
    assert _effects(als) == before
    assert als.ctx.splash_entities["splash-TermA"]["name"] == "Term A"


# ------------------------------------------------------------------ admin (AUTH-05)


def test_admin_privileged_work_takes_effect(als):
    admin = als.client("admin")
    assert admin.patch("/graph/node/matkg:TermA", json={"label": "Term A admin"}).status_code == 200
    assert als.ctx.splash_entities["splash-TermA"]["name"] == "Term A admin"
    assert admin.post("/graph/upload", json=UPLOAD).status_code == 200
    assert (als.workdir / "uploads" / "wiring_upload.json").exists()
    assert admin.put("/settings", json={"extraction_mode": "full"}).json()["extraction_mode"] == "full"
    body = admin.post("/chat", json={"message": INSUFFICIENT, "session_id": "s-adm",
                                     "client_turn_id": "t-adm"}).json()
    assert body["pending"]["kind"] == "download"
    resp = admin.post("/chat/action", json={"decision": "yes", "kind": "download", "candidate_index": 0,
                                            "session_id": "s-adm"})
    assert resp.status_code == 200 and resp.json()["status"] == "awaiting_extraction_decision"
    assert admin.get("/health").json()["workdir"] == "f2w://workdir"


def test_admin_approval_records_verified_subjects_and_the_writer(als):
    """AUTH-05 at the service boundary: requester/approver are Tiled subjects, executor the writer."""
    token = access_token(als.inst, "admin")
    cred = TiledCredential(token, tiled_target=als.inst.url)
    ctx = resolve_context(als.settings, cred, request_id="r", identity_port=als.port, clock=als.clock)
    plan = make_plan(als.inst.url)
    approval = create_approval(ctx, plan, als.settings, approval_id="a-1", clock=als.clock,
                               identity_port=als.port, approver_credential=cred)
    admin_uuid = principal_uuid(als.inst, "admin")
    assert approval.requested_by == approval.approved_by == admin_uuid
    assert approval.executed_as == als.inst.writer.uuid != admin_uuid
    assert approval.approver_is_admin is True and approval.approver_issuer == als.inst.url
    assert token not in approval.model_dump_json()


# ------------------------------------------------------------------ session lifetime (AUTH-07)


def test_access_token_refresh_is_transparent(als):
    reader = als.client("reader")
    (key,) = list(als.store._sessions)
    first = als.store._sessions[key].access_token
    time.sleep(1.1)  # a refreshed token minted in the same second as the login would be identical
    als.clock.advance(16 * 60)  # past Tiled's 15 min access-token lifetime, as F2W sees it
    assert reader.get("/settings").status_code == 200
    assert als.store._sessions[key].access_token != first  # refreshed through Tiled


def test_sign_out_ends_the_session_here_and_at_tiled(als):
    import requests

    reader = als.client("reader")
    (key,) = list(als.store._sessions)
    refresh = als.store._sessions[key].refresh_token
    assert reader.post("/auth/sign-out").json() == {"status": "signed_out"}
    assert len(als.store) == 0
    # The browser keeps nothing usable: reusing the old cookie value is signed out.
    stale = als.browser()
    stale.cookies.set(SESSION_COOKIE, "anything")
    assert reader.get("/settings").status_code == 401 and stale.get("/settings").status_code == 401
    resp = requests.post(f"{als.inst.url}/api/v1/auth/session/refresh", json={"refresh_token": refresh},
                         timeout=10)
    assert resp.status_code == 401  # the Tiled session is revoked too


def test_refused_refresh_signs_the_user_out(als):
    import requests

    reader = als.client("reader")
    (key,) = list(als.store._sessions)
    requests.post(f"{als.inst.url}/api/v1/auth/session/revoke",
                  json={"refresh_token": als.store._sessions[key].refresh_token}, timeout=10)
    als.clock.advance(16 * 60)
    assert reader.get("/settings").status_code == 401 and len(als.store) == 0


def test_expired_token_held_by_a_session_is_sign_in_required(als):
    from app.api.sessions import _Session
    from live_support import mint_expired_token

    expired = mint_expired_token(als.inst, access_token(als.inst, "admin"))
    als.store._sessions[als.store._key("cookie-expired")] = _Session(expired, None, None, als.clock())
    browser = als.browser()
    browser.cookies.set(SESSION_COOKIE, "cookie-expired")
    resp = browser.get("/settings")
    assert resp.status_code == 401 and resp.json()["code"] == "sign_in_required"


def test_role_downgrade_denies_the_next_privileged_request(monkeypatch, tmp_path, live_fresh):
    als = build_live_als(monkeypatch, tmp_path, live_fresh)
    admin = als.client("admin")
    assert admin.patch("/graph/node/matkg:TermA", json={"label": "before"}).status_code == 200
    drop_role(live_fresh, "admin")
    resp = admin.patch("/graph/node/matkg:TermA", json={"label": "after"})
    assert resp.status_code == 403 and resp.json()["code"] == "denied"
    assert als.ctx.splash_entities["splash-TermA"]["name"] == "before"
    assert admin.post("/chat", json={"message": ASK, "session_id": "s-d", "client_turn_id": "t-d"}).status_code \
        == 200  # still a signed-in user


def test_identity_outage_fails_closed(monkeypatch, tmp_path, live_fresh):
    als = build_live_als(monkeypatch, tmp_path, live_fresh)
    reader, admin = als.client("reader"), als.client("admin")
    assert reader.get("/settings").status_code == 200 and admin.get("/settings").status_code == 200
    live_fresh.stop()
    # Chat for an already-verified user continues within the role-cache age (60 s) ...
    assert reader.post("/chat", json={"message": ASK, "session_id": "s-o", "client_turn_id": "t-o"}) \
        .status_code == 200
    # ... privileged work needs a fresh check and is unavailable (retryable), never local trust.
    resp = admin.patch("/graph/node/matkg:TermA", json={"label": "outage"})
    assert resp.status_code == 503 and resp.json()["code"] == "unavailable"
    assert als.ctx.splash_entities["splash-TermA"]["name"] == "Term A"
    # After the cache age nothing but health works.
    als.clock.advance(61)
    assert reader.get("/settings").status_code == 503
    assert als.browser().get("/health").json() == {"status": "ok"}
    signin = als.browser().post("/auth/sign-in", json={"provider": live_fresh.provider, "username": "reader",
                                                       "password": live_fresh.passwords["reader"]})
    assert signin.status_code == 503


# ------------------------------------------------------------------ grants, delegates, recording


def test_local_approval_cannot_authorize_the_als_target(als):
    """LOCAL-10: a local-operator approval never authorizes an ALS deployment's target."""
    local = DeploymentSettings(profile="local", writer_identity=als.settings.writer_identity)
    plan = make_plan(local.tiled_target)
    approval = create_approval(local_operator_context(local, request_id="r"), plan, local, approval_id="a-l",
                               clock=als.clock)
    with pytest.raises(ContractError) as err:
        verify_approval(approval, make_plan(als.inst.url), als.settings, at=als.clock())
    assert err.value.code is ErrorCode.DENIED


def test_forge_delegate_stays_chat_only_and_cannot_approve(als):
    """AUTH-10: delegation keeps the caller's capability; a delegate never approves."""
    for user in ("reader", "admin"):
        cred = TiledCredential(access_token(als.inst, user), tiled_target=als.inst.url)
        ctx = resolve_context(als.settings, cred, request_id="r", identity_port=als.port, clock=als.clock,
                              actor_kind=ActorKind.FORGE_DELEGATE)
        assert ctx.actor_kind is ActorKind.FORGE_DELEGATE
        if user == "reader":
            assert ctx.capabilities == CHAT_CAPABILITIES
        with pytest.raises(ContractError) as err:
            create_approval(ctx, make_plan(als.inst.url), als.settings, approval_id="a-d", clock=als.clock,
                            identity_port=als.port, approver_credential=cred)
        assert err.value.code is ErrorCode.DENIED


def test_chat_recording_makes_no_tiled_writes(als):
    """AUTH-11: sessions, traces, telemetry and annotations stay F2W-local."""
    before = entity_count(als.inst)
    for who in ("reader", "admin"):
        client = als.client(who)
        client.post("/chat", json={"message": ASK, "session_id": f"s-{who}", "client_turn_id": f"t-{who}"})
        client.post("/telemetry/ui", json={"events": [{"type": "citation_click", "turn_id": f"t-{who}"}]})
        client.post("/annotations", json={"turn_id": f"t-{who}", "verdict": "pass"})
    assert entity_count(als.inst) == before
