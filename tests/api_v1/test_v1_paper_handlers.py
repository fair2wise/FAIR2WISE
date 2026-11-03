"""The real paper handlers on the ``/v1/jobs`` lifecycle: admin gate, proposals in the review queue, stop, resume."""
from _v1_support import ActorKind, local_operator_context

from app.modules.contracts.jobs import JobState
from app.modules.jobs.paper_handlers import CurationProposalSink, build_paper_handlers
from app.modules.jobs.paper_jobs import register_paper_jobs
from tests.jobs.paper_handler_fakes import FakeWorks, work

ROUTE = "/v1/jobs/topic-sweep"
BODY = {"corpus": "rsoxs", "topics": ["gyroid", "lamellae", "cylinders"], "limit": 5}


def wire(app, works):
    sink = CurationProposalSink(app.ops.curation)
    handlers = build_paper_handlers(sink=sink, works=works)

    def ctx(job):
        return local_operator_context(app.settings, request_id=job.job_id, actor_kind=ActorKind.WORKER,
                                      run_id=job.job_id)

    (registered,) = register_paper_jobs(app.ops.jobs, handlers, ctx)
    return registered


def make_works():
    return FakeWorks({t: [work(f"W{i}", f"{t} paper", doi=f"10.1/{i}")] for i, t in enumerate(BODY["topics"])})


def test_real_handler_runs_and_leaves_only_review_proposals(local):
    works = make_works()
    wire(local, works)
    job = local.client().post(ROUTE, json=BODY).json()
    assert local.work() == (job["job_id"], JobState.SUCCEEDED)
    stage = local.ops.jobs.store.stage(job["job_id"], "run")
    assert stage.result["papers_added"] == 3 and stage.result["proposals_new"] == 3
    cases = local.ops.curation.list_cases(["rsoxs"], state="needs_review")
    assert len(cases) == 3 and {x["case_type"] for x in cases} == {"graph_proposal"}


def test_stop_ends_at_a_unit_boundary(local):
    works = make_works()
    wire(local, works)
    c = local.client()
    job = c.post(ROUTE, json=BODY).json()
    original = works.works
    seen = []

    def stop_during_first(ctx, **kw):
        seen.append(kw["search"])
        if len(seen) == 1:
            local.ops.jobs.store.request_stop(job["job_id"], actor="local-operator")   # what POST .../cancel does
        return original(ctx, **kw)

    works.works = stop_during_first
    assert local.work() == (job["job_id"], JobState.CANCELLED)
    assert seen == ["gyroid"]                                  # nothing after the stop ran
    assert local.work() is None


def test_failed_unit_makes_the_job_retryable_and_resume_skips_settled_units(local):
    works = make_works()
    wire(local, works)
    c = local.client()
    works.paused_for = {"lamellae"}                            # quota breaker open mid-sweep
    job = c.post(ROUTE, json=BODY).json()
    assert local.work() == (job["job_id"], JobState.FAILED)    # incomplete coverage, not success
    works.paused_for = set()
    works.calls.clear()
    c.post(f"/v1/jobs/{job['job_id']}/resume", json={})
    assert local.work() == (job["job_id"], JobState.SUCCEEDED)
    searched = [call[0] for call in works.calls if call[2] == 1]
    assert searched == ["lamellae", "cylinders"]                 # "gyroid" was settled
    assert len(local.ops.curation.list_cases(["rsoxs"], state="needs_review")) == 3


def test_als_ordinary_user_cannot_submit_and_admin_can(als):
    works = make_works()
    wire(als, works)
    resp = als.client("user").post(ROUTE, json=BODY)
    assert resp.status_code == 403 and resp.json()["code"] == "denied"
    assert als.ops.jobs.store.list_jobs() == [] and works.calls == []
    assert als.client("admin").post(ROUTE, json=BODY).status_code == 200
