"""Service factories a worker process may load (``--factory app.modules.workers.factories:<name>``).

Only callables under ``app.modules.`` are loadable (see ``jobs.process_support.worker_command``).
Real pipelines are registered by the application wiring (T14); the idle service here lets a
worker process start, observe the store and exit, which is what the process tests exercise.
"""

from __future__ import annotations

from ..jobs.service import JobService
from ..jobs.store import JobStore


def idle_service(db_path: str) -> JobService:
    """A service over the job store at ``db_path`` with no kinds registered."""
    return JobService(JobStore(db_path), heartbeat_interval=30.0)


__all__ = ("idle_service",)
