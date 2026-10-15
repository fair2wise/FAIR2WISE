"""Durable jobs: operational state, owned leases, replay-safe stages and controlled publication (T10).

The job store is SQLite operational state, separate from the scientific KG. Stage outcomes
(``StageOutcome``) are stored apart from the overall lifecycle (``JobState``); pauses and
waiting for review are job states, never verdicts on a paper.
"""

from .keys import OperationInputs, SecretInKey, stage_key
from .pipeline import (
    AwaitReview,
    JobStopped,
    PauseBudget,
    PauseProvider,
    Pipeline,
    PipelineRunner,
    QuarantineStage,
    StageContext,
    StageResult,
    StageSpec,
)
from .service import JobService
from .store import JobRecord, JobStore, Lease, LeaseLost

__all__ = (
    "AwaitReview",
    "JobRecord",
    "JobService",
    "JobStopped",
    "JobStore",
    "Lease",
    "LeaseLost",
    "OperationInputs",
    "PauseBudget",
    "PauseProvider",
    "Pipeline",
    "PipelineRunner",
    "QuarantineStage",
    "SecretInKey",
    "StageContext",
    "StageResult",
    "StageSpec",
    "stage_key",
)
