"""Queued projection of audit events (K-14).

A single ordered worker thread projects audit events into an optional sink (a graph or telemetry
store). The audit rows are already durable before an event reaches this queue, so a slow or
failing sink only makes the *projection* lag: failed events stay ``pending`` with an attempt
count and an error, are retried, and are reported by :meth:`QueuedRecorder.stats`. ``flush``
drains with a bounded wait and reports what remains; it never reports success while events are
unprojected, and ``close`` does not discard them (they remain in the outbox for the next start).

Adapted from als-computing/forge_rsoxs_analysis src/forge/kg/recorder_queue.py
(``QueuedRecorder``) (BSD-3-Clause): one ordered worker instead of per-call threads, a durable
backlog instead of an in-memory queue.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Protocol

from .events import GraphWriteEvent
from .recorder import AuditLog

logger = logging.getLogger(__name__)


class ProjectionSink(Protocol):
    """Optional consumer of audit events (graph provenance, telemetry)."""

    def project(self, seq: int, event: GraphWriteEvent) -> None:
        """Project one event; raise to signal a retryable failure."""
        ...


class QueuedRecorder:
    """Drains the durable outbox into a :class:`ProjectionSink` on one background thread."""

    def __init__(
        self,
        log: AuditLog,
        sink: ProjectionSink,
        *,
        interval: float = 0.05,
        batch: int = 100,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._log = log
        self._sink = sink
        self._interval = interval
        self._batch = batch
        self._clock = clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures = 0
        self._drain_lock = threading.Lock()

    def start(self) -> QueuedRecorder:
        """Start the worker (idempotent)."""
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="audit-projection", daemon=True)
            self._thread.start()
        return self

    def notify(self) -> None:
        """Hint that new events exist."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.drain_once()
            self._wake.wait(self._interval)
            self._wake.clear()

    def drain_once(self) -> int:
        """Project up to one batch in order; stop at the first failure so order is preserved."""
        with self._drain_lock:
            return self._drain_locked()

    def _drain_locked(self) -> int:
        projected = 0
        rows = self._log.db.query(
            "SELECT o.seq, a.event FROM projection_outbox o JOIN audit_events a ON a.seq=o.seq "
            "WHERE o.status='pending' ORDER BY o.seq LIMIT ?",
            (self._batch,),
        )
        for row in rows:
            event = GraphWriteEvent.model_validate_json(row["event"])
            try:
                self._sink.project(row["seq"], event)
            except Exception as exc:  # noqa: BLE001 - the sink is optional and may be down
                self._failures += 1
                with self._log.db.tx() as con:
                    con.execute(
                        "UPDATE projection_outbox SET attempts=attempts+1, error=?, updated_at=? WHERE seq=?",
                        (type(exc).__name__, self._clock(), row["seq"]),
                    )
                logger.warning("audit projection failed for seq %s: %s", row["seq"], type(exc).__name__)
                break
            with self._log.db.tx() as con:
                con.execute(
                    "UPDATE projection_outbox SET status='projected', error='', updated_at=? WHERE seq=?",
                    (self._clock(), row["seq"]),
                )
            projected += 1
        return projected

    def flush(self, timeout: float = 5.0) -> bool:
        """Drain until empty or ``timeout``; ``True`` only when every event has been projected."""
        deadline = self._clock() + timeout
        while self._log.pending_projection():
            if self.drain_once() == 0 and self._clock() >= deadline:
                return False
            if self._clock() >= deadline:
                return not self._log.pending_projection()
        return True

    def stats(self) -> dict[str, Any]:
        """Backlog, failures and the last error, never hidden."""
        row = self._log.db.one("SELECT COUNT(*) FROM projection_outbox WHERE error != ''")
        return {
            "pending": self._log.pending_projection(),
            "failures": self._failures,
            "events_with_errors": int(row[0]),
        }

    def close(self, timeout: float = 5.0) -> bool:
        """Stop the worker after a bounded drain. Undrained events stay in the outbox."""
        drained = self.flush(timeout)
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return drained


__all__ = ("ProjectionSink", "QueuedRecorder")
