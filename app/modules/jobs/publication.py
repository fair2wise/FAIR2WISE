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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Any

from ..contracts.changes import ApprovalRecord, ChangePlan, ImportReceipt
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
from .publish_store import PlanRecord, Pointer, PublishStore
from .service import JobService
from .store import JobRecord

if TYPE_CHECKING:
    from .publish_store import PlanRecord

logger = logging.getLogger(__name__)

KIND = "publish"
#: ``ContractError.reason`` of a pointer-only rollback refused because the deployment has a release marker.
ROLLBACK_REPLACEMENT_REQUIRED = "rollback_replacement_required"
#: Corpus removal (retire-all) and purge are separate jobs with their own plan and approval (Q3).
RETIRE_KIND = "corpus_retire"
PURGE_KIND = "corpus_purge"
OPERATION_KIND_NAMES = {RETIRE_KIND: "retire", PURGE_KIND: "purge"}
OPERATION_STAGES = ("plan", "review", "apply", "record")
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
        #: The step keeps a Tiled release marker (replacement importer): pointer and marker move together.
        self.replacement = bool(getattr(deps.step, "has_release_marker", False))
        if hasattr(deps.step, "plan_operation"):
            self.operation_pipeline = Pipeline(self._operation_specs())
            for kind in OPERATION_KIND_NAMES:
                jobs.register(kind, self.operation_pipeline, capability="graph.publish")
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
        extra: dict[str, Any] = {}
        if getattr(self.deps.step, "plan_options", False):
            extra["options"] = dict(self._params(sc).get("options") or {})
        plan = self.deps.step.plan(ctx, snapshot, target, operation_id=f"plan-{job.job_id}", **extra)
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
    def _effect_id(record: PlanRecord | None) -> str:
        return f"apply:{record.plan.plan_sha256}" if record is not None else "apply:none"

    def _freshness(self, sc: StageContext, record: Any, *, check_target: bool) -> list[str]:  # noqa: ANN401  # Any: plan record: a PlanRecord or None
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
        if check_target and self._current_revision(sc.job, plan) != plan.base_revision:
            reasons.append("target_revision_changed")
        return reasons

    def _current_revision(self, job: JobRecord, plan: ChangePlan) -> str:
        """Current target revision; a scoped importer answers per plan (``plan_revision``), else per target."""
        ctx = self.deps.ctx_for(job)
        scoped = getattr(self.deps.step, "plan_revision", None)
        if scoped is not None:
            return str(scoped(ctx, plan))
        return self.deps.step.target_revision(ctx, plan.target)

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

    def _approval_for(self, sc: StageContext, record: PlanRecord) -> ApprovalRecord:
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

    def _plan_record(self, sc: StageContext) -> PlanRecord:
        record = self.store.current_plan(sc.job.job_id)
        if record is None:
            raise ContractError(ErrorCode.NOT_FOUND, "no plan yet")
        return record

    def _import(self, sc: StageContext) -> StageResult:
        record = self._plan_record(sc)
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
        record = self._plan_record(sc)
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
        record = self._plan_record(sc)
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
        result: dict[str, Any] = {"receipt_id": stored.receipt_id}
        reporter = getattr(self.deps.step, "report", None)
        if reporter is not None:
            # Q14: a foreign record that changed during the run is reported here, never a failure.
            result["report"] = reporter(ctx, plan, stored)
        return StageResult(artifact_refs=(f"receipt:{stored.receipt_id}",), data=result)

    def _activate(self, sc: StageContext) -> StageResult:
        record = self._plan_record(sc)
        plan, ctx = record.plan, self.deps.ctx_for(sc.job)
        receipt = self.store.receipt_for(plan.plan_sha256, plan.target)
        if receipt is None or not receipt.verified:
            raise QuarantineStage("no verified receipt to activate")
        snapshot = self.deps.snapshots.load(str(self._params(sc)["snapshot_ref"]))
        if self.replacement:
            self._check_marker(sc, ctx, plan, snapshot)
        rollback_info = self._params(sc).get("rollback")
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
                    reason=f"rollback: {rollback_info.get('reason', '')}" if rollback_info else "activate",
                    action="rollback" if rollback_info else "activate",
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

    def _check_marker(self, sc: StageContext, ctx: RequestContext, plan: ChangePlan, snapshot: FrozenSnapshot) -> None:
        """TIL-14: the Tiled release marker must name the version the pointer is about to name."""
        marker = self.deps.step.release_marker(ctx, plan.corpus_scope[0])  # type: ignore[attr-defined]
        if (
            marker is None
            or marker.get("snapshot_id") != snapshot.manifest.snapshot_id
            or marker.get("plan_sha256") != plan.plan_sha256
        ):
            self.store.quarantine(
                sc.job.job_id,
                plan.target,
                "the Tiled release marker does not name the version being activated; pointer not switched",
                {"marker": marker, "snapshot_id": snapshot.manifest.snapshot_id},
            )
            raise QuarantineStage("release marker and pointer would disagree; the previous pointer stays active")

    def verify_agreement(self, ctx: RequestContext, target: str, scope: str) -> dict[str, Any]:
        """Whether F2W's pointer and the Tiled release marker name the same version (TIL-14).

        Returns ``{"agree", "pointer_snapshot_id", "marker_snapshot_id"}``; both ``None`` (and ``agree``)
        when nothing is live (before the first publication or after a corpus removal).
        """
        pointer = self.store.pointer(target)
        pointer_id = pointer.snapshot_ref.split("/")[-1] if pointer else None
        marker = self.deps.step.release_marker(ctx, scope) if self.replacement else None  # type: ignore[attr-defined]
        marker_id = marker.get("snapshot_id") if marker else None
        return {"agree": pointer_id == marker_id, "pointer_snapshot_id": pointer_id, "marker_snapshot_id": marker_id}

    # --- corpus removal and purge jobs ------------------------------------------------------

    def _operation_specs(self) -> list[StageSpec]:
        return [
            StageSpec("plan", self._op_plan, phase="validation", relevant=("params",)),
            StageSpec("review", self._op_review, phase="review", depends_on=("plan",)),
            StageSpec("apply", self._op_apply, phase="import", depends_on=("review",), always_run=False,
                      required_capability="graph.publish"),
            StageSpec("record", self._op_record, phase="import", depends_on=("apply",)),
        ]

    def _op_plan(self, sc: StageContext) -> StageResult:
        sc.require("graph.publish")
        params = self._params(sc)
        plan = self.deps.step.plan_operation(  # type: ignore[attr-defined]
            self.deps.ctx_for(sc.job), OPERATION_KIND_NAMES[sc.job.kind], params
        )
        self.store.save_plan(
            sc.job.job_id,
            plan,
            schema_fingerprint=self.deps.probe.schema_fingerprint(),
            vocabulary_revision=self.deps.probe.vocabulary_revision(),
        )
        return StageResult(
            artifact_refs=(f"plan:{plan.plan_sha256}",),
            data={"plan_id": plan.plan_id, "plan_sha256": plan.plan_sha256, "supersessions": plan.supersessions},
        )

    def _op_started(self, sc: StageContext) -> bool:
        record = self.store.current_plan(sc.job.job_id)
        return record is not None and sc.store.effect(sc.job.job_id, "apply", self._effect_id(record)) is not None

    def _op_review(self, sc: StageContext) -> StageResult:
        record = self.store.current_plan(sc.job.job_id)
        if record is None:
            raise QuarantineStage("no plan recorded")
        if not self._op_started(sc) and self._current_revision(sc.job, record.plan) != record.plan.base_revision:
            self.store.invalidate_approvals(reason="stale: target_revision_changed", job_id=sc.job.job_id)
            sc.store.invalidate(sc.lease, sc.job.job_id, OPERATION_STAGES, "stale: target_revision_changed")
            raise RestartStages("plan is stale (target_revision_changed); recomputing the plan for a new review")
        approval = self._approval_for(sc, record)
        return StageResult(data={"approval_id": approval.approval_id, "plan_sha256": record.plan.plan_sha256})

    def _op_apply(self, sc: StageContext) -> StageResult:
        record = self.store.current_plan(sc.job.job_id)
        if record is None:
            raise QuarantineStage("no plan recorded")
        approval = self._approval_for(sc, record)
        plan, ctx, step = record.plan, self.deps.ctx_for(sc.job), self.deps.step
        sc.require("graph.publish")
        op_id = operation_id(plan)
        lease = sc.store.acquire_lease(f"publish:{plan.target}", self.deps.owner)
        if lease is None:
            raise ContractError(ErrorCode.CONFLICT, "another publisher holds this target", retryable=True)

        def guard() -> None:
            sc.guard()
            sc.store.check_lease(lease)

        def effect() -> dict[str, Any]:
            def run() -> dict[str, Any]:
                outcome, receipt = step.apply_operation(  # type: ignore[attr-defined]
                    ctx, plan, approval, guard=guard,
                    pointer_to_none=(
                        (lambda: self.store.clear_pointer(plan.target, actor=sc.job.created_by,
                                                          reason=f"corpus removal {plan.plan_sha256[:12]}"))
                        if sc.job.kind == RETIRE_KIND else None
                    ),
                )
                return {**outcome.as_dict(), "receipt": receipt.model_dump(mode="json")}

            return self.deps.audit.guard(
                kind=f"scope_{OPERATION_KIND_NAMES[sc.job.kind]}",
                operation_id=op_id,
                actor=sc.job.created_by,
                target=plan.target,
                run_id=sc.job.job_id,
                payload={"plan_sha256": plan.plan_sha256, "approval_id": approval.approval_id,
                         "scope": list(plan.corpus_scope)},
                mutation=run,
            )

        try:
            result = sc.run_effect(self._effect_id(record), effect)
        finally:
            sc.store.release_lease(lease)
        return StageResult(effects=tuple(result.get("effects") or ()),
                           data={**result, "approval_id": approval.approval_id})

    def _op_record(self, sc: StageContext) -> StageResult:
        record = self.store.current_plan(sc.job.job_id)
        row = sc.store.stage(sc.job.job_id, "apply")
        if record is None or row is None or not row.result:
            raise QuarantineStage("no applied operation to record")
        receipt = ImportReceipt.model_validate(row.result["receipt"])
        if (not receipt.verified or receipt.plan_sha256 != record.plan.plan_sha256
                or receipt.target != record.plan.target):
            self.store.quarantine(sc.job.job_id, record.plan.target, "receipt does not match the plan", {})
            raise QuarantineStage("receipt does not match the plan or target")
        stored = self.store.save_receipt(sc.job.job_id, receipt)
        self.store.consume_approval(str(row.result.get("approval_id", "")))
        report = self.deps.step.operation_report(record.plan)  # type: ignore[attr-defined]
        return StageResult(artifact_refs=(f"receipt:{stored.receipt_id}",),
                           data={"receipt_id": stored.receipt_id, "report": report})

    def _submit_operation(self, ctx: RequestContext, kind: str, scope: str, target: str,
                          params: dict[str, Any]) -> JobHandle:
        if not hasattr(self.deps.step, "plan_operation"):
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, "this importer has no corpus operations")
        current = self.store.pointer(target)
        body = {"scope": scope, "target": target, **params, "after_pointer_version": current.version if current else 0}
        inputs = OperationInputs(corpus_scope=(scope,), params=body)
        return self.jobs.submit(ctx, kind, inputs)

    def submit_retire(self, ctx: RequestContext, scope: str, target: str, *, confirm_scope: str,
                      resolutions: Sequence[Mapping[str, Any]] = ()) -> JobHandle:
        """Submit a corpus removal (retire-all).

        The corpus name must be typed in as ``confirm_scope``; open edits block the plan unless each carries a
        reviewed ``discarded`` resolution.
        """
        return self._submit_operation(ctx, RETIRE_KIND, scope, target,
                                      {"confirm_scope": confirm_scope, "resolutions": [dict(r) for r in resolutions]})

    def submit_purge(self, ctx: RequestContext, scope: str, target: str, *, include_previous: bool = False) -> JobHandle:
        """Submit a purge of retired records (own plan and approval).

        ``include_previous`` is the second explicit choice a full purge after a corpus removal needs (the live
        version's predecessor is kept otherwise).
        """
        return self._submit_operation(ctx, PURGE_KIND, scope, target, {"include_previous": bool(include_previous)})

    # --- service interface ------------------------------------------------------------------

    def submit(
        self,
        ctx: RequestContext,
        snapshot_ref: str,
        target: str,
        *,
        adopt_legacy: bool = False,
        resolutions: Sequence[Mapping[str, Any]] = (),
        rollback_reason: str | None = None,
    ) -> JobHandle:
        """Submit a publication job for a frozen snapshot (requires ``graph.publish``).

        With a replacement importer a version change, a rollback and a roll-forward are all this call (a
        rollback names an older snapshot; the importer revives its retired records). ``adopt_legacy`` retires
        the one-time per-version imports of the corpus into its scope and always needs ``jobs.admin`` and an
        approval of the resulting plan. ``resolutions`` are reviewed :class:`ConflictResolution` dicts for edits
        a rollback would otherwise drop (Q15). The pointer version at submission is part of the job identity,
        so a repeated rollback or roll-forward is a new job while a retried submit stays idempotent.
        """
        if adopt_legacy and not ctx.has("jobs.admin"):
            raise ContractError(ErrorCode.DENIED, "legacy adoption needs jobs.admin and an explicit approval")
        snapshot = self.deps.snapshots.load(snapshot_ref)
        params: dict[str, Any] = {"snapshot_ref": snapshot.ref, "target": target}
        if self.replacement:
            current = self.store.pointer(target)
            params["after_pointer_version"] = current.version if current else 0
            options = {"adopt_legacy": adopt_legacy, "resolutions": [dict(r) for r in resolutions]}
            if adopt_legacy or resolutions:
                params["options"] = options
            if rollback_reason is not None:
                params["rollback"] = {"reason": rollback_reason}
        inputs = OperationInputs(
            corpus_scope=(snapshot.corpus,),
            source_sha256=snapshot.sha256,
            source_version=snapshot.manifest.snapshot_id,
            params=params,
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
        stale = []
        if self.deps.probe.schema_fingerprint() != record.schema_fingerprint:
            stale.append("schema_changed")
        if self.deps.probe.vocabulary_revision() != record.vocabulary_revision:
            stale.append("vocabulary_changed")
        operation = job.kind in OPERATION_KIND_NAMES
        if not operation and (
            self.deps.snapshots.load(str(job.inputs.params["snapshot_ref"])).sha256 != record.plan.input_sha256
        ):
            stale.append("snapshot_changed")
        started = self.jobs.store.stage(job_id, "apply" if operation else "import")
        if not started and self._current_revision(job, record.plan) != record.plan.base_revision:
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
        if self.replacement:
            raise ContractError(
                ErrorCode.CONFLICT,
                "this deployment moves a Tiled release marker with the pointer; a pointer-only rollback would "
                "leave them disagreeing (TIL-14): use rollback_replacement "
                "(POST /v1/publications/rollback-replacement)",
                retryable=False,
                reason=ROLLBACK_REPLACEMENT_REQUIRED,
            )
        return self.deps.audit.guard(
            kind="pointer_rollback",
            operation_id=f"rollback-{target}-{int(self.deps.clock())}",
            actor=ctx.principal.subject,
            target=target,
            payload={"reason": reason},
            mutation=lambda: self.store.rollback(target, actor=ctx.principal.subject, reason=reason),
        )

    def rollback_replacement(self, ctx: RequestContext, target: str, *, reason: str) -> JobHandle:
        """Roll back (or, a second time, forward) as a replacement plan to the previous pointer's version.

        The toggle is over the last two pointers (B28): the version named by the pointer one step back becomes
        the replacement source, so the importer revives its retired records (same Tiled UUIDs) and the marker
        and pointer move together. The job still plans, waits for approval, imports, verifies and activates.

        Raises:
            ContractError: ``DENIED`` (human with ``graph.publish`` required); ``CONFLICT`` when there is no
                previous pointer or this deployment keeps no release marker.
        """
        if ctx.actor_kind not in (ActorKind.INTERACTIVE, ActorKind.CLI) or not ctx.has("graph.publish"):
            raise ContractError(ErrorCode.DENIED, "rollback requires a human with graph.publish")
        if not self.replacement:
            raise ContractError(ErrorCode.CONFLICT, "this deployment has no replacement importer; use rollback")
        previous = self.store.previous_record(target)
        if previous is None:
            raise ContractError(ErrorCode.CONFLICT, "there is no previous pointer to roll back to")
        return self.submit(ctx, str(previous["snapshot_ref"]), target, rollback_reason=reason)

    def _on_scope_change(self, change: Mapping[str, Any]) -> None:
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


__all__ = ("ROLLBACK_REPLACEMENT_REQUIRED", "KIND", "OPERATION_KIND_NAMES", "OPERATION_STAGES", "PURGE_KIND", "PUBLISH_STAGES", "RETIRE_KIND", "Publication", "PublicationDeps", "default_approval_verifier", "operation_id")
