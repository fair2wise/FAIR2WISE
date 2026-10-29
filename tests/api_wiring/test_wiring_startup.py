"""T18B: startup profile selection, declared routes and the zero-identity-call :local path."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _wiring_support import (  # noqa: E402
    FORGED_HEADERS, H, REPO, STAGING, UPLOAD, FakeTiledIdentityPort, InMemoryCredentialStore, make_cfg,
    staging_settings)

from app.api.auth import undeclared_routes  # noqa: E402
from app.api.legacy_ui import deployment_settings_from  # noqa: E402
from app.modules.contracts.context import DeploymentProfile  # noqa: E402
from app.modules.runtime_bridge.mode import RuntimeConfigError  # noqa: E402
from app.modules.security.authorization import AGENT_SURFACE, ROUTE_POLICIES, StartupConfigError  # noqa: E402

AGENT_ROUTES = {(p.method, p.path) for p in ROUTE_POLICIES if p.surface == AGENT_SURFACE}


def _create(monkeypatch, tmp_path, **kwargs):
    from app.modules.f2w_agent import api as api_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    return api_mod.create_app(make_cfg(ctx), **kwargs)


def test_the_19_agent_policies_are_the_app_routes(local):
    routes = {(m, r.path) for r in local.app.routes for m in (getattr(r, "methods", None) or ())
              if m not in ("HEAD", "OPTIONS")}
    assert len(AGENT_ROUTES) == 19
    assert AGENT_ROUTES <= routes
    assert routes - AGENT_ROUTES == {("GET", "/docs"), ("GET", "/docs/oauth2-redirect"), ("GET", "/redoc"),
                                     ("GET", "/openapi.json")}


def test_startup_refuses_an_undeclared_route(monkeypatch, tmp_path):
    """T28A: ``undeclared_routes(app) == []`` is asserted at startup, not only in tests (fail closed)."""
    from app.modules.f2w_agent import api as api_mod

    monkeypatch.setattr(api_mod, "undeclared_routes", lambda app, surface=None: ["GET /unexpected"])
    with pytest.raises(StartupConfigError, match="routes without a declared access policy: GET /unexpected"):
        _create(monkeypatch, tmp_path)


def test_api_factory_startup_refuses_an_undeclared_route(monkeypatch, tmp_path):
    from app.api import main as main_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.setattr(main_mod, "undeclared_routes", lambda app, surface=None: ["POST /v1/unexpected"])
    with pytest.raises(StartupConfigError, match="POST /v1/unexpected"):
        main_mod.create_api_app(make_cfg(ctx))


def test_undeclared_routes_is_empty_on_every_profile(local, als):
    assert undeclared_routes(local.app) == []
    assert undeclared_routes(als.app) == []


def test_profile_defaults_to_local_when_unset():
    settings = deployment_settings_from({})
    assert settings.profile is DeploymentProfile.LOCAL and settings.is_local
    assert deployment_settings_from({"F2W_DEPLOYMENT_PROFILE": "  "}).is_local


def test_create_app_reads_the_profile_once_from_startup_config(monkeypatch, tmp_path):
    app = _create(monkeypatch, tmp_path, startup_config={})
    assert app.state.f2w_access.settings.is_local
    monkeypatch.setenv("F2W_DEPLOYMENT_PROFILE", "als-dev")
    with pytest.raises(StartupConfigError, match="identity port"):
        _create(monkeypatch, tmp_path / "env")  # os.environ is the startup config by default


@pytest.mark.parametrize("missing", ["identity_port", "credential_store"])
def test_als_without_identity_port_or_session_store_refuses_to_start(monkeypatch, tmp_path, missing):
    kwargs = {"deployment": staging_settings(), "identity_port": FakeTiledIdentityPort(),
              "credential_store": InMemoryCredentialStore()}
    kwargs[missing] = None
    with pytest.raises(StartupConfigError, match="refusing to start unprotected"):
        _create(monkeypatch, tmp_path, **kwargs)


def test_als_refuses_an_identity_port_for_another_tiled(monkeypatch, tmp_path):
    port = FakeTiledIdentityPort(tiled_target="https://tiled.als.lbl.gov")
    with pytest.raises(StartupConfigError, match="not bound"):
        _create(monkeypatch, tmp_path, deployment=staging_settings(), identity_port=port,
                credential_store=InMemoryCredentialStore())


@pytest.mark.parametrize("config", [
    {"F2W_DEPLOYMENT_PROFILE": "als-prod", "F2W_AUTHORIZATION_MODE": "local_trusted"},
    {"F2W_DEPLOYMENT_PROFILE": "staging"},
    {"F2W_DEPLOYMENT_PROFILE": "als-dev", "F2W_TILED_URL": "http://127.0.0.1:8765"},
])
def test_rejected_startup_values_refuse(monkeypatch, tmp_path, config):
    with pytest.raises(StartupConfigError):
        _create(monkeypatch, tmp_path, startup_config=config, identity_port=FakeTiledIdentityPort(),
                credential_store=InMemoryCredentialStore())


def test_langgraph_read_path_is_refused_on_als(monkeypatch, tmp_path):
    from app.modules.f2w_agent import api as api_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    monkeypatch.setenv("F2W_RUNTIME", "langgraph")
    with pytest.raises((StartupConfigError, RuntimeConfigError)):
        api_mod.create_app(make_cfg(ctx), deployment=staging_settings(),
                           identity_port=FakeTiledIdentityPort(tiled_target=STAGING),
                           credential_store=InMemoryCredentialStore())


def _local_calls():
    """One request per agent route, valid on :local (a fresh app)."""
    return [
        ("GET", "/health", {}), ("GET", "/settings", {}),
        ("PUT", "/settings", {"json": {"tiled_uri": "http://127.0.0.1:8001", "graph_source": "splash"}}),
        ("POST", "/session/reset", {"json": {"session_id": "s-local"}}),
        ("GET", "/graph", {}), ("POST", "/graph/nodes/search", {"json": {"query": "resonant", "limit": 3}}),
        ("GET", "/graph/neighborhood/beamline:ESAF-contract", {"params": {"hops": 3}}),
        ("GET", "/graph/node/matkg:TermA", {}),
        ("PATCH", "/graph/node/matkg:TermA", {"json": {"description": "local edit"}}),
        ("POST", "/graph/upload", {"json": UPLOAD}),
        ("POST", "/chat", {"json": {"message": "What is Term A?", "session_id": "s-local",
                                    "client_turn_id": "t-local-1"}}),
        ("POST", "/chat/action", {"json": {"decision": "no", "session_id": "s-local"}}),
        ("POST", "/publications/search", {"json": {"query": "resonant", "max_results": 3}}),
        ("POST", "/chat/stream", {"json": {"message": "Explain the insufficient synthetic topic",
                                           "session_id": "s-local", "client_turn_id": "t-local-2"}}),
        ("POST", "/chat/action/stream", {"json": {"decision": "yes", "kind": "download", "candidate_index": 0,
                                                  "session_id": "s-local", "client_turn_id": "t-local-3"}}),
        ("GET", "/traces/t-local-1", {}),
        ("POST", "/telemetry/ui", {"json": {"events": [{"type": "citation_click", "turn_id": "t-local-1"}]}}),
        ("POST", "/annotations", {"json": {"turn_id": "t-local-1", "verdict": "pass", "annotator": "x"}}),
        ("DELETE", "/session/s-local", {}),
    ]


@pytest.mark.parametrize("headers", [None, FORGED_HEADERS], ids=["plain", "forged-trust-headers"])
def test_local_serves_every_route_with_zero_identity_calls(local, headers):
    client = local.client("anon", headers=headers)
    seen = set()
    for method, path, kwargs in _local_calls():
        resp = client.request(method, path, **kwargs)
        assert resp.status_code == 200, (method, path, resp.status_code, resp.text[:300])
        assert "sign_in_required" not in resp.text
        seen.add(method)
    assert local.port.calls == 0
    assert local.ctx.calls.get("download.download_selected") == 1  # the approved yes ran (full access)
    assert local.ctx.calls.get("splash.update_entity") == 1


def test_local_tiled_uri_stays_editable(local):
    client = local.client("anon")
    resp = client.put("/settings", json={"tiled_uri": "http://127.0.0.1:8001"})
    assert resp.status_code == 200 and "x-f2w-warning" not in resp.headers
    assert resp.json()["tiled_uri"] == "http://127.0.0.1:8001"


def test_local_health_is_full_and_path_free(local):
    body = local.client("anon").get("/health").json()
    assert body["workdir"] == "f2w://workdir" and body["session_memory"].startswith("f2w://workdir/")
    assert {"model", "backend", "pending_approval", "max_rounds"} <= set(body)
    assert str(local.workdir) not in str(body)


def test_local_trace_is_raw_and_annotator_is_the_local_operator(local):
    client = local.client("anon")
    client.post("/chat", json={"message": "What is Term A?", "session_id": "s1", "client_turn_id": "t-raw"})
    trace = client.get("/traces/t-raw").json()
    assert "settings" in trace["env"]  # raw record (projection drops env.settings)
    client.post("/annotations", json={"turn_id": "t-raw", "verdict": "pass", "annotator": "someone-else"})
    text = (local.ctx.workspace / "traces" / "annotations.jsonl").read_text(encoding="utf-8")
    assert '"annotator": "local-operator"' in text and "someone-else" not in text
