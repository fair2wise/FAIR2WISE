"""T25C item 3: local workflows with no sign-in and zero identity calls, against real servers.

The identity port in every local test is the *real* whoami adapter bound to a live
multi-user Tiled, wrapped in a call counter: if local resolution ever asked it, the
counter would show it. Callers present no credential, a server-expired token, or a real
non-admin token (as the ``f2w_session`` cookie and as a bearer header); none of it
changes anything. Publication goes through the T08B importer to a disposable managed
single-user server with its writer key.

Binary cases: LOCAL-01 (every route, no sign-in, any credential), LOCAL-02 (JSON build,
approval, local publication, graph read and edit, Tiled URL setting), LOCAL-03 (audit:
local-operator and the configured writer), LOCAL-04 (API, functions and worker share the
policy), LOCAL-05 (zero user-role calls).
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from live_support import CountingPort, access_token, bearer, loopback_only, mint_expired_token, utcnow
from tiled_server import tiled_python

import _legacy_ui_harness as H
from _wiring_support import UPLOAD, make_cfg

from app.api.auth import SESSION_COOKIE
from app.modules.contracts.context import CAPABILITIES, LOCAL_OPERATOR
from app.modules.graph.backends.tiled_client import TiledGraphClient
from app.modules.graph.backends.tiled_mutations import TiledMutationClient
from app.modules.graph.imports import tiled_json as tj
from app.modules.graph.snapshot import SnapshotRequest, create_snapshot
from app.modules.schema.registry import SchemaRegistry
from app.modules.security.authorization import (
    DeploymentSettings, WorkerAuthority, create_approval, require, resolve_context, verify_approval,
)
from app.modules.security.tiled_identity import TiledCredential, TiledWhoamiAdapter
from tests.snapshots.snapshot_fakes import COMMIT, INPUTS

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")

REPO = Path(__file__).resolve().parents[2]
WRITER = "f2w-local-writer"
CREDENTIALS = ["none", "expired", "non-admin"]


def _token(kind, live_auth):
    if kind == "none":
        return None
    if kind == "expired":
        return mint_expired_token(live_auth, access_token(live_auth, "admin"))
    return access_token(live_auth, "reader")


@pytest.fixture
def counting_port(live_auth):
    with httpx.Client() as http:
        yield CountingPort(TiledWhoamiAdapter(live_auth.url, http))


# ------------------------------------------------------------------ API: every route (LOCAL-01, LOCAL-05)


def _local_calls():
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


@pytest.mark.parametrize("kind", CREDENTIALS)
def test_local_app_serves_every_route_without_sign_in(monkeypatch, tmp_path, live_auth, counting_port, kind):
    from app.modules.f2w_agent import api as api_mod

    token = _token(kind, live_auth)
    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    loopback_only(monkeypatch)
    settings = DeploymentSettings(profile="local", writer_identity=WRITER)
    app = api_mod.create_app(make_cfg(ctx), deployment=settings, identity_port=counting_port)
    client = TestClient(app, headers=bearer(token) if token else None, raise_server_exceptions=False)
    if token:
        client.cookies.set(SESSION_COOKIE, token)
    for method, path, kwargs in _local_calls():
        resp = client.request(method, path, **kwargs)
        assert resp.status_code == 200, (kind, method, path, resp.status_code, resp.text[:300])
        assert "sign_in_required" not in resp.text
    assert counting_port.calls == []
    assert ctx.calls.get("download.download_selected") == 1  # the approved yes ran (full access)
    assert ctx.calls.get("splash.update_entity") == 1         # the graph edit ran
    assert client.get("/settings").json()["tiled_uri"] == "http://127.0.0.1:8001"  # editable on :local
    assert client.post("/auth/sign-in", json={}).status_code == 404  # there is no local login
    text = (ctx.workspace / "traces" / "annotations.jsonl").read_text(encoding="utf-8")
    assert json.loads(text.splitlines()[-1])["annotator"] == LOCAL_OPERATOR


# ------------------------------------------------------------------ workflows (LOCAL-02, LOCAL-03, LOCAL-04)


@pytest.fixture(scope="module")
def registry():
    return SchemaRegistry.discover()


@pytest.mark.parametrize("kind", CREDENTIALS)
def test_local_json_build_approval_publication_and_graph_read(tmp_path, registry, live_auth, live_single,
                                                              counting_port, kind):
    settings = DeploymentSettings(profile="local", writer_identity=WRITER, tiled_target=live_single.url)
    token = _token(kind, live_auth)
    credential = TiledCredential(token, tiled_target=live_auth.url) if token else None
    ctx = resolve_context(settings, credential, request_id="r-local", identity_port=counting_port, clock=utcnow)
    assert ctx.principal.subject == LOCAL_OPERATOR and ctx.capabilities == CAPABILITIES

    # Versioned JSON creation (T23B snapshot).
    require(ctx, "json.build")
    storage = tmp_path / "storage" / "kg"
    storage.mkdir(parents=True)
    snap_root = create_snapshot(SnapshotRequest(
        corpus="synthetic", terms_path=INPUTS / "core_dict.terms.json", storage_kg=storage,
        terms_ref="inputs/core_dict.terms.json", code_commit=COMMIT, registry=registry))
    snap = storage / "synthetic" / "v1"
    assert (snap / "graph.json").is_file() and (snap / "manifest.json").is_file(), snap_root
    graph = json.loads((snap / "graph.json").read_text(encoding="utf-8"))

    # Plan, explicit local approval, and publication through the importer with the writer key.
    fingerprints = tj.default_supported_schema_fingerprints(registry)
    client = TiledMutationClient.from_managed(live_single.server)
    plan = tj.plan_import(snap, client=client, settings=settings, supported_schema_fingerprints=fingerprints)
    approval = create_approval(ctx, plan.change, settings, approval_id="a-local", clock=utcnow)
    assert approval.requested_by == approval.approved_by == LOCAL_OPERATOR
    assert approval.executed_as == WRITER
    assert approval.approver_issuer is None and approval.role_checked_at is None
    assert approval.approver_is_admin is None
    verify_approval(approval, plan.change, settings, at=utcnow())
    worker = WorkerAuthority(ctx, settings, identity_port=counting_port, clock=utcnow)
    out = tj.apply_import(worker.check("graph.publish"), plan, approval, source=snap, client=client,
                          settings=settings, supported_schema_fingerprints=fingerprints,
                          state_dir=tmp_path / "state", clock=utcnow)
    assert out.status == "applied" and out.receipt.receipt.verified
    assert out.receipt.receipt.executed_as == WRITER and out.receipt.extras.approved_by == LOCAL_OPERATOR
    assert out.receipt.extras.requested_by == LOCAL_OPERATOR

    # Graph read back from the managed server (read-only client, writer key).
    reader = TiledGraphClient.from_managed(live_single.server)
    names = {row["name"] for row in reader.query("{ entities(limit: 10000) { name properties } }")["entities"]
             if (row.get("properties") or {}).get("f2w_owner") == "f2w-literature"}
    assert len(names) and {t["name"] for t in graph["things"]} >= names
    assert counting_port.calls == []  # zero user-identity calls through the whole workflow


def test_local_graph_edit_and_tiled_uri_need_no_identity(monkeypatch, tmp_path, live_auth, counting_port):
    """LOCAL-02: graph editing and upload through the API with a non-admin token present."""
    from app.modules.f2w_agent import api as api_mod

    ctx = H.install(monkeypatch, tmp_path / "ws", REPO)
    loopback_only(monkeypatch)
    app = api_mod.create_app(make_cfg(ctx), deployment=DeploymentSettings(profile="local", writer_identity=WRITER),
                             identity_port=counting_port)
    client = TestClient(app, headers=bearer(access_token(live_auth, "reader")), raise_server_exceptions=False)
    assert client.patch("/graph/node/matkg:TermA", json={"label": "Local edit"}).status_code == 200
    assert ctx.splash_entities["splash-TermA"]["name"] == "Local edit"
    up = client.post("/graph/upload", json=UPLOAD)
    assert up.status_code == 200
    node = client.get("/graph/node/matkg:U1", params={"json_graph_path": up.json()["graph_path"]})
    assert node.status_code == 200 and node.json()["id"] == "matkg:U1"
    assert counting_port.calls == []
