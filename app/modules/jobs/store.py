"""Durable job store: leases, stage records, units, effects and versioned events (J-04, plan 11).

This is *operational* state, separate from the scientific KG. Every mutation of a job or
stage names the :class:`Lease` it acts under and checks it inside the same transaction as the
write: a stale owner (lease expired, or taken over by a newer owner with a higher fence)
cannot apply a later mutation even if its process is still alive. The job ``revision`` counter
rises on every mutation so API-level callers can compare-and-swap.

Job state and stage outcome are stored separately (plan 11.1). Events are written in the same
transaction as the change they describe, so the durable audit trail and the state cannot
diverge.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/ingest_journal.py (``IngestJournal``)
(BSD-3-Clause): owned leases, heartbeats, stop flags, per-operation stage checkpoints and
recovery of running jobs whose lease expired.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..contracts.errors import ContractError, ErrorCode
from ..contracts.jobs import JOB_EVENT_VERSION, TERMINAL_STATES, JobEvent, JobHandle, JobState, StageOutcome
from .keys import OperationInputs, canonical_json
from .sqlite import OperationalDB

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, owner TEXT NOT NULL, token TEXT NOT NULL, "
    "fence INTEGER NOT NULL, expires_at REAL NOT NULL);\n",
    "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, "
    "inputs TEXT NOT NULL, created_by TEXT NOT NULL, settings_revision TEXT, submitted_at REAL NOT NULL, "
    "updated_at REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 1, stop_requested INTEGER NOT NULL DEFAULT 0, "
    "pause_reason TEXT NOT NULL DEFAULT '', last_progress_at REAL);\n",
    "CREATE TABLE IF NOT EXISTS job_stages (job_id TEXT NOT NULL, stage TEXT NOT NULL, position INTEGER NOT NULL, "
    "status TEXT NOT NULL, op_key TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, "
    "error_code TEXT, error TEXT NOT NULL DEFAULT '', intent_at REAL, completed_at REAL, "
    "effects TEXT NOT NULL DEFAULT '[]', artifact_refs TEXT NOT NULL DEFAULT '[]', "
    "progress TEXT NOT NULL DEFAULT '{}', result TEXT, PRIMARY KEY(job_id, stage));\n",
    "CREATE TABLE IF NOT EXISTS job_units (job_id TEXT NOT NULL, stage TEXT NOT NULL, unit_id TEXT NOT NULL, "
    "unit_key TEXT NOT NULL, status TEXT NOT NULL, result TEXT, error TEXT NOT NULL DEFAULT '', "
    "updated_at REAL NOT NULL, PRIMARY KEY(job_id, stage, unit_id));\n",
    "CREATE TABLE IF NOT EXISTS job_effects (job_id TEXT NOT NULL, stage TEXT NOT NULL, effect_id TEXT NOT NULL, "
    "status TEXT NOT NULL, result TEXT, updated_at REAL NOT NULL, PRIMARY KEY(job_id, stage, effect_id));\n",
    "CREATE TABLE IF NOT EXISTS job_events (job_id TEXT NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL, "
    "detail TEXT NOT NULL DEFAULT '{}', at REAL NOT NULL, PRIMARY KEY(job_id, sequence));\n",
)

#: Allowed lifecycle transitions. Terminal states have none except an explicit operator retry.
TRANSITIONS: Mapping[JobState, frozenset] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.CANCELLED}),
    JobState.RUNNING: frozenset(
        {
            JobState.QUEUED,
            JobState.PAUSED_BUDGET,
            JobState.PAUSED_PROVIDER,
            JobState.AWAITING_REVIEW,
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
        }
    ),
    JobState.PAUSED_BUDGET: frozenset({JobState.QUEUED, JobState.CANCELLED}),
    JobState.PAUSED_PROVIDER: frozenset({JobState.QUEUED, JobState.CANCELLED}),
    JobState.AWAITING_REVIEW: frozenset({JobState.QUEUED, JobState.CANCELLED}),
    JobState.FAILED: frozenset({JobState.QUEUED}),
    JobState.SUCCEEDED: frozenset(),
    JobState.CANCELLED: frozenset(),
}


class LeaseLost(ContractError):
    """The caller no longer owns the lease it acts under (expired or taken over)."""

    def __init__(self, resource: str) -> None:
        super().__init__(
            ErrorCode.CONFLICT, f"lease on {resource} is no longer held by this owner", retryable=False
        )
        self.resource = resource


class InvalidTransition(ContractError):
    """The requested job state change is not allowed from the current state."""

    def __init__(self, old: JobState, new: JobState) -> None:
        super().__init__(ErrorCode.CONFLICT, f"job cannot move from {old.value} to {new.value}", retryable=False)


@dataclass(frozen=True)
class Lease:
    """Proof of ownership of one resource. ``fence`` rises on every acquisition."""

    resource: str
    owner: str
    token: str
    fence: int
    expires_at: float


@dataclass(frozen=True)
class JobRecord:
    """One job row."""

    job_id: str
    kind: str
    state: JobState
    inputs: OperationInputs
    created_by: str
    settings_revision: Optional[str]
    submitted_at: float
    updated_at: float
    revision: int
    stop_requested: bool
    pause_reason: str
    last_progress_at: Optional[float]

    def handle(self) -> JobHandle:
        """The public handle."""
        return JobHandle(
            job_id=self.job_id,
            kind=self.kind,
            state=self.state,
            submitted_at=_dt(self.submitted_at),
            settings_revision=self.settings_revision,
        )


@dataclass(frozen=True)
class StageRecord:
    """One stage row."""

    job_id: str
    stage: str
    position: int
    status: StageOutcome
    op_key: str
    attempts: int
    error_code: Optional[ErrorCode]
    error: str
    intent_at: Optional[float]
    completed_at: Optional[float]
    effects: Tuple[str, ...]
    artifact_refs: Tuple[str, ...]
    progress: Dict[str, int]
    result: Optional[Dict[str, Any]]


def _dt(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _json(value: Any) -> str:
    return canonical_json(value)


class JobStore:
    """SQLite job store.

    Args:
        path: Database file or ``":memory:"``.
        lease_seconds: Default lease length.
        clock: Epoch seconds, injectable.
        db: An existing :class:`OperationalDB` to share (publication state lives beside the jobs).
    """

    def __init__(
        self,
        path: Any = ":memory:",
        *,
        lease_seconds: float = 120.0,
        clock: Callable[[], float] = time.time,
        db: Optional[OperationalDB] = None,
    ) -> None:
        self.db = db or OperationalDB(path)
        self.lease_seconds = lease_seconds
        self.clock = clock
        with self.db.tx() as con:
            for statement in SCHEMA:
                con.execute(statement.rstrip(";\n"))

    # --- leases -----------------------------------------------------------------------

    def acquire_lease(self, resource: str, owner: str, ttl: Optional[float] = None) -> Optional[Lease]:
        """Take the lease on ``resource`` unless another owner holds an unexpired one."""
        now = self.clock()
        with self.db.tx() as con:
            row = con.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
            if row is not None and row["expires_at"] > now:
                return None
            fence = (row["fence"] if row is not None else 0) + 1
            lease = Lease(resource, owner, uuid.uuid4().hex, fence, now + (ttl or self.lease_seconds))
            con.execute(
                "INSERT INTO leases VALUES (?, ?, ?, ?, ?) ON CONFLICT(resource) DO UPDATE SET "
                "owner=excluded.owner, "
                "token=excluded.token, fence=excluded.fence, expires_at=excluded.expires_at",
                (resource, lease.owner, lease.token, lease.fence, lease.expires_at),
            )
        return lease

    def _check_lease(self, con: Any, lease: Lease) -> None:
        row = con.execute(
            "SELECT token, fence, expires_at FROM leases WHERE resource=?", (lease.resource,)
        ).fetchone()
        if (
            row is None
            or row["token"] != lease.token
            or row["fence"] != lease.fence
            or row["expires_at"] < self.clock()
        ):
            raise LeaseLost(lease.resource)

    def check_lease(self, lease: Lease) -> None:
        """Raise :class:`LeaseLost` unless ``lease`` is still the current, unexpired owner."""
        with self.db.tx(immediate=False) as con:
            self._check_lease(con, lease)

    def renew_lease(self, lease: Lease, ttl: Optional[float] = None) -> Lease:
        """Extend the lease; refused once expired or superseded (a stale owner cannot revive it)."""
        with self.db.tx() as con:
            self._check_lease(con, lease)
            expires = self.clock() + (ttl or self.lease_seconds)
            con.execute("UPDATE leases SET expires_at=? WHERE resource=?", (expires, lease.resource))
        return Lease(lease.resource, lease.owner, lease.token, lease.fence, expires)

    def release_lease(self, lease: Lease) -> None:
        """Give the lease up (a no-op when it was already lost)."""
        with self.db.tx() as con:
            con.execute(
                "UPDATE leases SET expires_at=0 WHERE resource=? AND token=?", (lease.resource, lease.token)
            )

    def lease_holder(self, resource: str) -> Optional[Tuple[str, float]]:
        """``(owner, expires_at)`` of an unexpired lease, else ``None``."""
        row = self.db.one("SELECT owner, expires_at FROM leases WHERE resource=?", (resource,))
        return (row["owner"], row["expires_at"]) if row is not None and row["expires_at"] > self.clock() else None

    # --- jobs -------------------------------------------------------------------------

    @staticmethod
    def job_resource(job_id: str) -> str:
        """Lease resource name of a job."""
        return f"job:{job_id}"

    def _record(self, row: Any) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            kind=row["kind"],
            state=JobState(row["state"]),
            inputs=OperationInputs.from_dict(json.loads(row["inputs"])),
            created_by=row["created_by"],
            settings_revision=row["settings_revision"],
            submitted_at=row["submitted_at"],
            updated_at=row["updated_at"],
            revision=row["revision"],
            stop_requested=bool(row["stop_requested"]),
            pause_reason=row["pause_reason"],
            last_progress_at=row["last_progress_at"],
        )

    def submit(
        self, kind: str, inputs: OperationInputs, *, created_by: str, settings_revision: Optional[str] = None
    ) -> JobRecord:
        """Register a job; the same kind and inputs return the existing job (idempotent submit)."""
        job_id = inputs.job_identity(kind)
        now = self.clock()
        with self.db.tx() as con:
            if con.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone() is None:
                con.execute(
                    "INSERT INTO jobs (job_id, kind, state, inputs, created_by, settings_revision, submitted_at, "
                    "updated_at) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)",
                    (job_id, kind, _json(inputs.as_dict()), created_by, settings_revision, now, now),
                )
                self._emit(con, job_id, "submit", StageOutcome.PENDING, JobState.QUEUED, detail={"by": created_by})
        return self.get(job_id)

    def get(self, job_id: str) -> JobRecord:
        """The job, or ``NOT_FOUND``."""
        row = self.db.one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"no such job {job_id}")
        return self._record(row)

    def list_jobs(
        self, *, state: Optional[JobState] = None, kind: Optional[str] = None, limit: int = 100
    ) -> List[JobRecord]:
        """Most recently updated jobs, optionally filtered."""
        clauses, params = [], []
        if state is not None:
            clauses.append("state=?")
            params.append(state.value)
        if kind is not None:
            clauses.append("kind=?")
            params.append(kind)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM jobs{where} ORDER BY updated_at DESC, job_id LIMIT ?",  # noqa: S608
            (*params, max(1, min(limit, 1000))),
        )
        return [self._record(r) for r in rows]

    def claim_next(
        self, owner: str, *, kinds: Optional[Sequence[str]] = None, ttl: Optional[float] = None
    ) -> Optional[Tuple[Lease, JobRecord]]:
        """Lease the next runnable job: ``queued``, or ``running`` whose owner's lease expired."""
        now = self.clock()
        rows = self.db.query(
            "SELECT job_id, kind, state FROM jobs WHERE state IN ('queued', 'running') "
            "ORDER BY submitted_at, job_id"
        )
        for row in rows:
            if kinds is not None and row["kind"] not in kinds:
                continue
            lease = self.acquire_lease(self.job_resource(row["job_id"]), owner, ttl)
            if lease is None:
                continue
            with self.db.tx() as con:
                current = con.execute("SELECT state FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()
                if current["state"] not in ("queued", "running"):
                    con.execute(
                        "UPDATE leases SET expires_at=0 WHERE resource=? AND token=?",
                        (lease.resource, lease.token),
                    )
                    continue
                recovered = current["state"] == "running"
                con.execute(
                    "UPDATE jobs SET state='running', updated_at=?, revision=revision+1, "
                    "pause_reason='' WHERE job_id=?",
                    (now, row["job_id"]),
                )
                self._emit(
                    con,
                    row["job_id"],
                    "claim",
                    StageOutcome.PENDING,
                    JobState.RUNNING,
                    detail={"owner": owner, "fence": lease.fence, "recovered_after_expiry": recovered},
                )
            return lease, self.get(row["job_id"])
        return None

    def set_state(
        self,
        lease: Lease,
        job_id: str,
        new: JobState,
        *,
        stage: str = "job",
        outcome: StageOutcome = StageOutcome.PENDING,
        reason: str = "",
        error_code: Optional[ErrorCode] = None,
    ) -> JobRecord:
        """Move the job to ``new`` under ``lease`` (validated against :data:`TRANSITIONS`)."""
        with self.db.tx() as con:
            self._check_lease(con, lease)
            row = con.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            old = JobState(row["state"])
            if new is not old and new not in TRANSITIONS[old]:
                raise InvalidTransition(old, new)
            con.execute(
                "UPDATE jobs SET state=?, updated_at=?, revision=revision+1, pause_reason=? WHERE job_id=?",
                (new.value, self.clock(), reason if new is not JobState.RUNNING else "", job_id),
            )
            self._emit(con, job_id, stage, outcome, new, error_code=error_code, detail={"reason": reason})
        return self.get(job_id)

    def release_to_queue(self, lease: Lease, job_id: str, reason: str) -> None:
        """Give a running job back (graceful shutdown): ``running -> queued`` and release the lease."""
        self.set_state(lease, job_id, JobState.QUEUED, reason=reason)
        self.release_lease(lease)

    def transition_without_lease(
        self, job_id: str, new: JobState, *, reason: str, actor: str, expected_revision: Optional[int] = None
    ) -> JobRecord:
        """Operator/service transition for a job nobody is running (cancel, resume, retry).

        Refused while another owner holds a live lease, or when ``expected_revision`` is stale.
        """
        with self.db.tx() as con:
            row = con.execute("SELECT state, revision FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ContractError(ErrorCode.NOT_FOUND, f"no such job {job_id}")
            if expected_revision is not None and row["revision"] != expected_revision:
                raise ContractError(ErrorCode.CONFLICT, "job changed since it was read", retryable=True)
            if self.lease_holder(self.job_resource(job_id)) is not None:
                raise ContractError(ErrorCode.CONFLICT, "job is owned by a running worker", retryable=True)
            old = JobState(row["state"])
            if new not in TRANSITIONS[old]:
                raise InvalidTransition(old, new)
            con.execute(
                "UPDATE jobs SET state=?, updated_at=?, revision=revision+1, stop_requested=0, "
                "pause_reason='' WHERE job_id=?",
                (new.value, self.clock(), job_id),
            )
            self._emit(con, job_id, "job", StageOutcome.PENDING, new, detail={"reason": reason, "actor": actor})
        return self.get(job_id)

    def request_stop(self, job_id: str, *, actor: str) -> None:
        """Ask a running job to stop at its next boundary (J-04 stop flag)."""
        with self.db.tx() as con:
            con.execute("UPDATE jobs SET stop_requested=1, revision=revision+1 WHERE job_id=?", (job_id,))
            row = con.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is not None:
                self._emit(
                    con,
                    job_id,
                    "job",
                    StageOutcome.PENDING,
                    JobState(row["state"]),
                    detail={"stop_requested_by": actor},
                )

    def stop_requested(self, job_id: str) -> bool:
        """Whether a stop was requested."""
        row = self.db.one("SELECT stop_requested FROM jobs WHERE job_id=?", (job_id,))
        return bool(row and row["stop_requested"])

    # --- stages -----------------------------------------------------------------------

    def _stage(self, row: Any) -> StageRecord:
        return StageRecord(
            job_id=row["job_id"],
            stage=row["stage"],
            position=row["position"],
            status=StageOutcome(row["status"]),
            op_key=row["op_key"],
            attempts=row["attempts"],
            error_code=ErrorCode(row["error_code"]) if row["error_code"] else None,
            error=row["error"],
            intent_at=row["intent_at"],
            completed_at=row["completed_at"],
            effects=tuple(json.loads(row["effects"])),
            artifact_refs=tuple(json.loads(row["artifact_refs"])),
            progress=json.loads(row["progress"]),
            result=json.loads(row["result"]) if row["result"] else None,
        )

    def stages(self, job_id: str) -> List[StageRecord]:
        """Stage rows in pipeline order."""
        return [
            self._stage(r)
            for r in self.db.query("SELECT * FROM job_stages WHERE job_id=? ORDER BY position", (job_id,))
        ]

    def stage(self, job_id: str, stage: str) -> Optional[StageRecord]:
        """One stage row or ``None``."""
        row = self.db.one("SELECT * FROM job_stages WHERE job_id=? AND stage=?", (job_id, stage))
        return self._stage(row) if row is not None else None

    def begin_stage(self, lease: Lease, job_id: str, stage: str, position: int, op_key: str) -> StageRecord:
        """Record the *intent* to run a stage (attempt count rises) before any effect."""
        now = self.clock()
        with self.db.tx() as con:
            self._check_lease(con, lease)
            con.execute(
                "INSERT INTO job_stages (job_id, stage, position, status, op_key, attempts, intent_at) "
                "VALUES (?, ?, ?, 'running', ?, 1, ?) ON CONFLICT(job_id, stage) DO UPDATE SET status='running', "
                "op_key=excluded.op_key, attempts=attempts+1, intent_at=excluded.intent_at, error_code=NULL, "
                "error='', position=excluded.position",
                (job_id, stage, position, op_key, now),
            )
            attempts = con.execute(
                "SELECT attempts FROM job_stages WHERE job_id=? AND stage=?", (job_id, stage)
            ).fetchone()[0]
            self._emit(con, job_id, stage, StageOutcome.RUNNING, JobState.RUNNING, retry=attempts - 1)
        return self.stage(job_id, stage)  # type: ignore[return-value]

    def complete_stage(
        self,
        lease: Lease,
        job_id: str,
        stage: str,
        op_key: str,
        *,
        effects: Sequence[str] = (),
        artifact_refs: Sequence[str] = (),
        progress: Optional[Mapping[str, int]] = None,
        result: Optional[Mapping[str, Any]] = None,
        outcome: StageOutcome = StageOutcome.COMPLETED,
    ) -> None:
        """Durable completion record, written only after the stage's effects were verified."""
        with self.db.tx() as con:
            self._check_lease(con, lease)
            con.execute(
                "UPDATE job_stages SET status=?, op_key=?, completed_at=?, effects=?, "
                "artifact_refs=?, progress=?, "
                "result=?, error_code=NULL, error='' WHERE job_id=? AND stage=?",
                (
                    outcome.value,
                    op_key,
                    self.clock(),
                    _json(list(effects)),
                    _json(list(artifact_refs)),
                    _json(dict(progress or {})),
                    _json(dict(result)) if result is not None else None,
                    job_id,
                    stage,
                ),
            )
            self._emit(
                con,
                job_id,
                stage,
                outcome,
                JobState.RUNNING,
                progress=progress,
                artifact_refs=artifact_refs,
                retry=self._retries(con, job_id, stage),
            )

    def mark_stage(
        self,
        lease: Lease,
        job_id: str,
        stage: str,
        outcome: StageOutcome,
        *,
        error_code: Optional[ErrorCode] = None,
        error: str = "",
        job_state: JobState = JobState.RUNNING,
    ) -> None:
        """Record a non-completing outcome (failed, quarantined, or back to pending for a pause)."""
        with self.db.tx() as con:
            self._check_lease(con, lease)
            con.execute(
                "UPDATE job_stages SET status=?, error_code=?, error=? WHERE job_id=? AND stage=?",
                (outcome.value, error_code.value if error_code else None, error[:500], job_id, stage),
            )
            self._emit(
                con,
                job_id,
                stage,
                outcome,
                job_state,
                error_code=error_code,
                retry=self._retries(con, job_id, stage),
                detail={"error": error[:500]},
            )

    def update_progress(self, lease: Lease, job_id: str, stage: str, progress: Mapping[str, int]) -> None:
        """Merge named counters into a running stage and renew the lease."""
        with self.db.tx() as con:
            self._check_lease(con, lease)
            row = con.execute(
                "SELECT progress FROM job_stages WHERE job_id=? AND stage=?", (job_id, stage)
            ).fetchone()
            merged = {**json.loads(row["progress"]), **{k: int(v) for k, v in progress.items()}} if row else {}
            con.execute(
                "UPDATE job_stages SET progress=? WHERE job_id=? AND stage=?", (_json(merged), job_id, stage)
            )
            now = self.clock()
            con.execute("UPDATE jobs SET last_progress_at=?, updated_at=? WHERE job_id=?", (now, now, job_id))
            con.execute(
                "UPDATE leases SET expires_at=? WHERE resource=? AND token=?",
                (now + self.lease_seconds, lease.resource, lease.token),
            )
            self._emit(
                con,
                job_id,
                stage,
                StageOutcome.RUNNING,
                JobState.RUNNING,
                progress=merged,
                retry=self._retries(con, job_id, stage),
            )

    def invalidate(self, lease: Lease, job_id: str, stages: Iterable[str], reason: str) -> None:
        """Reset incompatible stages to ``pending`` (their units and compatible siblings are kept)."""
        with self.db.tx() as con:
            self._check_lease(con, lease)
            for name in stages:
                row = con.execute(
                    "SELECT status FROM job_stages WHERE job_id=? AND stage=?", (job_id, name)
                ).fetchone()
                if row is None or row["status"] == "pending":
                    continue
                con.execute(
                    "UPDATE job_stages SET status='pending', op_key='', completed_at=NULL, effects='[]', "
                    "artifact_refs='[]', progress='{}', result=NULL, error_code=NULL, error='' "
                    "WHERE job_id=? AND stage=?",
                    (job_id, name),
                )
                con.execute("DELETE FROM job_effects WHERE job_id=? AND stage=?", (job_id, name))
                self._emit(
                    con, job_id, name, StageOutcome.PENDING, JobState.RUNNING, detail={"invalidated": reason}
                )

    @staticmethod
    def _retries(con: Any, job_id: str, stage: str) -> int:
        row = con.execute("SELECT attempts FROM job_stages WHERE job_id=? AND stage=?", (job_id, stage)).fetchone()
        return max(0, (row[0] if row else 1) - 1)

    # --- units (chunk-level retention) -------------------------------------------------

    def get_unit(self, job_id: str, stage: str, unit_id: str, unit_key: str) -> Optional[Dict[str, Any]]:
        """The settled result of a unit when its key still matches, else ``None``."""
        row = self.db.one(
            "SELECT unit_key, status, result FROM job_units WHERE job_id=? AND stage=? AND unit_id=?",
            (job_id, stage, unit_id),
        )
        if row is None or row["status"] != "done" or row["unit_key"] != unit_key or row["result"] is None:
            return None
        return json.loads(row["result"])

    def save_unit(
        self,
        lease: Lease,
        job_id: str,
        stage: str,
        unit_id: str,
        unit_key: str,
        result: Optional[Mapping[str, Any]],
        error: str = "",
    ) -> None:
        """Record a unit: a settled ``result`` (``done``) or an ``error`` (``failed``, retried on resume)."""
        with self.db.tx() as con:
            self._check_lease(con, lease)
            con.execute(
                "INSERT INTO job_units VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, stage, unit_id) "
                "DO UPDATE "
                "SET unit_key=excluded.unit_key, status=excluded.status, result=excluded.result, "
                "error=excluded.error, updated_at=excluded.updated_at",
                (
                    job_id,
                    stage,
                    unit_id,
                    unit_key,
                    "done" if result is not None else "failed",
                    _json(dict(result)) if result is not None else None,
                    error[:500],
                    self.clock(),
                ),
            )

    def unit_counts(
        self, job_id: str, stage: str, valid_keys: Optional[Mapping[str, str]] = None
    ) -> Dict[str, int]:
        """``done``/``failed`` counts; with ``valid_keys`` (unit id -> key) stale-keyed units count as missing."""
        rows = self.db.query(
            "SELECT unit_id, unit_key, status FROM job_units WHERE job_id=? AND stage=?", (job_id, stage)
        )
        done = failed = 0
        for row in rows:
            if valid_keys is not None and valid_keys.get(row["unit_id"]) != row["unit_key"]:
                continue
            done += row["status"] == "done"
            failed += row["status"] == "failed"
        return {"done": done, "failed": failed}

    # --- effects ----------------------------------------------------------------------

    def effect(self, job_id: str, stage: str, effect_id: str) -> Optional[Tuple[str, Optional[Dict[str, Any]]]]:
        """``(status, result)`` of a stable-ID effect, or ``None`` if never started."""
        row = self.db.one(
            "SELECT status, result FROM job_effects WHERE job_id=? AND stage=? AND effect_id=?",
            (job_id, stage, effect_id),
        )
        if row is None:
            return None
        return row["status"], (json.loads(row["result"]) if row["result"] else None)

    def record_effect(
        self,
        lease: Lease,
        job_id: str,
        stage: str,
        effect_id: str,
        status: str,
        result: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Write an effect's ``intent`` before it runs and ``done`` after it is verified."""
        with self.db.tx() as con:
            self._check_lease(con, lease)
            con.execute(
                "INSERT INTO job_effects VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, stage, effect_id) "
                "DO UPDATE "
                "SET status=excluded.status, result=excluded.result, updated_at=excluded.updated_at",
                (
                    job_id,
                    stage,
                    effect_id,
                    status,
                    _json(dict(result)) if result is not None else None,
                    self.clock(),
                ),
            )

    # --- events -----------------------------------------------------------------------

    def _emit(
        self,
        con: Any,
        job_id: str,
        stage: str,
        outcome: StageOutcome,
        state: JobState,
        *,
        progress: Optional[Mapping[str, int]] = None,
        error_code: Optional[ErrorCode] = None,
        retry: int = 0,
        artifact_refs: Sequence[str] = (),
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        now = self.clock()
        sequence = con.execute(
            "SELECT COALESCE(MAX(sequence), -1) + 1 FROM job_events WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        event = JobEvent(
            version=JOB_EVENT_VERSION,
            job_id=job_id,
            sequence=sequence,
            stage=stage,
            stage_outcome=outcome,
            state=state,
            progress={k: int(v) for k, v in (progress or {}).items()},
            error_code=error_code,
            retry_count=retry,
            artifact_refs=tuple(artifact_refs),
            at=_dt(now),
        )
        con.execute(
            "INSERT INTO job_events VALUES (?, ?, ?, ?, ?)",
            (job_id, sequence, event.model_dump_json(), _json(dict(detail or {})), now),
        )

    def events(self, job_id: str, *, after: int = -1, limit: int = 500) -> List[JobEvent]:
        """Durable events with ``sequence > after``."""
        rows = self.db.query(
            "SELECT event FROM job_events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (job_id, after, max(1, min(limit, 5000))),
        )
        return [JobEvent.model_validate_json(r["event"]) for r in rows]

    def event_details(self, job_id: str) -> List[Dict[str, Any]]:
        """Non-contract audit detail (actor, reason) beside each event, for operators."""
        rows = self.db.query("SELECT sequence, detail FROM job_events WHERE job_id=? ORDER BY sequence", (job_id,))
        return [{"sequence": r["sequence"], **json.loads(r["detail"])} for r in rows]

    def close(self) -> None:
        """Close the database."""
        self.db.close()


__all__ = (
    "InvalidTransition",
    "JobRecord",
    "JobStore",
    "Lease",
    "LeaseLost",
    "SCHEMA",
    "StageRecord",
    "TERMINAL_STATES",
    "TRANSITIONS",
)
