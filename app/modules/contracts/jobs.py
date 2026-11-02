"""Durable job handles and events.

Stage outcome and overall lifecycle are separate (plan section 11.1): a paused or
failed stage is not a statement about the paper's science. Events carry artifact
references, never large PDF or base64 payloads.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .errors import ErrorCode

JOB_EVENT_VERSION = 1


class JobState(StrEnum):
    """Overall job lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_BUDGET = "paused_budget"
    PAUSED_PROVIDER = "paused_provider"
    AWAITING_REVIEW = "awaiting_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})


class StageOutcome(StrEnum):
    """Outcome of one pipeline stage, independent of the job lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED_COMPATIBLE = "skipped_compatible"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class JobHandle(BaseModel):
    """Reference returned when a job is submitted.

    Attributes:
        job_id: Stable job identifier.
        kind: Job type (for example ``"ingest"``, ``"build_snapshot"``, ``"import"``).
        state: Current lifecycle state.
        submitted_at: Submission time.
        settings_revision: Resolved non-secret generation settings revision, if recorded (O03).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    kind: str
    state: JobState
    submitted_at: datetime
    settings_revision: str | None = None


class JobEvent(BaseModel):
    """One versioned progress event for a job.

    Attributes:
        version: Event schema version.
        job_id: Job this event belongs to.
        sequence: Monotonic sequence number within the job.
        stage: Stage identifier (for example ``"parse"``).
        stage_outcome: Outcome of ``stage`` at this event.
        state: Overall job state after this event.
        progress: Named counters (for example ``{"chunks_done": 12, "chunks_total": 40}``).
        error_code: Error code when the stage failed.
        retry_count: Retries so far for this stage.
        artifact_refs: Durable artifacts produced so far.
        at: Event time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = JOB_EVENT_VERSION
    job_id: str
    sequence: int = Field(ge=0)
    stage: str
    stage_outcome: StageOutcome
    state: JobState
    progress: dict[str, int] = Field(default_factory=dict)
    error_code: ErrorCode | None = None
    retry_count: int = Field(default=0, ge=0)
    artifact_refs: tuple[str, ...] = ()
    at: datetime
