"""Model C over ``/v1`` (owner decision 2026-11-09): the additive ``corpora`` map of V19, the optional ``corpus`` of
V20 and V53, and the job ``ui_state`` that compares a version with the *same corpus's* live version.

The fake importer has no release marker, so the pointer-only rollback (V20) is the one that runs here; the
replacement rollback with a real importer is covered in ``tests/jobs/test_replacement_publication.py``.
"""
from __future__ import annotations

from _v1_support import build_local

from app.api.policies import API_ROUTE_POLICIES
from app.modules.contracts.jobs import JobState


def publish(local, ref: str) -> str:
    c = local.client()
    job_id = c.post("/v1/publications", json={"snapshot_ref": ref, "target": local.target}).json()["job_id"]
    assert local.work() == (job_id, JobState.AWAITING_REVIEW)
    plan = c.get(f"/v1/publications/{job_id}/plan").json()
    assert c.post(f"/v1/publications/{job_id}/approval", json={"plan_sha256": plan["plan_sha256"]}).status_code == 200
    assert local.work() == (job_id, JobState.SUCCEEDED)
    return job_id


def pointer(local) -> dict:
    return local.client().get("/v1/publications/pointer", params={"target": local.target}).json()


def test_v19_keeps_its_fields_and_gains_a_corpora_map(local):
    assert pointer(local) == {"pointer": None, "corpora": {}}
    local.snapshots.add(ref="rsoxs/v2", nodes=("a", "b", "c", "d"))
    local.snapshots.add(ref="bl1101/v4", nodes=("x", "y"), links=(("x", "y"),))
    publish(local, "rsoxs/v1")
    first = publish(local, "rsoxs/v2")
    other = publish(local, "bl1101/v4")
    body = pointer(local)
    assert set(body["pointer"]) >= {"target", "version", "snapshot_ref", "snapshot_sha256", "receipt_id", "index_ref"}
    assert body["pointer"]["snapshot_ref"] == "bl1101/v4" and body["pointer"]["version"] == 3
    assert set(body["corpora"]) == {"rsoxs", "bl1101"}
    assert body["corpora"]["bl1101"] == {"snapshot_ref": "bl1101/v4", "version_of_pointer_that_activated_it": 3,
                                          "receipt_id": body["pointer"]["receipt_id"]}
    assert body["corpora"]["rsoxs"]["snapshot_ref"] == "rsoxs/v2"
    assert body["corpora"]["rsoxs"]["version_of_pointer_that_activated_it"] == 2
    assert set(body["corpora"]["rsoxs"]) == {"snapshot_ref", "version_of_pointer_that_activated_it", "receipt_id"}
    # ui_state: another corpus being published later does not supersede this corpus's version.
    c = local.client()
    assert c.get(f"/v1/jobs/{first}/view").json()["ui_state"] == "published"
    assert c.get(f"/v1/jobs/{other}/view").json()["ui_state"] == "published"


def test_job_is_superseded_only_by_another_version_of_the_same_corpus(local):
    local.snapshots.add(ref="rsoxs/v2", nodes=("a", "b", "c", "d"))
    local.snapshots.add(ref="bl1101/v4", nodes=("x", "y"), links=(("x", "y"),))
    v1 = publish(local, "rsoxs/v1")
    other = publish(local, "bl1101/v4")
    c = local.client()
    assert c.get(f"/v1/jobs/{v1}/view").json()["ui_state"] == "published"
    v2 = publish(local, "rsoxs/v2")
    assert c.get(f"/v1/jobs/{v1}/view").json()["ui_state"] == "superseded"
    assert c.get(f"/v1/jobs/{v2}/view").json()["ui_state"] == "published"
    assert c.get(f"/v1/jobs/{other}/view").json()["ui_state"] == "published"


def test_v20_with_corpus_steps_back_within_that_corpus_only(local):
    local.snapshots.add(ref="rsoxs/v2", nodes=("a", "b", "c", "d"))
    local.snapshots.add(ref="bl1101/v4", nodes=("x", "y"), links=(("x", "y"),))
    publish(local, "rsoxs/v1")
    publish(local, "rsoxs/v2")
    publish(local, "bl1101/v4")
    c = local.client()
    # Without a corpus while two are live: refused (owner decision 2026-11-09, nineteenth set), nothing moves.
    plain = c.post("/v1/publications/rollback", json={"target": local.target, "reason": "legacy"})
    body = plain.json()
    assert plain.status_code == 409 and body["code"] == "conflict" and body["reason"] == "corpus_required", plain.text
    assert body["retryable"] is False and "bl1101" in body["message"] and "rsoxs" in body["message"]
    assert pointer(local)["pointer"]["snapshot_ref"] == "bl1101/v4"
    back = c.post("/v1/publications/rollback", json={"target": local.target, "reason": "A", "corpus": "rsoxs"})
    assert back.status_code == 200 and back.json()["pointer"]["snapshot_ref"] == "rsoxs/v1"
    body = pointer(local)
    assert body["corpora"]["rsoxs"]["snapshot_ref"] == "rsoxs/v1" and body["corpora"]["bl1101"]["snapshot_ref"] == "bl1101/v4"
    assert body["pointer"]["snapshot_ref"] == "rsoxs/v1", "the pointer stays 'last activated'"


def test_v20_without_corpus_still_means_the_previous_pointer_with_one_live_corpus(local):
    local.snapshots.add(ref="rsoxs/v2", nodes=("a", "b", "c", "d"))
    publish(local, "rsoxs/v1")
    publish(local, "rsoxs/v2")
    plain = local.client().post("/v1/publications/rollback", json={"target": local.target, "reason": "legacy"})
    assert plain.status_code == 200 and plain.json()["pointer"]["snapshot_ref"] == "rsoxs/v1"


def test_corpus_with_no_earlier_version_is_refused_with_a_typed_error(local):
    local.snapshots.add(ref="bl1101/v4", nodes=("x", "y"), links=(("x", "y"),))
    publish(local, "rsoxs/v1")
    publish(local, "bl1101/v4")
    c = local.client()
    for route in ("rollback", "rollback-replacement"):
        resp = c.post(f"/v1/publications/{route}", json={"target": local.target, "reason": "x", "corpus": "bl1101"})
        assert resp.status_code == 409 and resp.json()["code"] == "conflict", (route, resp.text)
        if route == "rollback":
            assert resp.json()["reason"] == "no_previous_corpus_version"
        resp = c.post(f"/v1/publications/{route}", json={"target": local.target, "reason": "x", "corpus": "nope"})
        assert resp.status_code in (404, 409)
    assert pointer(local)["pointer"]["snapshot_ref"] == "bl1101/v4"
    assert c.post("/v1/publications/rollback", json={"target": local.target, "reason": "x", "corpus": ""}
                  ).status_code == 422


def test_policies_of_the_changed_routes_are_unchanged():
    rows = {p.route_id: p for p in API_ROUTE_POLICIES if p.route_id in ("V19", "V20", "V53")}
    assert rows["V19"].capabilities == frozenset({"chat.read_support"})
    assert rows["V20"].capabilities == rows["V53"].capabilities == frozenset({"graph.publish"})
