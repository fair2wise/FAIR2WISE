"""Forward ``logging`` records of named loggers into one job's log channel (T33).

A library job runs code that logs through the standard library (for example the term extractor's
``Orchestrator.process_pdf``, which fans pages out over its own ``ThreadPoolExecutor``). The handler
therefore never filters by the calling thread. Instead exactly one capture may be active per process
(a module-level guard) and every record of the named loggers belongs to that one job.

Records are scrubbed and bounded by :meth:`JobStore.append_log`. A line that cannot be stored (lease
lost, stop requested, shutdown) is dropped and counted, never raised into the caller's logging call.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
import logging
import threading

from .pipeline import StageContext

_ACTIVE_LOCK = threading.Lock()
_ACTIVE_JOB: str | None = None


class CaptureBusy(RuntimeError):
    """A log capture is already active in this process (one active job per process)."""


def _level_name(levelno: int) -> str:
    """Map a ``logging`` level number to a job log level."""
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warning"
    if levelno >= logging.INFO:
        return "info"
    return "debug"


class JobLogHandler(logging.Handler):
    """Handler that writes each record to ``sc.log``.

    Args:
        sc: Stage context of the job that owns the capture.
        level: Minimum record level forwarded.

    Attributes:
        dropped: Number of records that could not be stored.
    """

    def __init__(self, sc: StageContext, level: int = logging.INFO) -> None:
        """Bind the handler to ``sc`` and forward records at or above ``level``."""
        super().__init__(level)
        self.sc = sc
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        """Store one record; never raise into the logging caller."""
        try:
            self.sc.log(record.getMessage(), _level_name(record.levelno))
        except Exception:  # noqa: BLE001 - lease lost, stop requested or a bad format must not break the caller
            self.dropped += 1


@contextmanager
def capture_job_logs(
    sc: StageContext,
    logger_names: Iterable[str],
    *,
    level: int = logging.INFO,
    set_logger_level: bool = True,
) -> Iterator[JobLogHandler]:
    """Forward the named loggers' records to ``sc.log`` for the duration of the block.

    Pass ``"f2w.output"`` (``cli_output.OUTPUT_LOGGER``) to include the user-facing output lines: that
    logger does not propagate, so the handler is attached to it directly, like every named logger.

    Args:
        sc: Stage context of the one active job.
        logger_names: Logger names to attach to (duplicates are ignored).
        level: Minimum level forwarded.
        set_logger_level: Lower a logger's own level to ``level`` while captured (restored afterwards)
            so records the logger would otherwise discard reach the handler.

    Yields:
        The attached handler (``dropped`` counts lines that could not be stored).

    Raises:
        CaptureBusy: Another capture is active in this process.
    """
    global _ACTIVE_JOB  # noqa: PLW0603 - the one-active-job-per-process guard
    if not _ACTIVE_LOCK.acquire(blocking=False):
        raise CaptureBusy(f"job logs of {_ACTIVE_JOB} are already being captured in this process")
    handler = JobLogHandler(sc, level)
    attached: list[tuple[logging.Logger, int]] = []
    try:
        _ACTIVE_JOB = sc.job.job_id
        for name in dict.fromkeys(logger_names):
            logger = logging.getLogger(name)
            attached.append((logger, logger.level))
            logger.addHandler(handler)
            if set_logger_level and logger.getEffectiveLevel() > level:
                logger.setLevel(level)
        yield handler
    finally:
        for logger, previous in attached:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        _ACTIVE_JOB = None
        _ACTIVE_LOCK.release()


__all__ = ("CaptureBusy", "JobLogHandler", "capture_job_logs")
