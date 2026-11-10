"""``POST /v1/publications/corpus-retire`` end to end over HTTP on ``:local`` (owner decision 2026-11-09, eighteenth
set: a corpus removal moves the pointer to a still-live corpus, and clears it only when the last live corpus goes).

The ``tests/api_v1`` suite cannot run this: its fake importer has no release marker and cannot run retire jobs. These
tests use the real wiring of ``test_local_save.py`` (``create_api_app(local_publication=True)``: the real
publication job, release state and replacement importer) over the in-memory Tiled double; one variant runs the same
path against a real disposable Tiled on 127.0.0.1 and skips without ``F2W_TILED_PYTHON``. The job level is covered in
``tests/jobs/test_replacement_publication.py``. Plan and run are driven through the routes (V54, V17, V18, V19); only
the worker pass is called directly, as the sibling tests do.
"""
import json

import pytest

from test_local_save import (  # noqa: F401  (fixtures and helpers of the sibling wiring tests)
    OTHER, SCOPE, World, freeze_as, publish_as, template, tiled_base, tiled_python, tiled_seeded_pool, w)

from app.modules.contracts.jobs import JobState


def two_corpora(w):
    """``contract/v1`` and ``othercorpus/v1`` live on one target; the pointer is the last activated one."""
    from test_local_save import renamed

    w.freeze("v1", w.payload)
    w.publish("v1")
    freeze_as(w, OTHER, "v1", renamed(w.payload, OTHER))
    publish_as(w, OTHER)
    return pointer(w)


def pointer(w):
    return w.client.get("/v1/publications/pointer", params={"target": w.target}).json()


def retire_plan(w, scope, confirm=None):
    return w.client.post("/v1/publications/corpus-retire", json={
        "scope": scope, "target": w.target, "confirm_scope": scope if confirm is None else confirm})


def retire(w, scope):
    """Plan, approve through V18 and run a retirement; returns the job id."""
    resp = retire_plan(w, scope)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert w.ops.jobs.work_one("w1") == (job_id, JobState.AWAITING_REVIEW)
    plan = w.client.get(f"/v1/publications/{job_id}/plan").json()
    approved = w.client.post(f"/v1/publications/{job_id}/approval", json={"plan_sha256": plan["plan_sha256"]})
    assert approved.status_code == 200, approved.text
    assert w.ops.jobs.work_one("w1") == (job_id, JobState.SUCCEEDED)
    return job_id


def test_retiring_one_of_two_corpora_moves_the_pointer_to_the_survivor(w):
    before = two_corpora(w)
    assert before["pointer"]["snapshot_ref"] == f"{OTHER}/v1" and set(before["corpora"]) == {SCOPE, OTHER}
    retire(w, OTHER)
    after = pointer(w)
    assert after["pointer"] is not None and after["pointer"]["snapshot_ref"] == f"{SCOPE}/v1"
    assert list(after["corpora"]) == [SCOPE] and after["corpora"][SCOPE]["snapshot_ref"] == f"{SCOPE}/v1"
    assert after["pointer"]["version"] > before["pointer"]["version"]


def test_retiring_the_last_live_corpus_clears_the_pointer(w):
    two_corpora(w)
    retire(w, OTHER)
    retire(w, SCOPE)
    assert pointer(w) == {"pointer": None, "corpora": {}}


def test_a_wrong_confirm_scope_fails_the_job_with_a_typed_error_and_nothing_moves(w):
    """The corpus name must be typed in: the job is accepted (200) and fails at planning with a typed pause reason."""
    before = two_corpora(w)
    resp = retire_plan(w, OTHER, confirm=SCOPE)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert w.ops.jobs.work_one("w1") == (job_id, JobState.FAILED)
    view = w.client.get(f"/v1/jobs/{job_id}/view").json()
    assert view["job"]["state"] == view["ui_state"] == JobState.FAILED.value
    assert "typed in" in view["pause_reason"] and "Traceback" not in json.dumps(view)
    approval = w.client.post(f"/v1/publications/{job_id}/approval", json={"plan_sha256": "0" * 64})
    assert approval.status_code in (400, 404, 409, 422) and approval.json()["code"] != "internal_error"
    assert pointer(w) == before


def test_without_an_approval_the_retire_job_stays_awaiting_review_and_the_pointer_holds(w):
    before = two_corpora(w)
    resp = retire_plan(w, OTHER)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    assert w.ops.jobs.work_one("w1") == (job_id, JobState.AWAITING_REVIEW)
    assert w.ops.jobs.work_one("w1") in (None, (job_id, JobState.AWAITING_REVIEW))   # nothing to run yet
    state = w.client.get(f"/v1/jobs/{job_id}").json()
    assert state["state"] == JobState.AWAITING_REVIEW.value
    assert pointer(w) == before
    # an approval of another plan's hash is refused, and the job still waits
    bad = w.client.post(f"/v1/publications/{job_id}/approval", json={"plan_sha256": "0" * 64})
    assert bad.status_code in (400, 409, 422) and bad.json()["code"] != "internal_error"
    assert w.client.get(f"/v1/jobs/{job_id}").json()["state"] == JobState.AWAITING_REVIEW.value
    assert pointer(w) == before


def test_corpus_retire_against_a_real_loopback_tiled(monkeypatch, tmp_path, template, request):
    if tiled_python() is None:
        pytest.skip("F2W_TILED_PYTHON is not set (needs tiled 0.2.18)")
    inst = request.getfixturevalue("tiled_seeded_pool").acquire()
    world = World(monkeypatch, tmp_path, template, instance=inst)
    two_corpora(world)
    retire(world, OTHER)
    body = pointer(world)
    assert body["pointer"]["snapshot_ref"] == f"{SCOPE}/v1" and list(body["corpora"]) == [SCOPE]
    retire(world, SCOPE)
    assert pointer(world) == {"pointer": None, "corpora": {}}
