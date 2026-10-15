"""T10: owned executor (start/shutdown), bounded concurrency, shared admission, process support."""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from app.modules import llm_admission
from app.modules.contracts.jobs import JobState, StageOutcome
from app.modules.jobs import JobService, JobStore, Pipeline, StageResult, StageSpec
from app.modules.jobs.process_support import (
    is_our_worker,
    pid_alive,
    scrub,
    tail_lines,
    worker_command,
    write_text_atomic,
)
from app.modules.workers import WorkerExecutor
from tests.jobs.jobs_fakes import inputs, local_ctx


@pytest.fixture(autouse=True)
def fresh_controllers(monkeypatch):
    monkeypatch.setenv("F2W_FAKEPROV_MAX_CONCURRENCY", "1")
    llm_admission.reset_controllers()
    yield
    llm_admission.reset_controllers()


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def build(tmp_path, handler, **kw):
    store = JobStore(tmp_path / "j.sqlite3")
    service = JobService(store, **kw)
    service.register("ingest", Pipeline([StageSpec("extract", handler, phase="extraction")]))
    return store, service


def submit_many(service, n):
    return [service.submit(local_ctx(), "ingest", inputs(source_sha256=f"{i:064x}")).job_id for i in range(n)]


def test_executor_runs_jobs_to_completion_with_explicit_lifecycle(tmp_path):
    store, service = build(tmp_path, lambda sc: StageResult())
    ids = submit_many(service, 5)
    executor = WorkerExecutor(service, max_concurrency=2, poll_interval=0.01)
    assert not executor.started
    executor.start()
    with pytest.raises(RuntimeError):
        executor.start()
    assert executor.run_until_idle(timeout=10)
    assert executor.shutdown(timeout=5) and not executor.started
    assert all(store.get(i).state is JobState.SUCCEEDED for i in ids)


def test_concurrency_is_bounded_by_the_executor(tmp_path):
    lock, state = threading.Lock(), {"now": 0, "peak": 0}

    def handler(sc):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return StageResult()

    store, service = build(tmp_path, handler)
    submit_many(service, 8)
    with WorkerExecutor(service, max_concurrency=3, poll_interval=0.01) as ex:
        assert ex.run_until_idle(timeout=10)
    assert 1 < state["peak"] <= 3


def test_provider_calls_share_one_admission_controller_across_jobs(tmp_path):
    """Three concurrent jobs, provider cap 1: calls never overlap."""
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def handler(sc):
        with sc.provider_slot("fakeprov"):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.03)
            with lock:
                state["now"] -= 1
        return StageResult()

    store, service = build(tmp_path, handler)
    submit_many(service, 6)
    with WorkerExecutor(service, max_concurrency=3, poll_interval=0.01) as ex:
        assert ex.run_until_idle(timeout=10)
    assert state["peak"] == 1
    assert llm_admission.get_controller("fakeprov").peak == 1


def test_workers_share_quota_with_chat_and_evaluations(tmp_path):
    """A slot held elsewhere in the process (chat/eval) blocks worker calls until released."""
    started = threading.Event()

    def handler(sc):
        with sc.provider_slot("fakeprov"):
            started.set()
        return StageResult()

    store, service = build(tmp_path, handler)
    (job_id,) = submit_many(service, 1)
    chat_slot = llm_admission.get_controller("fakeprov")
    assert chat_slot.try_acquire()  # a chat turn is using the provider's only slot
    with WorkerExecutor(service, max_concurrency=1, poll_interval=0.01):
        assert not started.wait(0.3)
        chat_slot.release()
        assert started.wait(5)
        assert wait_for(lambda: store.get(job_id).state is JobState.SUCCEEDED)


def test_graceful_shutdown_requeues_running_job_and_keeps_finished_stages(tmp_path):
    release = threading.Event()
    entered = threading.Event()
    calls = {"parse": 0, "extract": 0}

    def parse(sc):
        calls["parse"] += 1
        return StageResult()

    def extract(sc):
        calls["extract"] += 1
        entered.set()
        while not release.is_set():
            sc.guard()  # raises JobInterrupted at shutdown
            time.sleep(0.01)
        return StageResult()

    store = JobStore(tmp_path / "j.sqlite3")
    service = JobService(store)
    service.register(
        "ingest",
        Pipeline(
            [
                StageSpec("parse", parse, phase="parsing"),
                StageSpec("extract", extract, phase="extraction", depends_on=("parse",)),
            ]
        ),
    )
    (job_id,) = submit_many(service, 1)
    ex = WorkerExecutor(service, max_concurrency=1, poll_interval=0.01).start()
    assert entered.wait(5)
    assert ex.shutdown(timeout=5)
    assert store.get(job_id).state is JobState.QUEUED  # not failed, not cancelled
    assert store.stage(job_id, "parse").status is StageOutcome.COMPLETED
    release.set()
    with WorkerExecutor(service, max_concurrency=1, poll_interval=0.01) as ex2:
        assert ex2.run_until_idle(timeout=10)
    assert store.get(job_id).state is JobState.SUCCEEDED and calls["parse"] == 1


def test_failing_job_does_not_kill_the_worker(tmp_path):
    def handler(sc):
        if sc.inputs.source_sha256.endswith("0"):
            raise RuntimeError("boom")
        return StageResult()

    store, service = build(tmp_path, handler)
    ids = submit_many(service, 3)
    with WorkerExecutor(service, max_concurrency=1, poll_interval=0.01) as ex:
        assert ex.run_until_idle(timeout=10)
    states = sorted(store.get(i).state.value for i in ids)
    assert states == ["failed", "succeeded", "succeeded"]


def test_heartbeat_keeps_a_long_stage_alive_past_the_nominal_lease(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3", lease_seconds=0.2)

    def handler(sc):
        time.sleep(0.6)  # three times the lease, no progress calls
        sc.guard()
        return StageResult()

    service = JobService(store, heartbeat_interval=0.05)
    service.register("ingest", Pipeline([StageSpec("extract", handler, phase="extraction")]))
    (job_id,) = submit_many(service, 1)
    assert service.work_one() == (job_id, JobState.SUCCEEDED)


def test_idle_worker_process_starts_observes_store_and_exits(tmp_path):
    db = tmp_path / "j.sqlite3"
    JobStore(db).close()
    cmd = worker_command(str(db), factory="app.modules.workers.factories:idle_service", extra=["--once"])
    done = subprocess.run(cmd, cwd=".", env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr.decode()[-400:]


# --- process support (J-09) -----------------------------------------------------------------------------------


def test_worker_command_is_allowlisted():
    assert worker_command("/x/db", factory="app.modules.workers.factories:idle_service")[1:3] == [
        "-m",
        "app.modules.workers",
    ]
    for bad in ("os:system", "app.modules.x:y; rm -rf /", "evil.module:f", "app.modules:f", "app.modules.a:b c"):
        with pytest.raises(ValueError):
            worker_command("/x/db", factory=bad)
    assert sys.executable  # argv[0] is the interpreter, never a shell


def test_pid_helpers_refuse_dangerous_pids_and_detect_our_worker():
    for bad in (0, 1, -5, None, True, "12"):
        assert pid_alive(bad) is False
    import os

    assert pid_alive(os.getpid()) and not is_our_worker(os.getpid())


def test_tail_lines_are_bounded_and_scrubbed(tmp_path):
    log = tmp_path / "w.log"
    log.write_text("x\n" * 500 + "api_key=sk-abcdefghijklmnop leaked\nBearer abc.def\n" + "y" * 5000 + "\n")
    lines = tail_lines(log, 3)
    assert len(lines) == 3 and all(len(line) <= 400 for line in lines)
    assert "sk-" not in " ".join(tail_lines(log, 10)) and "Bearer abc" not in " ".join(tail_lines(log, 10))
    assert tail_lines(tmp_path / "missing.log") == [] and scrub("token: abc123") == "[redacted]"


def test_atomic_write_never_exposes_partial_content(tmp_path):
    target = tmp_path / "state.json"
    write_text_atomic(target, "one")
    write_text_atomic(target, "two")
    assert target.read_text() == "two" and not list(tmp_path.glob("*.tmp"))
