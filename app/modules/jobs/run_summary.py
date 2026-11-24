"""One job's live status for a person watching it (``GET /v1/jobs/{id}/progress`` key ``summary``).

Stage and phase, papers done of total, the paper in progress, KG growth counters, elapsed time, a final outcome and
the size of the log; the compact form is also on ``GET /v1/jobs?detail=summary``.

Everything is read from the job store (the worker is another process that writes into the same database), so a
reloaded page sees the same thing a page that never closed saw.

**Outcome.** ``outcome`` is the job state, except for a job that ``failed``: it is ``partially_failed`` when at
least one paper (unit) of the run finished and at least one paper failed, and ``failed`` otherwise (every paper
failed, or the run stopped early, for example CBORG refused this machine's address; what finished before the stop is
kept and is counted in ``papers.done``). No job state is added; this is derived from the per-paper outcomes.
Importing this module has no side effects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..contracts.jobs import TERMINAL_STATES, JobState
from .process_support import scrub
from .store import JobRecord, JobStore, StageRecord

#: Most failed papers listed in a summary (the count is always exact).
MAX_FAILED_LISTED = 50
#: Job input parameters shown in a summary (a fixed allowlist: ``confirmation`` and anything else stay out).
_SHOWN_PARAMS = ("bundle", "model", "backend", "schema", "topic")
#: Counters of the stage that are KG growth (shown under ``growth``, not under ``counters``).
GROWTH_KEYS = ("entities_new", "entities_merged", "relations_new", "relations_merged", "entities_total",
               "relations_total")


def _iso(value: float | None) -> str | None:
    return None if value is None else datetime.fromtimestamp(value, UTC).isoformat()


def _active_stage(stages: list[StageRecord]) -> StageRecord | None:
    """The stage being shown: the running one, else the last one that has begun."""
    for row in stages:
        if row.status.value == "running":
            return row
    begun = [row for row in stages if row.intent_at is not None]
    return begun[-1] if begun else (stages[-1] if stages else None)


def derive_outcome(state: JobState, done: int, failed: int) -> str:
    """The outcome word of a job (see the module docstring for ``partially_failed``)."""
    if state is JobState.FAILED and done >= 1 and failed >= 1:
        return "partially_failed"
    return state.value


def run_summary(store: JobStore, job: JobRecord, *, detail: bool = True) -> dict[str, Any]:
    """Build the summary of ``job``.

    Args:
        store: The job store.
        job: The job (already checked visible by the caller).
        detail: Include the list of failed papers (``False`` for the compact form used in lists).

    Returns:
        A JSON-ready dict: ``outcome``, ``state``, ``started_at``, ``finished_at``, ``elapsed_seconds``, ``stage``
        (``name``, ``status``, ``phase``), ``current_paper`` (display name or ``None``; only while running),
        ``papers`` (``total``, ``done``, ``failed``, ``skipped`` and, with ``detail``, ``failed_list``), ``growth``,
        ``counters`` (the stage's other counters), ``params`` (bundle, model, backend, schema, topic), ``error``
        (scrubbed) and ``logs`` (``kept``, ``total``, ``cap``).
    """
    stages = store.stages(job.job_id)
    active = _active_stage(stages)
    progress = dict(active.progress) if active is not None else {}
    units = store.unit_rows(job.job_id, active.stage, limit=5000) if active is not None else []
    failed_units = [u for u in units if u["status"] == "failed"]
    done_units = [u for u in units if u["status"] == "done"]
    done = max(int(progress.get("units_done", 0)), len(done_units))
    failed = len(failed_units)
    total = int(progress.get("units_total", len(units)))
    terminal = job.state in TERMINAL_STATES
    started = active.intent_at if active is not None and active.intent_at else job.submitted_at
    ended = job.updated_at if terminal else store.clock()
    status = store.status(job.job_id) if job.state is JobState.RUNNING else None
    error = next((r.error for r in reversed(stages) if r.error), "")
    params = job.inputs.params if isinstance(job.inputs.params, dict) else {}
    out: dict[str, Any] = {
        "outcome": derive_outcome(job.state, done, failed),
        "state": job.state.value,
        "started_at": _iso(started),
        "finished_at": _iso(job.updated_at) if terminal else None,
        "elapsed_seconds": round(max(ended - started, 0.0), 1),
        "stage": {
            "name": active.stage if active is not None else None,
            "status": active.status.value if active is not None else None,
            "phase": (status or {}).get("phase") or None,
        },
        "current_paper": (status or {}).get("item") or None,
        "papers": {"total": total, "done": done, "failed": failed, "skipped": int(progress.get("skipped", 0))},
        "growth": {key: int(progress[key]) for key in GROWTH_KEYS if key in progress},
        "counters": {k: v for k, v in progress.items() if k not in GROWTH_KEYS},
        "params": {key: params[key] for key in _SHOWN_PARAMS if isinstance(params.get(key), str) and params[key]},
        "error": scrub(error)[:400] or None,
        "logs": store.log_stats(job.job_id),
    }
    if detail:
        out["papers"]["failed_list"] = [
            {"name": Path(str(u["error"]).split(": ", 1)[0]).name or u["unit_id"],
             "reason": scrub(str(u["error"]).split(": ", 1)[1] if ": " in str(u["error"]) else str(u["error"]))[:300]}
            for u in failed_units[:MAX_FAILED_LISTED]
        ]
    return out


__all__ = ("GROWTH_KEYS", "MAX_FAILED_LISTED", "derive_outcome", "run_summary")
