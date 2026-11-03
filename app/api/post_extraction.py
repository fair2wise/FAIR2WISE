"""Post-extraction publication: what replaced the Splash wipe-and-reimport (T18E, owner decision Q11).

After an extraction run has rebuilt the session KG, :class:`PostExtractionPublisher` freezes the next
snapshot ``v<N+1>`` (``relation = rebuild``, the scoped edit journal replayed onto it), submits a
publication job for it (the T10B :class:`~app.modules.jobs.replacement_step.ReplacementPublishStep` computes
the read-only replacement plan of :mod:`app.modules.graph.imports.corpus_scope`) and applies the plan under
the approval policy of :func:`decide_approval`:

* ``:local`` auto-approves **only a safe plan** (:attr:`ReplacementPlan.is_safe`: no boundary links, no legacy
  adoption). The approval is made by the local operator identity through ``create_approval`` (so it is a
  normal, plan-hash-bound, profile-bound record) and carries ``reason = "auto_safe_plan"``; it is never
  anonymous and is not a different kind of object.
* an unsafe plan on ``:local`` **waits for review**;
* on ALS the plan **always** waits for review: this module has no code path that creates an ALS approval.
  One verified admin approves through the jobs API (``/v1/publications/{job_id}/approval``) or the CLI.

Nothing here deletes anything: every write, including every removal of a record that is no longer in the
corpus, is part of the approved plan and is done by the importer under the plan's guards.

The result is a plain dict the legacy SSE phases ``splash_reimport_started`` / ``splash_reimport_result``
report (names kept for the frozen UI): ``status`` is ``success`` (published), ``awaiting_review`` or ``error``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import uuid

from ..modules.contracts.changes import ChangePlan
from ..modules.contracts.context import ActorKind, RequestContext
from ..modules.contracts.errors import ContractError
from ..modules.contracts.jobs import JobState
from ..modules.graph.edit_journal import EditJournal
from ..modules.graph.snapshot import SnapshotRequest, create_snapshot
from ..modules.graph.snapshot_manifest import SNAPSHOT_ID_RE
from ..modules.schema.registry import SchemaRegistry
from ..modules.security.authorization import DeploymentSettings, create_approval, local_operator_context
from .operations import F2WOperations, default_approval_ttl

#: ``ApprovalRecord.reason`` of an approval the ``:local`` policy made for the operator.
AUTO_SAFE_PLAN = "auto_safe_plan"

#: A job is driven at most this many times: plan, then (approval and import) once per stale re-plan (B27).
MAX_DRIVES = 6

STATUS_PUBLISHED = "success"
STATUS_REVIEW = "awaiting_review"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class ApprovalDecision:
    """What the policy decided for one plan: auto-approve, or wait for review and why."""

    auto_approve: bool
    why: str


def decide_approval(settings: DeploymentSettings, plan: ChangePlan, is_safe: Callable[[ChangePlan], bool]) -> ApprovalDecision:
    """The profile-aware approval policy of post-extraction publication.

    ALS never auto-approves, whatever the plan (``is_safe`` is not even consulted). ``:local`` auto-approves
    only when ``is_safe`` says the plan has no boundary links and no legacy adoption.
    """
    if not settings.is_local:
        return ApprovalDecision(False, "ALS publication always waits for a verified admin to approve the plan")
    if not is_safe(plan):
        return ApprovalDecision(False, "the plan has boundary links or legacy adoption: review required")
    return ApprovalDecision(True, AUTO_SAFE_PLAN)


def snapshot_freezer(*, storage_kg: Path, corpus: str, registry: SchemaRegistry | None = None,
                     edit_journal: EditJournal | None = None, journal_target: str | None = None,
                     code_commit: str | None = None, terms_ref: str | None = None) -> Callable[[Path], str]:
    """The default ``freeze`` of :class:`PostExtractionPublisher`: the next ``v<N+1>`` of ``corpus``.

    The newest existing snapshot of the corpus is the parent (``relation = rebuild``); the scoped edit
    ``edit_journal`` is replayed onto the rebuilt graph (T18D), so a Save made before the extraction survives it
    or is recorded as a conflict that blocks the import until reviewed.
    """
    def freeze(terms_path: Path) -> str:
        corpus_dir = Path(storage_kg) / corpus
        numbers = sorted(int(p.name[1:]) for p in corpus_dir.iterdir()
                         if SNAPSHOT_ID_RE.match(p.name)) if corpus_dir.is_dir() else []
        result = create_snapshot(SnapshotRequest(
            corpus=corpus, terms_path=Path(terms_path), storage_kg=Path(storage_kg),
            parent_snapshot_id=f"v{numbers[-1]}" if numbers else None, parent_relation="rebuild",
            edit_journal=edit_journal, journal_target=journal_target, journal_scope=(corpus,),
            terms_ref=terms_ref or f"private:session/{corpus}/terms.json", code_commit=code_commit,
            registry=registry))
        return f"{corpus}/{result.snapshot_id}"

    return freeze


class PostExtractionPublisher:
    """Freeze, plan and publish the corpus after an extraction run; see the module docstring.

    Args:
        operations: The deployment's :class:`F2WOperations` (jobs, publication, H5 gate, clock).
        freeze: Freezes the extracted terms as the next snapshot and returns its ``<corpus>/v<N>`` reference.
        is_safe: ``plan -> bool``, normally :meth:`ReplacementPublishStep.plan_is_safe`.
        target: Publication target (Tiled URL with optional ``#namespace``).
        worker_owner: Lease owner name used when this publisher drives the job itself.
    """

    def __init__(self, operations: F2WOperations, *, freeze: Callable[[Path], str],
                 is_safe: Callable[[ChangePlan], bool], target: str, worker_owner: str = "post-extraction") -> None:
        self.ops, self.freeze, self.is_safe = operations, freeze, is_safe
        self.target, self.worker_owner = target, worker_owner

    # -- the one entry point ----------------------------------------------------------------------

    def publish(self, ctx: RequestContext, terms_path: str | Path) -> dict[str, Any]:
        """Publish the result of an extraction run; never raises (an error is ``status = error``).

        ``ctx`` is the extracting caller: the local operator on ``:local``, a verified admin on ALS. It is
        used to submit the job; an approval is never made with it unless the policy allows (``:local``).
        """
        try:
            ref = self.freeze(Path(terms_path))
        except Exception as exc:  # a failed freeze leaves the session KG and the committed snapshots as they were
            return {"status": STATUS_ERROR, "stage": "freeze", "message": f"snapshot freeze failed: {exc}"}
        try:
            job_id = self.ops.submit_publication(ctx, ref, self.target).job_id
        except ContractError as exc:
            return {"status": STATUS_ERROR, "stage": "submit", "snapshot_ref": ref,
                    "message": f"publication refused: {exc.code.value}: {exc}"}
        auto_approved: list[str] = []
        for _ in range(MAX_DRIVES):
            try:
                self._drive(job_id)
                view = self.ops.job_view(ctx, job_id)
            except ContractError as exc:
                return {"status": STATUS_ERROR, "stage": "run", "job_id": job_id, "snapshot_ref": ref,
                        "message": f"publication stopped: {exc.code.value}: {exc}"}
            ui = view["ui_state"]
            if ui in ("published", "completed"):
                return self._result(STATUS_PUBLISHED, job_id, ref, auto_approved, view,
                                    f"published {ref} to Tiled")
            if ui != "staged":
                return self._result(STATUS_ERROR, job_id, ref, auto_approved, view,
                                    f"publication job {job_id} ended as {ui}")
            plan = self.ops.current_plan(ctx, job_id)
            decision = decide_approval(self.ops.settings, plan, self.is_safe)
            if not decision.auto_approve:
                return self._result(STATUS_REVIEW, job_id, ref, auto_approved, view,
                                    f"publication awaiting review ({decision.why}); plan {plan.plan_sha256[:12]}")
            try:
                auto_approved.append(self._auto_approve(job_id, plan))
            except ContractError as exc:
                return self._result(STATUS_ERROR, job_id, ref, auto_approved, view,
                                    f"auto-approval refused: {exc.code.value}: {exc}")
        return self._result(STATUS_REVIEW, job_id, ref, auto_approved, self.ops.job_view(ctx, job_id),
                            "publication kept changing under the run; awaiting review")

    # -- internals --------------------------------------------------------------------------------

    def _drive(self, job_id: str) -> None:
        """Run queued jobs until ``job_id`` is no longer runnable (a concurrent worker may take it first)."""
        jobs = self.ops.jobs
        for _ in range(MAX_DRIVES):
            handle = jobs.store.get(job_id).handle()
            if handle.state is not JobState.QUEUED:
                return
            if jobs.work_one(self.worker_owner) is None:
                return

    def _auto_approve(self, job_id: str, plan: ChangePlan) -> str:
        """Record the local operator's ``auto_safe_plan`` approval and hand it to the job; returns its id."""
        settings = self.ops.settings
        operator = local_operator_context(settings, request_id=f"auto-{job_id}", actor_kind=ActorKind.INTERACTIVE)
        ttl = default_approval_ttl(settings)
        now: datetime = self.ops.clock()
        approval = create_approval(
            operator, plan, settings, approval_id="appr-" + uuid.uuid4().hex, clock=self.ops.clock,
            operation_id=job_id, expires_at=(now + ttl) if ttl is not None else None, reason=AUTO_SAFE_PLAN)
        self.ops.apply_approval(operator, plan, approval)
        return approval.approval_id

    @staticmethod
    def _result(status: str, job_id: str, ref: str, auto: list[str], view: dict[str, Any],
                message: str) -> dict[str, Any]:
        detail = view.get("publication") or {}
        return {"status": status, "message": message, "job_id": job_id, "snapshot_ref": ref,
                "ui_state": view["ui_state"], "auto_approved": bool(auto), "approval_ids": list(auto),
                "plan": detail.get("plan"), "receipt_id": detail.get("receipt_id")}


__all__ = ("AUTO_SAFE_PLAN", "ApprovalDecision", "PostExtractionPublisher", "decide_approval", "snapshot_freezer")
