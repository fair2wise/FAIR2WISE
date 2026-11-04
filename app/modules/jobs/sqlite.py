"""Operational SQLite connection helper shared by the job, curation and audit stores (U-02).

One connection per instance (no process-global cache), WAL journal for file databases, a
configured busy timeout, and an RLock so one instance is safe to share between threads. Each
transaction is explicit: ``tx()`` begins immediately by default so a read-check-write sequence
cannot interleave with another writer. Tests use a per-test file or ``":memory:"``; nothing here
points at a shared production database.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/thread_sqlite.py (``thread_connection``)
(BSD-3-Clause); the thread-local connection cache is replaced by an owned connection.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from typing import Any


class OperationalDB:
    """Owned SQLite connection with explicit transactions.

    Args:
        path: Database file, or ``":memory:"``.
        busy_timeout: Seconds SQLite waits on a locked database.
        schema: SQL script run once at open (``CREATE TABLE IF NOT EXISTS ...``).
    """

    def __init__(self, path: object, *, busy_timeout: float = 30.0, schema: Sequence[str] = ()) -> None:
        target = str(path)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self.path = target
        self._con = sqlite3.connect(target, timeout=busy_timeout, check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        self._con.execute(f"PRAGMA busy_timeout={int(busy_timeout * 1000)}")
        if target != ":memory:":
            self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._depth = 0
        for script in schema:
            with self.tx() as con:
                for statement in [s for s in script.split(";\n") if s.strip()]:
                    con.execute(statement)

    @contextmanager
    def tx(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Run a transaction; nested calls join the outer one."""
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self._con
                finally:
                    self._depth -= 1
                return
            self._con.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            self._depth = 1
            try:
                yield self._con
            except BaseException:
                self._depth = 0
                self._con.execute("ROLLBACK")
                raise
            self._depth = 0
            self._con.execute("COMMIT")

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[Any]:  # Any: sqlite3 row objects
        """Run a read-only statement and return all rows."""
        with self._lock:
            return self._con.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Sequence[Any] = ()) -> Any:  # noqa: ANN401  # Any: sqlite row
        """Run a read-only statement and return the first row or ``None``."""
        with self._lock:
            return self._con.execute(sql, tuple(params)).fetchone()

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._con.close()


__all__ = ("OperationalDB",)
