"""Controlled publication job: plan -> review -> revalidate -> import -> verify -> receipt -> activate.

JSON construction is complete before this job starts: it consumes a *frozen* snapshot and never
changes it, so a failed import, a quarantine or a rollback cannot invalidate the snapshot. The
job drives the :class:`~.publish_ports.PublishStep` port (T08B) and never writes to Tiled itself.

Guarantees (plan 11, T10 acceptance):

* **No incomplete package reaches published state.** Incomplete or failed staged packages are
  quarantined with a reason and block the plan; a stage that did not verify quarantines instead of
  receipting.
* **Stale approvals are refused.** An approval binds to the plan hash and to the snapshot bytes,
  schema fingerprint, vocabulary revision and target revision the plan was computed under; if any
  moved, the approval is invalid, the plan is recomputed and a *new* review is required. A scope
  change or credential revocation invalidates dependent approvals too.
* **Single publisher per target, no exactly-once claim.** A fenced per-target lease prevents
  competing publishers; a crash after external effects is handled by stable operation IDs,
  ``reconcile`` and an idempotent ``apply``, then completed on resume.
* **Audit before mutation.** The import and the pointer switch are critical mutations behind the
  write-ahead audit guard; if durable audit cannot be written they do not run.
* **The old graph/index pair stays active until a verified new pair is activated**, and the pointer
  can be rolled back to its previous value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any, Callable, Sequence

from ..contracts.changes import ApprovalRecord, ChangePlan
from ..contracts.context import ActorKind, RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..contracts.jobs import JobHandle, JobState
from ..provenance.recorder import AuditRecorder
from .keys import OperationInputs, digest
from .pipeline import AwaitReview, Pipeline, QuarantineStage, RestartStages, StageContext, StageResult, StageSpec
from .publish_ports import (
    ApplyOutcome,
    FrozenSnapshot,
    IndexBuilder,
    PackageProblem,
    PublishStep,
    RevisionProbe,
    SnapshotSource,
)
from .publish_store import Pointer, PublishStore
from .service import JobService
from .store import JobRecord

logger = logging.getLogger(__name__)

KIND = "publish"
PUBLISH_STAGES = ("plan", "review", "revalidate", "import", "verify", "receipt", "activate")
ApprovalVerifier = Callable[[ApprovalRecord, ChangePlan, datetime], ApprovalRecord]
PackageCheck = Callable[[FrozenSnapshot], Sequence[PackageProblem]]


def default_approval_verifier(approval: ApprovalRecord, plan: ChangePlan, at: datetime) -> ApprovalRecord:
    """Check only that the approval is bound to the plan (hash, target, scope, expiry).

    Deployments pass ``security.authorization.verify_approval``.

    Raises:
        ContractError: ``STALE_APPROVAL`` when the approval does not authorize the plan at `at`.
    """
    if not approval.authorizes(plan, at):
        raise ContractError(ErrorCode.STALE_APPROVAL, "approval does not cover this plan, target or time")
    return approval


@dataclass
class PublicationDeps:
    """Everything the publication job composes (all injected; nothing global).

    Attributes:
        snapshots: Frozen snapshot reader.
        step: The importer port (T08B).
        index: Index builder/verifier.
        probe: Current schema/vocabulary revisions.
        store: Publication tables (plans, approvals, receipts, pointers, quarantine).
        audit: Write-ahead audit recorder for critical mutations.
        ctx_for: Builds the worker :class:`RequestContext` for a job (``actor_kind=worker``).
        packages: Optional completeness check of the packages a snapshot was built from.
        verify_approval: Approval verifier (profile, issuer, writer identity); default checks the binding only.
        clock: Epoch seconds.
        owner: Lease owner name for the per-target publisher lease.
    """

    snapshots: SnapshotSource
    step: PublishStep
    index: IndexBuilder
    probe: RevisionProbe
    store: PublishStore
    audit: AuditRecorder
    ctx_for: Callable[[JobRecord], RequestContext]
    clock: Callable[[], float]
    packages: PackageCheck | None = None
    verify_approval: ApprovalVerifier = default_approval_verifier
    owner: str = "publisher"


def operation_id(plan: ChangePlan) -> str:
    """Stable operation ID of the import of ``plan``: the same plan always replays the same operation."""
    return "import-" + digest([plan.plan_sha256, plan.target])[:24]


class Publication:
    """The publication job: registers the pipeline and offers the trusted approval/rollback interface."""

    def __init__(self, jobs: JobService, deps: PublicationDeps) -> None:
        self.jobs = jobs
        self.deps = deps
        self.store = deps.store
        self.pipeline = Pipeline(self._specs())
        jobs.register(KIND, self.pipeline, capability="graph.publish")
        jobs.on_scope_change(self._on_scope_change)

    # --- stages ----------------------------------------------------------------------------

    def _specs(self) -> list[StageSpec]:
        return [
            StageSpec("plan", self._plan, phase="validation", relevant=("source_sha256", "params")),
            StageSpec("review", self._review, phase="review", depends_on=("plan",)),
            StageSpec("revalidate", self._revalidate, phase="validation", depends_on=("review",), always_run=True),
            StageSpec(
                "import",
                self._import,
                phase="import",
                depends_on=("revalidate",),
                required_capability="graph.publish",
            ),
            StageSpec("verify", self._verify, phase="import", depends_on=("import",)),
            StageSpec("receipt", self._receipt, phase="import", depends_on=("verify",)),
            StageSpec("activate", self._activate, phase="import", depends_on=("receipt",)),
        ]

    @staticmethod
    def _params(sc: StageContext) -> dict[str, Any]:
        return dict(sc.inputs.params)

    def _snapshot(self, sc: StageContext) -> FrozenSnapshot:
        ref = str(self._params(sc)["snapshot_ref"])
        try:
            snapshot = self.deps.snapshots.load(ref)
        except (ContractError, ValueError, OSError) as exc:
            self.store.quarantine(sc.job.job_id, ref, f"snapshot failed verification: {exc}", {})
            raise QuarantineStage(f"snapshot {ref} failed verification", {"error": type(exc).__name__}) from exc
        if snapshot.sha256 != sc.inputs.source_sha256:
            self.store.quarantine(sc.job.job_id, ref, "snapshot bytes changed since the job was submitted", {})
            raise QuarantineStage("snapshot bytes changed since the job was submitted")
        return snapshot

    def _plan(self, sc: StageContext) -> StageResult:
        job = sc.job
        sc.require("graph.publish")
        snapshot = self._snapshot(sc)
        if self.deps.packages is not None:
            problems = list(self.deps.packages(snapshot))
            for problem in problems:
                self.store.quarantine(job.job_id, problem.key, problem.reason, problem.detail)
            if problems:
                raise QuarantineStage(
                    f"{len(problems)} incomplete or failed package(s); first: {problems[0].key[:12]}: "
                    f"{problems[0].reason}",
                    {"packages": [p.key for p in problems]},
                )
        target = str(self._params(sc)["target"])
        ctx = self.deps.ctx_for(job)
        plan = self.deps.step.plan(ctx, snapshot, target, operation_id=f"plan-{job.job_id}")
        if (
            plan.input_sha256 != snapshot.sha256
            or plan.target != target
            or not ({snapshot.ref, snapshot.manifest.snapshot_id} & {plan.snapshot_id})
        ):
            self.store.quarantine(
                job.job_id, snapshot.ref, "step returned a plan for different input or target", {}
            )
            raise QuarantineStage("plan does not match the frozen snapshot and target")
        self.store.save_plan(
            job.job_id,
            plan,
            schema_fingerprint=self.deps.probe.schema_fingerprint(),
            vocabulary_revision=self.deps.probe.vocabulary_revision(),
        )
        return StageResult(
            artifact_refs=(f"plan:{plan.plan_sha256}",),
            data={
                "plan_id": plan.plan_id,
                "plan_sha256": plan.plan_sha256,
                "creates": plan.creates,
                "links": plan.links,
            },
        )

    def _import_started(self, sc: StageContext) -> bool:
        row = sc.store.stage(sc.job.job_id, "import")
        return bool(
            row
            and (
                row.status.value == "completed"
                or sc.store.effect(
                    sc.job.job_id, "import", self._effect_id(self.store.current_plan(sc.job.job_id))
                )
            )
        )

    @staticmethod
    def _effect_id(record: Any) -> str:
        return f"apply:{record.plan.plan_sha256}" if record is not None else "apply:none"

    def _freshness(self, sc: StageContext, record: Any, *, check_target: bool) -> list[str]:
        """Reasons the plan no longer matches the world (empty when fresh)."""
        plan, reasons = record.plan, []
        ref = str(self._params(sc)["snapshot_ref"])
        try:
            if self.deps.snapshots.load(ref).sha256 != plan.input_sha256:
                reasons.append("snapshot_changed")
        except (ContractError, ValueError, OSError):
            reasons.append("snapshot_changed")
        if self.deps.probe.schema_fingerprint() != record.schema_fingerprint:
            reasons.append("schema_changed")
        if self.deps.probe.vocabulary_revision() != record.vocabulary_revision:
            reasons.append("vocabulary_changed")
        if (
            check_target
            and self.deps.step.target_revision(self.deps.ctx_for(sc.job), plan.target) != plan.base_revision
        ):
            reasons.append("target_revision_changed")
        return reasons

    def _stale(self, sc: StageContext, reasons: list[str]) -> None:
        """Invalidate approvals and everything from ``plan`` on, then re-plan in this same run (B27).

        Owner decision B27 (2026-10-30): the runner re-plans automatically after a stale plan or
        approval and then waits for approval of the new plan, instead of pausing for a manual
        re-run first. A changed snapshot is still quarantined, never re-planned.
        """
        text = ", ".join(reasons)
        if "snapshot_changed" in reasons:
            self.store.quarantine(
                sc.job.job_id,
                str(self._params(sc)["snapshot_ref"]),
                "frozen snapshot bytes changed after planning",
                {},
            )
            raise QuarantineStage("frozen snapshot bytes changed after planning")
        self.store.invalidate_approvals(reason=f"stale: {text}", job_id=sc.job.job_id)
        sc.store.invalidate(sc.lease, sc.job.job_id, PUBLISH_STAGES, f"stale: {text}")
        raise RestartStages(f"plan is stale ({text}); recomputing the plan for a new review")

    def _approval_for(self, sc: StageContext, record: Any) -> ApprovalRecord:
        now = datetime.fromtimestamp(self.deps.clock(), tz=UTC)
        for approval in reversed(self.store.valid_approvals(sc.job.job_id, record.plan)):
            try:
                return self.deps.verify_approval(approval, record.plan, now)
            except ContractError as exc:
                self.store.invalidate_approvals(reason=f"{exc.code.value}: {exc}"[:200], job_id=sc.job.job_id)
        raise AwaitReview(f"approval required for plan {record.plan.plan_sha256[:12]} on {record.plan.target}")

    def _review(self, sc: StageContext) -> StageResult:
        record = self.store.current_plan(sc.job.job_id)
        if record is None:
            raise QuarantineStage("no plan recorded")
        reasons = self._freshness(sc, record, check_target=not self._import_started(sc))
        if reasons:
            self._stale(sc, reasons)
        approval = self._approval_for(sc, record)
        return StageResult(data={"approval_id": approval.approval_id, "plan_sha256": record.plan.plan_sha256})

    def _revalidate(self, sc: StageContext) -> StageResult:
        record = self.store.current_plan(sc.job.job_id)
        if record is None:
            raise QuarantineStage("no plan recorded")
        if self.store.receipt_for(record.plan.plan_sha256, record.plan.target) is not None:
            # Already imported, verified and receipted: only the frozen bytes matter while activation resumes.
            if "snapshot_changed" in self._freshness(sc, record, check_target=False):
                self._stale(sc, ["snapshot_changed"])
            return StageResult(data={"resumed": True, "receipted": True})
        reasons = self._freshness(sc, record, check_target=not self._import_started(sc))
        if reasons:
            self._stale(sc, reasons)
        approval = self._approval_for(sc, record)
        return StageResult(data={"approval_id": approval.approval_id, "resumed": self._import_started(sc)})

    def _import(self, sc: StageContext) -> StageResult:
        record = self.store.current_plan(sc.job.job_id)
        approval = self._approval_for(sc, record)
        plan, ctx = record.plan, self.deps.ctx_for(sc.job)
        sc.require("graph.publish")
        op_id = operation_id(plan)
        lease = sc.store.acquire_lease(f"publish:{plan.target}", self.deps.owner)
        if lease is None:
            raise ContractError(ErrorCode.CONFLICT, "another publisher holds this target", retryable=True)

        def guard() -> None:
            sc.guard()
            sc.store.check_lease(lease)

        def effect() -> dict[str, Any]:
            return self.deps.audit.guard(
                kind="import_step",
                operation_id=op_id,
                actor=sc.job.created_by,
                target=plan.target,
                run_id=sc.job.job_id,
                payload={
                    "plan_sha256": plan.plan_sha256,
                    "approval_id": approval.approval_id,
                    "snapshot_sha256": plan.input_sha256,
                    "creates": plan.creates,
                    "links": plan.links,
                },
                mutation=lambda: self.deps.step.apply(
                    ctx, plan, approval, operation_id=op_id, guard=guard
                ).as_dict(),
            )

        def reconcile() -> dict[str, Any] | None:
            landed = self.deps.step.reconcile(ctx, plan, operation_id=op_id)
            return landed.as_dict() if landed is not None and landed.complete else None

        try:
            result = sc.run_effect(self._effect_id(record), effect, reconcile=reconcile)
        finally:
            sc.store.release_lease(lease)
        outcome = ApplyOutcome.from_dict(result)
        if not outcome.complete:
            raise ContractError(
                ErrorCode.UNAVAILABLE, "import did not complete the plan; resume to finish", retryable=True
            )
        return StageResult(
            effects=outcome.effects,
            data={**outcome.as_dict(), "approval_id": approval.approval_id},
            progress={"created": outcome.created, "reconciled": outcome.reconciled},
        )

    def _outcome(self, sc: StageContext) -> ApplyOutcome:
        row = sc.store.stage(sc.job.job_id, "import")
        return ApplyOutcome.from_dict((row.result if row else None) or {})

    def _verify(self, sc: StageContext) -> StageResult:
        record = self.store.current_plan(sc.job.job_id)
        ctx = self.deps.ctx_for(sc.job)
        verification = self.deps.step.verify(ctx, record.plan, self._outcome(sc))
        if not verification.ok:
            self.store.quarantine(
                sc.job.job_id,
                record.plan.target,
                f"read-back verification failed: {verification.detail}",
                {"plan_sha256": record.plan.plan_sha256},
            )
            raise QuarantineStage(f"read-back verification failed: {verification.detail}")
        return StageResult(
            data={"checked": verification.checked, "target_revision": verification.target_revision},
            progress={"checked": verification.checked},
        )

    def _receipt(self, sc: StageContext) -> StageResult:
        record = self.store.current_plan(sc.job.job_id)
        plan, ctx = record.plan, self.deps.ctx_for(sc.job)
        import_row = sc.store.stage(sc.job.job_id, "import")
        approval = (
            self.store.get_approval(str((import_row.result or {}).get("approval_id", ""))) if import_row else None
        )
        if approval is None:
            raise QuarantineStage("the approval used for the import is not on record")
        outcome = self._outcome(sc)
        verify_row = sc.store.stage(sc.job.job_id, "verify")
        from .publish_ports import VerifyOutcome

        data = (verify_row.result if verify_row else None) or {}
        verification = VerifyOutcome(True, "", int(data.get("checked", 0)), str(data.get("target_revision", "")))
        receipt = self.deps.step.receipt(ctx, plan, approval, outcome, verification)
        if (
            not receipt.verified
            or receipt.plan_sha256 != plan.plan_sha256
            or receipt.target != plan.target
            or receipt.snapshot_sha256 != plan.input_sha256
            or receipt.approval_id != approval.approval_id
        ):
            self.store.quarantine(
                sc.job.job_id, plan.target, "receipt does not match the plan, approval or target", {}
            )
            raise QuarantineStage("receipt does not match the plan, approval or target")
        stored = self.store.save_receipt(sc.job.job_id, receipt)
        self.store.consume_approval(approval.approval_id)
        return StageResult(artifact_refs=(f"receipt:{stored.receipt_id}",), data={"receipt_id": stored.receipt_id})

    def _activate(self, sc: StageContext) -> StageResult:
        record = self.store.current_plan(sc.job.job_id)
        plan, ctx = record.plan, self.deps.ctx_for(sc.job)
        receipt = self.store.receipt_for(plan.plan_sha256, plan.target)
        if receipt is None or not receipt.verified:
            raise QuarantineStage("no verified receipt to activate")
        snapshot = self.deps.snapshots.load(str(self._params(sc)["snapshot_ref"]))
        lease = sc.store.acquire_lease(f"publish:{plan.target}", self.deps.owner)
        if lease is None:
            raise ContractError(ErrorCode.CONFLICT, "another publisher holds this target", retryable=True)
        try:
            index = self.deps.index.build(ctx, snapshot, receipt)
            if not self.deps.index.verify(ctx, index):
                self.store.quarantine(
                    sc.job.job_id, index.ref, "index failed verification; pointer not switched", {}
                )
                raise QuarantineStage("index failed verification; the previous pair stays active")
            current = self.store.pointer(plan.target)
            expected = current.version if current else 0

            def switch() -> dict[str, Any]:
                sc.guard()
                sc.store.check_lease(lease)
                pointer = self.store.activate(
                    plan.target,
                    snapshot_ref=snapshot.ref,
                    snapshot_sha256=snapshot.sha256,
                    receipt=receipt,
                    index_ref=index.ref,
                    expected_version=expected,
                    actor=sc.job.created_by,
                )
                return {"version": pointer.version}

            result = self.deps.audit.guard(
                kind="activation",
                operation_id=f"activate-{receipt.receipt_id}",
                actor=sc.job.created_by,
                target=plan.target,
                run_id=sc.job.job_id,
                mutation=switch,
                payload={"receipt_id": receipt.receipt_id, "snapshot_sha256": snapshot.sha256, "index": index.ref},
            )
        finally:
            sc.store.release_lease(lease)
        return StageResult(artifact_refs=(f"pointer:{plan.target}@{result['version']}",), data=result)

    # --- service interface ------------------------------------------------------------------

    def submit(self, ctx: RequestContext, snapshot_ref: str, target: str) -> JobHandle:
        """Submit a publication job for a frozen snapshot (requires ``graph.publish``)."""
        snapshot = self.deps.snapshots.load(snapshot_ref)
        inputs = OperationInputs(
            corpus_scope=(snapshot.corpus,),
            source_sha256=snapshot.sha256,
            source_version=snapshot.manifest.snapshot_id,
            params={"snapshot_ref": snapshot.ref, "target": target},
        )
        return self.jobs.submit(ctx, KIND, inputs)

    def current_plan(self, ctx: RequestContext, job_id: str) -> ChangePlan:
        """The plan a reviewer is asked to approve."""
        self.jobs.get(ctx, job_id)
        record = self.store.current_plan(job_id)
        if record is None:
            raise ContractError(ErrorCode.NOT_FOUND, "no plan yet")
        return record.plan

    def submit_approval(self, ctx: RequestContext, job_id: str, approval: ApprovalRecord) -> JobHandle:
        """Record a human approval for the job's current plan and re-queue the job.

        Only a human context (interactive or CLI) with ``curation.approve`` may call this; the approval
        itself is created by ``security.authorization.create_approval``. A model or tool has no path here.

        Raises:
            ContractError: ``DENIED``; ``NOT_FOUND``; ``STALE_APPROVAL`` when the approval is for another plan
                or when the snapshot, schema, vocabulary or target revision moved since the plan. In the
                latter case the job is re-queued first, so the runner recomputes the plan and waits for the
                approval of the new one (B27); the approver reviews that plan next.
        """
        if ctx.actor_kind not in (ActorKind.INTERACTIVE, ActorKind.CLI):
            raise ContractError(ErrorCode.DENIED, "approvals are recorded by a human, not a worker or model")
        if not ctx.has("curation.approve"):
            raise ContractError(ErrorCode.DENIED, "curation.approve is required")
        job = self.jobs.store.get(job_id)
        self.jobs.get(ctx, job_id)
        record = self.store.current_plan(job_id)
        if record is None:
            raise ContractError(ErrorCode.NOT_FOUND, "no plan to approve yet")
        if ctx.principal.subject != "local-operator" and not set(record.plan.corpus_scope) <= set(
            ctx.corpus_scope
        ):
            raise ContractError(ErrorCode.DENIED, "plan scope is outside the approver's scope")
        if approval.plan_sha256 != record.plan.plan_sha256 or approval.plan_id != record.plan.plan_id:
            raise ContractError(
                ErrorCode.STALE_APPROVAL, "approval is for a different plan; review the current one"
            )
        now = datetime.fromtimestamp(self.deps.clock(), tz=UTC)
        self.deps.verify_approval(approval, record.plan, now)
        probe_ctx = self.deps.ctx_for(job)
        stale = []
        if self.deps.probe.schema_fingerprint() != record.schema_fingerprint:
            stale.append("schema_changed")
        if self.deps.probe.vocabulary_revision() != record.vocabulary_revision:
            stale.append("vocabulary_changed")
        if self.deps.snapshots.load(str(job.inputs.params["snapshot_ref"])).sha256 != record.plan.input_sha256:
            stale.append("snapshot_changed")
        started = self.jobs.store.stage(job_id, "import")
        if (
            not started
            and self.deps.step.target_revision(probe_ctx, record.plan.target) != record.plan.base_revision
        ):
            stale.append("target_revision_changed")
        if stale:
            self.store.invalidate_approvals(reason=f"stale: {', '.join(stale)}", job_id=job_id)
            if job.state is JobState.AWAITING_REVIEW:
                self.jobs.resume(ctx, job_id)  # B27: the runner re-plans and waits for the new approval
            raise ContractError(
                ErrorCode.STALE_APPROVAL,
                f"candidate changed since planning ({', '.join(stale)}); the plan is being recomputed, "
                "review and approve the new plan",
            )
        self.store.record_approval(job_id, approval)
        return self.jobs.resume(ctx, job_id)

    def pointer(self, target: str) -> Pointer | None:
        """The active publication pointer of ``target``."""
        return self.store.pointer(target)

    def rollback(self, ctx: RequestContext, target: str, *, reason: str) -> Pointer:
        """Restore the previous active pointer (human, ``graph.publish``, audited). Candidates are retained."""
        if ctx.actor_kind not in (ActorKind.INTERACTIVE, ActorKind.CLI) or not ctx.has("graph.publish"):
            raise ContractError(ErrorCode.DENIED, "rollback requires a human with graph.publish")
        return self.deps.audit.guard(
            kind="pointer_rollback",
            operation_id=f"rollback-{target}-{int(self.deps.clock())}",
            actor=ctx.principal.subject,
            target=target,
            payload={"reason": reason},
            mutation=lambda: self.store.rollback(target, actor=ctx.principal.subject, reason=reason),
        )

    def _on_scope_change(self, change: dict[str, Any]) -> None:
        """Handle a scope change or revocation.

        Dependent approvals are invalidated; nothing falls back to a server credential.
        """
        reason = str(change.get("reason") or "scope changed")
        subject = change.get("subject")
        self.store.invalidate_approvals(
            reason=reason,
            job_id=change.get("job_id"),
            subject=subject,
            corpora=() if subject else (change.get("corpus_scope") or ()),
        )


__all__ = ("KIND", "PUBLISH_STAGES", "Publication", "PublicationDeps", "default_approval_verifier", "operation_id")
