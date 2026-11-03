"""Application service layer for jobs (plan 4.2: FastAPI starts and observes, workers call this).

The API/CLI (T14) call :class:`JobService` to submit, observe, cancel and resume jobs; the
worker executor calls :meth:`JobService.work_one` directly, never a recursive HTTP call. No
route is built here. Every operation takes the server-built :class:`RequestContext`; the
context is never accepted from a request body.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
import threading
from typing import Any

from ..contracts.context import LOCAL_OPERATOR, RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..contracts.jobs import JobEvent, JobHandle, JobState
from ..llm_admission import ProviderAdmission
from ..security.authorization import DeploymentSettings, JobAuthority, StartupConfigError
from .keys import OperationInputs
from .pipeline import Pipeline, PipelineRunner
from .store import JobLog, JobRecord, JobStore, LeaseLost

logger = logging.getLogger(__name__)

#: Stable kind names and the capability that submits them.
DEFAULT_KIND_CAPABILITIES: Mapping[str, str] = {
    "ingest": "ingest.extract",
    "build_snapshot": "json.build",
    "publish": "graph.publish",
    "curation": "curation.approve",
}


@dataclass(frozen=True)
class RegisteredKind:
    """A job kind: its pipeline, the capability to submit it and its runner."""

    kind: str
    pipeline: Pipeline
    capability: str
    runner: PipelineRunner


class JobService:
    """Submit, observe and drive jobs.

    Args:
        store: The durable job store.
        owner: Identity recorded as the lease owner of this process (for example ``worker-<pid>``).
        settings: Startup ``DeploymentSettings`` of this deployment. With them the service wires a
            :class:`~app.modules.security.authorization.JobAuthority` into every registered kind,
            so stages re-authorize the initiating subject before each effect (AUD-01). Without
            them a kind that declares a ``required_capability`` cannot be registered unless it
            brings its own ``authority``.
        identity_port: ALS identity port for the default authority.
        credential_lookup: ``subject -> current credential or None`` (the server-side session
            store) for the default authority on ALS.
        clock: Current time for the default authority.
    """

    def __init__(
        self,
        store: JobStore,
        *,
        owner: str = "worker",
        admission: Any = None,
        heartbeat_interval: float | None = None,
        settings: DeploymentSettings | None = None,
        identity_port: Any | None = None,
        credential_lookup: Callable[[str], Any] | None = None,
        clock: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self.owner = owner
        #: Default stage authority (``None`` when the service was built without deployment settings).
        self.authority: JobAuthority | None = None
        if settings is not None:
            self.authority = JobAuthority(settings, identity_port=identity_port, credential_lookup=credential_lookup,
                                          clock=clock)
        #: Shared provider admission (the same controllers chat, workers and evaluations use).
        self.admission = admission if admission is not None else ProviderAdmission()
        #: Seconds between lease renewals while a job runs (``None``: renew only through progress calls).
        self.heartbeat_interval = heartbeat_interval
        #: Set at shutdown: running jobs return to ``queued`` at their next boundary.
        self.interrupt = threading.Event()
        self._kinds: dict[str, RegisteredKind] = {}
        self._scope_hooks: list[Callable[[Mapping[str, Any]], None]] = []

    # --- registration -----------------------------------------------------------------

    def register(
        self,
        kind: str,
        pipeline: Pipeline,
        *,
        capability: str | None = None,
        authority: Callable[[JobRecord, Sequence[str]], Any] | None = None,
        admission: Any = None,
        extra: Mapping[str, Any] | None = None,
        on_denied: Callable[[JobRecord, str], None] | None = None,
    ) -> RegisteredKind:
        """Declare a job kind.

        ``authority`` re-authorizes the initiating subject before every stage; by default the
        service's :class:`~app.modules.security.authorization.JobAuthority`. A kind whose
        pipeline declares a ``required_capability`` is refused when neither exists (fail closed,
        AUD-01): a stage must never run its privileged effect without re-authorization.

        Raises:
            StartupConfigError: ``required_capability`` declared and no authority available.
            ValueError: No capability known for ``kind``.
        """
        cap = capability or DEFAULT_KIND_CAPABILITIES.get(kind)
        if cap is None:
            raise ValueError(f"no capability known for job kind {kind!r}")
        if authority is None:
            authority = self.authority
        if authority is None:
            guarded = [spec.name for spec in pipeline.specs if spec.required_capability]
            if guarded:
                raise StartupConfigError(
                    f"config_rejected: job kind {kind!r} declares required_capability on stage(s) "
                    f"{', '.join(guarded)} but no worker authority is wired; build the JobService with "
                    "deployment settings or pass authority="
                )
        runner = PipelineRunner(
            self.store,
            pipeline,
            authority=authority,
            admission=admission if admission is not None else self.admission,
            extra=extra,
            on_denied=on_denied or self._default_denied,
            interrupt=self.interrupt,
        )
        entry = RegisteredKind(kind, pipeline, cap, runner)
        self._kinds[kind] = entry
        return entry

    def has_kind(self, name: str) -> bool:
        """Whether ``name`` is a registered job kind."""
        return name in self._kinds

    def kind(self, name: str) -> RegisteredKind:
        """A registered kind, or ``INVALID_INPUT``."""
        try:
            return self._kinds[name]
        except KeyError:
            raise ContractError(ErrorCode.INVALID_INPUT, f"unknown job kind {name!r}") from None

    def _default_denied(self, job: JobRecord, reason: str) -> None:
        self.notify_scope_change(
            {
                "job_id": job.job_id,
                "subject": job.created_by,
                "corpus_scope": list(job.inputs.corpus_scope),
                "reason": reason,
            }
        )

    def on_scope_change(self, hook: Callable[[Mapping[str, Any]], None]) -> None:
        """Register a hook (approval invalidation, cache flush) run when scope/credentials change."""
        self._scope_hooks.append(hook)

    def notify_scope_change(self, change: Mapping[str, Any]) -> None:
        """Scope changed or a credential was revoked: run every hook (no fallback to a server credential)."""
        for hook in self._scope_hooks:
            hook(change)

    # --- authorization ----------------------------------------------------------------

    @staticmethod
    def _check_scope(ctx: RequestContext, corpora: Sequence[str]) -> None:
        if ctx.principal.subject == LOCAL_OPERATOR and ctx.mode.value == "local_trusted":
            return
        if not set(corpora) <= set(ctx.corpus_scope):
            raise ContractError(ErrorCode.DENIED, "corpus is outside the caller's scope")

    def _visible(self, ctx: RequestContext, job: JobRecord) -> None:
        if ctx.has("jobs.admin"):
            return
        local = ctx.mode.value == "local_trusted"
        if ctx.principal.subject == job.created_by and (
            local or set(job.inputs.corpus_scope) <= set(ctx.corpus_scope)
        ):
            return
        raise ContractError(ErrorCode.NOT_FOUND, "no such job")

    def list_visible(
        self, ctx: RequestContext, *, state: JobState | None = None, kind: str | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        """Most recently updated jobs ``ctx`` may see, filtered in SQL by the store (C3).

        The same rule as :meth:`_visible`, expressed as store filters: ``jobs.admin`` sees every
        job; anyone else sees the jobs they created whose corpus scope is within theirs (any scope
        for ``local_trusted``). Tests check the two agree on every job.
        """
        if ctx.has("jobs.admin"):
            return self.store.list_jobs_scoped(state=state, kind=kind, limit=limit)
        local = ctx.mode.value == "local_trusted"
        return self.store.list_jobs_scoped(
            created_by=ctx.principal.subject, within_scope=None if local else ctx.corpus_scope,
            state=state, kind=kind, limit=limit,
        )

    # --- API-facing operations ---------------------------------------------------------

    def submit(
        self, ctx: RequestContext, kind: str, inputs: OperationInputs, *, settings_revision: str | None = None
    ) -> JobHandle:
        """Register a job (idempotent for identical kind and inputs). Does not run it."""
        entry = self.kind(kind)
        if not ctx.has(entry.capability):
            raise ContractError(ErrorCode.DENIED, f"{entry.capability} is required to submit {kind} jobs")
        self._check_scope(ctx, inputs.corpus_scope)
        return self.store.submit(
            kind, inputs, created_by=ctx.principal.subject, settings_revision=settings_revision
        ).handle()

    def get(self, ctx: RequestContext, job_id: str) -> JobHandle:
        """The job handle (current state)."""
        job = self.store.get(job_id)
        self._visible(ctx, job)
        return job.handle()

    def events(self, ctx: RequestContext, job_id: str, *, after: int = -1) -> list[JobEvent]:
        """Durable events after sequence ``after`` (for polling and SSE)."""
        self._visible(ctx, self.store.get(job_id))
        return self.store.events(job_id, after=after)

    def logs(self, ctx: RequestContext, job_id: str, *, after: int = -1, limit: int = 500) -> list[JobLog]:
        """Log lines after ``seq`` ``after`` (visibility rule of :meth:`events`)."""
        self._visible(ctx, self.store.get(job_id))
        return self.store.logs(job_id, after=after, limit=limit)

    def progress(self, ctx: RequestContext, job_id: str) -> dict[str, Any]:
        """Per-phase progress (parsing, extraction, validation, review, import)."""
        from .telemetry import job_progress

        job = self.store.get(job_id)
        self._visible(ctx, job)
        return job_progress(self.store, self.kind(job.kind).pipeline, job_id)

    def cancel(self, ctx: RequestContext, job_id: str) -> JobHandle:
        """Cancel: immediate when nobody runs it, otherwise a stop request honored at the next boundary."""
        job = self.store.get(job_id)
        self._visible(ctx, job)
        if job.state in (JobState.SUCCEEDED, JobState.CANCELLED):
            return job.handle()
        if self.store.lease_holder(self.store.job_resource(job_id)) is not None:
            self.store.request_stop(job_id, actor=ctx.principal.subject)
            return self.store.get(job_id).handle()
        if job.state is JobState.RUNNING:  # owner died: no live lease, so nobody can honor a stop
            lease = self.store.acquire_lease(self.store.job_resource(job_id), f"cancel:{ctx.principal.subject}")
            if lease is None:
                raise ContractError(ErrorCode.CONFLICT, "job is owned by a running worker", retryable=True)
            try:
                return self.store.set_state(
                    lease,
                    job_id,
                    JobState.CANCELLED,
                    reason="cancelled by operator",
                    error_code=ErrorCode.CANCELLED,
                ).handle()
            finally:
                self.store.release_lease(lease)
        return self.store.transition_without_lease(
            job_id, JobState.CANCELLED, reason="cancelled by operator", actor=ctx.principal.subject
        ).handle()

    def resume(self, ctx: RequestContext, job_id: str, *, expected_revision: int | None = None) -> JobHandle:
        """Re-queue a paused, review-resolved or failed job; only compatible completed stages are reused."""
        job = self.store.get(job_id)
        self._visible(ctx, job)
        entry = self.kind(job.kind)
        if not ctx.has(entry.capability) and not ctx.has("jobs.admin"):
            raise ContractError(ErrorCode.DENIED, "not allowed to resume this job")
        return self.store.transition_without_lease(
            job_id,
            JobState.QUEUED,
            reason="resume",
            actor=ctx.principal.subject,
            expected_revision=expected_revision,
        ).handle()

    # --- worker-facing ----------------------------------------------------------------

    def work_one(
        self, owner: str | None = None, *, kinds: Sequence[str] | None = None
    ) -> tuple[str, JobState | None] | None:
        """Claim and run one job. Returns ``(job_id, state)``, or ``None`` when nothing is runnable.

        ``state`` is ``None`` when the owner lost its lease mid-run (a newer owner took over);
        this worker then stops without applying anything further.
        """
        claimed = self.store.claim_next(owner or self.owner, kinds=kinds or tuple(self._kinds))
        if claimed is None:
            return None
        lease, job = claimed
        entry = self.kind(job.kind)
        beat = self._start_heartbeat(lease)
        try:
            state: JobState | None = entry.runner.run(lease, job)
        except LeaseLost:
            beat.set()
            self.store.release_lease(lease)
            return job.job_id, None
        except Exception:
            beat.set()
            self.store.release_lease(lease)
            raise
        # A BaseException (a crash, a killed thread) skips this on purpose: the lease is left to expire.
        beat.set()
        self.store.release_lease(lease)
        return job.job_id, state

    def _start_heartbeat(self, lease: Any) -> threading.Event:
        """Renew ``lease`` periodically while the job runs; returns the event that stops the renewals."""
        done = threading.Event()
        interval = self.heartbeat_interval
        if not interval:
            return done

        def beat() -> None:
            while not done.wait(interval):
                try:
                    self.store.renew_lease(lease)
                except (
                    Exception
                ):  # noqa: BLE001 - a lost lease ends the heartbeat; the runner sees LeaseLost itself
                    logger.debug("heartbeat stopped: lease renewal failed", exc_info=True)
                    return

        threading.Thread(target=beat, name=f"heartbeat-{lease.resource}", daemon=True).start()
        return done


__all__ = ("DEFAULT_KIND_CAPABILITIES", "JobService", "RegisteredKind")
