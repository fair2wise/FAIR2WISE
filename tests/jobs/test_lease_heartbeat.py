"""Lease heartbeat and double-claim regressions (live e2e finding: a healthy library_extract job failed ``conflict``).

A handler that blocks for longer than the lease must keep its job: the heartbeat renews independently of the
handler, a lapsed-but-untaken lease is revived, a second claim of a job this service runs is refused, and a
stale owner's writes never land. Everything is fake (no network, no sleep beyond fractions of a second).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time

import pytest

from app.modules.contracts.jobs import JobState
from app.modules.jobs import JobService, JobStore, Pipeline, StageResult, StageSpec
from app.modules.jobs.pipeline import StageBusy
from app.modules.jobs.store import LeaseLost
from tests.jobs.jobs_fakes import Clock, inputs, local_ctx


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def service_over(store, handler, **kw):
    service = JobService(store, **kw)
    service.register("ingest", Pipeline([StageSpec("extract", handler, phase="extraction")]))
    return service


def claims(store, job_id):
    return [e for e in store.events(job_id) if e.stage == "claim"]


def test_heartbeat_keeps_the_lease_while_the_handler_blocks_longer_than_the_lease(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=0.4)
    entered, release = threading.Event(), threading.Event()

    def handler(sc):
        entered.set()
        release.wait(5)  # a hung LLM call: no progress call, no log line, for several lease lengths
        return StageResult()

    service = service_over(store, handler, heartbeat_interval=0.05)
    other = service_over(store, handler, owner="other")  # a second worker (own service) polling for work
    job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
    runner = threading.Thread(target=service.work_one)
    runner.start()
    assert entered.wait(5)
    try:
        deadline = time.monotonic() + 1.3
        while time.monotonic() < deadline:
            assert other.work_one() is None  # the lease is live: nothing to claim
            time.sleep(0.05)
        assert store.lease_holder(store.job_resource(job_id)) is not None
    finally:
        release.set()
        runner.join(5)
    assert store.get(job_id).state is JobState.SUCCEEDED
    assert len(claims(store, job_id)) == 1


def test_a_lapsed_lease_of_a_live_run_is_revived_not_stolen(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    entered, release = threading.Event(), threading.Event()

    def handler(sc):
        entered.set()
        release.wait(5)
        return StageResult()

    service = service_over(store, handler, heartbeat_interval=0.02)
    job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
    runner = threading.Thread(target=service.work_one)
    runner.start()
    assert entered.wait(5)
    try:
        clock.advance(3600)  # the machine slept: the wall clock jumped past the lease
        # The same service never re-claims a job it runs, even while the lease looks expired.
        assert service.work_one() is None
        assert wait_for(lambda: store.lease_holder(store.job_resource(job_id)) is not None)  # heartbeat revived it
    finally:
        release.set()
        runner.join(5)
    assert store.get(job_id).state is JobState.SUCCEEDED
    assert len(claims(store, job_id)) == 1


def test_renew_with_allow_expired_revives_an_untaken_lease_but_never_a_taken_one(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=30, clock=clock)
    store.submit("ingest", inputs(), created_by="u1")
    lease1, _ = store.claim_next("w1")
    clock.advance(31)
    with pytest.raises(LeaseLost):
        store.renew_lease(lease1)  # strict default: a lapsed lease is not revived
    store.renew_lease(lease1, allow_expired=True)  # the owner's heartbeat may: nobody took it over
    store.check_lease(lease1)
    clock.advance(31)
    lease2, _ = store.claim_next("w2")
    assert lease2.fence == lease1.fence + 1
    with pytest.raises(LeaseLost):
        store.renew_lease(lease1, allow_expired=True)  # taken over: refused even with allow_expired


def test_two_workers_racing_for_one_job_yield_exactly_one_claim(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3")
    store.submit("ingest", inputs(), created_by="u1")
    results: list[object] = []
    barrier = threading.Barrier(2)

    def worker(name):
        other = JobStore(tmp_path / "j.sqlite3")  # its own connection, like a second process
        barrier.wait()
        results.append(other.claim_next(name))

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sum(r is not None for r in results) == 1


def test_heartbeat_failures_are_logged_at_warning_and_retried(tmp_path, caplog):
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60)
    real = store.renew_lease
    attempts: list[int] = []
    renewed = threading.Event()

    def flaky(lease, ttl=None, **kw):
        attempts.append(1)
        if len(attempts) <= 2:
            raise sqlite3.OperationalError("database is locked")
        result = real(lease, ttl, **kw)
        renewed.set()
        return result

    store.renew_lease = flaky  # type: ignore[method-assign]

    def handler(sc):
        assert renewed.wait(5)  # returns only after the heartbeat survived two failures and renewed
        return StageResult()

    service = service_over(store, handler, heartbeat_interval=0.02)
    service.submit(local_ctx(), "ingest", inputs())
    with caplog.at_level(logging.WARNING, logger="app.modules.jobs.service"):
        _, state = service.work_one()
    assert state is JobState.SUCCEEDED
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "renewal" in r.getMessage()]
    assert len(warnings) >= 2


def test_heartbeat_stops_with_a_warning_when_the_lease_was_taken_over(tmp_path, caplog):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)
    taken = threading.Event()

    def handler(sc):
        clock.advance(61)
        assert store.claim_next("w2") is not None  # another owner takes the job over
        assert wait_for(lambda: any("taken over" in r.getMessage() for r in caplog.records))
        taken.set()
        return StageResult()

    service = service_over(store, handler, heartbeat_interval=0.02)
    service.submit(local_ctx(), "ingest", inputs())
    with caplog.at_level(logging.WARNING, logger="app.modules.jobs.service"):
        job_id, state = service.work_one()
    assert taken.is_set() and state is None  # the stale owner's completion write was refused
    assert store.get(job_id).state is JobState.RUNNING  # nothing was applied after the takeover


def test_a_busy_stage_requeues_the_job_and_backs_off(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3")
    busy = [True]

    def handler(sc):
        if busy[0]:
            raise StageBusy("earlier run still winding down")
        return StageResult()

    service = service_over(store, handler)
    job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
    assert service.work_one() == (job_id, JobState.QUEUED)  # not failed
    assert service.work_one() is None  # backing off: not re-claimed in a tight loop
    busy[0] = False
    service._backoff_until.clear()
    assert service.work_one() == (job_id, JobState.SUCCEEDED)
