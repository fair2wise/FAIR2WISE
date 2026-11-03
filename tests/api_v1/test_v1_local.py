"""T14 on ``:local``: the ``/v1`` routes over the shared operations, with no identity call.

Covers the route table (DEP-19), the legacy entrypoint, jobs (submit/get/events/SSE/progress/
cancel/resume), the staged/approved/published UI mapping, publication against a rehearsal
target (H5 gate), curation review, cost approvals, audit status and the origin/body guards.
"""
from __future__ import annotations

import inspect
import json
from datetime import timedelta

import pytest

from _v1_support import CORPUS, NOW, build_local, ingest_inputs

from app.api.auth import undeclared_routes
from app.api.policies import API_ROUTE_POLICIES, V1_PREFIX
from app.modules.contracts.jobs import JobState
from app.modules.curation.models import CaseState
from app.modules.security.authorization import AGENT_SURFACE, ROUTE_POLICIES
from tests.curation.cur_fakes import case as make_case


def _routes(app):
    return {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())
            if m not in ("HEAD", "OPTIONS")}


def _sse(text):
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((fields.get("event"), fields.get("id"), json.loads(fields.get("data", "null"))))
    return events


# ------------------------------------------------------------------ route table and entrypoints


def test_every_route_is_declared_and_v1_matches_the_policy_table(local):
    assert undeclared_routes(local.app) == []
    routes = _routes(local.app)
    v1 = {r for r in routes if r[1].startswith(V1_PREFIX + "/")}
    assert v1 == {(p.method, p.path) for p in API_ROUTE_POLICIES}
    assert len(API_ROUTE_POLICIES) == len({p.route_id for p in API_ROUTE_POLICIES}) == 63
    legacy = {(p.method, p.path) for p in ROUTE_POLICIES if p.surface == AGENT_SURFACE}
    assert len(legacy) == 19 and legacy <= routes  # the 19 UI routes are still served
    assert routes - v1 - legacy == {("GET", "/docs"), ("GET", "/docs/oauth2-redirect"), ("GET", "/redoc"),
                                    ("GET", "/openapi.json")}
    assert local.port.calls == 0


def test_legacy_create_app_signature_and_routes_are_unchanged():
    from app.modules.f2w_agent import api as api_mod

    params = list(inspect.signature(api_mod.create_app).parameters)
    assert params == ["cfg", "cors_origins", "deployment", "startup_config", "identity_port", "credential_store",
                      "clock"]
    assert list(inspect.signature(api_mod.run_api).parameters) == ["cfg", "host", "port", "cors_origins"]


def test_legacy_ui_routes_still_answer_through_the_new_factory(local):
    c = local.client()
    assert c.get("/health").json()["status"] == "ok"
    assert c.get("/graph").status_code == 200


def test_cli_jobs_api_flag_selects_the_factory(monkeypatch):
    from app.modules.f2w_agent import cli

    args = cli.build_parser().parse_args(["api", "--jobs-api"])
    assert args.jobs_api is True
    assert cli.build_parser().parse_args(["api"]).jobs_api is False


@pytest.mark.parametrize("entry", ["jobs_api", "legacy"])
def test_run_api_warns_when_local_binds_beyond_loopback(monkeypatch, caplog, entry):
    """AUD-16: both entry points log a WARNING naming raw traces; loopback binds log nothing."""
    import logging
    from types import SimpleNamespace

    import uvicorn

    from app.api import main as api_main
    from app.modules.f2w_agent import api as legacy

    from _v1_support import local_settings

    app = SimpleNamespace(state=SimpleNamespace(f2w_access=SimpleNamespace(settings=local_settings())))
    served = []
    monkeypatch.setattr(uvicorn, "run", lambda a, **kw: served.append((a, kw["host"])))
    monkeypatch.delenv("F2W_PUBLIC_PREFIX", raising=False)
    if entry == "jobs_api":
        monkeypatch.setattr(api_main, "create_api_app", lambda cfg, cors_origins=None, **kw: app)  # B7: model_api kwargs
        run = lambda host: api_main.run_api(object(), host=host, port=1)  # noqa: E731
    else:
        monkeypatch.setattr(legacy, "create_app", lambda cfg, cors_origins=None: app)
        run = lambda host: legacy.run_api(object(), host=host, port=1)  # noqa: E731
    with caplog.at_level(logging.WARNING, logger="app.api.auth"):
        run("127.0.0.1")
        assert not [r for r in caplog.records if "raw traces" in r.getMessage()]
        run("0.0.0.0")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "raw traces" in r.getMessage()]
    assert len(warnings) == 1 and "'0.0.0.0'" in warnings[0].getMessage()
    assert served == [(app, "127.0.0.1"), (app, "0.0.0.0")]  # the server still starts; no policy change


# ------------------------------------------------------------------ jobs


def test_job_lifecycle_over_http(local):
    c = local.client()
    resp = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()})
    assert resp.status_code == 200, resp.text
    job = resp.json()
    assert job["state"] == "queued" and job["kind"] == "ingest"
    again = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()}).json()
    assert again["job_id"] == job["job_id"]  # idempotent for identical inputs
    assert [j["job_id"] for j in c.get("/v1/jobs").json()["jobs"]] == [job["job_id"]]
    assert local.work() == (job["job_id"], JobState.SUCCEEDED)
    assert local.ingest_log == [job["job_id"]]
    assert c.get(f"/v1/jobs/{job['job_id']}").json()["state"] == "succeeded"
    progress = c.get(f"/v1/jobs/{job['job_id']}/progress").json()
    assert progress["phases"]["parsing"]["stages"] == {"parse": "completed"}
    events = c.get(f"/v1/jobs/{job['job_id']}/events").json()["events"]
    assert events and events[-1]["state"] == "succeeded"
    later = c.get(f"/v1/jobs/{job['job_id']}/events", params={"after": events[0]["sequence"]}).json()["events"]
    assert len(later) == len(events) - 1
    view = c.get(f"/v1/jobs/{job['job_id']}/view").json()
    assert view["ui_state"] == "completed" and view["publication"] is None
    assert local.port.calls == 0


def test_unknown_kind_and_bad_inputs_are_invalid_input(local):
    c = local.client()
    resp = c.post("/v1/jobs", json={"kind": "nope", "inputs": ingest_inputs()})
    assert resp.status_code == 422 and resp.json()["code"] == "invalid_input"
    secret = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs(api_key="sk-abcdefabcdefabcd")})
    assert secret.status_code == 422 and "sk-abc" not in secret.text
    assert c.post("/v1/jobs", json={"kind": "ingest", "inputs": {}, "mode": "local_trusted"}).status_code == 422
    assert c.get("/v1/jobs/job-missing").status_code == 404


def test_cancel_and_resume(local):
    c = local.client()
    job = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs(wait_for_review=True)}).json()
    assert local.work() == (job["job_id"], JobState.AWAITING_REVIEW)
    assert c.get(f"/v1/jobs/{job['job_id']}/view").json()["ui_state"] == "awaiting_review"
    resumed = c.post(f"/v1/jobs/{job['job_id']}/resume", json={}).json()
    assert resumed["state"] == "queued"
    cancelled = c.post(f"/v1/jobs/{job['job_id']}/cancel").json()
    assert cancelled["state"] == "cancelled"
    assert local.work() is None  # nothing runs after a cancel


def test_job_stream_is_legacy_sse_with_ids_and_a_single_end(local):
    c = local.client()
    job = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()}).json()
    local.work()
    resp = c.get(f"/v1/jobs/{job['job_id']}/stream")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    events = _sse(resp.text)
    kinds = [e[0] for e in events]
    assert kinds.count("end") == 1 and kinds[-1] == "end" and set(kinds[:-1]) == {"job_event"}
    assert events[-1][2] == {"state": "succeeded", "reason": "terminal"}
    ids = [int(e[1]) for e in events[:-1]]
    assert ids == sorted(ids) and ids == [e[2]["sequence"] for e in events[:-1]]
    resumed = _sse(c.get(f"/v1/jobs/{job['job_id']}/stream", headers={"Last-Event-ID": str(ids[0])}).text)
    assert [int(e[1]) for e in resumed[:-1]] == ids[1:]


def test_job_stream_ends_waiting_for_review_and_times_out_while_queued(local):
    c = local.client()
    job = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs(wait_for_review=True)}).json()
    queued = _sse(c.get(f"/v1/jobs/{job['job_id']}/stream").text)
    assert queued[-1][2] == {"state": "queued", "reason": "timeout"}
    local.work()
    waiting = _sse(c.get(f"/v1/jobs/{job['job_id']}/stream").text)
    assert waiting[-1][2] == {"state": "awaiting_review", "reason": "waiting"}
    assert c.get("/v1/jobs/job-missing/stream").status_code == 404


# ------------------------------------------------------------------ publication (rehearsal target only)


def _publish_to_review(local, ref="rsoxs/v1"):
    c = local.client()
    resp = c.post("/v1/publications", json={"snapshot_ref": ref, "target": local.target})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert local.work() == (job_id, JobState.AWAITING_REVIEW)
    return job_id


def test_publication_staged_approved_published_and_rollback(local):
    c = local.client()
    job_id = _publish_to_review(local)
    view = c.get(f"/v1/jobs/{job_id}/view").json()
    assert view["ui_state"] == "staged" and view["publication"]["approvals"] == []
    assert local.tiled.writes == []  # staged is not published
    plan = c.get(f"/v1/publications/{job_id}/plan").json()
    assert plan["target"] == local.target and plan["creates"] == 3
    stale = c.post(f"/v1/publications/{job_id}/approval", json={"plan_sha256": "0" * 64})
    assert stale.status_code == 409 and stale.json()["code"] == "stale_approval"
    ok = c.post(f"/v1/publications/{job_id}/approval", json={"plan_sha256": plan["plan_sha256"]})
    assert ok.status_code == 200, ok.text
    approval = ok.json()["approval"]
    assert approval["plan_sha256"] == plan["plan_sha256"] and approval["target"] == local.target
    assert approval["requested_by"] == approval["approved_by"] == "local-operator"
    assert approval["executed_as"] == "f2w-writer" and approval["approver_issuer"] is None
    assert approval["expires_at"] is None  # :local approvals never expire (O-T2, D3.5/D3.8)
    assert ok.json()["job"]["state"] == "queued"
    assert local.work() == (job_id, JobState.SUCCEEDED)
    view = c.get(f"/v1/jobs/{job_id}/view").json()
    assert view["ui_state"] == "published" and view["publication"]["active"] is True
    assert len(local.tiled.entities) == 3
    pointer = c.get("/v1/publications/pointer", params={"target": local.target}).json()["pointer"]
    assert pointer["version"] == 1 and pointer["snapshot_ref"] == "rsoxs/v1"

    local.snapshots.add(ref="rsoxs/v2", nodes=("a", "b", "c", "d"))
    second = _publish_to_review(local, "rsoxs/v2")
    plan2 = c.get(f"/v1/publications/{second}/plan").json()
    c.post(f"/v1/publications/{second}/approval", json={"plan_sha256": plan2["plan_sha256"]})
    assert local.work() == (second, JobState.SUCCEEDED)
    back = c.post("/v1/publications/rollback", json={"target": local.target, "reason": "rehearsal"})
    assert back.status_code == 200 and back.json()["pointer"]["snapshot_ref"] == "rsoxs/v1"
    assert c.get(f"/v1/jobs/{second}/view").json()["ui_state"] == "superseded"
    assert c.get(f"/v1/jobs/{job_id}/view").json()["ui_state"] == "published"
    kinds = {e.kind for _s, e in local.audit_log.events()}
    assert {"import_step", "activation", "pointer_rollback"} <= kinds


def test_approval_for_a_replanned_job_is_refused(local):
    c = local.client()
    job_id = _publish_to_review(local)
    shown = c.get(f"/v1/publications/{job_id}/plan").json()["plan_sha256"]
    local.tiled.entities.add("foreign")  # the target moved after the reviewer looked
    resp = c.post(f"/v1/publications/{job_id}/approval", json={"plan_sha256": shown})
    assert resp.status_code == 409 and resp.json()["code"] == "stale_approval"
    assert local.tiled.writes == []


def test_h5_gate_refuses_every_target_without_a_rehearsal_allowlist(monkeypatch, tmp_path):
    gated = build_local(monkeypatch, tmp_path, rehearsal=False)
    c = gated.client()
    resp = c.post("/v1/publications", json={"snapshot_ref": "rsoxs/v1", "target": gated.target})
    assert resp.status_code == 501 and resp.json()["code"] == "unsupported_capability"
    assert "H5" in resp.json()["message"]
    assert c.get("/v1/jobs").json()["jobs"] == [] and gated.tiled.writes == []
    rb = c.post("/v1/publications/rollback", json={"target": gated.target, "reason": "x"})
    assert rb.status_code == 501


def test_publication_target_outside_the_deployment_is_denied(local):
    c = local.client()
    for target in ("http://tiled.example:8000/api#f2w", "https://tiled.als.lbl.gov/api#f2w"):
        resp = c.post("/v1/publications", json={"snapshot_ref": "rsoxs/v1", "target": target})
        assert resp.status_code == 403 and resp.json()["code"] == "denied"
    assert c.get("/v1/jobs").json()["jobs"] == []


def test_publication_not_configured_is_unsupported(monkeypatch, tmp_path):
    bare = build_local(monkeypatch, tmp_path, publication=False)
    resp = bare.client().post("/v1/publications", json={"snapshot_ref": "rsoxs/v1", "target": bare.target})
    assert resp.status_code == 501


# ------------------------------------------------------------------ curation review


def test_case_review_is_bound_to_the_revision_and_audited(local):
    store = local.ops.curation
    case_id, _ = store.enqueue(make_case(evidence="x" * 5000))
    (leased,) = store.claim("triage", [CORPUS])
    store.route_case(case_id, leased["lease_token"], "human", state=CaseState.NEEDS_REVIEW)
    c = local.client()
    listed = c.get("/v1/curation/cases").json()["cases"]
    assert [x["id"] for x in listed] == [case_id] and "evidence" not in listed[0]
    detail = c.get(f"/v1/curation/cases/{case_id}").json()
    assert len(detail["case"]["evidence"]) == 600 and detail["case"]["evidence_truncated"] is True
    assert "lease_token" not in detail["case"]
    revision = detail["case"]["revision"]
    stale = c.post(f"/v1/curation/cases/{case_id}/review", json={"bound_revision": "old", "accept": False})
    assert stale.status_code == 409 and stale.json()["code"] == "stale_approval"
    blind = c.post(f"/v1/curation/cases/{case_id}/review", json={"bound_revision": revision, "accept": True})
    assert blind.status_code == 422  # no validated proposal: cannot accept blindly
    done = c.post(f"/v1/curation/cases/{case_id}/review",
                  json={"bound_revision": revision, "accept": False, "reason": "not a term"})
    assert done.status_code == 200 and done.json()["case"]["resolved_by"] == "local-operator"
    assert any(e.kind == "case_review" for _s, e in local.audit_log.events())
    summary = c.get("/v1/curation/summary").json()
    assert summary["cases"] and "budget" in summary


def test_bulk_review_reports_one_result_per_case(local):
    """T31 (B5): every decision runs through review_case; a refused item does not stop the others."""
    store = local.ops.curation
    ids = []
    for _ in range(2):
        case_id, _new = store.enqueue(make_case(evidence="e" * 1000, payload={"surface": f"T{len(ids)}",
                                                                            "category": "technique"}))
        ids.append(case_id)
    leased = store.claim("triage", [CORPUS], limit=2)
    for row in leased:
        store.route_case(row["id"], row["lease_token"], "human", state=CaseState.NEEDS_REVIEW)
    c = local.client()
    revisions = {cid: c.get(f"/v1/curation/cases/{cid}").json()["case"]["revision"] for cid in ids}
    body = {"decisions": [
        {"case_id": ids[0], "bound_revision": revisions[ids[0]], "accept": False, "reason": "not a term"},
        {"case_id": ids[1], "bound_revision": "stale", "accept": False},
        {"case_id": "case-missing", "bound_revision": "r", "accept": False},
    ]}
    resp = c.post("/v1/curation/cases/bulk-review", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert (out["accepted"], out["rejected"], out["failed"]) == (0, 1, 2), out
    assert [r["status"] for r in out["results"]] == ["ok", "error", "error"]
    assert out["results"][0]["case"]["resolved_by"] == "local-operator"
    assert len(out["results"][0]["case"]["evidence"]) == 600 and "lease_token" not in out["results"][0]["case"]
    assert out["results"][1]["code"] == "stale_approval" and out["results"][2]["code"] == "not_found"
    assert store.get_case(ids[1], [CORPUS])["state"] == CaseState.NEEDS_REVIEW.value  # untouched
    assert sum(e.kind == "case_review" for _s, e in local.audit_log.events()) >= 1
    assert c.post("/v1/curation/cases/bulk-review", json={"decisions": []}).status_code == 422
    too_many = {"decisions": [{"case_id": "x", "bound_revision": "r", "accept": False}] * 201}
    assert c.post("/v1/curation/cases/bulk-review", json=too_many).status_code == 422


def test_cost_control_read_set_and_audit(local):
    """T31 (B5): the donor cborg_control switch and cap, read with the ledger spend, set under audit."""
    c = local.client()
    shown = c.get("/v1/curation/cost-control").json()
    assert shown["configured"] is True and shown["enabled"] is False and shown["budget_cap_usd"] is None
    assert shown["spend"]["charged_usd"] == 0 and shown["spend"]["cap_exceeded"] is False
    saved = c.put("/v1/curation/cost-control", json={"enabled": True, "budget_cap_usd": 0.5})
    assert saved.status_code == 200, saved.text
    assert saved.json()["enabled"] is True and saved.json()["budget_cap_usd"] == 0.5
    assert saved.json()["spend"]["cap_usd"] == 0.5
    local.ops.curation.ledger.reserve(corpus=CORPUS, case_id=None, model="m", amount_usd=0.75, cap_usd=None)
    again = c.get("/v1/curation/cost-control").json()
    assert again["spend"]["reserved_usd"] == 0.75 and again["spend"]["cap_exceeded"] is True
    assert c.put("/v1/curation/cost-control", json={"enabled": True, "budget_cap_usd": -1}).status_code == 422
    assert c.put("/v1/curation/cost-control", json={"enabled": True, "budget_cap_usd": 1, "x": 1}).status_code == 422
    cleared = c.put("/v1/curation/cost-control", json={"enabled": False, "budget_cap_usd": None}).json()
    assert cleared["enabled"] is False and cleared["budget_cap_usd"] is None
    assert sum(e.kind == "cost_control" for _s, e in local.audit_log.events()) == 4  # intent + outcome, twice
    assert not (local.ops.control_path.read_text()).startswith("{\n")  # one atomic JSON document


def test_vocabulary_quality_lists_snapshots_newest_first(local):
    """T31 (B5): the C-16 snapshot history behind a read route (counts only)."""
    from datetime import UTC, datetime

    from app.modules.canonicalization.quality_snapshots import summarise, write_snapshot

    c = local.client()
    assert c.get("/v1/vocabulary/quality").json() == {"snapshots": [], "count": 0, "latest": None}
    report = {"candidate_nodes": 10, "candidate_terms": 12, "by_class": {"technique": 7}, "canonical_vocabulary_entries": 3,
              "source_revision": "src1", "vocabulary_revision": "v1"}
    write_snapshot(local.ops.quality_dir, summarise(report), taken_at=datetime(2026, 10, 1, tzinfo=UTC))
    write_snapshot(local.ops.quality_dir, summarise({**report, "candidate_terms": 14}),
                   taken_at=datetime(2026, 10, 2, tzinfo=UTC))
    out = c.get("/v1/vocabulary/quality").json()
    assert out["count"] == 2 and out["latest"]["summary"]["candidate"]["terms"] == 14
    assert [s["summary"]["candidate"]["terms"] for s in out["snapshots"]] == [14, 12]
    assert out["snapshots"][0]["summary"]["canonical"] == {"vocabulary_entries": 3}
    assert c.get("/v1/vocabulary/quality", params={"limit": 1}).json()["count"] == 1


def test_figure_scale_records_local_flow(local):
    """T31 (B5): a human-read scale is recorded, listed and read back; nothing but ids and numbers is stored."""
    c = local.client()
    body = {"corpus": CORPUS, "work_id": "10.1/x", "figure_id": "fig3", "panel_index": 0, "nm_per_px": 1.5,
            "bar_length_px": 100, "bar_length_nm": 150, "note": "bar read at /abs/private/dir/figure.png"}
    created = c.post("/v1/figures/scales", json=body)
    assert created.status_code == 200, created.text
    record = created.json()["record"]
    assert record["id"].startswith("fsc-") and record["recorded_by"] == "local-operator"
    assert "/abs/private" not in created.text  # the note passes the projector (paths opaque)
    assert c.post("/v1/figures/scales", json={**body, "nm_per_px": 0}).status_code == 422
    assert c.post("/v1/figures/scales", json={**body, "length_source": "guess"}).status_code == 422
    listed = c.get("/v1/figures/scales", params={"work_id": "10.1/x", "figure_id": "fig3"}).json()["records"]
    assert [r["id"] for r in listed] == [record["id"]]
    assert c.get("/v1/figures/scales", params={"corpus": "other"}).json() == {"records": []}
    assert c.get(f"/v1/figures/scales/{record['id']}").json()["record"]["bar_length_nm"] == 150
    assert c.get("/v1/figures/scales/fsc-missing").status_code == 404
    assert any(e.kind == "figure_scale" for _s, e in local.audit_log.events())


def test_cost_approval_create_and_revoke(local):
    c = local.client()
    body = {"scope": [CORPUS], "models": ["m1"], "cap_usd": 2.5,
            "expires_at": (NOW + timedelta(hours=1)).isoformat()}
    created = c.post("/v1/curation/cost-approvals", json=body)
    assert created.status_code == 200, created.text
    approval = created.json()
    assert approval["approved_by"] == "local-operator" and approval["cap_usd"] == 2.5
    past = c.post("/v1/curation/cost-approvals",
                  json={**body, "expires_at": (NOW - timedelta(hours=1)).isoformat()})
    assert past.status_code == 422
    revoked = c.post(f"/v1/curation/cost-approvals/{approval['approval_id']}/revoke").json()
    assert revoked == {"approval_id": approval["approval_id"], "status": "revoked"}
    assert local.ops.curation.get_cost_approval(approval["approval_id"])[1] == "revoked"


# ------------------------------------------------------------------ audit and status


def test_audit_status_reports_the_degraded_flag(local):
    c = local.client()
    healthy = c.get("/v1/audit/status").json()
    assert healthy["configured"] and healthy["healthy"] and healthy["degraded"] is False
    local.audit_log.db.close()  # the audit store becomes unreadable
    degraded = c.get("/v1/audit/status").json()
    assert degraded["degraded"] is True and degraded["healthy"] is False and degraded["reason"]
    ops = c.get("/v1/ops/status").json()
    assert ops["audit"]["degraded"] is True and ops["kinds"] == ["ingest", "knowledge_operation", "library_extract",
                                                           "library_harvest", "library_index", "library_probe",
                                                           "library_verify", "publish"]


def test_ops_status_monitor_dto(local):
    c = local.client()
    c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()})
    status = c.get("/v1/ops/status").json()
    assert status["jobs_by_state"] == {"queued": 1}
    assert status["publication"] == {"configured": True, "rehearsal_targets": 1}
    assert status["review_queue"]["cases"] == {}


# ------------------------------------------------------------------ browser-origin and body guards


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://testserver.evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_cross_site_state_changes_are_refused_before_any_effect(local, headers):
    resp = local.client(headers=headers).post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()})
    assert resp.status_code == 403 and resp.json()["code"] == "cross_site_request"
    assert local.ops.jobs.store.list_jobs() == []
    assert local.client(headers=headers).get("/v1/jobs").status_code == 200  # reads are not gated


@pytest.mark.parametrize("origin", ["http://testserver", "http://localhost:5173", "http://127.0.0.1:5173"])
def test_same_origin_and_cors_allowlisted_origins_pass(local, origin):
    resp = local.client(headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"}).post(
        "/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()})
    assert resp.status_code == 200, resp.text


def test_gateway_forwarded_host_counts_as_same_origin(local):
    headers = {"Origin": "https://f2w.example.org", "X-Forwarded-Host": "f2w.example.org",
               "X-Forwarded-Proto": "https"}
    resp = local.client(headers=headers).post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()})
    assert resp.status_code == 200


def test_legacy_routes_are_gated_too(local):
    """T28A (AUD-04): the legacy routes carry the same origin gate; see docs/migration/api/README.md section 5."""
    resp = local.client(headers={"Origin": "https://evil.example"}).post(
        "/telemetry/ui", json={"events": [{"type": "citation_click"}]})
    assert resp.status_code == 403 and resp.json()["code"] == "cross_site_request"
    same = local.client(headers={"Origin": "http://testserver"}).post(
        "/telemetry/ui", json={"events": [{"type": "citation_click"}]})
    assert same.status_code == 200


def test_request_body_cap_on_v1(monkeypatch, tmp_path):
    small = build_local(monkeypatch, tmp_path, max_body_bytes=256)
    big = small.client().post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs(pad="x" * 1000)})
    assert big.status_code == 413 and big.json()["code"] == "payload_too_large"
    assert small.ops.jobs.store.list_jobs() == []
    assert small.client().post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()}).status_code == 200
