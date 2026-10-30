"""Typed stage pipeline and the runner that enforces replay safety (plan 4.2, 11.1, 11.2).

A pipeline is a DAG of :class:`StageSpec`. Prompt wording cannot bypass it: the runner is the
only code that moves a job between states. For each stage it

1. computes the stage's operation key (relevant inputs + the keys of the stages it consumes);
2. reuses a completed stage only when that key is unchanged, otherwise invalidates that stage
   and *only its downstream* stages;
3. records the intent, runs the handler under the job's owned lease, verifies the result,
   and only then writes the durable completion record.

Stage outcomes and job state are separate. A budget or provider pause and a wait for review
are job states; they leave the stage ``pending`` and say nothing about the paper. A crash is
simply a process that stops: the lease expires and another owner resumes from the records.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..contracts.errors import ContractError, ErrorCode
from ..contracts.jobs import JobState, StageOutcome
from .keys import OperationInputs, changed_inputs, stage_key
from .store import JobRecord, JobStore, Lease, LeaseLost

logger = logging.getLogger(__name__)

PHASES = ("parsing", "extraction", "validation", "review", "import", "other")


class StagePause(Exception):
    """Base for non-failure interruptions that put the *job* in a waiting state."""

    state: JobState = JobState.QUEUED

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PauseBudget(StagePause):
    """A configured cost/call budget is used up: job -> ``paused_budget``."""

    state = JobState.PAUSED_BUDGET


class PauseProvider(StagePause):
    """The provider is unavailable or rate limited: job -> ``paused_provider``."""

    state = JobState.PAUSED_PROVIDER


class AwaitReview(StagePause):
    """A human decision is required: job -> ``awaiting_review``."""

    state = JobState.AWAITING_REVIEW


class RestartStages(Exception):
    """The stage invalidated earlier stages; re-run the pipeline from the first pending stage now.

    Raised by a stage that found its inputs stale and reset upstream stages (for example the
    publication review stage after the schema, vocabulary or target moved: owner decision B27,
    2026-10-30, the runner re-plans automatically and then waits for approval of the new plan).
    The runner restarts its stage loop within the same lease at most once per run; a second
    restart ends the run as a pause so a stage cannot loop.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JobStopped(Exception):
    """A stop or cancel was requested; the job ends ``cancelled`` at this boundary."""


class JobInterrupted(Exception):
    """The owning worker is shutting down; the job goes back to ``queued`` for the next owner."""


class QuarantineStage(Exception):
    """The stage's output must not be used; stage -> ``quarantined`` with a reason."""

    def __init__(self, reason: str, detail: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = dict(detail or {})


@dataclass(frozen=True)
class StageResult:
    """What a handler produced.

    Attributes:
        effects: Stable IDs of effects performed.
        artifact_refs: Durable artifacts (content addresses, package keys), never payloads.
        progress: Named counters.
        data: Small JSON result stored with the completion record.
        incomplete: Unit IDs still missing. A non-empty list means coverage is incomplete: the
            stage fails (retryable) and publication stays blocked.
    """

    effects: Tuple[str, ...] = ()
    artifact_refs: Tuple[str, ...] = ()
    progress: Mapping[str, int] = field(default_factory=dict)
    data: Mapping[str, Any] = field(default_factory=dict)
    incomplete: Tuple[str, ...] = ()


StageRun = Callable[["StageContext"], StageResult]
StageVerify = Callable[["StageContext", StageResult], Optional[str]]


@dataclass(frozen=True)
class StageSpec:
    """Declaration of one stage.

    Attributes:
        name: Stage identifier (``"parse"``, ``"extract"``...).
        run: Handler. Effects must use stable IDs so a repeated run reconciles instead of duplicating.
        phase: Telemetry phase: ``parsing``, ``extraction``, ``validation``, ``review``, ``import``.
        depends_on: Stages whose output this one consumes.
        relevant: Input fields (see ``keys.INPUT_FIELDS``) the stage depends on.
        params_keys: Parameter names that matter (empty: all parameters, when ``params`` is relevant).
        verify: Returns ``None`` when the outputs check out, else a reason; run before completion is recorded.
        required_capability: Capability re-checked before the stage runs (workers re-authorize).
        always_run: Never reuse a completed record: the stage re-checks on every run (for example revalidation
            of an approval just before an import resumes).
    """

    name: str
    run: StageRun
    phase: str = "other"
    depends_on: Tuple[str, ...] = ()
    relevant: Tuple[str, ...] = ("corpus_scope",)
    params_keys: Tuple[str, ...] = ()
    verify: Optional[StageVerify] = None
    required_capability: Optional[str] = None
    always_run: bool = False


class Pipeline:
    """Ordered, acyclic set of stages."""

    def __init__(self, specs: Sequence[StageSpec]) -> None:
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise ValueError("duplicate stage names")
        seen: set = set()
        for spec in specs:
            for dep in spec.depends_on:
                if dep not in seen:
                    raise ValueError(f"stage {spec.name} depends on {dep}, which must be listed before it")
            if spec.phase not in PHASES:
                raise ValueError(f"unknown phase {spec.phase}")
            seen.add(spec.name)
        self.specs: Tuple[StageSpec, ...] = tuple(specs)
        self._by_name: Dict[str, StageSpec] = {s.name: s for s in specs}

    def __getitem__(self, name: str) -> StageSpec:
        return self._by_name[name]

    def keys(self, inputs: OperationInputs) -> Dict[str, str]:
        """Operation key of every stage for ``inputs`` (upstream keys chained in)."""
        keys: Dict[str, str] = {}
        for spec in self.specs:
            keys[spec.name] = stage_key(
                inputs, spec.name, spec.relevant, {d: keys[d] for d in spec.depends_on}, spec.params_keys
            )
        return keys

    def downstream(self, name: str) -> List[str]:
        """``name`` and every stage that (transitively) consumes it."""
        out = {name}
        for spec in self.specs:
            if out & set(spec.depends_on):
                out.add(spec.name)
        return [s.name for s in self.specs if s.name in out]

    def phase_of(self, stage: str) -> str:
        """Telemetry phase of a stage (``other`` for unknown stages)."""
        return self._by_name[stage].phase if stage in self._by_name else "other"


class StageContext:
    """What a handler may use: ownership guard, progress, unit checkpoints and stable-ID effects."""

    def __init__(
        self,
        store: JobStore,
        lease: Lease,
        job: JobRecord,
        spec: StageSpec,
        op_key: str,
        *,
        authority: Optional[Callable[[JobRecord, Sequence[str]], Any]] = None,
        admission: Any = None,
        extra: Optional[Mapping[str, Any]] = None,
        interrupt: Optional[Any] = None,
    ) -> None:
        self.store = store
        self.lease = lease
        self.job = job
        self.spec = spec
        self.stage = spec.name
        self.op_key = op_key
        self.inputs = job.inputs
        self.admission = admission
        self.extra: Mapping[str, Any] = dict(extra or {})
        self._authority = authority
        self._interrupt = interrupt

    def provider_slot(self, provider: str) -> Any:
        """Shared-admission slot for ``provider`` (the quota chat, workers and evaluations all share)."""
        if self.admission is None:
            raise RuntimeError("no admission configured for this job kind")
        return self.admission.slot(provider)

    def guard(self) -> None:
        """Raise before an effect when ownership was lost, shutdown began, or a stop was requested."""
        self.store.check_lease(self.lease)
        if self._interrupt is not None and self._interrupt.is_set():
            raise JobInterrupted("worker shutting down")
        if self.store.stop_requested(self.job.job_id):
            raise JobStopped("stop requested")

    def require(self, *capabilities: str) -> Any:
        """Re-authorize the initiating subject before an effect (workers never keep stale authority).

        Raises:
            ContractError: ``DENIED`` when no authority is wired for this kind (fail closed; trusted
                wiring registers every kind with one, see ``JobService.register``), or when the
                authority stops the run (sign-out, downgrade, revocation, expiry).
        """
        if self._authority is None:
            raise ContractError(ErrorCode.DENIED, "worker stopped: no authority is wired for this job kind")
        return self._authority(self.job, capabilities)

    def progress(self, **counters: int) -> None:
        """Publish named counters and renew the lease."""
        self.guard()
        self.store.update_progress(self.lease, self.job.job_id, self.stage, counters)

    def unit(self, unit_id: str, unit_key: str) -> Optional[Dict[str, Any]]:
        """A previously settled, still compatible unit result (for example an extracted chunk)."""
        return self.store.get_unit(self.job.job_id, self.stage, unit_id, unit_key)

    def save_unit(self, unit_id: str, unit_key: str, result: Mapping[str, Any]) -> None:
        """Checkpoint one settled unit."""
        self.guard()
        self.store.save_unit(self.lease, self.job.job_id, self.stage, unit_id, unit_key, result)

    def fail_unit(self, unit_id: str, unit_key: str, error: str) -> None:
        """Record a unit that failed (retried on resume; blocks publication until it settles)."""
        self.store.save_unit(self.lease, self.job.job_id, self.stage, unit_id, unit_key, None, error)

    def run_effect(
        self,
        effect_id: str,
        effect: Callable[[], Mapping[str, Any]],
        reconcile: Optional[Callable[[], Optional[Mapping[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        """Perform an effect at most once per stable ID, reconciling after a crash.

        ``done`` returns the stored result. ``intent`` means an earlier run may have crashed
        mid-effect: ``reconcile`` (when given) inspects the world and returns the result if the
        effect actually landed; otherwise ``effect`` runs again, which must be idempotent.
        """
        found = self.store.effect(self.job.job_id, self.stage, effect_id)
        if found is not None and found[0] == "done":
            return dict(found[1] or {})
        self.guard()
        self.store.record_effect(self.lease, self.job.job_id, self.stage, effect_id, "intent")
        landed = reconcile() if (found is not None and reconcile is not None) else None
        result = dict(landed) if landed is not None else dict(effect())
        self.guard()
        self.store.record_effect(self.lease, self.job.job_id, self.stage, effect_id, "done", result)
        return result


class PipelineRunner:
    """Runs one job's pipeline under its lease."""

    def __init__(
        self,
        store: JobStore,
        pipeline: Pipeline,
        *,
        authority: Optional[Callable[[JobRecord, Sequence[str]], Any]] = None,
        admission: Any = None,
        extra: Optional[Mapping[str, Any]] = None,
        on_denied: Optional[Callable[[JobRecord, str], None]] = None,
        interrupt: Optional[Any] = None,
    ) -> None:
        self._interrupt = interrupt
        self.store = store
        self.pipeline = pipeline
        self._authority = authority
        self._admission = admission
        self._extra = extra
        self._on_denied = on_denied

    def run(self, lease: Lease, job: JobRecord) -> JobState:
        """Drive ``job`` until it succeeds, waits, fails or is stopped. Returns the resulting state.

        Only ``Exception`` subclasses are handled: a ``BaseException`` (a crash) propagates and
        leaves the job ``running`` with its lease to expire, exactly like a dead process.
        """
        job_id = job.job_id
        keys = self.pipeline.keys(job.inputs)
        stored = {s.stage: s for s in self.store.stages(job_id)}
        stage_name = "job"
        current: Dict[str, str] = {"stage": "job"}  # the stage a pause or stop is attributed to
        # A per-run authority (``JobAuthority.for_job``) keeps its stop across the run's stages.
        authority = self._authority
        begin = getattr(authority, "for_job", None)
        if callable(begin):
            authority = begin(job)
        try:
            self._invalidate_incompatible(lease, job, keys, stored)
            restarts = 0
            while True:
                try:
                    self._run_stages(lease, job, keys, authority, current)
                    break
                except RestartStages as restart:
                    stage_name = current["stage"]
                    if restarts >= 1:
                        raise AwaitReview(f"{restart.reason}; a second restart in one run is not attempted") from None
                    restarts += 1
                    logger.info("job %s: %s; re-running the invalidated stages", job_id, restart.reason)
            blockers = self.publication_blockers(job_id)
            if blockers:
                self.store.set_state(
                    lease, job_id, JobState.FAILED, reason="; ".join(blockers), error_code=ErrorCode.INVALID_INPUT
                )
                return JobState.FAILED
            self.store.set_state(lease, job_id, JobState.SUCCEEDED, reason="all stages complete")
            return JobState.SUCCEEDED
        except LeaseLost:
            logger.warning("job %s: lease lost; stopping without further mutation", job_id)
            raise
        except _Ended as ended:
            return ended.state
        except JobInterrupted as exc:
            stage_name = current["stage"]
            self.store.set_state(lease, job_id, JobState.QUEUED, stage=stage_name, reason=str(exc))
            return JobState.QUEUED
        except JobStopped as exc:
            stage_name = current["stage"]
            self.store.set_state(
                lease,
                job_id,
                JobState.CANCELLED,
                stage=stage_name,
                reason=str(exc),
                error_code=ErrorCode.CANCELLED,
            )
            return JobState.CANCELLED
        except StagePause as pause:
            stage_name = current["stage"]
            self.store.mark_stage(
                lease,
                job_id,
                stage_name,
                StageOutcome.PENDING,
                error_code=(
                    ErrorCode.BUDGET_EXHAUSTED
                    if isinstance(pause, PauseBudget)
                    else (ErrorCode.UNAVAILABLE if isinstance(pause, PauseProvider) else None)
                ),
                error=pause.reason,
                job_state=pause.state,
            )
            self.store.set_state(lease, job_id, pause.state, stage=stage_name, reason=pause.reason)
            return pause.state

    def _run_stages(self, lease: Lease, job: JobRecord, keys: Dict[str, str],
                    authority: Optional[Callable[[JobRecord, Sequence[str]], Any]],
                    current: Dict[str, str]) -> None:
        """One pass over the stages: reuse compatible completed ones, run the rest in order."""
        job_id = job.job_id
        for position, spec in enumerate(self.pipeline.specs):
            current["stage"] = spec.name
            self.store.check_lease(lease)
            if self._interrupt is not None and self._interrupt.is_set():
                raise JobInterrupted("worker shutting down")
            if self.store.stop_requested(job_id):
                raise JobStopped("stop requested")
            row = self.store.stage(job_id, spec.name)
            if (
                not spec.always_run
                and row is not None
                and row.op_key == keys[spec.name]
                and row.status in (StageOutcome.COMPLETED, StageOutcome.SKIPPED_COMPATIBLE)
            ):
                if row.status is StageOutcome.COMPLETED:
                    self.store.complete_stage(
                        lease,
                        job_id,
                        spec.name,
                        row.op_key,
                        effects=row.effects,
                        artifact_refs=row.artifact_refs,
                        progress=row.progress,
                        result=row.result,
                        outcome=StageOutcome.SKIPPED_COMPATIBLE,
                    )
                continue
            try:
                self._run_stage(lease, job, spec, position, keys[spec.name], authority)
            except RestartStages as restart:
                self.store.mark_stage(lease, job_id, spec.name, StageOutcome.PENDING, error=restart.reason)
                raise

    def _invalidate_incompatible(
        self, lease: Lease, job: JobRecord, keys: Mapping[str, str], stored: Mapping[str, Any]
    ) -> None:
        """Invalidate only stages whose key changed, plus the stages downstream of them."""
        stale: List[str] = []
        for spec in self.pipeline.specs:
            row = stored.get(spec.name)
            if row is not None and row.op_key and row.op_key != keys[spec.name]:
                stale.extend(self.pipeline.downstream(spec.name))
        if stale:
            ordered = [s.name for s in self.pipeline.specs if s.name in set(stale)]
            self.store.invalidate(lease, job.job_id, ordered, "inputs changed; recomputing affected stages only")

    def _run_stage(self, lease: Lease, job: JobRecord, spec: StageSpec, position: int, key: str,
                   authority: Optional[Callable[[JobRecord, Sequence[str]], Any]] = None) -> None:
        job_id = job.job_id
        ctx = StageContext(
            self.store,
            lease,
            job,
            spec,
            key,
            authority=authority if authority is not None else self._authority,
            admission=self._admission,
            extra=self._extra,
            interrupt=self._interrupt,
        )
        self.store.begin_stage(lease, job_id, spec.name, position, key)
        try:
            if spec.required_capability:
                ctx.require(spec.required_capability)
            result = spec.run(ctx)
            if result.incomplete:
                raise _IncompleteCoverage(result.incomplete)
            problem = spec.verify(ctx, result) if spec.verify else None
            if problem:
                raise QuarantineStage(f"verification failed: {problem}")
            ctx.guard()
            self.store.complete_stage(
                lease,
                job_id,
                spec.name,
                key,
                effects=result.effects,
                artifact_refs=result.artifact_refs,
                progress=result.progress,
                result=dict(result.data),
            )
        except (StagePause, JobStopped, JobInterrupted, LeaseLost, RestartStages):
            raise
        except _IncompleteCoverage as exc:
            self.store.mark_stage(
                lease,
                job_id,
                spec.name,
                StageOutcome.FAILED,
                error_code=ErrorCode.UNAVAILABLE,
                error=f"incomplete coverage: {len(exc.units)} unit(s) missing",
            )
            self.store.set_state(
                lease,
                job_id,
                JobState.FAILED,
                stage=spec.name,
                outcome=StageOutcome.FAILED,
                reason=f"{spec.name}: incomplete coverage ({len(exc.units)} units); retry resumes",
                error_code=ErrorCode.UNAVAILABLE,
            )
            raise _Ended(JobState.FAILED) from exc
        except QuarantineStage as exc:
            self.store.mark_stage(
                lease,
                job_id,
                spec.name,
                StageOutcome.QUARANTINED,
                error_code=ErrorCode.INVALID_INPUT,
                error=exc.reason,
            )
            self.store.set_state(
                lease,
                job_id,
                JobState.FAILED,
                stage=spec.name,
                outcome=StageOutcome.QUARANTINED,
                reason=f"quarantined: {exc.reason}",
                error_code=ErrorCode.INVALID_INPUT,
            )
            raise _Ended(JobState.FAILED) from exc
        except ContractError as exc:
            if exc.code is ErrorCode.DENIED:
                self._deny(lease, job, spec, exc)
            else:
                self.store.mark_stage(
                    lease, job_id, spec.name, StageOutcome.FAILED, error_code=exc.code, error=str(exc)
                )
                self.store.set_state(
                    lease,
                    job_id,
                    JobState.FAILED,
                    stage=spec.name,
                    outcome=StageOutcome.FAILED,
                    reason=str(exc)[:300],
                    error_code=exc.code,
                )
            raise _Ended(JobState.FAILED) from exc
        except Exception as exc:  # noqa: BLE001 - recorded, bounded, and the job ends failed
            logger.exception("job %s stage %s failed", job_id, spec.name)
            self.store.mark_stage(
                lease,
                job_id,
                spec.name,
                StageOutcome.FAILED,
                error_code=ErrorCode.INTERNAL,
                error=type(exc).__name__,
            )
            self.store.set_state(
                lease,
                job_id,
                JobState.FAILED,
                stage=spec.name,
                outcome=StageOutcome.FAILED,
                reason=type(exc).__name__,
                error_code=ErrorCode.INTERNAL,
            )
            raise _Ended(JobState.FAILED) from exc

    def _deny(self, lease: Lease, job: JobRecord, spec: StageSpec, exc: ContractError) -> None:
        """Authority revoked or insufficient: stop permanently and invalidate dependent approvals."""
        self.store.mark_stage(
            lease, job.job_id, spec.name, StageOutcome.FAILED, error_code=ErrorCode.DENIED, error=str(exc)
        )
        self.store.set_state(
            lease,
            job.job_id,
            JobState.FAILED,
            stage=spec.name,
            outcome=StageOutcome.FAILED,
            reason=str(exc)[:300],
            error_code=ErrorCode.DENIED,
        )
        if self._on_denied is not None:
            self._on_denied(job, str(exc))

    def publication_blockers(self, job_id: str) -> List[str]:
        """Reasons the job's output may not be published: any stage not completed or compatible."""
        bad = [
            f"{s.stage}:{s.status.value}"
            for s in self.store.stages(job_id)
            if s.status not in (StageOutcome.COMPLETED, StageOutcome.SKIPPED_COMPATIBLE)
        ]
        known = {s.stage for s in self.store.stages(job_id)}
        bad += [f"{s.name}:not_run" for s in self.pipeline.specs if s.name not in known]
        return bad


class _IncompleteCoverage(Exception):
    def __init__(self, units: Sequence[str]) -> None:
        super().__init__("incomplete coverage")
        self.units = tuple(units)


class _Ended(Exception):
    """Internal: the stage already recorded its terminal state."""

    def __init__(self, state: JobState) -> None:
        super().__init__(state.value)
        self.state = state


__all__ = (
    "AwaitReview",
    "JobInterrupted",
    "JobStopped",
    "PauseBudget",
    "PauseProvider",
    "Pipeline",
    "PipelineRunner",
    "QuarantineStage",
    "RestartStages",
    "StageContext",
    "StagePause",
    "StageResult",
    "StageSpec",
    "PHASES",
    "changed_inputs",
)
