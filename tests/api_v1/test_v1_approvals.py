"""Pending Approvals (owner instruction 2026-11-09, sixteenth set): ``GET /v1/approvals/pending`` (V82) and the
``can_approve`` capability flag (V60).

The read aggregates what already waits for a human and names, per item, the existing route that decides it;
the tests approve and reject *through those routes* (V18, V08, V09, V13) and never through a new path. On ALS the
read is admin-only; on ``:local`` the local operator reads it.
"""
from __future__ import annotations

import pytest

from _v1_support import CORPUS, build_local, ingest_inputs

from app.api.operations import PLAN_ITEM_KINDS
from app.api.policies import API_ROUTE_POLICIES
from app.api.routes_capabilities import capabilities_for_caller
from app.modules.contracts.jobs import JobState
from app.modules.curation.models import CaseState
from app.modules.security.authorization import can_review_approvals
from tests.curation.cur_fakes import case as make_case
from tests.runtime.runtime_fakes import als_ctx, local_ctx

URL = "/v1/approvals/pending"
FORGED = {"X-Local-Trusted": "1", "X-F2W-Is-Admin": "true", "X-F2W-Mode": "local_trusted",
          "X-Tiled-Roles": "admin", "X-Forwarded-User": "admin", "Authorization": "Bearer forged-admin"}


def _call(client, action, **override):
    """Call an item's action exactly as the UI does: the API base plus the relative path, the given body."""
    body = dict(action["body"] or {})
    body.update(override)
    return client.request(action["method"], "/" + action["path"], json=body if action["body"] is not None else None)


def _stage_plan(app, who="local", ref="rsoxs/v1"):
    c = app.client(who)
    resp = c.post("/v1/publications", json={"snapshot_ref": ref, "target": app.target})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert app.work() == (job_id, JobState.AWAITING_REVIEW)
    return job_id


def _case_to_review(store):
    case_id, _ = store.enqueue(make_case(evidence="SECRET-EVIDENCE " * 20))
    (leased,) = store.claim("triage", [CORPUS])
    store.route_case(case_id, leased["lease_token"], "human", state=CaseState.NEEDS_REVIEW)
    return case_id


# ------------------------------------------------------------------ policy and the capability flag


def test_policy_row_is_graph_publish_on_every_profile():
    row = next(p for p in API_ROUTE_POLICIES if p.route_id == "V82")
    assert (row.method, row.path, row.capabilities, row.on_als) == ("GET", URL, frozenset({"graph.publish"}), True)
    assert row.needs_fresh_role_check  # graph.publish is an admin capability: the ALS role check re-runs on entry


def test_plan_item_kinds_mirror_the_publication_job_kinds():
    from app.modules.jobs.publication import KIND, OPERATION_KIND_NAMES

    assert set(PLAN_ITEM_KINDS) == {KIND, *OPERATION_KIND_NAMES}
    assert PLAN_ITEM_KINDS[KIND] == "publication"
    assert {PLAN_ITEM_KINDS[k] for k in OPERATION_KIND_NAMES} == {"retire", "purge"}


def test_can_review_approvals_is_the_one_check_behind_the_route_and_the_flag():
    assert can_review_approvals(local_ctx()) is True
    assert can_review_approvals(local_ctx(capabilities=frozenset({"chat.ask"}))) is False
    assert can_review_approvals(als_ctx("a", admin=True)) is True
    assert can_review_approvals(als_ctx("u")) is False
    # The explicit admin clause: graph.publish without a verified admin identity is refused on ALS.
    forged = als_ctx("u", capabilities=frozenset({"chat.ask", "graph.publish"}))
    assert forged.principal.is_admin is False and can_review_approvals(forged) is False
    assert capabilities_for_caller(als_ctx("a", admin=True))["can_approve"] is True
    assert capabilities_for_caller(als_ctx("u"))["can_approve"] is False
    assert capabilities_for_caller(forged)["can_approve"] is False


def test_capabilities_flag_on_local_and_als(local, als):
    assert local.client().get("/v1/capabilities").json()["can_approve"] is True
    assert als.client("admin").get("/v1/capabilities").json()["can_approve"] is True
    assert als.client("user").get("/v1/capabilities").json()["can_approve"] is False
    assert als.client("user", headers=FORGED).get("/v1/capabilities").json()["can_approve"] is False


# ------------------------------------------------------------------ :local


def test_empty_queue_on_local(local):
    body = local.client().get(URL).json()
    assert body["items"] == [] and body["total"] == 0 and body["counts"] == {}
    assert body["includes"] == {"plans": True, "review_cases": True, "cost_control": True, "merge_apply": False}
    assert body["generated_at"].startswith("2026-10-15T12:00")
    assert local.port.calls == 0


def test_staged_plan_is_listed_with_its_hash_and_approved_through_the_route_it_names(local):
    c = local.client()
    job_id = _stage_plan(local)
    plan = c.get(f"/v1/publications/{job_id}/plan").json()
    body = c.get(URL).json()
    assert body["total"] == 1 and body["counts"] == {"publication": 1}
    (item,) = body["items"]
    assert item["kind"] == "publication" and item["id"] == item["job_id"] == job_id and item["job_kind"] == "publish"
    assert item["corpus_scope"] == [CORPUS] and item["target"] == local.target and item["snapshot_ref"] == "rsoxs/v1"
    assert item["plan"]["plan_sha256"] == plan["plan_sha256"] and item["plan"]["creates"] == 3
    assert item["plan"]["plan_id"] == plan["plan_id"] and item["plan"]["supersessions"] == plan["supersessions"]
    assert item["requested_by"] == "local-operator" and item["submitted_at"].startswith("2026-10-15T12:00")
    assert item["reason"] is None
    # The routes survive the response projector (relative to the API base; the UI joins them to its own base).
    assert item["approve"] == {"method": "POST", "path": f"v1/publications/{job_id}/approval",
                               "body": {"plan_sha256": plan["plan_sha256"]}}
    assert item["reject"] == {"method": "POST", "path": f"v1/jobs/{job_id}/cancel", "body": None}
    assert local.tiled.writes == []  # listing approves nothing

    # A stale hash through the named route is the typed 409 and the item stays pending.
    stale = _call(c, item["approve"], plan_sha256="0" * 64)
    assert stale.status_code == 409 and stale.json()["code"] == "stale_approval"
    assert c.get(URL).json()["total"] == 1
    # The real approval: V18, created by create_approval; the item leaves the queue (the job is re-queued).
    ok = _call(c, item["approve"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["approval"]["plan_sha256"] == plan["plan_sha256"] and ok.json()["job"]["state"] == "queued"
    assert c.get(URL).json()["total"] == 0
    assert local.work() == (job_id, JobState.SUCCEEDED)
    assert c.get(URL).json()["total"] == 0


def test_reject_is_the_existing_cancel_route(local):
    c = local.client()
    job_id = _stage_plan(local)
    (item,) = c.get(URL).json()["items"]
    resp = _call(c, item["reject"])
    assert resp.status_code == 200 and resp.json()["state"] == "cancelled"
    assert c.get(URL).json()["total"] == 0
    assert local.tiled.writes == []


def test_rollback_replacement_plan_is_a_rollback_item(local):
    """A publication job that carries a rollback reason is listed as kind ``rollback`` with that reason."""
    c = local.client()
    # The T10 fake step has no replacement importer, so ``rollback_replacement`` itself is unavailable here; the
    # job it would submit is a publication whose params carry ``rollback.reason`` (recorded only on a replacement
    # deployment), so submit that job directly with the flag set for the submission.
    pub = local.ops.publication
    pub.replacement = True
    try:
        handle = pub.submit(local.ops.cli_context(), "rsoxs/v1", local.target, rollback_reason="bad v2")
    finally:
        pub.replacement = False
    assert local.work() == (handle.job_id, JobState.AWAITING_REVIEW)
    body = c.get(URL).json()
    assert body["counts"] == {"rollback": 1}
    (item,) = body["items"]
    assert item["kind"] == "rollback" and item["reason"] == "bad v2" and item["snapshot_ref"] == "rsoxs/v1"
    assert item["approve"]["path"] == f"v1/publications/{handle.job_id}/approval"


def test_review_cases_are_ids_and_counts_only_and_decided_through_v13(local):
    c = local.client()
    store = local.ops.curation
    case_id = _case_to_review(store)
    body = c.get(URL).json()
    assert body["counts"] == {"review_case": 1}
    (item,) = body["items"]
    assert item["kind"] == "review_case" and item["id"] == item["case_id"] == case_id and item["corpus"] == CORPUS
    assert item["case_type"] == "unresolved_term" and item["route"] == "human" and item["revision"]
    assert "evidence" not in item and "payload" not in item and "SECRET-EVIDENCE" not in c.get(URL).text
    review = f"v1/curation/cases/{case_id}/review"
    assert item["approve"] == {"method": "POST", "path": review,
                               "body": {"bound_revision": item["revision"], "accept": True}}
    assert item["reject"] == {"method": "POST", "path": review,
                              "body": {"bound_revision": item["revision"], "accept": False}}
    stale = _call(c, item["reject"], bound_revision="old")
    assert stale.status_code == 409 and stale.json()["code"] == "stale_approval"
    done = _call(c, item["reject"], reason="not a term")
    assert done.status_code == 200 and done.json()["case"]["state"] == "rejected"
    assert c.get(URL).json()["total"] == 0


def test_budget_paused_job_is_listed_with_resume_and_cancel(local):
    c = local.client()
    job = c.post("/v1/jobs", json={"kind": "ingest", "inputs": ingest_inputs()}).json()
    store = local.ops.jobs.store
    store.transition_without_lease(job["job_id"], JobState.RUNNING, reason="test", actor="test")
    store.transition_without_lease(job["job_id"], JobState.PAUSED_BUDGET, reason="budget", actor="test")
    body = c.get(URL).json()
    assert body["counts"] == {"paused_job": 1}
    (item,) = body["items"]
    assert item["kind"] == "paused_job" and item["job_id"] == job["job_id"] and item["job_kind"] == "ingest"
    assert item["corpus_scope"] == [CORPUS] and item["requested_by"] == "local-operator"
    assert item["approve"]["path"] == f"v1/jobs/{job['job_id']}/resume" and item["approve"]["body"] == {
        "expected_revision": store.get(job["job_id"]).revision}
    assert item["reject"]["path"] == f"v1/jobs/{job['job_id']}/cancel"
    resumed = _call(c, item["approve"])
    assert resumed.status_code == 200 and resumed.json()["state"] == "queued"
    assert c.get(URL).json()["total"] == 0


def test_exceeded_cost_cap_is_listed_without_a_one_click_body(local):
    c = local.client()
    assert c.put("/v1/curation/cost-control", json={"enabled": True, "budget_cap_usd": 0.5}).status_code == 200
    assert c.get(URL).json()["total"] == 0
    local.ops.curation.ledger.reserve(corpus=CORPUS, case_id=None, model="m", amount_usd=0.75, cap_usd=None)
    body = c.get(URL).json()
    assert body["counts"] == {"cost_control": 1}
    (item,) = body["items"]
    assert item["kind"] == "cost_control" and item["approve"] is None and item["reject"] is None
    assert item["budget_cap_usd"] == 0.5 and item["spend"]["cap_exceeded"] is True
    assert [(r["method"], r["path"]) for r in item["routes"]] == [
        ("PUT", "v1/curation/cost-control"), ("POST", "v1/curation/cost-approvals")]


def test_accepted_merges_are_listed_when_the_merge_service_is_wired(local):
    from test_v1_merge_apply import _wire
    from tests.canonicalization.test_canon_apply_adapters import CORPUS as MERGE_CORPUS
    from tests.canonicalization.test_canon_apply_adapters import accept, resolved, term_case

    c = local.client()
    store, _port = _wire(local)
    assert c.get(URL).json()["includes"]["merge_apply"] is True and c.get(URL).json()["total"] == 0
    accept(store, term_case("GI-WAXS"), resolved("GIWAXS"))
    body = c.get(URL).json()
    assert body["counts"] == {"merge_apply": 1}
    (item,) = body["items"]
    assert item["kind"] == "merge_apply" and item["corpus"] == MERGE_CORPUS and item["accepted_cases"] == 1
    assert len(item["case_ids"]) == 1 and item["plan"] is None and item["reject"] is None
    assert item["plan_route"] == {"method": "POST", "path": "v1/curation/merges/apply",
                                  "body": {"corpus": MERGE_CORPUS, "route": "apply_migration", "dry_run": True}}
    assert item["approve"]["body"] == {"corpus": MERGE_CORPUS, "route": "apply_migration", "dry_run": False,
                                       "plan_sha256": None}
    # The UI runs the dry run first and names its hash; the real apply is still gate-closed (T07B).
    dry = _call(c, item["plan_route"])
    assert dry.status_code == 200 and dry.json()["report"]["plan_sha256"]
    real = _call(c, item["approve"], plan_sha256=dry.json()["report"]["plan_sha256"])
    assert real.status_code == 501 and real.json()["reason"] == "gate_closed"


def test_everything_pending_is_grouped_and_counted(local):
    c = local.client()
    job_id = _stage_plan(local)
    case_id = _case_to_review(local.ops.curation)
    body = c.get(URL).json()
    assert body["counts"] == {"publication": 1, "review_case": 1} and body["total"] == 2
    assert [i["id"] for i in body["items"]] == [job_id, case_id]


def test_without_publication_or_curation_the_queue_still_answers(monkeypatch, tmp_path):
    bare = build_local(monkeypatch, tmp_path, publication=False)
    bare.ops.curation = None
    body = bare.client().get(URL).json()
    assert body["items"] == [] and body["includes"]["plans"] is False and body["includes"]["review_cases"] is False


@pytest.mark.parametrize("headers", [{}, FORGED])
def test_forged_headers_do_not_change_the_local_answer(local, headers):
    assert local.client("local", headers=headers).get(URL).json()["total"] == 0


# ------------------------------------------------------------------ ALS


def test_als_anonymous_is_401_and_user_is_403_before_any_effect(als):
    assert als.client("anon").get(URL).status_code == 401
    user = als.client("user").get(URL)
    assert user.status_code == 403 and user.json()["code"] == "denied"
    forged = als.client("user", headers=FORGED).get(URL)
    assert forged.status_code == 403
    assert als.ops.jobs.store.list_jobs() == []


def test_als_admin_reads_the_queue_and_approves_through_v18(als):
    admin = als.client("admin")
    job_id = _stage_plan(als, "admin")
    body = admin.get(URL).json()
    assert body["counts"] == {"publication": 1}
    (item,) = body["items"]
    assert item["requested_by"] == "uuid-admin" and item["target"] == als.target
    assert "token-admin" not in admin.get(URL).text and "cookie-admin" not in admin.get(URL).text
    ok = _call(admin, item["approve"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["approval"]["approved_by"] == "uuid-admin" and ok.json()["approval"]["approver_is_admin"] is True
    assert admin.get(URL).json()["total"] == 0


def test_als_admin_who_lost_the_role_is_refused_on_the_next_read(als):
    admin = als.client("admin")
    _stage_plan(als, "admin")
    assert admin.get(URL).status_code == 200
    als.port.tokens["token-admin"].roles = ("user",)
    resp = admin.get(URL)
    assert resp.status_code == 403 and resp.json()["code"] == "denied"
    assert admin.get("/v1/capabilities").json()["can_approve"] is False


def test_als_user_sees_no_admin_job_in_the_queue_even_through_the_sdk(als):
    from test_v1_als import _ctx

    _stage_plan(als, "admin")
    from app.modules.contracts.errors import ContractError

    with pytest.raises(ContractError) as err:
        als.ops.pending_approvals(_ctx(als, "user"))
    assert err.value.code.value == "denied"
    assert als.ops.pending_approvals(_ctx(als, "admin"))["total"] == 1
