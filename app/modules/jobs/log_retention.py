"""Retention of the per-job full log files (``<jobs-db-dir>/job-logs/<job_id>.log``, written by ``JobStore``).

Only a worker process enforces it (at start and every ``SWEEP_INTERVAL_SECONDS``); an API request never
deletes anything. A sweep removes, oldest modification time first, matching files that are older than the
retention age, then more of the oldest until the total size of the matching files fits the size limit.

Safety rules, all enforced here and tested:

* only regular files named exactly ``<id>.log`` with ``id`` matching ``[A-Za-z0-9_-]{1,100}`` directly in the
  directory are looked at; symlinks are never followed, nothing is recursed into, everything else is ignored;
* the log of a job that is running or holds a live lease is never deleted, and neither is a file modified in
  the last ``SAFETY_MARGIN_SECONDS`` (such files still count towards the size total);
* if the running jobs cannot be looked up, nothing is deleted;
* a sweep never raises into the worker and logs one summary line with counts only (no paths).

Limits come from ``F2W_JOB_LOG_RETENTION_DAYS`` (default 30, ``0`` turns the age rule off) and
``F2W_JOB_LOG_MAX_MIB`` (default 500, ``0`` turns the size rule off).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import threading
import time

from .store import JobStore

logger = logging.getLogger(__name__)

RETENTION_DAYS_ENV = "F2W_JOB_LOG_RETENTION_DAYS"
MAX_MIB_ENV = "F2W_JOB_LOG_MAX_MIB"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_MIB = 500
#: Seconds between two sweeps of the worker's timer.
SWEEP_INTERVAL_SECONDS = 15 * 60
#: A file modified this recently is never deleted (a job may be about to write to it).
SAFETY_MARGIN_SECONDS = 5 * 60

_LOG_NAME = re.compile(r"[A-Za-z0-9_-]{1,100}\.log")


@dataclass(frozen=True)
class SweepResult:
    """Counts of one sweep (never paths)."""

    scanned: int = 0
    removed_aged: int = 0
    removed_size: int = 0
    kept: int = 0
    protected: int = 0
    bytes_kept: int = 0
    failed: bool = False

    @property
    def removed(self) -> int:
        """Files deleted by this sweep."""
        return self.removed_aged + self.removed_size


@dataclass(frozen=True)
class _Entry:
    mtime: float
    size: int
    name: str
    deletable: bool


def _limit(environ: Mapping[str, str], name: str, default: int) -> int:
    """A non-negative integer limit from the environment; an unset or invalid value gives ``default``."""
    raw = str(environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s is not an integer; using %d", name, default)
        return default
    return value if value >= 0 else default


def retention_limits(environ: Mapping[str, str] | None = None) -> tuple[int, int]:
    """``(retention_days, max_mib)`` from the environment (``0`` disables that rule)."""
    env = os.environ if environ is None else environ
    return _limit(env, RETENTION_DAYS_ENV, DEFAULT_RETENTION_DAYS), _limit(env, MAX_MIB_ENV, DEFAULT_MAX_MIB)


def _protected_job_ids(store: JobStore, now: float) -> set[str]:
    """Ids of jobs that are running or hold an unexpired lease (raises when the store cannot be read)."""
    ids = {str(r[0]) for r in store.db.query("SELECT job_id FROM jobs WHERE state=?", ("running",))}
    prefix = store.job_resource("")
    for resource, expires_at in store.db.query("SELECT resource, expires_at FROM leases"):
        if str(resource).startswith(prefix) and float(expires_at) > now:
            ids.add(str(resource)[len(prefix):])
    return ids


def sweep_job_logs(
    store: JobStore,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    max_mib: int = DEFAULT_MAX_MIB,
    now: float | None = None,
) -> SweepResult:
    """Apply the retention rules once; log one summary line; never raise."""
    directory = store.log_dir
    if directory is None:
        return SweepResult()
    try:
        result = _sweep(store, directory, retention_days, max_mib, time.time() if now is None else now)
    except Exception as exc:  # noqa: BLE001  # a sweep must never take the worker down; only the class name is logged
        logger.warning("job log sweep failed (%s); nothing more was deleted", type(exc).__name__)
        return SweepResult(failed=True)
    logger.info(
        "job log sweep: scanned=%d removed=%d (aged=%d, size=%d) kept=%d protected=%d bytes_kept=%d",
        result.scanned, result.removed, result.removed_aged, result.removed_size, result.kept, result.protected,
        result.bytes_kept,
    )
    return result


def _sweep(store: JobStore, directory: Path, retention_days: int, max_mib: int, now: float) -> SweepResult:
    if directory.is_symlink() or not directory.is_dir():
        return SweepResult()
    protected_ids = _protected_job_ids(store, now)  # raises: without it no file is safe to delete
    files: list[_Entry] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if not _LOG_NAME.fullmatch(entry.name):
                continue
            try:
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            deletable = entry.name[: -len(".log")] not in protected_ids and info.st_mtime <= now - SAFETY_MARGIN_SECONDS
            files.append(_Entry(info.st_mtime, info.st_size, entry.name, deletable))
    files.sort(key=lambda f: (f.mtime, f.name))  # oldest first
    total = sum(f.size for f in files)
    aged = sized = 0
    remaining: list[_Entry] = []
    cutoff = now - retention_days * 86400 if retention_days > 0 else None
    for item in files:
        if item.deletable and cutoff is not None and item.mtime < cutoff and _remove(directory, item.name):
            aged += 1
            total -= item.size
        else:
            remaining.append(item)
    limit = max_mib * 1024 * 1024 if max_mib > 0 else None
    kept: list[_Entry] = []
    for item in remaining:
        if limit is not None and total > limit and item.deletable and _remove(directory, item.name):
            sized += 1
            total -= item.size
        else:
            kept.append(item)
    return SweepResult(
        scanned=len(files), removed_aged=aged, removed_size=sized, kept=len(kept),
        protected=sum(1 for f in kept if not f.deletable), bytes_kept=total,
    )


def _remove(directory: Path, name: str) -> bool:
    """Unlink one already-validated file name inside ``directory`` (a symlink swapped in would only lose the link)."""
    try:
        (directory / name).unlink()
    except OSError:
        return False
    return True


class JobLogRetention:
    """Worker-side enforcement: one sweep at :meth:`start`, then one every ``interval`` seconds on a daemon thread."""

    def __init__(
        self,
        store: JobStore,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_mib: int = DEFAULT_MAX_MIB,
        interval: float = SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self.store = store
        self.retention_days = retention_days
        self.max_mib = max_mib
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_env(cls, store: JobStore, environ: Mapping[str, str] | None = None) -> JobLogRetention:
        """Limits from ``F2W_JOB_LOG_RETENTION_DAYS`` and ``F2W_JOB_LOG_MAX_MIB``."""
        days, mib = retention_limits(environ)
        return cls(store, retention_days=days, max_mib=mib)

    def sweep(self) -> SweepResult:
        """One sweep with the configured limits."""
        return sweep_job_logs(self.store, retention_days=self.retention_days, max_mib=self.max_mib)

    def start(self) -> JobLogRetention:
        """Sweep now (so a restarted worker catches up), then keep sweeping on a daemon thread."""
        self.sweep()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="job-log-retention", daemon=True)
            self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.sweep()

    def stop(self) -> None:
        """Stop the timer thread (a sweep in progress finishes first)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None


__all__ = (
    "DEFAULT_MAX_MIB", "DEFAULT_RETENTION_DAYS", "MAX_MIB_ENV", "RETENTION_DAYS_ENV", "SAFETY_MARGIN_SECONDS",
    "SWEEP_INTERVAL_SECONDS", "JobLogRetention", "SweepResult", "retention_limits", "sweep_job_logs",
)
