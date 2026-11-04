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

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import sqlite3
import time
from typing import Any, cast
import uuid

from ..contracts.errors import ContractError, ErrorCode
from ..contracts.jobs import JOB_EVENT_VERSION, TERMINAL_STATES, JobEvent, JobHandle, JobState, StageOutcome
from .keys import OperationInputs, canonical_json
from .process_support import scrub
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
    "CREATE TABLE IF NOT EXISTS job_logs (job_id TEXT NOT NULL, seq INTEGER NOT NULL, at REAL NOT NULL, "
    "level TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '', message TEXT NOT NULL, PRIMARY KEY(job_id, seq));\n",
)

#: Longest stored log message (characters, after scrubbing).
LOG_MESSAGE_CHARS = 400
#: Most log rows kept per job; the oldest are dropped first.
LOG_ROWS_PER_JOB = 2000
#: Accepted log levels.
LOG_LEVELS = frozenset({"debug", "info", "warning", "error"})

#: Allowed lifecycle transitions. Terminal states have none except an explicit operator retry.
TRANSITIONS: Mapping[JobState, frozenset[JobState]] = {
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
    settings_revision: str | None
    submitted_at: float
    updated_at: float
    revision: int
    stop_requested: bool
    pause_reason: str
    last_progress_at: float | None

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
    error_code: ErrorCode | None
    error: str
    intent_at: float | None
    completed_at: float | None
    effects: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    progress: dict[str, int]
    result: dict[str, Any] | None


@dataclass(frozen=True)
class JobLog:
    """One free-text log line of a job (``seq`` rises by one per job; ``at`` is epoch seconds)."""

    job_id: str
    seq: int
    at: float
    level: str
    stage: str
    message: str


def _dt(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


def _json(value: object) -> str:
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
        path: object = ":memory:",
        *,
        lease_seconds: float = 120.0,
        clock: Callable[[], float] = time.time,
        db: OperationalDB | None = None,
    ) -> None:
        self.db = db or OperationalDB(path)
        self.lease_seconds = lease_seconds
        self.clock = clock
        with self.db.tx() as con:
            for statement in SCHEMA:
                con.execute(statement.rstrip(";\n"))

    # --- leases -----------------------------------------------------------------------

    def acquire_lease(self, resource: str, owner: str, ttl: float | None = None) -> Lease | None:
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

    def _check_lease(self, con: sqlite3.Connection, lease: Lease, *, allow_expired: bool = False) -> None:
        row = con.execute(
            "SELECT token, fence, expires_at FROM leases WHERE resource=?", (lease.resource,)
        ).fetchone()
        if (
            row is None
            or row["token"] != lease.token
            or row["fence"] != lease.fence
            or (not allow_expired and row["expires_at"] < self.clock())
        ):
            raise LeaseLost(lease.resource)

    def check_lease(self, lease: Lease) -> None:
        """Raise :class:`LeaseLost` unless ``lease`` is still the current, unexpired owner."""
        with self.db.tx(immediate=False) as con:
            self._check_lease(con, lease)

    def renew_lease(self, lease: Lease, ttl: float | None = None, *, allow_expired: bool = False) -> Lease:
        """Extend the lease; refused once expired or superseded (a stale owner cannot revive it).

        ``allow_expired`` is for the owner's own heartbeat only: a lease that lapsed (the process was
        suspended, the clock jumped) but was never taken over still carries this owner's token and fence,
        so it can safely be extended. A lease another owner acquired is refused either way.
        """
        with self.db.tx() as con:
            self._check_lease(con, lease, allow_expired=allow_expired)
            expires = self.clock() + (ttl or self.lease_seconds)
            con.execute("UPDATE leases SET expires_at=? WHERE resource=?", (expires, lease.resource))
        return Lease(lease.resource, lease.owner, lease.token, lease.fence, expires)

    def release_lease(self, lease: Lease) -> None:
        """Give the lease up (a no-op when it was already lost)."""
        with self.db.tx() as con:
            con.execute(
                "UPDATE leases SET expires_at=0 WHERE resource=? AND token=?", (lease.resource, lease.token)
            )

    def lease_holder(self, resource: str) -> tuple[str, float] | None:
        """``(owner, expires_at)`` of an unexpired lease, else ``None``."""
        row = self.db.one("SELECT owner, expires_at FROM leases WHERE resource=?", (resource,))
        return (row["owner"], row["expires_at"]) if row is not None and row["expires_at"] > self.clock() else None

    # --- jobs -------------------------------------------------------------------------

    @staticmethod
    def job_resource(job_id: str) -> str:
        """Lease resource name of a job."""
        return f"job:{job_id}"

    def _record(self, row: sqlite3.Row) -> JobRecord:
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
        self, kind: str, inputs: OperationInputs, *, created_by: str, settings_revision: str | None = None
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
        self, *, state: JobState | None = None, kind: str | None = None, limit: int = 100
    ) -> list[JobRecord]:
        """Most recently updated jobs, optionally filtered."""
        return self.list_jobs_scoped(state=state, kind=kind, limit=limit)

    def list_jobs_scoped(
        self,
        *,
        created_by: str | None = None,
        within_scope: Iterable[str] | None = None,
        state: JobState | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        """Most recently updated jobs, filtered in SQL (C3; T14 Q8).

        Args:
            created_by: Only jobs whose ``created_by`` is this subject; ``None`` for any creator.
            within_scope: Only jobs whose ``inputs.corpus_scope`` is a subset of these corpora (a job
                with an empty scope always qualifies); ``None`` for any scope.
            state: Only jobs in this state; ``None`` for any.
            kind: Only jobs of this kind; ``None`` for any.
            limit: Maximum rows, clamped to 1..1000, applied after every filter.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if created_by is not None:
            clauses.append("created_by=?")
            params.append(created_by)
        if within_scope is not None:
            allowed = sorted(set(within_scope))
            outside = "s.value NOT IN (" + ", ".join("?" * len(allowed)) + ")" if allowed else "1"
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM json_each(jobs.inputs, '$.corpus_scope') AS s "  # noqa: S608 - SQL text is built only from fixed fragments and ? placeholders; values are bound parameters
                f"WHERE s.type != 'null' AND {outside})"
            )
            params.extend(allowed)
        if state is not None:
            clauses.append("state=?")
            params.append(state.value)
        if kind is not None:
            clauses.append("kind=?")
            params.append(kind)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM jobs{where} ORDER BY updated_at DESC, job_id LIMIT ?",  # noqa: S608 - SQL text is built only from fixed fragments and ? placeholders; values are bound parameters
            (*params, max(1, min(limit, 1000))),
        )
        return [self._record(r) for r in rows]

    def claim_next(
        self,
        owner: str,
        *,
        kinds: Sequence[str] | None = None,
        ttl: float | None = None,
        exclude: Collection[str] = (),
    ) -> tuple[Lease, JobRecord] | None:
        """Lease the next runnable job: ``queued``, or ``running`` whose owner's lease expired.

        ``exclude`` lists job ids the caller must not claim (jobs it already runs, or is backing off from).
        """
        now = self.clock()
        rows = self.db.query(
            "SELECT job_id, kind, state FROM jobs WHERE state IN ('queued', 'running') "
            "ORDER BY submitted_at, job_id"
        )
        for row in rows:
            if kinds is not None and row["kind"] not in kinds:
                continue
            if row["job_id"] in exclude:
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
        error_code: ErrorCode | None = None,
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
        self, job_id: str, new: JobState, *, reason: str, actor: str, expected_revision: int | None = None
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

    def _stage(self, row: sqlite3.Row) -> StageRecord:
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

    def stages(self, job_id: str) -> list[StageRecord]:
        """Stage rows in pipeline order."""
        return [
            self._stage(r)
            for r in self.db.query("SELECT * FROM job_stages WHERE job_id=? ORDER BY position", (job_id,))
        ]

    def stage(self, job_id: str, stage: str) -> StageRecord | None:
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
        progress: Mapping[str, int] | None = None,
        result: Mapping[str, Any] | None = None,
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
        error_code: ErrorCode | None = None,
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
    def _retries(con: sqlite3.Connection, job_id: str, stage: str) -> int:
        row = con.execute("SELECT attempts FROM job_stages WHERE job_id=? AND stage=?", (job_id, stage)).fetchone()
        return max(0, (row[0] if row else 1) - 1)

    # --- units (chunk-level retention) -------------------------------------------------

    def get_unit(self, job_id: str, stage: str, unit_id: str, unit_key: str) -> dict[str, Any] | None:
        """The settled result of a unit when its key still matches, else ``None``."""
        row = self.db.one(
            "SELECT unit_key, status, result FROM job_units WHERE job_id=? AND stage=? AND unit_id=?",
            (job_id, stage, unit_id),
        )
        if row is None or row["status"] != "done" or row["unit_key"] != unit_key or row["result"] is None:
            return None
        return cast(dict[str, Any], json.loads(row["result"]))  # Any: JSON payload

    def save_unit(
        self,
        lease: Lease,
        job_id: str,
        stage: str,
        unit_id: str,
        unit_key: str,
        result: Mapping[str, Any] | None,
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
        self, job_id: str, stage: str, valid_keys: Mapping[str, str] | None = None
    ) -> dict[str, int]:
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

    def effect(self, job_id: str, stage: str, effect_id: str) -> tuple[str, dict[str, Any] | None] | None:
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
        result: Mapping[str, Any] | None = None,
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
        con: sqlite3.Connection,
        job_id: str,
        stage: str,
        outcome: StageOutcome,
        state: JobState,
        *,
        progress: Mapping[str, int] | None = None,
        error_code: ErrorCode | None = None,
        retry: int = 0,
        artifact_refs: Sequence[str] = (),
        detail: Mapping[str, Any] | None = None,
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

    def events(self, job_id: str, *, after: int = -1, limit: int = 500) -> list[JobEvent]:
        """Durable events with ``sequence > after``."""
        rows = self.db.query(
            "SELECT event FROM job_events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (job_id, after, max(1, min(limit, 5000))),
        )
        return [JobEvent.model_validate_json(r["event"]) for r in rows]

    def event_details(self, job_id: str) -> list[dict[str, Any]]:
        """Non-contract audit detail (actor, reason) beside each event, for operators."""
        rows = self.db.query("SELECT sequence, detail FROM job_events WHERE job_id=? ORDER BY sequence", (job_id,))
        return [{"sequence": r["sequence"], **json.loads(r["detail"])} for r in rows]

    # --- logs -------------------------------------------------------------------------

    def append_log(self, lease: Lease, job_id: str, stage: str, message: str, level: str = "info") -> JobLog:
        """Append one scrubbed, bounded log line under ``lease`` and drop rows beyond the per-job cap.

        Args:
            lease: The lease the caller holds on the job; checked in the same transaction.
            job_id: The job the line belongs to.
            stage: Name of the running stage (``""`` when none).
            message: Free text; credential-like text is redacted, then cut to ``LOG_MESSAGE_CHARS``.
            level: One of ``debug``, ``info``, ``warning``, ``error``.

        Returns:
            The stored line.

        Raises:
            ValueError: ``level`` is not an accepted level.
            LeaseLost: The lease is no longer current.
        """
        if level not in LOG_LEVELS:
            raise ValueError(f"unknown log level {level!r}")
        text = scrub(message)[:LOG_MESSAGE_CHARS]
        now = self.clock()
        with self.db.tx() as con:
            self._check_lease(con, lease)
            seq = con.execute("SELECT COALESCE(MAX(seq), -1) + 1 FROM job_logs WHERE job_id=?", (job_id,)).fetchone()[0]
            con.execute("INSERT INTO job_logs VALUES (?, ?, ?, ?, ?, ?)", (job_id, seq, now, level, stage, text))
            con.execute("DELETE FROM job_logs WHERE job_id=? AND seq<=?", (job_id, seq - LOG_ROWS_PER_JOB))
        return JobLog(job_id, seq, now, level, stage, text)

    def logs(self, job_id: str, *, after: int = -1, limit: int = 500) -> list[JobLog]:
        """Log lines with ``seq > after`` in ``seq`` order (at most ``limit``, clamped to 1..5000)."""
        rows = self.db.query(
            "SELECT job_id, seq, at, level, stage, message FROM job_logs WHERE job_id=? AND seq>? "
            "ORDER BY seq LIMIT ?",
            (job_id, after, max(1, min(limit, 5000))),
        )
        return [JobLog(r["job_id"], r["seq"], r["at"], r["level"], r["stage"], r["message"]) for r in rows]

    def close(self) -> None:
        """Close the database."""
        self.db.close()


__all__ = (
    "InvalidTransition",
    "JobLog",
    "JobRecord",
    "JobStore",
    "LOG_LEVELS",
    "LOG_MESSAGE_CHARS",
    "LOG_ROWS_PER_JOB",
    "Lease",
    "LeaseLost",
    "SCHEMA",
    "StageRecord",
    "TERMINAL_STATES",
    "TRANSITIONS",
)
