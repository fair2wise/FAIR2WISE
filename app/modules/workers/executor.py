"""Owned worker executor: explicit start/shutdown, bounded concurrency, shared admission (plan T10, [W4]).

Heavy, restart-sensitive work runs here, not in a FastAPI request handler or ``BackgroundTasks``.
There is no Celery or Redis: a fixed number of worker threads (``max_concurrency``) each loop
``JobService.work_one``, which claims a job under an owned, fenced lease and runs its pipeline.
FastAPI (T14) only submits and observes jobs through the service layer.

Provider concurrency is **not** owned here: stage handlers take slots from the shared
``llm_admission`` controllers (``StageContext.provider_slot``), the same per-provider quota that
chat and evaluations use, so ``max_concurrency`` bounds jobs while the provider cap bounds calls.

``shutdown`` is graceful: it sets the service's interrupt flag, running jobs return to ``queued``
at their next boundary (their completed stages and units are kept), and the workers are joined
with a timeout. A worker that cannot stop in time is reported, not hidden; its lease simply
expires and another owner resumes.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from ..jobs.service import JobService

logger = logging.getLogger(__name__)


class WorkerExecutor:
    """Fixed-size pool of job workers.

    Args:
        service: The job service whose registered kinds this executor runs.
        max_concurrency: Maximum jobs running at once (at least 1).
        poll_interval: Idle sleep between claim attempts, in seconds.
        owner: Lease owner prefix; defaults to ``worker-<pid>``.
    """

    def __init__(
        self,
        service: JobService,
        *,
        max_concurrency: int = 2,
        poll_interval: float = 0.25,
        owner: str | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self.service = service
        self.max_concurrency = max_concurrency
        self.poll_interval = poll_interval
        self.owner = owner or f"worker-{os.getpid()}"
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._running = 0
        self.errors: list[str] = []
        self.completed = 0

    @property
    def started(self) -> bool:
        """Whether any worker thread is alive."""
        return any(t.is_alive() for t in self._threads)

    @property
    def running(self) -> int:
        """Jobs being executed right now."""
        with self._lock:
            return self._running

    def start(self) -> WorkerExecutor:
        """Start the workers. Calling it twice is an error (explicit lifecycle)."""
        if self._threads:
            raise RuntimeError("executor already started")
        self._stop.clear()
        self.service.interrupt.clear()
        for index in range(self.max_concurrency):
            thread = threading.Thread(target=self._loop, args=(index,), name=f"{self.owner}-{index}", daemon=True)
            self._threads.append(thread)
            thread.start()
        return self

    def _loop(self, index: int) -> None:
        owner = f"{self.owner}-{index}"
        while not self._stop.is_set():
            try:
                with self._lock:
                    self._running += 1
                try:
                    outcome = self.service.work_one(owner)
                finally:
                    with self._lock:
                        self._running -= 1
            except Exception as exc:  # noqa: BLE001 - a failing job must not kill the worker
                logger.exception("worker %s: job raised", owner)
                self.errors.append(type(exc).__name__)
                outcome = None
            if outcome is None:
                self._stop.wait(self.poll_interval)
            else:
                self.completed += 1

    def run_until_idle(self, *, timeout: float = 30.0) -> bool:
        """Block until no job is ``queued`` or ``running`` (paused, waiting and finished jobs do not count)."""
        waiter = threading.Event()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            row = self.service.store.db.one("SELECT COUNT(*) FROM jobs WHERE state IN ('queued', 'running')")
            if row[0] == 0:
                return True
            waiter.wait(0.02)
        return False

    def shutdown(self, *, timeout: float = 10.0) -> bool:
        """Stop gracefully; returns ``True`` when every worker thread ended within ``timeout``."""
        self._stop.set()
        self.service.interrupt.set()
        for thread in self._threads:
            thread.join(timeout)
        stopped = not any(t.is_alive() for t in self._threads)
        if stopped:
            self._threads.clear()
        else:
            logger.warning(
                "executor %s: %d worker(s) did not stop within %.1fs",
                self.owner,
                sum(t.is_alive() for t in self._threads),
                timeout,
            )
        return stopped

    def __enter__(self) -> WorkerExecutor:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()


__all__ = ("WorkerExecutor",)
