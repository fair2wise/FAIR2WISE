"""Progress telemetry that distinguishes parsing, extraction, validation, review and import (J-08).

There is deliberately no single "percent complete": each phase reports its own stage outcomes
and unit counters, review reports the queue, and import reports plan/approval/receipt progress.
A running job that has not progressed for ``stall_after`` seconds is flagged ``stalled``; a
finished or waiting job never is.

Adapted from als-computing/forge_rsoxs_analysis
als_knowledge_agent/src/als_knowledge_agent/graph/ingest_telemetry.py (``annotate_stall``,
``choose_job``, ``build_telemetry``) (BSD-3-Clause); the donor's model-service probing and
graph-database reads are not ported.
"""

from __future__ import annotations

from typing import Any

from ..contracts.jobs import JobState, StageOutcome
from .pipeline import PHASES, Pipeline
from .store import JobStore

STALL_AFTER_SECONDS = 300.0

_COUNTED = (
    StageOutcome.COMPLETED,
    StageOutcome.SKIPPED_COMPATIBLE,
    StageOutcome.FAILED,
    StageOutcome.QUARANTINED,
    StageOutcome.RUNNING,
    StageOutcome.PENDING,
)


def job_progress(
    store: JobStore, pipeline: Pipeline, job_id: str, *, stall_after: float = STALL_AFTER_SECONDS
) -> dict[str, Any]:
    """Per-phase progress of one job.

    Returns:
        ``{"job_id", "state", "phases": {phase: {"stages": {name: outcome}, "counts": {outcome: n},
        "progress": {counter: n}}}, "stalled", "seconds_since_progress", "pause_reason"}``.
    """
    job = store.get(job_id)
    rows = {r.stage: r for r in store.stages(job_id)}
    phases: dict[str, dict[str, Any]] = {p: {"stages": {}, "counts": {}, "progress": {}} for p in PHASES}
    for spec in pipeline.specs:
        row = rows.get(spec.name)
        outcome = row.status if row is not None else StageOutcome.PENDING
        phase = phases[spec.phase]
        phase["stages"][spec.name] = outcome.value
        phase["counts"][outcome.value] = phase["counts"].get(outcome.value, 0) + 1
        if row is not None:
            for key, value in row.progress.items():
                phase["progress"][f"{spec.name}.{key}"] = value
    now = store.clock()
    marker = job.last_progress_at or job.updated_at
    running = job.state is JobState.RUNNING
    since = max(now - marker, 0.0) if running else None
    return {
        "job_id": job_id,
        "state": job.state.value,
        "phases": {name: data for name, data in phases.items() if data["stages"]},
        "stalled": bool(running and since is not None and since >= stall_after),
        "seconds_since_progress": round(since, 1) if since is not None else None,
        "pause_reason": job.pause_reason,
    }


def summarize(store: JobStore, *, limit: int = 200) -> dict[str, Any]:
    """Counts of jobs by state (staged, paused, awaiting review, failed...) without one completion number."""
    counts: dict[str, int] = {}
    for job in store.list_jobs(limit=limit):
        counts[job.state.value] = counts.get(job.state.value, 0) + 1
    return {"jobs_by_state": counts}


def choose_job(store: JobStore, kind: str | None = None) -> str | None:
    """The job worth showing: a running one, else the most recently updated one."""
    jobs = store.list_jobs(kind=kind, limit=50)
    for job in jobs:
        if job.state is JobState.RUNNING:
            return job.job_id
    return jobs[0].job_id if jobs else None


__all__ = ("STALL_AFTER_SECONDS", "choose_job", "job_progress", "summarize")
