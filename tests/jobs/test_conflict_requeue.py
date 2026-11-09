"""A retryable CONFLICT from any stage requeues the job with backoff and a bounded retry count (T-leasefix2 a)."""

from __future__ import annotations

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.contracts.jobs import JobState
from app.modules.jobs import JobService, JobStore, Pipeline, StageResult, StageSpec
from app.modules.jobs.pipeline import MAX_CONFLICT_REQUEUES
from tests.jobs.jobs_fakes import Clock, inputs, local_ctx


def service_over(store, handler, **kw):
    service = JobService(store, **kw)
    service.register("ingest", Pipeline([StageSpec("go", handler)]))
    return service


def test_a_retryable_conflict_requeues_instead_of_failing(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3")
    busy = [True]

    def handler(sc):
        if busy[0]:
            raise ContractError(ErrorCode.CONFLICT, "another process holds the resource", retryable=True)
        return StageResult()

    service = service_over(store, handler)
    job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
    assert service.work_one() == (job_id, JobState.QUEUED)  # not FAILED
    assert service.work_one() is None  # backing off
    busy[0] = False
    service._backoff_until.clear()
    assert service.work_one() == (job_id, JobState.SUCCEEDED)


def test_the_conflict_retry_count_is_bounded_and_the_backoff_grows(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3")

    def handler(sc):
        raise ContractError(ErrorCode.CONFLICT, "still busy", retryable=True)

    service = service_over(store, handler)
    service.requeue_backoff = 1.0
    job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
    waits: list[float] = []
    states = []
    for _ in range(MAX_CONFLICT_REQUEUES + 1):
        service._backoff_until.clear()
        _, state = service.work_one()
        states.append(state)
        waits.append(service._backoff_until.get(job_id, 0.0))
    assert states[:MAX_CONFLICT_REQUEUES] == [JobState.QUEUED] * MAX_CONFLICT_REQUEUES
    assert states[-1] is JobState.FAILED  # bound reached: a real, persistent conflict fails the job
    assert store.get(job_id).state is JobState.FAILED
    import time

    delays = [w - time.monotonic() for w in waits[:MAX_CONFLICT_REQUEUES]]
    assert delays[2] > delays[0] * 1.5  # exponential, not a fixed tight loop


def test_a_non_retryable_conflict_still_fails_at_once(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3")

    def handler(sc):
        raise ContractError(ErrorCode.CONFLICT, "schema is locked", retryable=False)

    service = service_over(store, handler)
    job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
    assert service.work_one() == (job_id, JobState.FAILED)


def test_a_conflict_after_the_lease_was_taken_over_does_not_requeue(tmp_path):
    clock = Clock()
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=60, clock=clock)

    def handler(sc):
        clock.advance(61)
        assert store.claim_next("w2") is not None  # another process took the job over
        raise ContractError(ErrorCode.CONFLICT, "busy", retryable=True)

    service = service_over(store, handler)
    job_id = service.submit(local_ctx(), "ingest", inputs()).job_id
    assert service.work_one() == (job_id, None)  # stale: no state change by this run
    assert store.get(job_id).state is JobState.RUNNING  # the new owner's, not requeued or failed
