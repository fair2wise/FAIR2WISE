"""Resumable ingest journal: owned leases, heartbeats, per-file stage checkpoints (J-04).

A job's identity is the hash of its configuration (folder, pipeline fingerprint,
options), so re-running the same job resumes it. Each file is an *operation*
(its content sha256) with stage rows (``intake``, ``complete``). Only settled
outcomes are checkpointed; retryable ones (registry outage, quota pause) and
failures are recorded with an error and retried on resume. Retries are
at-least-once, so the per-file effect must be idempotent (content-addressed
store, package keys, import receipts).

Not ported: the donor's ``adopt_tiled_checkpoints`` (graph-checkpoint adoption
belongs to T08/T10 with the publication split).

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/ingest_journal.py (``IngestJournal``)
(BSD-3-Clause); the process-global connection cache is replaced by one connection per instance.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Iterator, Mapping


class IngestJournal:
    """SQLite journal of ingest jobs and per-file stage checkpoints.

    Args:
        path: Database file (``":memory:"`` for tests).
        lease_seconds: Lease length renewed by :meth:`heartbeat`.
        clock: Epoch seconds, injectable.
    """

    def __init__(self, path: Any, *, lease_seconds: float = 120.0, clock: Callable[[], float] = time.time) -> None:
        target = str(path)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(target, timeout=30, check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        if target != ":memory:":
            self._con.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        self.lease_seconds = lease_seconds
        self._clock = clock
        with self._tx() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, config TEXT NOT NULL, state TEXT "
                "NOT NULL, owner TEXT, lease_until REAL NOT NULL DEFAULT 0, stop_requested INTEGER NOT NULL "
                "DEFAULT 0, updated REAL NOT NULL)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS stages (job_id TEXT NOT NULL, operation TEXT NOT NULL, stage TEXT "
                "NOT NULL, result TEXT, attempts INTEGER NOT NULL DEFAULT 0, error TEXT, "
                "PRIMARY KEY(job_id, operation, stage))"
            )

    @contextmanager
    def _tx(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._con.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self._con
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
            self._con.execute("COMMIT")

    @staticmethod
    def job_key(config: Mapping[str, Any]) -> str:
        """Identity of a job configuration."""
        return hashlib.sha256(json.dumps(dict(config), sort_keys=True, default=str).encode()).hexdigest()

    def acquire(self, config: Mapping[str, Any], owner: str) -> str | None:
        """Start or resume the job for ``config``; ``None`` while another job holds a live lease.

        ``force`` in the config clears the checkpoints of a previously *completed* job;
        stopped/failed jobs always keep theirs so a long run resumes.
        """
        key = self.job_key(config)
        now = self._clock()
        with self._tx(immediate=True) as con:
            if con.execute("SELECT id FROM jobs WHERE state='running' AND lease_until>?", (now,)).fetchone():
                return None
            previous = con.execute("SELECT state FROM jobs WHERE id=?", (key,)).fetchone()
            if config.get("force") and previous and previous["state"] == "completed":
                con.execute("DELETE FROM stages WHERE job_id=?", (key,))
            con.execute(
                "INSERT INTO jobs VALUES (?, ?, 'running', ?, ?, 0, ?) ON CONFLICT(id) DO UPDATE SET "
                "state='running', owner=excluded.owner, lease_until=excluded.lease_until, stop_requested=0, "
                "updated=excluded.updated",
                (key, json.dumps(dict(config), sort_keys=True, default=str), owner, now + self.lease_seconds, now),
            )
        return key

    def heartbeat(self, key: str, owner: str) -> bool:
        """Renew the lease; ``False`` when ``owner`` no longer holds it."""
        now = self._clock()
        with self._tx() as con:
            cur = con.execute(
                "UPDATE jobs SET lease_until=?, updated=? WHERE id=? AND owner=? AND state='running'",
                (now + self.lease_seconds, now, key, owner),
            )
            return cur.rowcount == 1

    def finish(self, key: str, owner: str, state: str) -> None:
        """End the job as ``completed``, ``stopped`` or ``failed``."""
        with self._tx() as con:
            con.execute(
                "UPDATE jobs SET state=?, lease_until=0, updated=? WHERE id=? AND owner=?",
                (state, self._clock(), key, owner),
            )

    def checkpoint(self, key: str, operation: str, stage: str) -> dict[str, Any] | None:
        """The settled result of a stage, else ``None``."""
        with self._lock:
            row = self._con.execute(
                "SELECT result FROM stages WHERE job_id=? AND operation=? AND stage=?", (key, operation, stage)
            ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def save(
        self, key: str, operation: str, stage: str, result: Mapping[str, Any] | None, error: str = ""
    ) -> None:
        """Record a stage: a settled ``result``, or ``None`` plus an ``error`` to retry later."""
        with self._tx() as con:
            con.execute(
                "INSERT INTO stages VALUES (?, ?, ?, ?, 1, ?) ON CONFLICT(job_id, operation, stage) DO UPDATE "
                "SET result=excluded.result, attempts=attempts+1, error=excluded.error",
                (key, operation, stage, json.dumps(dict(result)) if result is not None else None, error),
            )

    def completion_snapshot(self, key: str) -> dict[str, Any]:
        """Durable progress: settled verdicts, retryable/failed ones, counts by status."""
        with self._lock:
            rows = self._con.execute(
                "SELECT operation, result, error, attempts FROM stages WHERE job_id=? AND "
                "stage='complete' ORDER BY rowid",
                (key,),
            ).fetchall()
        settled: list[dict[str, Any]] = [json.loads(r["result"]) for r in rows if r["result"]]
        pending = [
            {"operation": r["operation"], "error": r["error"], "attempts": r["attempts"]}
            for r in rows
            if not r["result"]
        ]
        return {
            "progress": len(settled),
            "counts": dict(Counter(v.get("status", "") for v in settled)),
            "verdicts": settled,
            "pending": pending,
        }

    def _job(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._lock:
            row = self._con.execute(sql, params).fetchone()
        if not row:
            return None
        result = dict(row)
        result["config"] = json.loads(result["config"])
        return result

    def latest(self) -> dict[str, Any] | None:
        """The live job, else the most recently updated one."""
        return self._job(
            "SELECT * FROM jobs ORDER BY (state='running' AND lease_until>?) DESC, updated DESC LIMIT 1",
            (self._clock(),),
        )

    def recoverable(self) -> dict[str, Any] | None:
        """A running job whose lease expired (its owner died) and that was not asked to stop."""
        return self._job(
            "SELECT * FROM jobs WHERE state='running' AND lease_until<? AND stop_requested=0 "
            "ORDER BY updated LIMIT 1",
            (self._clock(),),
        )

    def request_stop(self) -> None:
        """Ask every running job to stop after its current files."""
        with self._tx() as con:
            con.execute("UPDATE jobs SET stop_requested=1 WHERE state='running'")

    def stopped(self, key: str) -> bool:
        """Whether a stop was requested for ``key``."""
        with self._lock:
            row = self._con.execute("SELECT stop_requested FROM jobs WHERE id=?", (key,)).fetchone()
        return bool(row and row[0])

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._con.close()


__all__ = ("IngestJournal",)
