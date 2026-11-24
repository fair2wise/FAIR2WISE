"""Retention of ``<jobs-db-dir>/job-logs/*.log``: age, size, running-job and name/symlink safety, worker wiring."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from app.modules.jobs import log_retention as lr
from app.modules.jobs.log_retention import JobLogRetention, retention_limits, sweep_job_logs
from app.modules.jobs.store import LOG_DIRNAME, JobStore
from app.modules.workers import __main__ as worker_main
from tests.jobs.jobs_fakes import inputs

NOW = 2_000_000_000.0
DAY = 86400
MIB = 1024 * 1024


@pytest.fixture
def store(tmp_path: Path):
    s = JobStore(tmp_path / "jobs.sqlite3")
    yield s
    s.close()


def put(store: JobStore, name: str, size: int = 10, age_days: float = 0.0, age_seconds: float = 0.0) -> Path:
    assert store.log_dir is not None
    store.log_dir.mkdir(exist_ok=True)
    path = store.log_dir / name
    path.write_bytes(b"x" * size)
    mtime = NOW - age_days * DAY - age_seconds
    os.utime(path, (mtime, mtime))
    return path


def names(store: JobStore) -> set[str]:
    assert store.log_dir is not None
    return {p.name for p in store.log_dir.iterdir()}


def test_log_dir_is_the_job_logs_directory_beside_the_database(store: JobStore, tmp_path: Path):
    assert store.log_dir == tmp_path / LOG_DIRNAME


def test_files_older_than_the_retention_age_are_removed(store: JobStore):
    put(store, "old.log", age_days=31)
    put(store, "edge.log", age_days=29)
    put(store, "new.log", age_days=1)
    result = sweep_job_logs(store, retention_days=30, max_mib=500, now=NOW)
    assert names(store) == {"edge.log", "new.log"}
    assert (result.removed_aged, result.removed_size, result.kept, result.scanned) == (1, 0, 2, 3)


def test_size_limit_removes_oldest_first_until_it_fits(store: JobStore):
    put(store, "a.log", size=MIB, age_days=5)
    put(store, "b.log", size=MIB, age_days=4)
    put(store, "c.log", size=MIB, age_days=3)
    put(store, "d.log", size=MIB, age_days=2)
    result = sweep_job_logs(store, retention_days=30, max_mib=2, now=NOW)
    assert names(store) == {"c.log", "d.log"}
    assert result.removed_size == 2 and result.bytes_kept == 2 * MIB


def test_a_running_jobs_log_is_kept_even_when_oldest_and_over_the_limit(store: JobStore):
    running = store.submit("ingest", inputs(), created_by="u1")
    lease, record = store.claim_next("w1")
    assert record.job_id == running.job_id
    put(store, f"{record.job_id}.log", size=3 * MIB, age_days=90)
    put(store, "other.log", size=MIB, age_days=2)
    result = sweep_job_logs(store, retention_days=30, max_mib=1, now=NOW)
    assert names(store) == {f"{record.job_id}.log"}  # the older file is protected, so the size rule takes the other one
    assert result.protected == 1 and result.removed_aged == 0 and result.removed_size == 1


def test_a_live_lease_protects_a_file_whatever_the_job_state(store: JobStore):
    lease = store.acquire_lease(store.job_resource("leasedjob"), "w1", ttl=10_000_000_000)
    assert lease is not None
    put(store, "leasedjob.log", age_days=90)
    sweep_job_logs(store, retention_days=30, now=NOW)
    assert names(store) == {"leasedjob.log"}


def test_recently_modified_files_are_never_deleted(store: JobStore):
    put(store, "fresh.log", size=2 * MIB, age_seconds=60)
    put(store, "settled.log", size=MIB, age_seconds=lr.SAFETY_MARGIN_SECONDS + 5)
    sweep_job_logs(store, retention_days=30, max_mib=1, now=NOW)
    assert names(store) == {"fresh.log"}


def test_unrelated_files_directories_and_bad_names_are_untouched(store: JobStore):
    put(store, "keep.txt", age_days=400)
    put(store, "weird name.log", age_days=400)
    put(store, "a.b.log", age_days=400)
    put(store, ".log", age_days=400)
    put(store, ("x" * 101) + ".log", age_days=400)
    put(store, "ok.log.bak", age_days=400)
    sub = store.log_dir / "sub"  # type: ignore[operator]
    sub.mkdir()
    inner = sub / "inner.log"
    inner.write_text("data")
    os.utime(inner, (NOW - 400 * DAY, NOW - 400 * DAY))
    dirlike = store.log_dir / "dir.log"  # type: ignore[operator]
    dirlike.mkdir()
    before = names(store)
    result = sweep_job_logs(store, retention_days=1, max_mib=1, now=NOW)
    assert names(store) == before and inner.exists()
    assert result.scanned == 0 and result.removed == 0


def test_a_symlink_is_neither_followed_nor_deleted_nor_counted(store: JobStore, tmp_path: Path):
    target = tmp_path / "outside.txt"
    target.write_text("precious" * 1000)
    os.utime(target, (NOW - 400 * DAY, NOW - 400 * DAY))
    assert store.log_dir is not None
    store.log_dir.mkdir()
    (store.log_dir / "link.log").symlink_to(target)
    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    (outside_dir / "x.log").write_text("keep")
    (store.log_dir / "dirlink.log").symlink_to(outside_dir)
    result = sweep_job_logs(store, retention_days=1, max_mib=1, now=NOW)
    assert target.read_text().startswith("precious") and (outside_dir / "x.log").exists()
    assert (store.log_dir / "link.log").is_symlink() and (store.log_dir / "dirlink.log").is_symlink()
    assert result.scanned == 0


def test_a_symlinked_log_directory_is_not_swept(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    victim = real / "a.log"
    victim.write_text("x")
    os.utime(victim, (NOW - 400 * DAY, NOW - 400 * DAY))
    base = tmp_path / "state"
    base.mkdir()
    (base / LOG_DIRNAME).symlink_to(real, target_is_directory=True)
    s = JobStore(base / "jobs.sqlite3")
    try:
        assert sweep_job_logs(s, retention_days=1, now=NOW).scanned == 0
        assert victim.exists()
    finally:
        s.close()


def test_a_limit_of_zero_turns_that_rule_off(store: JobStore):
    put(store, "a.log", size=2 * MIB, age_days=400)
    sweep_job_logs(store, retention_days=0, max_mib=0, now=NOW)
    assert names(store) == {"a.log"}


def test_one_summary_line_with_counts_and_no_paths(store: JobStore, caplog: pytest.LogCaptureFixture):
    put(store, "old.log", age_days=40)
    put(store, "new.log", age_days=1)
    with caplog.at_level(logging.INFO, logger=lr.__name__):
        sweep_job_logs(store, now=NOW)
    lines = [r.getMessage() for r in caplog.records if r.name == lr.__name__]
    assert len(lines) == 1
    assert "removed=1" in lines[0] and "kept=1" in lines[0]
    assert "old" not in lines[0].replace("job log sweep", "") and str(store.log_dir) not in lines[0]


def test_a_sweep_error_never_raises_and_deletes_nothing_more(store: JobStore, monkeypatch, caplog):
    path = put(store, "old.log", age_days=40)

    def boom(*_a, **_k):
        raise RuntimeError("/secret/path exploded")

    monkeypatch.setattr(lr, "_protected_job_ids", boom)  # cannot prove which jobs run: delete nothing
    with caplog.at_level(logging.WARNING, logger=lr.__name__):
        result = sweep_job_logs(store, now=NOW)
    assert result.failed and path.exists()
    assert "RuntimeError" in caplog.text and "/secret" not in caplog.text
    monkeypatch.undo()
    monkeypatch.setattr(lr.os, "scandir", boom)
    assert sweep_job_logs(store, now=NOW).failed


def test_a_failed_delete_is_skipped_not_raised(store: JobStore, monkeypatch):
    put(store, "old.log", age_days=40)
    monkeypatch.setattr(lr.os, "unlink", lambda p: (_ for _ in ()).throw(PermissionError("no")))
    result = sweep_job_logs(store, now=NOW)
    assert not result.failed and result.removed == 0 and names(store) == {"old.log"}


def test_in_memory_store_and_missing_directory_are_no_ops(tmp_path: Path):
    mem = JobStore(":memory:")
    assert sweep_job_logs(mem).scanned == 0
    mem.close()
    s = JobStore(tmp_path / "j.sqlite3")
    assert not (tmp_path / LOG_DIRNAME).exists()
    assert sweep_job_logs(s).scanned == 0
    s.close()


def test_limits_come_from_the_environment_with_defaults():
    assert retention_limits({}) == (30, 500)
    assert retention_limits({"F2W_JOB_LOG_RETENTION_DAYS": "7", "F2W_JOB_LOG_MAX_MIB": "50"}) == (7, 50)
    assert retention_limits({"F2W_JOB_LOG_RETENTION_DAYS": "x", "F2W_JOB_LOG_MAX_MIB": "-3"}) == (30, 500)
    assert retention_limits({"F2W_JOB_LOG_RETENTION_DAYS": "0", "F2W_JOB_LOG_MAX_MIB": "0"}) == (0, 0)


def test_the_timer_sweeps_at_start_and_again_on_its_interval(store: JobStore):
    calls: list[int] = []
    retention = JobLogRetention(store, interval=0.01)
    retention.sweep = lambda: calls.append(1) or lr.SweepResult()  # type: ignore[method-assign]
    retention.start()
    assert calls  # the first sweep ran synchronously inside start()
    deadline = time.monotonic() + 3
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    retention.stop()
    assert len(calls) >= 3
    count = len(calls)
    time.sleep(0.05)
    assert len(calls) == count


class _Executor:
    events: list[str] = []

    def __init__(self, service, max_concurrency):
        pass

    def start(self):
        _Executor.events.append("executor-start")
        return self

    def run_until_idle(self):
        _Executor.events.append("idle")

    def shutdown(self):
        return True


def test_the_worker_sweeps_before_it_starts_executing_jobs(store: JobStore, monkeypatch):
    old = put(store, "old.log", age_days=1)
    os.utime(old, (1.0, 1.0))  # 1970: older than any retention age and the safety margin
    fresh = put(store, "fresh.log")
    os.utime(fresh)  # now
    _Executor.events = []
    service = SimpleNamespace(store=store)
    monkeypatch.setattr(worker_main, "WorkerExecutor", _Executor)
    monkeypatch.setattr(worker_main, "load_factory", lambda spec: (lambda db: service))
    monkeypatch.setenv("F2W_JOB_LOG_RETENTION_DAYS", "30")
    real_sweep = JobLogRetention.sweep

    def spy(self):
        _Executor.events.append("sweep")
        return real_sweep(self)

    monkeypatch.setattr(JobLogRetention, "sweep", spy)
    assert worker_main.main(["--db", "d", "--factory", "app.modules.a:b", "--once"]) == 0
    assert _Executor.events[:2] == ["sweep", "executor-start"]
    assert not old.exists() and fresh.exists()


def test_the_worker_tolerates_a_service_without_a_store(monkeypatch):
    _Executor.events = []
    monkeypatch.setattr(worker_main, "WorkerExecutor", _Executor)
    monkeypatch.setattr(worker_main, "load_factory", lambda spec: (lambda db: "svc"))
    assert worker_main.main(["--db", "d", "--factory", "app.modules.a:b", "--once"]) == 0
    assert "sweep" not in _Executor.events


def test_api_modules_never_import_the_retention_sweep():
    root = Path(lr.__file__).resolve().parents[2]
    offenders = [p.name for p in (root / "api").rglob("*.py") if "log_retention" in p.read_text(encoding="utf-8")]
    assert offenders == []
