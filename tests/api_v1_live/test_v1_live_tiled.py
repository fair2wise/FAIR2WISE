"""T14 against a real disposable Tiled: ``/v1`` permissions with real sign-in and real roles.

``create_api_app`` runs on ``:als-dev`` bound to the disposable server
(``DeploymentSettings.for_disposable_test_target``) with the real
``TiledWhoamiAdapter`` and ``TiledSessionStore``. Users sign in through
``POST /auth/sign-in``. Publication drives the T10 fake ``PublishStep`` (an in-memory
"graph"); the disposable Tiled is the identity authority and the rehearsal target name,
and no test writes to it.
"""
from __future__ import annotations

from typing import Dict

import httpx
import pytest
from fastapi.testclient import TestClient

from _v1_support import CORPUS, _build, ingest_inputs
from live_support import drop_role, loopback_only, principal_uuid, utcnow
from tiled_server import tiled_python

from app.api.auth import SESSION_COOKIE, undeclared_routes
from app.api.sessions import als_identity_wiring
from app.modules.contracts.jobs import JobState
from app.modules.security.authorization import (
    DeploymentSettings, WorkerAuthority, resolve_context, worker_context)

pytestmark = pytest.mark.skipif(tiled_python() is None, reason="F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")


def _build_live(monkeypatch, tmp_path, inst):
    settings = DeploymentSettings.for_disposable_test_target(
        "als-dev", tiled_target=inst.url, writer_identity=inst.writer.uuid, corpus_scope=frozenset({CORPUS}))
    http = httpx.Client(follow_redirects=False)
    port, store = als_identity_wiring(settings, http, cookie_path="/")
    sessions: Dict[str, str] = {}  # subject -> opaque session id (server side only)
    recorded: Dict[str, object] = {}

    def ctx_for(job):
        if job.job_id not in recorded:
            cred = store.get(sessions.get(job.created_by, ""))
            ctx = resolve_context(settings, cred, request_id=job.job_id, identity_port=port, clock=utcnow,
                                  require_fresh=True)
            recorded[job.job_id] = worker_context(ctx, run_id=job.job_id)
        return recorded[job.job_id]

    def authority(job, caps):
        def credential():
            return store.get(sessions.get(job.created_by, ""))

        return WorkerAuthority(ctx_for(job), settings, identity_port=port, clock=utcnow,
                               credential_provider=credential).check(*caps)

    v1 = _build(monkeypatch, tmp_path, settings, port=port, store=store, worker_ctx=ctx_for, authority=authority,
                clock=utcnow, after_install=loopback_only)

    def sign_in(user):
        client = TestClient(v1.app, base_url="https://testserver", raise_server_exceptions=False)
        resp = client.post("/auth/sign-in", json={"provider": inst.provider, "username": user,
                                                  "password": inst.passwords[user]})
        assert resp.status_code == 200, resp.text
        sessions[principal_uuid(inst, user)] = client.cookies.get(SESSION_COOKIE)
        return client

    return v1, sign_in


def _publish(client, v1):
    resp = client.post("/v1/publications", json={"snapshot_ref": "rsoxs/v1", "target": v1.target})
    assert resp.status_code == 200, resp.text
    return resp.json()["job_id"]


@pytest.fixture
def live(monkeypatch, tmp_path, live_v1):
    return _build_live(monkeypatch, tmp_path, live_v1)


def test_v1_routes_are_declared_and_anonymous_is_refused(live):
    v1, _sign_in = live
    assert undeclared_routes(v1.app) == []
    anon = TestClient(v1.app, base_url="https://testserver", raise_server_exceptions=False)
    resp = anon.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()})
    assert resp.status_code == 401 and resp.json()["code"] == "sign_in_required"
    assert anon.get("/v1/audit/status").status_code == 401


def test_reader_is_chat_only_and_admin_publishes_with_a_real_role_check(live, live_v1):
    v1, sign_in = live
    reader, admin = sign_in("reader"), sign_in("admin")
    for method, path, body in (("POST", "/v1/jobs", {"kind": "ingest", "inputs": ingest_inputs()}),
                               ("POST", "/v1/publications", {"snapshot_ref": "rsoxs/v1", "target": v1.target}),
                               ("GET", "/v1/curation/cases", None), ("GET", "/v1/audit/status", None)):
        resp = reader.request(method, path, json=body)
        assert resp.status_code == 403, (path, resp.text)
    assert v1.ops.jobs.store.list_jobs() == []

    job_id = _publish(admin, v1)
    assert v1.work() == (job_id, JobState.AWAITING_REVIEW)
    assert reader.get(f"/v1/jobs/{job_id}").status_code == 404
    plan = admin.get(f"/v1/publications/{job_id}/plan").json()
    ok = admin.post(f"/v1/publications/{job_id}/approval", json={"plan_sha256": plan["plan_sha256"]})
    assert ok.status_code == 200, ok.text
    approval = ok.json()["approval"]
    assert approval["approved_by"] == principal_uuid(live_v1, "admin")
    assert approval["approver_issuer"] == live_v1.url and approval["approver_is_admin"] is True
    assert approval["executed_as"] == live_v1.writer.uuid != approval["approved_by"]
    assert approval["role_check_evidence"]["tiled_library_version"]
    assert v1.work() == (job_id, JobState.SUCCEEDED)
    assert admin.get(f"/v1/jobs/{job_id}/view").json()["ui_state"] == "published"
    assert admin.get("/v1/audit/status").json()["degraded"] is False


def test_sign_out_invalidates_the_admins_pending_approval(live):
    v1, sign_in = live
    admin = sign_in("admin")
    job_id = _publish(admin, v1)
    v1.work()
    plan = admin.get(f"/v1/publications/{job_id}/plan").json()
    approval_id = admin.post(f"/v1/publications/{job_id}/approval",
                             json={"plan_sha256": plan["plan_sha256"]}).json()["approval"]["approval_id"]
    out = admin.post("/auth/sign-out", headers={"Origin": "https://testserver"})
    assert out.status_code == 200
    status, reason = v1.ops.publication.store.approval_status(approval_id)
    assert status == "invalidated" and reason == "signed_out"
    v1.work()
    assert v1.tiled.writes == [] and v1.ops.jobs.store.get(job_id).state is not JobState.SUCCEEDED
    assert admin.get("/v1/jobs").status_code == 401


def test_cross_site_sign_out_is_refused_against_the_real_session_store(live):
    v1, sign_in = live
    admin = sign_in("admin")
    resp = admin.post("/auth/sign-out", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert admin.get("/v1/jobs").status_code == 200  # still signed in


def test_real_role_downgrade_stops_the_worker_before_the_effect(monkeypatch, tmp_path, tiled_base):
    from live_support import create_service_account
    from tiled_server import start_tiled_instance

    inst = start_tiled_instance(tiled_base, auth=True, seed=False)
    try:
        inst.writer = create_service_account(inst)
        v1, sign_in = _build_live(monkeypatch, tmp_path, inst)
        admin = sign_in("admin")
        job_id = admin.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()}).json()["job_id"]
        assert drop_role(inst, "admin") == 1
        assert v1.work() == (job_id, JobState.FAILED)
        assert v1.ingest_log == []
        refused = admin.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs(second=True)})
        assert refused.status_code == 403
    finally:
        inst.destroy()
