"""T18C closure against the migrated API in-process (no Tiled, no network, no model).

* UI24: the six model-compatible endpoints (``kg_rag_api.create_fastapi_app``) against the
  frozen T18A fixtures, plus the D19 check on the proxy chat routes (passes since T28A).
* UI04: editable ("splash") mode with **no Splash service**: the real Splash client runs
  against a refused 127.0.0.1 port. Reads keep working; Save fails closed (502), no write.
* UI10/UI26 recovery: a pending decision survives an app restart; it applies exactly once;
  replays answer ``no_pending_action``.
* UI25: concurrent sessions on one app keep their own pending state and answers.
* UI28: the T14 factory, the legacy factory and the model proxy coexist in one process;
  two legacy apps on separate workdirs do not share sessions.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import _legacy_ui_harness as H
from _wiring_support import make_cfg
from closure_support import dead_port, loopback_guard

REPO = Path(__file__).resolve().parents[4]
FIXTURES = H.FIXTURE_DIR / "http"

MODEL_CASES = [step for step in H.MODEL_STEPS]


@pytest.fixture
def harness(monkeypatch, tmp_path):
    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.delenv("F2W_RUNTIME", raising=False)
    return ctx


def _fixture(fid):
    return json.loads((FIXTURES / f"{fid}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ UI24 six model endpoints


@pytest.mark.parametrize("fid,method,path,kwargs,note", MODEL_CASES, ids=[c[0] for c in MODEL_CASES])
def test_ui24_model_endpoint_matches_frozen_fixture(monkeypatch, harness, fid, method, path, kwargs, note):
    client = H.client_for(H.make_model_app(monkeypatch, harness))
    resp = H.perform(client, harness, method, path, kwargs)
    got = H.response_record(resp, harness)
    want = _fixture(fid)["response"]
    assert got["status_code"] == want["status_code"], (fid, got)
    assert H.shape(got.get("json")) == H.shape(want.get("json")), fid
    assert got.get("json") == want.get("json"), fid


def test_ui24_six_routes_and_no_streaming_format_change(monkeypatch, harness):
    app = H.make_model_app(monkeypatch, harness)
    routes = {(m, r.path) for r in app.routes for m in getattr(r, "methods", ()) or ()
              if r.path not in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")}
    assert routes == {("POST", "/api/chat"), ("POST", "/v1/chat/completions"), ("GET", "/api/tags"),
                      ("GET", "/api/version"), ("GET", "/v1/models"), ("GET", "/api/ps")}
    client = H.client_for(app)
    # Baseline: a stream=true request still gets the single JSON body the consumers parse.
    body = {"model": "kg-rag:latest", "stream": True, "messages": [{"role": "user", "content": "What is Term A?"}]}
    for path in ("/api/chat", "/v1/chat/completions"):
        resp = client.post(path, json=body)
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/json")


def test_ui24_model_proxy_does_not_echo_source_text(monkeypatch, harness):
    """T18C finding F3, fixed by T28A (AUD-03): the proxy chat routes run behind the D19 projection."""
    from app.modules import kg_rag_api

    H.make_model_app(monkeypatch, harness)  # harness patches (lexical retrieval, fake client)
    page = " ".join(f"Sentence {i} of the synthetic contract paper on resonant soft X-ray scattering."
                    for i in range(20))  # one ~1.7k-char page

    class Echo:
        model = "echo"

        async def chat(self, messages, *a, **k):
            return "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))

    monkeypatch.setattr(kg_rag_api, "load_pdf_text", lambda path: page, raising=False)
    monkeypatch.setattr(kg_rag_api, "make_chat_client", lambda backend, model=None: Echo())
    app = kg_rag_api.create_fastapi_app(str(harness.workspace / "storage" / "kg" / H.GRAPH_FILENAME),
                                        backend="ollama")
    resp = H.client_for(app).post("/api/chat", json={"messages": [
        {"role": "user", "content": "Term A resonant scattering contract paper"}]})
    assert resp.status_code == 200
    answer = resp.json()["message"]["content"]
    echoed = [i for i in range(20) if f"Sentence {i} of the synthetic contract paper" in answer]
    assert len(echoed) <= 8  # at most 40% of the page (O-D2), as on the UI chat routes


# ------------------------------------------------------------------ UI04 no Splash service


def _absent_splash_app(monkeypatch, harness, real):
    from app.modules.f2w_agent import api as api_mod
    from app.modules.f2w_agent import kg_update

    sock = dead_port()
    monkeypatch.setenv("KG_RAG_SPLASH_URI", f"http://127.0.0.1:{sock.getsockname()[1]}")
    for name, fn in real.items():
        monkeypatch.setattr(kg_update, name, fn)
    loopback_guard(monkeypatch)
    return sock, TestClient(api_mod.create_app(make_cfg(harness)), raise_server_exceptions=False)


SPLASH_FUNCS = ("splash_find_entity_by_matkg_id", "splash_update_entity", "splash_create_entity",
                "splash_create_link", "splash_find_links", "splash_delete_link", "load_splash_graph")


@pytest.fixture
def real_splash():
    from app.modules.f2w_agent import kg_update
    return {name: getattr(kg_update, name) for name in SPLASH_FUNCS}


def test_ui04_without_splash_reads_work_and_every_edit_fails_closed(monkeypatch, tmp_path, real_splash):
    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    sock, client = _absent_splash_app(monkeypatch, ctx, real_splash)
    try:
        assert client.get("/health").json()["kg_mode"] == "splash"
        assert client.get("/settings").json()["graph_source"] == "splash"
        graph = client.get("/graph").json()
        assert {n["id"] for n in graph["nodes"]} >= {"matkg:TermA", "matkg:Other"}
        assert client.get("/graph/node/matkg:TermA").status_code == 200
        before = client.get("/graph/node/matkg:TermA").json()
        work_before = sorted((p.relative_to(ctx.workspace).as_posix(), p.read_bytes())
                             for p in ctx.workspace.rglob("*.json") if "traces" not in p.parts)
        for body in ({"label": "x"},
                     {"relationship_updates": [{"action": "add", "source": "matkg:Other", "predicate": "rel:affects",
                                                "target": "matkg:TermA"}]},
                     {"linked_code_snippets": [{"id": "matkg:Code1", "_action": "unlink"}]}):
            resp = client.patch("/graph/node/matkg:TermA", json=body)
            assert resp.status_code == 502, resp.text
            assert resp.json()["detail"].startswith("Splash update failed: ")
            assert "127.0.0.1" not in resp.text and "Traceback" not in resp.text
        assert client.get("/graph/node/matkg:TermA").json() == before
        work_after = sorted((p.relative_to(ctx.workspace).as_posix(), p.read_bytes())
                            for p in ctx.workspace.rglob("*.json") if "traces" not in p.parts)
        assert work_after == work_before
        assert not any(k.startswith("splash.") and k != "splash.load_graph" for k in ctx.calls)
    finally:
        sock.close()


# ------------------------------------------------------------------ UI10/UI26 recovery


def _app(ctx, subdir="run"):
    from app.modules.f2w_agent import api as api_mod
    return TestClient(api_mod.create_app(make_cfg(ctx)), raise_server_exceptions=False)


def test_ui26_pending_survives_restart_and_applies_exactly_once(harness):
    sid = "t18c-restart"
    first = _app(harness)
    resp = first.post("/chat", json={"message": "Explain the insufficient synthetic topic", "session_id": sid,
                                     "client_turn_id": "rs-1"})
    assert resp.json()["status"] == "awaiting_download_decision"
    del first  # "process restart": a new app over the same workdir and session state

    second = _app(harness)
    yes = {"decision": "yes", "kind": "download", "candidate_index": 0, "session_id": sid,
           "client_turn_id": "rs-2", "parent_turn_id": "rs-1"}
    resp = second.post("/chat/action", json=yes)
    assert resp.json()["status"] == "awaiting_extraction_decision", resp.text
    assert harness.calls["download.download_selected"] == 1
    for body in (yes, dict(yes, client_turn_id="rs-3")):
        again = second.post("/chat/action", json=body)  # replay of the applied download decision
        assert again.status_code == 400
        assert again.json()["detail"] == "Pending action is extraction, not download"
        assert harness.calls["download.download_selected"] == 1  # never a second download
    ex = {"decision": "yes", "kind": "extraction", "session_id": sid, "client_turn_id": "rs-4"}
    assert second.post("/chat/action", json=ex).json()["status"] == "answered"
    assert second.post("/chat/action", json=dict(ex, client_turn_id="rs-5")).json()["status"] == "no_pending_action"
    assert harness.calls.get("kg_update.splash_reimport") == 1 and harness.calls.get("kg_update.rebuild_kg") == 1


def test_ui10_retry_after_a_streamed_action_does_not_apply_twice(harness):
    """A streamed action answered once; a non-stream retry of the same decision is refused explicitly."""
    client = _app(harness)
    sid = "t18c-fallback"
    client.post("/chat", json={"message": "Explain the insufficient synthetic topic", "session_id": sid,
                               "client_turn_id": "fb-1"})
    body = {"decision": "yes", "kind": "download", "candidate_index": 0, "session_id": sid,
            "client_turn_id": "fb-2", "parent_turn_id": "fb-1"}
    events = H.parse_sse(client.post("/chat/action/stream", json=body).content)
    assert events[0]["data"]["phase"] == "turn_started" and events[-1]["event"] == "complete"
    fallback = client.post("/chat/action", json=body)  # what a buggy retry would send
    assert fallback.status_code == 400
    assert fallback.json()["detail"] == "Pending action is extraction, not download"
    assert harness.calls["download.download_selected"] == 1


# ------------------------------------------------------------------ UI25 concurrency


def test_ui25_concurrent_sessions_keep_their_own_state(harness):
    """12 interleaved turns on one event loop (as under uvicorn), across 12 sessions."""
    import asyncio

    import httpx

    from app.modules.f2w_agent import api as api_mod

    app = api_mod.create_app(make_cfg(harness))

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async def turn(i):
                sid = f"t18c-cc-{i}"
                msg = "Explain the insufficient synthetic topic" if i % 2 else "What is Term A resonant scattering?"
                r = await client.post("/chat", json={"message": msg, "session_id": sid, "client_turn_id": f"cc-{i}"})
                return sid, i % 2, r.json()["status"], r.json()["turn_id"]

            results = await asyncio.gather(*(turn(i) for i in range(12)))
            decisions = await asyncio.gather(*(client.post("/chat/action", json={
                "decision": "no", "kind": "download", "session_id": sid, "client_turn_id": f"{sid}-no"})
                for sid, _ins, _st, _t in results))
            return results, [d.json()["status"] for d in decisions]

    results, decided = asyncio.run(run())
    for (sid, insufficient, status, turn_id), after in zip(results, decided):
        assert status == ("awaiting_download_decision" if insufficient else "answered"), (sid, status)
        assert turn_id == f"cc-{sid.rsplit('-', 1)[1]}"
        assert after == ("stopped_by_user" if insufficient else "no_pending_action"), sid
    assert harness.calls.get("download.download_selected", 0) == 0


# ------------------------------------------------------------------ UI28 coexistence


def test_ui28_factories_coexist_without_route_or_state_collisions(monkeypatch, harness, tmp_path):
    from app.api.main import create_api_app
    from app.modules.f2w_agent import api as api_mod

    v1 = create_api_app(make_cfg(harness))
    legacy = api_mod.create_app(make_cfg(harness, "splash"))
    model = H.make_model_app(monkeypatch, harness)
    legacy_paths = {r.path for r in legacy.routes}
    v1_paths = {r.path for r in v1.routes}
    assert legacy_paths <= v1_paths  # the T14 factory keeps every legacy route
    assert any(p.startswith("/v1/") for p in v1_paths) and not any(p.startswith("/v1/jobs") for p in legacy_paths)
    assert {r.path for r in model.routes} & (v1_paths - {"/openapi.json", "/docs", "/docs/oauth2-redirect",
                                                          "/redoc"}) == set()
    a, b = TestClient(v1, raise_server_exceptions=False), TestClient(legacy, raise_server_exceptions=False)
    assert a.post("/chat", json={"message": "Explain the insufficient synthetic topic", "session_id": "co-1",
                                 "client_turn_id": "co-1"}).json()["status"] == "awaiting_download_decision"
    # Second app: separate workdir -> the first app's pending decision is not visible there.
    from dataclasses import replace
    other = api_mod.create_app(replace(make_cfg(harness), workdir=harness.workspace / "run-other"))
    c = TestClient(other, raise_server_exceptions=False)
    assert c.post("/chat/action", json={"decision": "no", "session_id": "co-1",
                                        "client_turn_id": "co-2"}).json()["status"] == "no_pending_action"
    assert b.get("/health").status_code == 200
    assert a.post("/chat/action", json={"decision": "no", "session_id": "co-1",
                                        "client_turn_id": "co-3"}).json()["status"] == "stopped_by_user"
