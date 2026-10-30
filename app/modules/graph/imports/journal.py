"""Owned lease and durable operation journal for one import (T08B; catalog J-02/J-04 mechanics).

Adapted from als-computing/forge_rsoxs_analysis src/als_knowledge_agent/graph/ingest_journal.py and
import_staged.py (BSD-3-Clause).

* :class:`Lease` - single writer per ``(target, scope)``: an exclusive lease file with owner token,
  generation (fencing) and expiry. A stale owner finds its token replaced and stops
  (``CONFLICT``) before its next mutation (plan 11.3: "lease expiry while a worker is alive").
* :class:`ImportJournal` - append-only JSONL, fsynced. Every operation is journaled ``intent`` before
  and ``done`` after its effect, under a stable operation id. The journal is advisory evidence: a
  replay never trusts it over the target; it reconciles by scan.

Retries are at-least-once with reconciled effects; exactly-once is not claimed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, cast
import uuid

from ..ports import ContractError, ErrorCode

Clock = Callable[[], datetime]


def operation_id(plan_sha256: str, stage: str, kind: str, key: str) -> str:
    """Stable id of one operation (same plan + stage + kind + key gives the same id on every attempt)."""
    return hashlib.sha256(json.dumps([plan_sha256, stage, kind, key]).encode()).hexdigest()[:24]


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:8]}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class Lease:
    """File lease. Use :meth:`acquire`, then :meth:`check` before each batch of writes."""

    def __init__(self, path: Path, clock: Clock, seconds: float = 120.0) -> None:
        self.path = Path(path)
        self.clock = clock
        self.seconds = seconds
        self.token: str | None = None

    def _read(self) -> dict[str, Any] | None:
        try:
            return cast(dict[str, Any], json.loads(self.path.read_text(encoding="utf-8")))  # Any: JSON payload
        except FileNotFoundError:
            return None
        except ValueError:
            return {"token": None, "expires_at": "1970-01-01T00:00:00+00:00", "generation": 0}

    def _write(self, generation: int) -> None:
        body = {"token": self.token, "generation": generation,
                "expires_at": (self.clock() + timedelta(seconds=self.seconds)).isoformat()}
        _atomic_write(self.path, json.dumps(body))

    def acquire(self) -> Lease:
        """Take the lease or raise ``CONFLICT`` while another unexpired owner holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.token = uuid.uuid4().hex
        current = self._read()
        if current is None:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise ContractError(ErrorCode.CONFLICT, "another import holds the lease") from None
            os.close(fd)
            self._write(1)
            return self
        if datetime.fromisoformat(current["expires_at"]) > self.clock():
            raise ContractError(ErrorCode.CONFLICT, "another import holds the lease")
        self._write(int(current.get("generation", 0)) + 1)
        return self

    def check(self) -> None:
        """Renew; raise ``CONFLICT`` if this owner no longer holds the lease."""
        current = self._read()
        if current is None or current.get("token") != self.token:
            raise ContractError(ErrorCode.CONFLICT, "the import lease was taken over; stopping")
        self._write(int(current["generation"]))

    def release(self) -> None:
        """Release if still owned."""
        current = self._read()
        if current is not None and current.get("token") == self.token:
            self.path.unlink(missing_ok=True)


class ImportJournal:
    """Append-only operation journal for one plan."""

    def __init__(self, path: Path, clock: Clock) -> None:
        self.path = Path(path)
        self.clock = clock

    def append(self, event: str, **fields: Any) -> None:
        """Durably append one record."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": self.clock().isoformat(), "event": event, **fields}, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def records(self) -> list[dict[str, Any]]:
        """All records (a torn last line from a crash is ignored)."""
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def started(self) -> bool:
        """Whether a run for this plan has begun (resume rather than base-revision check)."""
        return any(r.get("event") == "run_started" for r in self.records())

    def done_ids(self) -> set[str]:
        """Operation ids journaled ``done``."""
        return {r["op_id"] for r in self.records() if r.get("event") == "done"}
