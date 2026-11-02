"""T33: the per-job log channel (store, stage context, service visibility, handler)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path

import pytest

from app.modules.contracts.errors import ContractError, ErrorCode
from app.modules.jobs.log_capture import CaptureBusy, capture_job_logs
from app.modules.jobs.pipeline import StageContext, StageResult, StageSpec
from app.modules.jobs.service import JobService
from app.modules.jobs.store import LOG_MESSAGE_CHARS, LOG_ROWS_PER_JOB, JobStore, LeaseLost
from tests.jobs.jobs_fakes import Clock, inputs, local_ctx, local_settings


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock):
    s = JobStore(tmp_path / "jobs.sqlite3", lease_seconds=60, clock=clock)
    yield s
    s.close()


def _claimed(store: JobStore):
    job = store.submit("ingest", inputs(), created_by="u1")
    lease, record = store.claim_next("w1")
    return lease, record


def _sc(store: JobStore) -> StageContext:
    lease, record = _claimed(store)
    spec = StageSpec("parse", lambda sc: StageResult(), phase="parsing")
    return StageContext(store, lease, record, spec, "op")


def test_logs_are_ordered_and_survive_a_reopen(tmp_path: Path, clock: Clock) -> None:
    path = tmp_path / "jobs.sqlite3"
    first = JobStore(path, clock=clock)
    lease, record = _claimed(first)
    for i in range(5):
        first.append_log(lease, record.job_id, "parse", f"line {i}", "info")
    first.close()
    second = JobStore(path, clock=clock)  # a worker restart
    rows = second.logs(record.job_id)
    assert [r.seq for r in rows] == [0, 1, 2, 3, 4]
    assert [r.message for r in rows] == [f"line {i}" for i in range(5)]
    assert rows[0].stage == "parse" and rows[0].level == "info" and rows[0].at == clock()
    second.close()


def test_after_and_limit(store: JobStore) -> None:
    lease, record = _claimed(store)
    for i in range(10):
        store.append_log(lease, record.job_id, "s", f"m{i}")
    assert [r.seq for r in store.logs(record.job_id, after=6)] == [7, 8, 9]
    assert [r.seq for r in store.logs(record.job_id, after=2, limit=3)] == [3, 4, 5]
    assert store.logs(record.job_id, after=9) == []
    assert store.logs("job-other") == []


def test_messages_are_scrubbed_then_truncated(store: JobStore) -> None:
    lease, record = _claimed(store)
    secret = store.append_log(lease, record.job_id, "s", "calling with api_key=sk-abcdefabcdefabcd and Bearer abc123")
    assert "sk-abc" not in secret.message and "abc123" not in secret.message and "[redacted]" in secret.message
    long = store.append_log(lease, record.job_id, "s", "x" * 5000)
    assert len(long.message) == LOG_MESSAGE_CHARS
    # a secret straddling the cut is redacted before the cut, not left half visible
    edge = store.append_log(lease, record.job_id, "s", "y" * (LOG_MESSAGE_CHARS - 5) + " token=supersecretvalue")
    assert "supersecret" not in edge.message
    with pytest.raises(ValueError):
        store.append_log(lease, record.job_id, "s", "m", "loud")


def test_per_job_cap_drops_the_oldest(store: JobStore) -> None:
    lease, record = _claimed(store)
    for i in range(LOG_ROWS_PER_JOB + 25):
        store.append_log(lease, record.job_id, "s", f"m{i}")
    rows = store.logs(record.job_id, limit=5000)
    assert len(rows) == LOG_ROWS_PER_JOB
    assert rows[0].seq == 25 and rows[-1].seq == LOG_ROWS_PER_JOB + 24 and rows[-1].message.endswith(str(rows[-1].seq))


def test_logs_of_two_jobs_do_not_mix_and_stale_owner_is_fenced(store: JobStore, clock: Clock) -> None:
    lease1, rec1 = _claimed(store)
    other = store.submit("ingest", inputs(source_sha256="b" * 64), created_by="u1")
    lease2, rec2 = store.claim_next("w2")
    store.append_log(lease1, rec1.job_id, "s", "one")
    store.append_log(lease2, rec2.job_id, "s", "two")
    assert [r.message for r in store.logs(rec1.job_id)] == ["one"]
    assert [r.message for r in store.logs(rec2.job_id)] == ["two"]
    clock.advance(61)
    with pytest.raises(LeaseLost):
        store.append_log(lease1, rec1.job_id, "s", "late")
    assert [r.message for r in store.logs(rec1.job_id)] == ["one"]


def test_stage_context_log_tags_the_stage_and_guards(store: JobStore) -> None:
    sc = _sc(store)
    sc.log("hello")
    sc.log("careful", "warning")
    rows = store.logs(sc.job.job_id)
    assert [(r.stage, r.level, r.message) for r in rows] == [("parse", "info", "hello"), ("parse", "warning", "careful")]
    store.request_stop(sc.job.job_id, actor="u1")
    with pytest.raises(Exception, match="stop requested"):
        sc.log("after stop")
    assert len(store.logs(sc.job.job_id)) == 2


def test_service_logs_follow_the_events_visibility_rule(tmp_path: Path, clock: Clock) -> None:
    service = JobService(JobStore(tmp_path / "j.sqlite3", clock=clock), settings=local_settings(), clock=clock)
    owner = local_ctx("jobs.admin")
    job = service.store.submit("ingest", inputs(), created_by="local-operator")
    lease, _ = service.store.claim_next("w1")
    service.store.append_log(lease, job.job_id, "s", "visible")
    assert [r.message for r in service.logs(owner, job.job_id)] == ["visible"]
    with pytest.raises(ContractError) as err:
        service.logs(owner, "job-missing")
    assert err.value.code is ErrorCode.NOT_FOUND
    service.store.close()


# ------------------------------------------------------------------ handler


def test_handler_captures_from_a_thread_pool_and_f2w_output(store: JobStore) -> None:
    sc = _sc(store)
    work = logging.getLogger("t33.worker")
    with capture_job_logs(sc, ["t33.worker", "f2w.output", "t33.worker"]) as handler:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda page: work.info("page %d done", page), range(8)))
        logging.getLogger("f2w.output").info("user line token=hunter2hunter2")
        work.debug("below the threshold")
    messages = [r.message for r in store.logs(sc.job.job_id, limit=100)]
    assert sorted(m for m in messages if m.startswith("page")) == sorted(f"page {i} done" for i in range(8))
    assert any(m.startswith("user line") and "hunter2" not in m for m in messages)
    assert not any("below" in m for m in messages) and handler.dropped == 0
    assert handler not in work.handlers and handler not in logging.getLogger("f2w.output").handlers


def test_handler_maps_levels_and_restores_logger_level(store: JobStore) -> None:
    sc = _sc(store)
    logger = logging.getLogger("t33.levels")
    logger.setLevel(logging.ERROR)
    with capture_job_logs(sc, ["t33.levels"], set_logger_level=False):
        logger.warning("dropped by the logger level")
        logger.error("kept")
    assert [(r.level, r.message) for r in store.logs(sc.job.job_id)] == [("error", "kept")]
    with capture_job_logs(sc, ["t33.levels"]):
        logger.warning("now kept")
    assert logger.level == logging.ERROR
    assert store.logs(sc.job.job_id)[-1].level == "warning"


def test_second_concurrent_capture_is_refused_and_first_survives(store: JobStore) -> None:
    sc = _sc(store)
    other = StageContext(store, sc.lease, sc.job, sc.spec, "op2")
    logger = logging.getLogger("t33.busy")
    with capture_job_logs(sc, ["t33.busy"]):
        with pytest.raises(CaptureBusy):
            with capture_job_logs(other, ["t33.busy"]):
                pytest.fail("must not enter")
        logger.info("still captured")
    assert [r.message for r in store.logs(sc.job.job_id)] == ["still captured"]
    with capture_job_logs(other, ["t33.busy"]):  # released afterwards
        pass


def test_handler_detaches_on_exception_and_never_raises_into_the_caller(store: JobStore) -> None:
    sc = _sc(store)
    logger = logging.getLogger("t33.boom")
    with pytest.raises(RuntimeError, match="boom"):
        with capture_job_logs(sc, ["t33.boom"]) as handler:
            store.request_stop(sc.job.job_id, actor="u1")
            logger.info("stopped job: this line is dropped, not raised")
            assert handler.dropped == 1
            raise RuntimeError("boom")
    assert logger.handlers == []
    with capture_job_logs(sc, ["t33.boom"]):  # the guard was released too
        pass
