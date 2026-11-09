"""Cross-process takeover: the first run notices at its next effect and only one run's effects land (T-leasefix2 b).

Two ``JobService`` instances over one store model two worker processes (own claim lock, own running-set).
"""

from __future__ import annotations

import threading

import pytest

from app.modules.contracts.jobs import JobState
from app.modules.jobs import JobService, JobStore, Pipeline, StageResult, StageSpec
from app.modules.jobs.store import Lease, LeaseLost
from tests.jobs.jobs_fakes import Clock, inputs, local_ctx


def make(store, handler, owner):
    service = JobService(store, owner=owner)
    service.register("ingest", Pipeline([StageSpec("go", handler)]))
    return service


def test_the_lease_check_compares_the_generation_not_only_expiry(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    store.submit("ingest", inputs(), created_by="u")
    lease, _ = store.claim_next("w1")
    store.check_lease(lease)
    with pytest.raises(LeaseLost):  # same token, older fence
        store.check_lease(Lease(lease.resource, lease.owner, lease.token, lease.fence - 1, lease.expires_at))
    with pytest.raises(LeaseLost):  # same fence, another token
        store.check_lease(Lease(lease.resource, lease.owner, "x" * 32, lease.fence, lease.expires_at))
    clock.advance(61)
    lease2, _ = store.claim_next("w2")
    assert lease2.fence == lease.fence + 1
    store.renew_lease(lease2)
    with pytest.raises(LeaseLost):  # the lease is live (not expired) but it is not this generation
        store.check_lease(lease)


def test_a_taken_over_run_loses_every_kind_of_write_and_only_the_new_run_lands(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    first_in, release_first = threading.Event(), threading.Event()
    landed: list[str] = []
    runs: list[str] = []
    errors: list[str] = []

    def handler(sc):
        me = f"run{len(runs) + 1}"
        runs.append(me)
        sc.run_effect("e1", lambda: landed.append(f"{me}:e1") or {"by": me})
        if me == "run1":
            first_in.set()
            assert release_first.wait(10)
            for name, call in (
                ("effect", lambda: sc.run_effect("e2", lambda: landed.append("run1:e2") or {})),
                ("unit", lambda: sc.save_unit("u1", "k", {"by": "run1"})),
                ("fail_unit", lambda: sc.fail_unit("u2", "k", "boom")),
                ("log", lambda: sc.log("stale")),
                ("progress", lambda: sc.progress(done=1)),
            ):
                with pytest.raises(LeaseLost):
                    call()
                errors.append(name)
            raise LeaseLost(sc.lease.resource)
        sc.run_effect("e2", lambda: landed.append("run2:e2") or {})
        sc.save_unit("u1", "k", {"by": "run2"})
        return StageResult()

    one, two = make(store, handler, "p1"), make(store, handler, "p2")
    job_id = one.submit(local_ctx(), "ingest", inputs()).job_id
    result: list[object] = []
    first = threading.Thread(target=lambda: result.append(one.work_one()))
    first.start()
    assert first_in.wait(10)
    clock.advance(61)  # the host slept past the lease; the other process claims the job
    assert two.work_one() == (job_id, JobState.SUCCEEDED)
    release_first.set()
    first.join(10)
    assert result == [(job_id, None)]
    assert errors == ["effect", "unit", "fail_unit", "log", "progress"]
    assert landed == ["run1:e1", "run2:e2"]  # run2 reused run1's finished e1; nothing from run1 after takeover
    assert store.get(job_id).state is JobState.SUCCEEDED  # not overwritten by the stale run
    assert store.get_unit(job_id, "go", "u1", "k") == {"by": "run2"}
    assert store.get_unit(job_id, "go", "u2", "k") is None
