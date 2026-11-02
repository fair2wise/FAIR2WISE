"""``/v1/jobs`` routes over ``F2WOperations`` / ``JobService`` (policies V01-V09).

``GET /v1/jobs/{job_id}/stream`` is Server-Sent Events in the legacy framing
(``event:``/``data:`` LF blocks, as ``/chat/stream``) with :class:`JobEvent` payloads:

* ``event: job_event`` per durable event, with ``id: <sequence>``; a reconnecting client
  sends ``Last-Event-ID`` (or ``?after=``) and gets only later events;
* ``event: end`` once, with ``{"state", "reason"}``: ``terminal`` (succeeded, failed,
  cancelled), ``waiting`` (awaiting review or paused: nothing moves until someone acts),
  ``timeout`` (``max_seconds`` reached) or ``denied`` (visibility lost mid-stream);
* ``event: job_log`` per log line (:meth:`JobStore.append_log`), with **no** ``id:`` so ``Last-Event-ID``
  keeps meaning job-event sequences; the line's own ``seq`` is in the data. ``?logs_after=`` skips earlier
  lines (default: all kept lines); a client that reconnects resumes logs through ``GET .../logs?after=``;
* ``event: error`` with ``{"code", "message"}`` if the stream cannot continue.

The context is re-resolved before every poll, so a sign-out or revocation ends the stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import time
from typing import Any

from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..modules.contracts.context import RequestContext
from ..modules.contracts.errors import ContractError
from ..modules.contracts.jobs import TERMINAL_STATES, JobState
from ..modules.security.authorization import Unauthenticated
from .auth import request_context
from .operations import job_event_json, job_log_json
from .routes_common import Prefixed, context, dump, fresh_context, operations, project

#: States in which a job stays until a person acts (the stream ends with ``waiting``).
WAITING_STATES = frozenset({JobState.AWAITING_REVIEW, JobState.PAUSED_BUDGET, JobState.PAUSED_PROVIDER})


class JobSubmitRequest(BaseModel):
    """Body of ``POST /v1/jobs``. The context never comes from the body."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=100)
    inputs: dict[str, Any]
    settings_revision: str | None = Field(default=None, max_length=200)


class JobResumeRequest(BaseModel):
    """Optional body of ``POST /v1/jobs/{job_id}/resume``."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=0)


def _sse(event: str, data: dict[str, Any], *, event_id: int | None = None) -> str:
    """One Server-Sent Event in the legacy framing."""
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def install(app: FastAPI, *, poll_seconds: float = 0.5, max_stream_seconds: float = 300.0) -> FastAPI:
    """Mount the jobs routes on ``app`` (SSE poll interval and maximum stream length are startup values)."""
    router = Prefixed(app, "/v1/jobs")

    @router.post("")
    def submit_job(request: Request, body: JobSubmitRequest,
                   ctx: RequestContext = Depends(fresh_context)) -> dict[str, Any]:
        """Submit a job of a registered kind (idempotent for identical inputs)."""
        handle = operations(request).submit_job(ctx, body.kind, body.inputs,
                                                settings_revision=body.settings_revision)
        return project(request, dump(handle))

    @router.get("")
    def list_jobs(request: Request, state: str | None = Query(default=None, max_length=40),
                  kind: str | None = Query(default=None, max_length=100),
                  limit: int = Query(default=100, ge=1, le=1000),
                  ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Jobs the caller may see, most recently updated first."""
        jobs = operations(request).list_jobs(ctx, state=state, kind=kind, limit=limit)
        return project(request, {"jobs": [dump(j) for j in jobs]})

    @router.get("/{job_id}")
    def get_job(request: Request, job_id: str, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """The job handle."""
        return project(request, dump(operations(request).get_job(ctx, job_id)))

    @router.get("/{job_id}/events")
    def job_events(request: Request, job_id: str, after: int = Query(default=-1, ge=-1),
                   ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Durable events after sequence ``after`` (polling)."""
        events = operations(request).job_events(ctx, job_id, after=after)
        return project(request, {"job_id": job_id, "events": [job_event_json(e) for e in events]})

    @router.get("/{job_id}/logs")
    def job_logs(request: Request, job_id: str, after: int = Query(default=-1, ge=-1),
                 limit: int = Query(default=500, ge=1, le=1000),
                 ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Log lines after ``seq`` ``after`` (polling; the SSE ``job_log`` frames carry the same lines)."""
        logs = operations(request).job_logs(ctx, job_id, after=after, limit=limit)
        return project(request, {"job_id": job_id, "logs": [job_log_json(entry) for entry in logs]})

    @router.get("/{job_id}/progress")
    def job_progress(request: Request, job_id: str, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Per-phase progress."""
        return project(request, operations(request).job_progress(ctx, job_id))

    @router.get("/{job_id}/view")
    def job_view(request: Request, job_id: str, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        """Job status view: staged versus approved versus published (``F2WOperations.job_view``)."""
        return project(request, operations(request).job_view(ctx, job_id))

    @router.post("/{job_id}/cancel")
    def cancel_job(request: Request, job_id: str, ctx: RequestContext = Depends(fresh_context)) -> dict[str, Any]:
        """Cancel now, or stop at the next boundary when a worker holds the job."""
        return project(request, dump(operations(request).cancel_job(ctx, job_id)))

    @router.post("/{job_id}/resume")
    def resume_job(request: Request, job_id: str, body: JobResumeRequest | None = Body(default=None),
                   ctx: RequestContext = Depends(fresh_context)) -> dict[str, Any]:
        """Re-queue a paused, review-resolved or failed job."""
        expected = body.expected_revision if body is not None else None
        return project(request, dump(operations(request).resume_job(ctx, job_id, expected_revision=expected)))

    @router.get("/{job_id}/stream")
    async def stream_job(request: Request, job_id: str, after: int | None = Query(default=None, ge=-1),
                         logs_after: int = Query(default=-1, ge=-1),
                         ctx: RequestContext = Depends(context)) -> StreamingResponse:
        """Server-Sent Events for one job (see the module docstring)."""
        ops = operations(request)
        ops.get_job(ctx, job_id)  # visibility before the stream starts: a hidden job is a plain 404
        start = after
        if start is None:
            header = request.headers.get("last-event-id", "").strip()
            start = int(header) if header.lstrip("-").isdigit() else -1

        async def events() -> AsyncIterator[str]:
            last = start
            last_log = logs_after
            current = ctx
            deadline = time.monotonic() + max_stream_seconds
            while True:
                try:
                    batch = ops.job_events(current, job_id, after=last)
                    handle = ops.get_job(current, job_id)
                    lines = ops.job_logs(current, job_id, after=last_log, limit=1000)
                except ContractError as exc:
                    yield _sse("end", {"state": None, "reason": "denied", "code": exc.code.value})
                    return
                for event in batch:
                    last = event.sequence
                    yield _sse("job_event", project(request, job_event_json(event)), event_id=event.sequence)
                for line in lines:
                    last_log = line.seq
                    yield _sse("job_log", project(request, job_log_json(line)))
                if handle.state in TERMINAL_STATES:
                    yield _sse("end", {"state": handle.state.value, "reason": "terminal"})
                    return
                if handle.state in WAITING_STATES:
                    yield _sse("end", {"state": handle.state.value, "reason": "waiting"})
                    return
                if time.monotonic() >= deadline:
                    yield _sse("end", {"state": handle.state.value, "reason": "timeout"})
                    return
                await asyncio.sleep(poll_seconds)
                if await request.is_disconnected():
                    return
                try:
                    current = await request_context(request)
                except Unauthenticated:
                    yield _sse("end", {"state": None, "reason": "denied", "code": Unauthenticated.code})
                    return
                except ContractError as exc:
                    yield _sse("error", {"code": exc.code.value, "message": "stream stopped"})
                    return

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


__all__ = ("JobResumeRequest", "JobSubmitRequest", "WAITING_STATES", "install")
