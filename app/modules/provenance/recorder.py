"""Durable audit log and recorder for graph writes (K-13/K-14).

Two separate things, on purpose:

* the **audit log**, a hash-chained SQLite table written synchronously and durably. It is the
  record of approvals and graph writes and does not depend on any optional graph projection;
* the **projection** of events into a graph/telemetry sink, which may lag or fail without
  losing anything (see :mod:`.queue`).

If the durable audit cannot be written, the recorder reports a *degraded* status and refuses
critical mutations: :meth:`AuditRecorder.guard` writes the ``intent`` event first and only then
runs the mutation, so an unauditable write never starts. A failed ``outcome`` write after a
successful mutation is kept in a spill buffer, reported, and retried before the next critical
mutation is allowed.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ..contracts.errors import ContractError, ErrorCode
from ..jobs.keys import digest
from ..jobs.sqlite import OperationalDB
from .events import GraphWriteEvent

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS audit_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "event_id TEXT UNIQUE NOT NULL, "
    "event TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL, at REAL NOT NULL);\n",
    "CREATE TABLE IF NOT EXISTS projection_outbox (seq INTEGER PRIMARY KEY, "
    "status TEXT NOT NULL DEFAULT 'pending', "
    "attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);\n",
)
GENESIS = "0" * 64


class AuditUnavailable(ContractError):
    """Durable audit cannot be guaranteed; the critical mutation was not performed."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            ErrorCode.UNAVAILABLE, f"audit unavailable, critical mutation blocked: {reason}", retryable=True
        )


@dataclass(frozen=True)
class AuditStatus:
    """Health of the audit path, for status endpoints and telemetry."""

    healthy: bool
    reason: str
    spilled: int
    dropped: int
    pending_projection: int
    last_seq: int


class _ProbeDone(Exception):
    """Internal: rolls the probe write back."""


class AuditLog:
    """Append-only, hash-chained SQLite audit log."""

    def __init__(
        self, path: Any = ":memory:", *, clock: Callable[[], float], db: Optional[OperationalDB] = None
    ) -> None:
        self.db = db or OperationalDB(path)
        self._clock = clock
        with self.db.tx() as con:
            for statement in SCHEMA:
                con.execute(statement.rstrip(";\n"))

    def append(self, event: GraphWriteEvent) -> int:
        """Durably append ``event``; returns its sequence. Idempotent per ``event_id``."""
        with self.db.tx() as con:
            existing = con.execute("SELECT seq FROM audit_events WHERE event_id=?", (event.event_id,)).fetchone()
            if existing is not None:
                return existing[0]
            row = con.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            body = event.model_dump_json()
            cur = con.execute(
                "INSERT INTO audit_events (event_id, event, prev_hash, hash, at) VALUES (?, ?, ?, ?, ?)",
                (event.event_id, body, prev, digest([prev, body]), self._clock()),
            )
            seq = cur.lastrowid
            con.execute("INSERT INTO projection_outbox (seq, updated_at) VALUES (?, ?)", (seq, self._clock()))
        return int(seq)

    def probe(self) -> None:
        """Check the log is writable without leaving a row behind (insert, then roll back)."""
        try:
            with self.db.tx() as con:
                con.execute(
                    "INSERT INTO audit_events (event_id, event, prev_hash, hash, at) VALUES (?, ?, ?, ?, ?)",
                    ("probe", "{}", GENESIS, GENESIS, self._clock()),
                )
                raise _ProbeDone()
        except _ProbeDone:
            return

    def events(
        self, *, after: int = 0, kind: Optional[str] = None, operation_id: Optional[str] = None, limit: int = 500
    ) -> List[Tuple[int, GraphWriteEvent]]:
        """Events with ``seq > after`` in order."""
        rows = self.db.query(
            "SELECT seq, event FROM audit_events WHERE seq>? ORDER BY seq LIMIT ?",
            (after, max(1, min(limit, 5000))),
        )
        out = [(r["seq"], GraphWriteEvent.model_validate_json(r["event"])) for r in rows]
        return [
            (s, e)
            for s, e in out
            if (kind is None or e.kind == kind) and (operation_id is None or e.operation_id == operation_id)
        ]

    def verify_chain(self) -> Tuple[bool, int]:
        """Check the hash chain; returns ``(ok, events_checked)``. A gap or edit makes it false."""
        prev, count = GENESIS, 0
        for row in self.db.query("SELECT seq, event, prev_hash, hash FROM audit_events ORDER BY seq"):
            if row["prev_hash"] != prev or row["hash"] != digest([prev, row["event"]]):
                return False, count
            prev, count = row["hash"], count + 1
        return True, count

    def last_seq(self) -> int:
        """Highest sequence written."""
        row = self.db.one("SELECT COALESCE(MAX(seq), 0) FROM audit_events")
        return int(row[0])

    def pending_projection(self) -> int:
        """Events not yet projected."""
        return int(self.db.one("SELECT COUNT(*) FROM projection_outbox WHERE status='pending'")[0])

    def close(self) -> None:
        """Close the database."""
        self.db.close()


class AuditRecorder:
    """Writes audit events and guards critical mutations.

    Args:
        log: The durable audit log.
        spill_limit: Maximum events held in memory while the log is unwritable; beyond it events are counted
            as ``dropped`` and the recorder stays degraded (never silently).
    """

    def __init__(self, log: AuditLog, *, spill_limit: int = 1000) -> None:
        self.log = log
        self._lock = threading.RLock()
        self._spill: List[GraphWriteEvent] = []
        self._spill_limit = spill_limit
        self._dropped = 0
        self._degraded_reason = ""

    # --- recording ---------------------------------------------------------------------

    def record(self, event: GraphWriteEvent) -> Optional[int]:
        """Durably record ``event``.

        Critical events raise :class:`AuditUnavailable` when they cannot be written. Other events
        are spilled (and the recorder is marked degraded) so they are retried, never dropped silently.
        """
        with self._lock:
            self._flush_spill_locked()
            try:
                seq = self.log.append(event)
            except Exception as exc:  # noqa: BLE001 - any storage failure degrades the audit path
                self._degraded_reason = f"{type(exc).__name__}: audit log not writable"
                if event.critical:
                    raise AuditUnavailable(self._degraded_reason) from exc
                self._hold(event)
                return None
            if not self._spill and not self._dropped:
                self._degraded_reason = ""
            return seq

    def _hold(self, event: GraphWriteEvent) -> None:
        if len(self._spill) >= self._spill_limit:
            self._dropped += 1
        else:
            self._spill.append(event)

    def _flush_spill_locked(self) -> None:
        while self._spill:
            try:
                self.log.append(self._spill[0])
            except Exception:  # noqa: BLE001
                return
            self._spill.pop(0)
        if not self._spill and self._dropped == 0:
            self._degraded_reason = ""

    def flush(self) -> bool:
        """Try to write spilled events; ``True`` when nothing remains spilled."""
        with self._lock:
            self._flush_spill_locked()
            return not self._spill

    def ensure_healthy(self) -> None:
        """Raise :class:`AuditUnavailable` unless the audit path can be guaranteed right now."""
        with self._lock:
            self._flush_spill_locked()
            if self._spill or self._dropped:
                raise AuditUnavailable(self._degraded_reason or "events are waiting to be persisted")
            try:
                self.log.probe()
            except Exception as exc:  # noqa: BLE001
                self._degraded_reason = f"{type(exc).__name__}: audit log not writable"
                raise AuditUnavailable(self._degraded_reason) from exc
            self._degraded_reason = ""

    def status(self) -> AuditStatus:
        """Current health, including backlog counts."""
        with self._lock:
            try:
                pending, last = self.log.pending_projection(), self.log.last_seq()
            except Exception:  # noqa: BLE001
                pending, last = -1, -1
                self._degraded_reason = self._degraded_reason or "audit log not readable"
            healthy = not (self._spill or self._dropped or self._degraded_reason)
            return AuditStatus(healthy, self._degraded_reason, len(self._spill), self._dropped, pending, last)

    # --- guarded mutation --------------------------------------------------------------

    def guard(
        self,
        *,
        kind: str,
        operation_id: str,
        actor: str,
        target: str,
        mutation: Callable[[], Any],
        payload: Optional[Mapping[str, Any]] = None,
        run_id: Optional[str] = None,
    ) -> Any:
        """Run a critical ``mutation`` with write-ahead audit.

        1. Make the ``intent`` event durable (and the log healthy): otherwise raise
           :class:`AuditUnavailable` and do **not** run the mutation.
        2. Run the mutation.
        3. Record the ``outcome`` (``ok``/``failed``). If that write fails the mutation already
           happened: the outcome is spilled, the recorder is degraded, and the next critical
           mutation is blocked until it persists.
        """
        self.ensure_healthy()
        common: Dict[str, Any] = dict(
            kind=kind, operation_id=operation_id, actor=actor, target=target, run_id=run_id, critical=True
        )
        self.record(GraphWriteEvent.build(phase="intent", status="started", payload=payload, **common))
        try:
            result = mutation()
        except BaseException as exc:
            self._outcome(
                GraphWriteEvent.build(
                    phase="outcome", status="failed", payload={"error": type(exc).__name__}, **common
                )
            )
            raise
        self._outcome(GraphWriteEvent.build(phase="outcome", status="ok", payload=payload, **common))
        return result

    def _outcome(self, event: GraphWriteEvent) -> None:
        with self._lock:
            try:
                self.record(event)
            except AuditUnavailable:
                self._hold(event)  # the mutation already ran: keep its outcome and stay degraded


def events_payload(event: GraphWriteEvent) -> Dict[str, Any]:
    """JSON-ready dict of an event (for projection sinks)."""
    return json.loads(event.model_dump_json())


__all__ = ("AuditLog", "AuditRecorder", "AuditStatus", "AuditUnavailable", "GENESIS", "events_payload")
