"""T18B: every legacy UI route guarded on a simulated ALS profile (fake Tiled identity port).

Anonymous callers get 401 everywhere except ``GET /health`` (reduced) and the public API
docs (O-A8); ordinary users are chat-only and are refused privileged work before any
effect; verified admins are allowed; forged trust headers change nothing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _wiring_support import FORGED_HEADERS, UPLOAD, build_als  # noqa: E402

from app.api.legacy_ui import NO_INGESTION_ANSWER, SETTING_WARNING_HEADER  # noqa: E402
from app.modules.security.authorization import AGENT_SURFACE, ROUTE_POLICIES  # noqa: E402

ASK = "What is Term A?"
INSUFFICIENT = "Explain the insufficient synthetic topic"

#: One representative request per agent route: (route template, method, concrete path, request kwargs).
ROUTE_CALLS = [
    ("/health", "GET", "/health", {}),
    ("/settings", "GET", "/settings", {}),
    ("/settings", "PUT", "/settings", {"json": {"backend": "ollama"}}),
    ("/session/reset", "POST", "/session/reset", {"json": {"session_id": "s-x"}}),
    ("/session/{session_id}", "DELETE", "/session/s-x", {}),
    ("/graph", "GET", "/graph", {}),
    ("/graph/nodes/search", "POST", "/graph/nodes/search", {"json": {"query": "resonant", "limit": 3}}),
    ("/graph/neighborhood/{node_id:path}", "GET", "/graph/neighborhood/beamline:ESAF-contract", {}),
    ("/graph/node/{node_id}", "GET", "/graph/node/matkg:TermA", {}),
    ("/graph/node/{node_id}", "PATCH", "/graph/node/matkg:TermA", {"json": {"description": "edit"}}),
    ("/graph/upload", "POST", "/graph/upload", {"json": UPLOAD}),
    ("/chat", "POST", "/chat", {"json": {"message": ASK, "session_id": "s-x", "client_turn_id": "t-x1"}}),
    ("/chat/action", "POST", "/chat/action", {"json": {"decision": "no", "session_id": "s-x"}}),
    ("/publications/search", "POST", "/publications/search", {"json": {"query": "resonant"}}),
    ("/chat/stream", "POST", "/chat/stream",
     {"json": {"message": ASK, "session_id": "s-x", "client_turn_id": "t-x2"}}),
    ("/chat/action/stream", "POST", "/chat/action/stream", {"json": {"decision": "no", "session_id": "s-x"}}),
    ("/traces/{turn_id}", "GET", "/traces/t-x1", {}),
    ("/telemetry/ui", "POST", "/telemetry/ui", {"json": {"events": [{"type": "citation_click"}]}}),
    ("/annotations", "POST", "/annotations", {"json": {"turn_id": "t-x1", "verdict": "pass"}}),
]
CALL_IDS = [f"{m} {p}" for _t, m, p, _k in ROUTE_CALLS]


def test_route_calls_cover_all_19_agent_routes():
    declared = {(p.method, p.path) for p in ROUTE_POLICIES if p.surface == AGENT_SURFACE}
    assert {(m, t) for t, m, _p, _k in ROUTE_CALLS} == declared


def _effects(als):
    return dict(als.ctx.calls), sorted(p.name for p in (als.workdir / "uploads").glob("*")) \
        if (als.workdir / "uploads").exists() else []


# ------------------------------------------------------------------ anonymous


@pytest.mark.parametrize("headers", [None, FORGED_HEADERS], ids=["plain", "forged"])
@pytest.mark.parametrize("template,method,path,kwargs", ROUTE_CALLS, ids=CALL_IDS)
def test_anonymous_is_refused_except_health(als, template, method, path, kwargs, headers):
    before = _effects(als)
    resp = als.client("anon", headers=headers).request(method, path, **kwargs)
    if path == "/health":
        assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    else:
        assert resp.status_code == 401, resp.text
        assert resp.json()["code"] == "sign_in_required"
    assert _effects(als) == before
    assert als.port.calls == []  # no credential: nothing to verify, and headers are never credentials


@pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/redoc"])
def test_api_docs_stay_public_on_als(als, path):
    assert als.client("anon").get(path).status_code == 200  # O-A8


def test_unknown_or_rejected_session_cookie_is_signed_out(als):
    als.port.rejected["token-user"] = "revoked"
    assert als.client("user").get("/graph").status_code == 401
    from fastapi.testclient import TestClient

    stranger = TestClient(als.app, cookies={"f2w_session": "not-a-session"})
    assert stranger.get("/graph").status_code == 401


def test_identity_outage_fails_closed(als):
    als.port.outage = True
    resp = als.client("admin").post("/graph/upload", json=UPLOAD)
    assert resp.status_code == 503 and resp.json()["code"] == "unavailable"
    assert not (als.workdir / "uploads" / "wiring_upload.json").exists()


# ------------------------------------------------------------------ ordinary user: allowed chat surface


def test_user_chat_surface_is_allowed(als):
    user = als.client("user")
    calls = [
        ("GET", "/settings", {}),
        ("PUT", "/settings", {"json": {"backend": "ollama"}}),
        ("POST", "/session/reset", {"json": {"session_id": "s-user"}}),
        ("GET", "/graph", {}),
        ("POST", "/graph/nodes/search", {"json": {"query": "resonant", "limit": 3}}),
        ("GET", "/graph/neighborhood/beamline:ESAF-contract", {}),
        ("GET", "/graph/node/matkg:TermA", {}),
        ("POST", "/chat", {"json": {"message": ASK, "session_id": "s-user", "client_turn_id": "t-u1"}}),
        ("POST", "/chat/action", {"json": {"decision": "no", "session_id": "s-user"}}),
        ("POST", "/publications/search", {"json": {"query": "resonant"}}),
        ("POST", "/chat/stream", {"json": {"message": ASK, "session_id": "s-user", "client_turn_id": "t-u2"}}),
        ("POST", "/chat/action/stream", {"json": {"decision": "no", "session_id": "s-user"}}),
        ("GET", "/traces/t-u1", {}),
        ("POST", "/telemetry/ui", {"json": {"events": [{"type": "citation_click", "turn_id": "t-u1"}]}}),
        ("POST", "/annotations", {"json": {"turn_id": "t-u1", "verdict": "pass", "annotator": "admin"}}),
        ("GET", "/health", {}),
        ("DELETE", "/session/s-user", {}),
    ]
    for method, path, kwargs in calls:
        resp = user.request(method, path, **kwargs)
        assert resp.status_code == 200, (method, path, resp.status_code, resp.text[:300])
    assert user.get("/health").json() == {"status": "ok"}  # reduced: no paths, model or pending state


def test_user_settings_are_reduced_and_admin_settings_are_full(als):
    user = als.client("user").get("/settings").json()
    assert user["tiled_uri"] is None and user["tiled_error"] is None
    assert user["json_graph_paths"] == [] and user["available_json_graphs"] == []
    assert all("path" not in g for g in user["available_graphs"])
    admin = als.client("admin").get("/settings").json()
    assert admin["available_json_graphs"] and all("path" in g for g in admin["available_graphs"])


def test_user_trace_is_projected_and_admin_trace_is_raw(als):
    als.client("user").post("/chat", json={"message": ASK, "session_id": "s-u", "client_turn_id": "t-u"})
    projected = als.client("user").get("/traces/t-u").json()
    raw = als.client("admin").get("/traces/t-u").json()
    assert "settings" not in projected["env"] and "settings" in raw["env"]
    assert set(projected) <= set(raw)


# ------------------------------------------------------------------ ordinary user: privileged work denied first


def _setup_pending_download(als, who="user", session="s-pend"):
    return als.client(who).post("/chat", json={"message": INSUFFICIENT, "session_id": session,
                                               "client_turn_id": f"t-{who}-{session}"})


PRIVILEGED = [
    ("PUT", "/settings", {"json": {"graph_source": "json"}}),
    ("PUT", "/settings", {"json": {"backend": "ollama", "kg_query_hops": 2}}),
    ("PATCH", "/graph/node/matkg:TermA", {"json": {"description": "user edit"}}),
    ("POST", "/graph/upload", {"json": UPLOAD}),
    ("POST", "/chat/action", {"json": {"decision": "yes", "kind": "download", "candidate_index": 0,
                                       "session_id": "s-pend"}}),
    ("POST", "/chat/action", {"json": {"decision": "yes", "session_id": "s-pend"}}),
    ("POST", "/chat/action/stream", {"json": {"decision": "yes", "kind": "download", "candidate_index": 0,
                                              "session_id": "s-pend"}}),
    ("POST", "/chat/action/stream", {"json": {"decision": "yes", "kind": "extraction", "session_id": "s-pend"}}),
]


@pytest.mark.parametrize("headers", [None, FORGED_HEADERS], ids=["plain", "forged"])
@pytest.mark.parametrize("method,path,kwargs", PRIVILEGED,
                         ids=[f"{m} {p} {i}" for i, (m, p, _k) in enumerate(PRIVILEGED)])
def test_user_privileged_work_is_denied_before_effects(als, method, path, kwargs, headers):
    _setup_pending_download(als)
    settings_before = als.client("admin").get("/settings").json()
    before = _effects(als)
    resp = als.client("user", headers=headers).request(method, path, **kwargs)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "denied"
    assert _effects(als) == before
    assert als.client("admin").get("/settings").json() == settings_before
    assert als.ctx.splash_entities["splash-TermA"]["name"] == "Term A"


def test_user_gets_no_ingestion_offer(als):
    body = _setup_pending_download(als).json()
    assert body["status"] == "insufficient_evidence" and body["pending"] is None
    assert body["answer"] == NO_INGESTION_ANSWER
    # The service holds no pending action for the session any more.
    after = als.client("user").post("/chat/action", json={"decision": "no", "session_id": "s-pend"}).json()
    assert after["status"] == "no_pending_action"
    raw = als.client("user").post("/chat/stream", json={"message": INSUFFICIENT, "session_id": "s-pend2",
                                                        "client_turn_id": "t-s-pend2"}).text
    assert "awaiting_download_decision" not in raw and '"pending": null' in raw
    assert als.ctx.calls.get("download.download_selected") is None


def test_admin_gets_the_offer_and_can_approve(als):
    admin = als.client("admin")
    body = _setup_pending_download(als, "admin", "s-adm").json()
    assert body["status"] == "awaiting_download_decision" and body["pending"]["kind"] == "download"
    resp = admin.post("/chat/action", json={"decision": "yes", "kind": "download", "candidate_index": 0,
                                            "session_id": "s-adm"})
    assert resp.status_code == 200 and resp.json()["status"] == "awaiting_extraction_decision"
    assert als.ctx.calls.get("download.download_selected") == 1


def test_privileged_capability_rechecks_the_role(als):
    _setup_pending_download(als, "admin", "s-adm")
    calls = len(als.port.calls)
    als.client("admin").post("/chat/action", json={"decision": "no", "session_id": "s-adm"})
    assert len(als.port.calls) == calls  # chat-group entry: the cached role check is enough
    # Downgrade inside the cache window: entry still passes from the cache, but the yes
    # boundary re-runs whoami and is refused before the download.
    _setup_pending_download(als, "admin", "s-adm2")
    calls = len(als.port.calls)
    als.port.tokens["token-admin"] = als.port.tokens["token-user"]
    resp = als.client("admin").post("/chat/action", json={"decision": "yes", "kind": "download",
                                                          "candidate_index": 0, "session_id": "s-adm2"})
    assert resp.status_code == 403 and resp.json()["code"] == "denied"
    assert len(als.port.calls) == calls + 1
    assert als.ctx.calls.get("download.download_selected") is None


def test_tiled_uri_is_not_writable_on_als(als):
    for who in ("user", "admin"):
        resp = als.client(who).put("/settings", json={"tiled_uri": "http://127.0.0.1:9999"})
        assert resp.status_code == 200
        assert resp.headers[SETTING_WARNING_HEADER] == "setting_not_writable=tiled_uri"
    assert als.client("admin").get("/settings").json()["tiled_uri"] != "http://127.0.0.1:9999"


# ------------------------------------------------------------------ own-or-any


def test_sessions_are_own_or_any(als):
    user, user2, admin = als.client("user"), als.client("user2"), als.client("admin")
    assert user.post("/chat", json={"message": ASK, "session_id": "s-alice", "client_turn_id": "t-a1"}) \
        .status_code == 200
    assert user2.post("/session/reset", json={"session_id": "s-alice"}).status_code == 403
    assert user2.delete("/session/s-alice").status_code == 403
    assert user2.post("/chat", json={"message": ASK, "session_id": "s-alice"}).status_code == 403
    assert user2.post("/chat/action", json={"decision": "no", "session_id": "s-alice"}).status_code == 403
    assert user.post("/session/reset", json={"session_id": "s-alice"}).status_code == 200
    assert admin.post("/session/reset", json={"session_id": "s-alice"}).status_code == 200
    assert user2.delete("/session/s-unowned").status_code == 403  # unowned counts as someone else's
    assert user.delete("/session/s-alice").status_code == 200
    # The shared legacy session (no session id) is nobody's: admins only.
    assert user.post("/session/reset").status_code == 403
    assert user.post("/chat", json={"message": ASK}).status_code == 403
    assert admin.post("/session/reset").status_code == 200


def test_traces_and_annotations_are_own_or_any(als):
    user, user2, admin = als.client("user"), als.client("user2"), als.client("admin")
    user.post("/chat", json={"message": ASK, "session_id": "s-a", "client_turn_id": "t-own"})
    assert user.get("/traces/t-own").status_code == 200
    assert user2.get("/traces/t-own").status_code == 403
    assert user2.get("/traces/t-missing").status_code == 403  # unowned: no existence oracle for users
    assert admin.get("/traces/t-own").status_code == 200
    assert admin.get("/traces/t-missing").status_code == 404
    # A user cannot reuse another principal's turn id (it would overwrite their trace).
    assert user2.post("/chat", json={"message": ASK, "session_id": "s-b", "client_turn_id": "t-own"}) \
        .status_code == 403
    assert user.post("/annotations", json={"turn_id": "t-own", "verdict": "pass", "annotator": "admin"}) \
        .status_code == 200
    assert user2.post("/annotations", json={"turn_id": "t-own", "verdict": "fail"}).status_code == 403
    assert admin.post("/annotations", json={"turn_id": "t-own", "verdict": "fail"}).status_code == 200
    lines = [json.loads(x) for x in (als.ctx.workspace / "traces" / "annotations.jsonl")
             .read_text(encoding="utf-8").splitlines()]
    assert [r["annotator"] for r in lines] == ["uuid-user", "uuid-admin"]


def test_telemetry_actor_comes_from_the_context(als):
    als.client("user").post("/telemetry/ui", json={"events": [
        {"type": "citation_click", "data": {"actor": "uuid-admin"}}]})
    files = list((als.ctx.workspace / "traces").rglob("ui_events.jsonl"))
    events = [json.loads(x) for f in files for x in f.read_text(encoding="utf-8").splitlines()]
    assert [e["actor"] for e in events] == ["uuid-user"]


def test_owners_survive_a_restart(monkeypatch, tmp_path):
    first = build_als(monkeypatch, tmp_path)
    first.client("user").post("/chat", json={"message": ASK, "session_id": "s-keep", "client_turn_id": "t-keep"})
    from app.modules.f2w_agent import api as api_mod
    from _wiring_support import FakeClock, make_cfg, staging_settings

    again = api_mod.create_app(make_cfg(first.ctx), deployment=staging_settings(), identity_port=first.port,
                               credential_store=first.store, clock=FakeClock())
    first.app = again
    first.clients.clear()
    assert first.client("user2").get("/traces/t-keep").status_code == 403
    assert first.client("user2").post("/session/reset", json={"session_id": "s-keep"}).status_code == 403
    assert first.client("user").get("/traces/t-keep").status_code == 200


# ------------------------------------------------------------------ admin


@pytest.mark.parametrize("template,method,path,kwargs", ROUTE_CALLS, ids=CALL_IDS)
def test_admin_is_allowed_on_every_route(als, template, method, path, kwargs):
    admin = als.client("admin")
    admin.post("/chat", json={"message": ASK, "session_id": "s-x", "client_turn_id": "t-x1"})
    resp = admin.request(method, path, **kwargs)
    assert resp.status_code not in (401, 403), resp.text
    assert resp.status_code < 400, resp.text


def test_admin_privileged_work_takes_effect(als):
    admin = als.client("admin")
    assert admin.patch("/graph/node/matkg:TermA", json={"label": "Term A admin"}).status_code == 200
    assert als.ctx.splash_entities["splash-TermA"]["name"] == "Term A admin"
    assert admin.post("/graph/upload", json=UPLOAD).status_code == 200
    assert (als.workdir / "uploads" / "wiring_upload.json").exists()
    assert admin.put("/settings", json={"kg_query_hops": 2}).json()["kg_query_hops"] == 2
    health = admin.get("/health").json()
    assert health["workdir"] == "f2w://workdir" and "model" in health
