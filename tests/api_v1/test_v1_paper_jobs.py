"""B5/J-11 (T10B): enrichment, topic-sweep and PDF-parse job kinds on the ``/v1/jobs`` lifecycle (RT-27..RT-32).

Each kind has a fake handler test (no model, parser or network), a policy row (V50-V52) and the
``jobs.admin`` capability; status is ``GET /v1/jobs/{id}`` and stop is ``.../cancel`` (V03, V08).
"""
import pytest
from _v1_support import ActorKind, local_operator_context

from app.api.policies import API_ROUTE_POLICIES
from app.modules.contracts.jobs import JobState
from app.modules.jobs.paper_jobs import (
    ENRICHMENT_KIND,
    PAPER_JOB_KINDS,
    PDF_PARSE_KIND,
    TOPIC_SWEEP_KIND,
    build_pipeline,
    register_paper_jobs,
)

ROUTES = {ENRICHMENT_KIND: "/v1/jobs/enrichment", TOPIC_SWEEP_KIND: "/v1/jobs/topic-sweep",
          PDF_PARSE_KIND: "/v1/jobs/pdf-parse"}
BODIES = {ENRICHMENT_KIND: {"corpus": "rsoxs", "limit": 5}, TOPIC_SWEEP_KIND: {"corpus": "rsoxs", "topics": ["a"]},
          PDF_PARSE_KIND: {"corpus": "rsoxs", "paper_ids": ["p1", "p2"], "force": True}}


class FakeHandlers:
    """Records every call; never touches a model, parser or network."""

    def __init__(self):
        self.calls = []
        self.fail_unit = None

    def handler(self, kind):
        def run(ctx, params, control):
            self.calls.append((kind, dict(params), ctx.actor_kind))
            done = 0
            missing = []
            for unit in ("u1", "u2", "u3"):
                control.guard()
                if control.settled(unit, "k1") is not None:
                    done += 1
                    continue
                if unit == self.fail_unit:
                    control.fail(unit, "k1", "boom")
                    missing.append(unit)
                    continue
                control.settle(unit, "k1", {"ok": True})
                done += 1
                control.progress(units=done)
            return {"units": done, "kind": kind, "incomplete": missing}
        return run


def wire(app, fakes, kinds=PAPER_JOB_KINDS):
    settings = app.settings
    registered = register_paper_jobs(
        app.ops.jobs, {k: fakes.handler(k) for k in kinds},
        lambda job: local_operator_context(settings, request_id=job.job_id, actor_kind=ActorKind.WORKER,
                                           run_id=job.job_id))
    assert len(registered) == len(kinds)


@pytest.mark.parametrize("kind", PAPER_JOB_KINDS)
def test_fake_handler_runs_on_the_job_lifecycle(local, kind):
    fakes = FakeHandlers()
    wire(local, fakes)
    c = local.client()
    resp = c.post(ROUTES[kind], json=BODIES[kind])
    assert resp.status_code == 200, resp.text
    job = resp.json()
    assert job["kind"] == kind and job["state"] == "queued"
    assert c.post(ROUTES[kind], json=BODIES[kind]).json()["job_id"] == job["job_id"]   # idempotent
    assert local.work() == (job["job_id"], JobState.SUCCEEDED)
    (call,) = fakes.calls
    assert call[0] == kind and call[1]["corpus"] == "rsoxs" and call[2] is ActorKind.WORKER
    assert c.get(f"/v1/jobs/{job['job_id']}").json()["state"] == "succeeded"
    assert c.get(f"/v1/jobs/{job['job_id']}/events").json()["events"][-1]["state"] == "succeeded"
    stage = local.ops.jobs.store.stage(job["job_id"], "run")
    assert stage.result["units"] == 3 and stage.result["kind"] == kind


def test_stop_is_the_ordinary_cancel_and_resume_skips_settled_units(local):
    fakes = FakeHandlers()
    wire(local, fakes)
    c = local.client()
    job = c.post(ROUTES[ENRICHMENT_KIND], json=BODIES[ENRICHMENT_KIND]).json()
    fakes.fail_unit = "u2"
    assert local.work() == (job["job_id"], JobState.FAILED)       # incomplete coverage: retryable
    fakes.fail_unit = None
    fakes.calls.clear()
    resumed = c.post(f"/v1/jobs/{job['job_id']}/resume", json={}).json()
    assert resumed["state"] == "queued"
    assert local.work() == (job["job_id"], JobState.SUCCEEDED)
    # a second job: a stop requested while it runs ends it at the next boundary (RT-29/RT-32 map to cancel)
    other = c.post(ROUTES[TOPIC_SWEEP_KIND], json=BODIES[TOPIC_SWEEP_KIND]).json()
    original = fakes.handler(TOPIC_SWEEP_KIND)

    def stop_midway(ctx, params, control):
        local.ops.jobs.store.request_stop(other["job_id"], actor="local-operator")
        return original(ctx, params, control)

    local.ops.jobs.register(
        TOPIC_SWEEP_KIND,
        build_pipeline(TOPIC_SWEEP_KIND, stop_midway,
                       lambda job: local_operator_context(local.settings, request_id=job.job_id,
                                                          actor_kind=ActorKind.WORKER, run_id=job.job_id)),
        capability="jobs.admin")
    assert local.work() == (other["job_id"], JobState.CANCELLED)


def test_bad_parameters_and_unregistered_kinds_are_refused_without_a_job(local):
    c = local.client()
    assert c.post(ROUTES[PDF_PARSE_KIND], json={"corpus": "rsoxs"}).json()["code"] == "unsupported_capability"
    wire(local, FakeHandlers(), kinds=(ENRICHMENT_KIND,))
    assert c.post(ROUTES[TOPIC_SWEEP_KIND], json={"corpus": "rsoxs"}).status_code != 200
    assert c.post(ROUTES[ENRICHMENT_KIND], json={"corpus": "bad corpus!"}).status_code == 422
    assert c.post(ROUTES[ENRICHMENT_KIND], json={"corpus": "rsoxs", "bogus": 1}).status_code == 422
    assert c.post(ROUTES[ENRICHMENT_KIND], json={"corpus": "rsoxs", "topics": ["x"]}).status_code == 422
    assert local.ops.jobs.store.list_jobs() == []


def test_policy_rows_declare_jobs_admin_with_unique_ids():
    rows = {p.path: p for p in API_ROUTE_POLICIES if p.route_id in ("V50", "V51", "V52")}
    assert set(rows) == set(ROUTES.values())
    for row in rows.values():
        assert row.method == "POST" and row.capabilities == frozenset({"jobs.admin"})
    ids = [p.route_id for p in API_ROUTE_POLICIES]
    assert len(ids) == len(set(ids))


def test_als_ordinary_user_is_refused_and_admin_may_submit(als):
    fakes = FakeHandlers()
    wire(als, fakes)
    for kind, route in ROUTES.items():
        resp = als.client("user").post(route, json=BODIES[kind])
        assert resp.status_code == 403 and resp.json()["code"] == "denied"
    assert als.ops.jobs.store.list_jobs() == [] and fakes.calls == []
    resp = als.client("admin").post(ROUTES[ENRICHMENT_KIND], json=BODIES[ENRICHMENT_KIND])
    assert resp.status_code == 200, resp.text
