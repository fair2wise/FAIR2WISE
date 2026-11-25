"""T14 on ``:als-dev`` (fake identity port, loopback rehearsal target): route and SDK permissions.

Anonymous callers get 401 on every ``/v1`` route; ordinary users are chat-only and are
refused every privileged action before any effect; verified admins can submit, review,
approve (ALS approval with a fresh role check) and publish to the rehearsal target. The
same refusals hold when the shared operations are called directly (SDK) and in the worker
(``WorkerAuthority``). A sign-out or a dropped admin role invalidates dependent approvals.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from _v1_support import ALS_TILED, CORPUS, ingest_inputs

from app.api.policies import API_ROUTE_POLICIES
from app.modules.contracts.context import ActorKind
from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.security.authorization import resolve_context
from app.modules.security.tiled_identity import TiledCredential

FORGED = {"X-Local-Trusted": "1", "X-F2W-Is-Admin": "true", "X-F2W-Mode": "local_trusted",
          "X-Tiled-Roles": "admin",
          "X-Forwarded-User": "admin", "Authorization": "Bearer forged-admin"}


def _sample(path):
    return (path.replace("{job_id}", "job-x").replace("{case_id}", "case-x")
            .replace("{approval_id}", "appr-x").replace("{record_id}", "fsc-x")
            .replace("{paper_id}", "0" * 16))


def _call(client, policy):
    path = _sample(policy.path)
    if policy.method == "GET":
        return client.get(path, params={"target": ALS_TILED + "/api#f2w"})
    return client.request(policy.method, path, json={})


def _ctx(als, who, **kw):
    cred = als.store.get(f"cookie-{who}")
    return resolve_context(als.settings, cred, request_id="sdk", identity_port=als.port, clock=als.ops.clock, **kw)


def _submit_ingest(client):
    resp = client.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()})
    assert resp.status_code == 200, resp.text
    return resp.json()["job_id"]


def _to_review(als):
    c = als.client("admin")
    resp = c.post("/v1/publications", json={"snapshot_ref": "rsoxs/v1", "target": als.target})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert als.work() == (job_id, JobState.AWAITING_REVIEW)
    return job_id


def test_anonymous_gets_sign_in_required_on_every_v1_route(als):
    """Every ALS-exposed route: 401. A ``:local``-only route (T32 folders) is not registered: plain 404."""
    anon = als.client("anon")
    local_only = 0
    for policy in API_ROUTE_POLICIES:
        resp = _call(anon, policy)
        if not policy.on_als:
            local_only += 1
            assert resp.status_code == 404 and resp.json() == {"detail": "Not Found"}, (policy.route_id, resp.text)
            continue
        assert resp.status_code == 401, (policy.route_id, resp.status_code, resp.text)
        assert resp.json()["code"] == "sign_in_required"
    assert local_only == 30 and {p.path for p in API_ROUTE_POLICIES if not p.on_als} == {
        "/v1/folders/roots", "/v1/folders/list", "/v1/folders/index",
        # T35 (A8): library status, papers (list and upload) and verify
        "/v1/library", "/v1/library/papers", "/v1/library/papers/{paper_id}/verify",
        # T38 (A8): KG bundles, schemas, models, estimate, continue and export
        "/v1/library/kgs", "/v1/library/schemas", "/v1/library/models", "/v1/library/estimate",
        "/v1/library/kgs/{name}/export", "/v1/library/kgs/{name}/download", "/v1/library/kgs/continue", "/v1/library/kgs/{name}/publish",
        # T45 (A8): Harvester runs, candidates, the paywalled queue
        "/v1/library/harvest/runs", "/v1/library/harvest/runs/{run_id}/candidates",
        "/v1/library/harvest/candidates/{candidate_id}/pdf", "/v1/library/harvest/candidates/{candidate_id}/dismiss",
        "/v1/library/harvest/candidates/{candidate_id}/undo", "/v1/library/harvest/runs/{run_id}/pdf",
        # owner bug list (task 9): the shared category list (GET and POST share the path)
        "/v1/library/categories",
        # schema generation: validate, insert-core, save (shares /schemas), list/read/diff/delete of user schemas
        "/v1/library/schemas/validate", "/v1/library/schemas/insert-core", "/v1/library/schemas/user",
        "/v1/library/schemas/user/{ref}", "/v1/library/schemas/user/{name}/diff", "/v1/library/schemas/generate", "/v1/library/schemas/preview"}
    assert als.ops.jobs.store.list_jobs() == []


def test_folder_routes_are_absent_on_als_whoever_calls(monkeypatch, tmp_path, caplog):
    """T32 (B4): no folder route exists on an ALS profile, even for a verified admin, even when
    ``F2W_FOLDER_ROOTS`` is set in the startup environment (ignored with a warning)."""
    import logging

    from _v1_support import build_als

    from app.api.folders import FolderRoots

    roots = FolderRoots.from_paths({"papers": tmp_path / "papers"})
    (tmp_path / "papers").mkdir()
    with caplog.at_level(logging.WARNING, logger="app.api.main"):
        als = build_als(monkeypatch, tmp_path, folder_roots=roots,
                        after_install=lambda mp: mp.setenv("F2W_FOLDER_ROOTS", str(tmp_path / "papers")))
    assert als.ops.folders is None  # never attached off :local
    assert any("F2W_FOLDER_ROOTS is ignored" in r.getMessage() for r in caplog.records)
    for who in ("anon", "user", "admin"):
        c = als.client(who)
        for path in ("/v1/folders/roots", "/v1/folders/list?root=papers", "/v1/folders/index?root=papers"):
            resp = c.get(path)
            assert resp.status_code == 404 and resp.json() == {"detail": "Not Found"}, (who, path, resp.text)
            assert "papers" not in resp.text and str(tmp_path) not in resp.text
    # The SDK path refuses too: the shared operations know the profile.
    with pytest.raises(ContractError) as err:
        als.ops.folder_roots(_ctx(als, "admin"))
    assert err.value.code is ErrorCode.DENIED


@pytest.mark.parametrize("headers", [None, FORGED])
def test_ordinary_user_is_refused_every_privileged_action_before_effects(als, headers):
    user = als.client("user", headers=headers)
    privileged = [
        ("POST", "/v1/jobs", {"kind": "ingest", "inputs": ingest_inputs()}),
        ("POST", "/v1/publications", {"snapshot_ref": "rsoxs/v1", "target": als.target}),
        ("POST", "/v1/publications/job-x/approval", {"plan_sha256": "a" * 64}),
        ("POST", "/v1/publications/rollback", {"target": als.target, "reason": "x"}),
        ("POST", "/v1/curation/cases/case-x/review", {"bound_revision": "r", "accept": False}),
        ("POST", "/v1/curation/cost-approvals", {"scope": [CORPUS], "models": ["m"], "cap_usd": 1,
                                                 "expires_at": "2030-01-01T00:00:00Z"}),
        ("POST", "/v1/curation/cost-approvals/appr-x/revoke", None),
        ("GET", "/v1/curation/cases", None), ("GET", "/v1/curation/summary", None),
        ("GET", "/v1/audit/status", None), ("GET", "/v1/ops/status", None),
        # T31 (B5): cost-control PUT, bulk review and recording a scale are curation.approve; the GET is jobs.admin
        ("GET", "/v1/curation/cost-control", None),
        ("PUT", "/v1/curation/cost-control", {"enabled": True, "budget_cap_usd": 5}),
        ("POST", "/v1/curation/cases/bulk-review",
         {"decisions": [{"case_id": "case-x", "bound_revision": "r", "accept": False}]}),
        ("POST", "/v1/figures/scales", {"corpus": CORPUS, "work_id": "10.1/x", "figure_id": "fig1",
                                        "panel_index": 0, "nm_per_px": 2.5}),
    ]
    for method, path, body in privileged:
        resp = user.request(method, path, json=body)
        assert resp.status_code == 403, (path, resp.status_code, resp.text)
        assert resp.json()["code"] == "denied"
    assert als.ops.jobs.store.list_jobs() == [] and als.tiled.writes == []
    assert als.ops.scales.list_records([CORPUS]) == [] and not (als.ops.control_path).exists()
    assert user.get("/v1/jobs").json() == {"jobs": []}
    pointer = user.get("/v1/publications/pointer", params={"target": als.target})
    assert pointer.status_code == 200 and pointer.json() == {"pointer": None, "corpora": {}}  # chat.read_support
    # T31 reads for any signed-in user (chat.read_support): vocabulary quality and figure-scale records
    assert user.get("/v1/vocabulary/quality").json() == {"snapshots": [], "count": 0, "latest": None}
    assert user.get("/v1/figures/scales").json() == {"records": []}
    assert user.get("/v1/figures/scales/fsc-x").status_code == 404


def test_admin_publishes_to_the_rehearsal_target_with_an_als_approval(als):
    admin = als.client("admin")
    job_id = _to_review(als)
    assert admin.get(f"/v1/jobs/{job_id}/view").json()["ui_state"] == "staged"
    plan = admin.get(f"/v1/publications/{job_id}/plan").json()
    before = len(als.port.calls)
    ok = admin.post(f"/v1/publications/{job_id}/approval", json={"plan_sha256": plan["plan_sha256"]})
    assert ok.status_code == 200, ok.text
    approval = ok.json()["approval"]
    assert approval["approved_by"] == "uuid-admin" and approval["approver_is_admin"] is True
    assert approval["approver_issuer"] == ALS_TILED and approval["executed_as"] == "f2w-writer"
    assert approval["role_check_evidence"]["method"] == "tiled_whoami"
    assert approval["expires_at"] is not None  # ALS approvals expire after 24 h (O-T2)
    expires = datetime.fromisoformat(approval["expires_at"].replace("Z", "+00:00"))
    approved = datetime.fromisoformat(approval["approved_at"].replace("Z", "+00:00"))
    assert expires - approved == timedelta(hours=24)
    assert len(als.port.calls) >= before + 2  # fresh role check at entry and inside create_approval
    assert "token-admin" not in ok.text and "cookie-admin" not in ok.text
    assert admin.get(f"/v1/jobs/{job_id}/view").json()["ui_state"] == "approved"
    assert als.work() == (job_id, JobState.SUCCEEDED)
    assert admin.get(f"/v1/jobs/{job_id}/view").json()["ui_state"] == "published"
    assert len(als.tiled.entities) == 3


def test_other_users_do_not_see_an_admins_job(als):
    job_id = _submit_ingest(als.client("admin"))
    for who in ("user", "user2"):
        c = als.client(who)
        assert c.get(f"/v1/jobs/{job_id}").status_code == 404
        assert c.post(f"/v1/jobs/{job_id}/cancel").status_code == 404
        assert c.get(f"/v1/jobs/{job_id}/stream").status_code == 404
    assert als.ops.jobs.store.get(job_id).state is JobState.QUEUED


def test_sdk_calls_apply_the_same_policy_as_http(als):
    """The shared operations refuse the user and admit the admin exactly as the routes do."""
    user, admin = _ctx(als, "user"), _ctx(als, "admin")
    refusals = [
        lambda: als.ops.submit_job(user, "ingest", ingest_inputs()),
        lambda: als.ops.submit_publication(user, "rsoxs/v1", als.target),
        lambda: als.ops.rollback_publication(user, als.target, reason="x"),
        lambda: als.ops.list_cases(user),
        lambda: als.ops.audit_status(user),
        lambda: als.ops.ops_status(user),
        lambda: als.ops.revoke_cost_approval(user, "appr-x"),
        # T31
        lambda: als.ops.cost_control(user),
        lambda: als.ops.set_cost_control(user, enabled=True, budget_cap_usd=1.0),
        lambda: als.ops.bulk_review(user, [{"case_id": "c", "bound_revision": "r", "accept": False}]),
        lambda: als.ops.record_figure_scale(user, corpus=CORPUS, work_id="w", figure_id="f", panel_index=0,
                                            nm_per_px=1.0),
        # T32: folders are a :local feature, whoever asks
        lambda: als.ops.folder_roots(admin),
    ]
    for call in refusals:
        with pytest.raises(ContractError) as err:
            call()
        assert err.value.code is ErrorCode.DENIED
    handle = als.ops.submit_job(admin, "ingest", ingest_inputs())
    assert als.ops.get_job(admin, handle.job_id).state is JobState.QUEUED
    with pytest.raises(ContractError) as hidden:
        als.ops.get_job(user, handle.job_id)
    assert hidden.value.code is ErrorCode.NOT_FOUND


def test_sdk_approval_needs_a_human_and_the_approvers_own_credential(als):
    job_id = _to_review(als)
    admin = _ctx(als, "admin")
    plan = als.ops.current_plan(admin, job_id)
    worker = admin.model_copy(update={"actor_kind": ActorKind.WORKER})
    with pytest.raises(ContractError) as err:
        als.ops.approve_publication(worker, job_id, plan.plan_sha256,
                                    approver_credential=als.store.get("cookie-admin"))
    assert err.value.code is ErrorCode.DENIED
    with pytest.raises(ContractError) as err:  # someone else's credential
        als.ops.approve_publication(admin, job_id, plan.plan_sha256,
                                    approver_credential=als.store.get("cookie-user"))
    assert err.value.code is ErrorCode.DENIED
    result = als.ops.approve_publication(admin, job_id, plan.plan_sha256,
                                         approver_credential=als.store.get("cookie-admin"))
    assert result["approval"]["approved_by"] == "uuid-admin"


def test_worker_reauthorizes_and_stops_when_the_initiator_lost_admin(als):
    """Same policy in the worker: the stage re-checks ingest.extract through WorkerAuthority."""
    job_id = _submit_ingest(als.client("admin"))
    als.port.tokens["token-admin"].roles = ("user",)
    assert als.work() == (job_id, JobState.FAILED)
    assert als.ingest_log == []  # refused before the effect
    events = als.ops.jobs.store.events(job_id)
    assert events[-1].error_code is ErrorCode.DENIED


def test_dropped_admin_role_invalidates_dependent_approvals(als):
    admin = als.client("admin")
    job_id = _to_review(als)
    plan = admin.get(f"/v1/publications/{job_id}/plan").json()
    approval_id = admin.post(f"/v1/publications/{job_id}/approval",
                             json={"plan_sha256": plan["plan_sha256"]}).json()["approval"]["approval_id"]
    store = als.ops.publication.store
    assert store.approval_status(approval_id)[0] == "valid"
    als.port.tokens["token-admin"].roles = ("user",)
    refused = admin.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()})
    assert refused.status_code == 403  # fresh role check sees the downgrade
    status, reason = store.approval_status(approval_id)
    assert status == "invalidated" and "role_downgraded" in reason
    assert als.work() == (job_id, JobState.AWAITING_REVIEW) and als.tiled.writes == []


def test_identity_outage_fails_closed_on_privileged_routes(als):
    als.port.outage = True
    resp = als.client("admin").post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()})
    assert resp.status_code == 503 and resp.json()["code"] == "unavailable"
    assert als.ops.jobs.store.list_jobs() == []


def test_cli_context_is_refused_on_als(als):
    with pytest.raises(ContractError) as err:
        als.ops.cli_context()
    assert err.value.code is ErrorCode.DENIED


def test_credential_for_another_tiled_is_rejected(als):
    als.store.sessions["cookie-admin"] = TiledCredential("token-admin", tiled_target="http://127.0.0.1:1")
    resp = als.client("admin").get("/v1/jobs")
    assert resp.status_code == 401


# ------------------------------------------------------------------ T31 on ALS: scope and audit


def test_admin_records_a_figure_scale_in_scope_and_every_user_reads_it(als):
    admin, user = als.client("admin"), als.client("user")
    body = {"corpus": CORPUS, "work_id": "10.1/x", "figure_id": "fig2", "panel_index": 1, "nm_per_px": 3.25,
            "bar_length_px": 80, "bar_length_nm": 260, "note": "read from the 200 nm bar"}
    outside = admin.post("/v1/figures/scales", json={**body, "corpus": "other"})
    assert outside.status_code == 403 and outside.json()["code"] == "denied"  # corpus outside the ALS scope
    created = admin.post("/v1/figures/scales", json=body)
    assert created.status_code == 200, created.text
    record = created.json()["record"]
    assert record["recorded_by"] == "uuid-admin" and record["length_source"] == "human"
    assert "token-admin" not in created.text and "cookie-admin" not in created.text
    assert [r["id"] for r in user.get("/v1/figures/scales", params={"work_id": "10.1/x"}).json()["records"]] == [
        record["id"]]
    assert user.get(f"/v1/figures/scales/{record['id']}").json()["record"]["nm_per_px"] == 3.25
    denied = user.get("/v1/figures/scales", params={"corpus": "other"})
    assert denied.status_code == 403  # a corpus outside a non-empty scope is denied (T19C rule)
    kinds = [e.kind for _s, e in als.audit_log.events()]
    assert "figure_scale" in kinds


def test_admin_sets_the_cost_control_with_a_fresh_role_check(als):
    admin = als.client("admin")
    before = len(als.port.calls)
    shown = admin.get("/v1/curation/cost-control").json()
    assert shown == {"configured": True, "enabled": False, "budget_cap_usd": None,
                     "spend": {**shown["spend"], "cap_usd": None, "cap_exceeded": False}}
    saved = admin.put("/v1/curation/cost-control", json={"enabled": True, "budget_cap_usd": 12.5})
    assert saved.status_code == 200, saved.text
    assert saved.json()["enabled"] is True and saved.json()["budget_cap_usd"] == 12.5
    assert saved.json()["spend"]["cap_usd"] == 12.5
    assert len(als.port.calls) > before  # fresh role check on the mutation
    als.port.tokens["token-admin"].roles = ("user",)
    refused = admin.put("/v1/curation/cost-control", json={"enabled": False, "budget_cap_usd": None})
    assert refused.status_code == 403
    assert admin.get("/v1/curation/cost-control").status_code == 403  # the read is jobs.admin: a demoted admin loses it
