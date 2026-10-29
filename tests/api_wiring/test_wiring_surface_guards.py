"""AUD-04 (T28A): the 19 legacy routes carry the browser-origin gate and the 128 MiB body cap; no ALS CORS fallback.

Response contracts for same-origin and non-browser callers are unchanged: the gate only refuses
state-changing requests that a browser marks cross-site, the cap only refuses bodies over the
gateway's ``client_max_body_size``. On ``als-dev``/``als-prod`` an empty CORS allowlist means no
cross-origin browser access instead of the Vite development origins with credentials; ``:local``
keeps that fallback.
"""
from __future__ import annotations

import pytest

from _wiring_support import build_als
from security_fakes import staging_settings

from app.api.upload_limits import LEGACY_MAX_BODY_BYTES
from app.modules.f2w_agent.api import DEFAULT_CORS_ORIGINS, effective_cors_origins

CROSS_SITE = [
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://testserver.evil.example"},
]
STATE_CHANGING = [
    ("POST", "/chat", {"message": "hello", "session_id": "s1"}),
    ("POST", "/graph/nodes/search", {"query": "x"}),
    ("PATCH", "/graph/node/matkg:X", {"name": "y"}),
    ("POST", "/graph/upload", {"graph": {"things": []}, "name": "g.json"}),
    ("POST", "/session/reset", {"session_id": "s1"}),
    ("DELETE", "/session/s1", None),
    ("POST", "/telemetry/ui", {"event": "x"}),
    ("POST", "/annotations", {"turn_id": "t", "verdict": "ok"}),
]


# ------------------------------------------------------------------ origin gate on legacy routes


@pytest.mark.parametrize("headers", CROSS_SITE, ids=lambda h: "+".join(f"{k}={v}" for k, v in h.items()))
@pytest.mark.parametrize("method,path,body", STATE_CHANGING, ids=[f"{m} {p}" for m, p, _ in STATE_CHANGING])
def test_cross_site_state_changes_on_legacy_routes_are_refused_before_any_route(local, headers, method, path, body):
    resp = local.client("local", headers=headers).request(method, path, json=body)
    assert resp.status_code == 403 and resp.json()["code"] == "cross_site_request", (path, resp.text)


def test_cross_site_refusal_holds_on_als_for_signed_in_callers_too(als):
    resp = als.client("admin", headers={"Origin": "https://evil.example"}).post(
        "/chat", json={"message": "hi", "session_id": "s1"})
    assert resp.status_code == 403 and resp.json()["code"] == "cross_site_request"
    assert als.port.calls == []  # refused before access control resolved anyone


@pytest.mark.parametrize("origin", ["http://testserver", "http://localhost:5173", "http://127.0.0.1:5175"])
def test_same_origin_and_local_dev_origins_pass_the_gate(local, origin):
    resp = local.client("local", headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"}).post(
        "/graph/nodes/search", json={"query": "Term"})
    assert resp.status_code != 403


def test_non_browser_callers_and_reads_are_untouched(local):
    client = local.client("local")
    assert client.get("/health").status_code == 200
    assert client.get("/settings").status_code == 200
    resp = client.post("/graph/nodes/search", json={"query": "Term"})
    assert resp.status_code != 403
    # A cross-site *read* is not state-changing: CORS, not the gate, governs what the browser may see.
    assert local.client("local", headers={"Origin": "https://evil.example"}).get("/health").status_code == 200
    preflight = local.client("local").options("/chat", headers={"Origin": "https://evil.example",
                                                                  "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 400  # the legacy CORS refusal, unchanged (frozen fixture)


def test_gateway_forwarded_host_counts_as_same_origin(local):
    headers = {"Origin": "https://f2w.example.org", "X-Forwarded-Host": "f2w.example.org",
               "X-Forwarded-Proto": "https"}
    resp = local.client("local", headers=headers).post("/graph/nodes/search", json={"query": "Term"})
    assert resp.status_code != 403


# ------------------------------------------------------------------ body cap on legacy routes


def test_legacy_upload_over_128_mib_is_refused_while_arriving(local):
    client = local.client("local")
    too_big = str(LEGACY_MAX_BODY_BYTES + 1)
    resp = client.post("/graph/upload", content=b"{}", headers={"Content-Length": too_big,
                                                                 "Content-Type": "application/json"})
    assert resp.status_code == 413 and resp.json()["code"] == "payload_too_large"
    assert not list((local.workdir / "uploads").glob("*")) if (local.workdir / "uploads").exists() else True


def test_legacy_cap_matches_the_gateway_limit():
    assert LEGACY_MAX_BODY_BYTES == 128 * 1024 * 1024
    nginx = open("docker/nginx.conf", encoding="utf-8").read()
    assert "client_max_body_size 128m" in nginx


def test_small_legacy_bodies_are_unchanged(local):
    resp = local.client("local").post("/graph/nodes/search", json={"query": "Term"})
    assert resp.status_code == 200


# ------------------------------------------------------------------ CORS fallback


def test_cors_fallback_only_on_local():
    assert effective_cors_origins(None, is_local=True) == DEFAULT_CORS_ORIGINS
    assert effective_cors_origins([], is_local=True) == DEFAULT_CORS_ORIGINS
    assert effective_cors_origins(None, is_local=False) == []
    assert effective_cors_origins([], is_local=False) == []
    assert effective_cors_origins(["https://ui.example"], is_local=False) == ["https://ui.example"]


def test_als_app_without_cors_origins_grants_no_dev_origin(als):
    headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    preflight = als.client("anon").options("/chat", headers=headers)
    assert preflight.status_code == 400 and "access-control-allow-origin" not in preflight.headers
    simple = als.client("admin", headers={"Origin": "http://localhost:5173"}).get("/health")
    assert "access-control-allow-origin" not in simple.headers


def test_local_app_keeps_the_dev_origin_fallback(local):
    headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    preflight = local.client("local").options("/chat", headers=headers)
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_als_app_with_explicit_cors_origins_allows_only_those(monkeypatch, tmp_path):
    from app.modules.f2w_agent import api as api_mod

    dep = build_als(monkeypatch, tmp_path)
    app = api_mod.create_app(
        __import__("_wiring_support").make_cfg(dep.ctx), cors_origins=["https://ui.example"],
        deployment=staging_settings(), identity_port=dep.port, credential_store=dep.store)
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)
    ok = client.options("/chat", headers={"Origin": "https://ui.example", "Access-Control-Request-Method": "POST"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "https://ui.example"
    dev = client.options("/chat", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert dev.status_code == 400 and "access-control-allow-origin" not in dev.headers
