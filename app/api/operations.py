"""The shared job, review and publication service contract for HTTP and direct use (task T14).

:class:`F2WOperations` is the one object the ``/v1`` routes, the operations CLI
(``python -m app.api.cli``) and direct Python callers (SDK, notebooks, FORGE) call. Every
method takes the server-resolved :class:`~app.modules.contracts.context.RequestContext`
first and forwards to the T10 services, so the same authorization runs on every path:

* jobs: :class:`~app.modules.jobs.service.JobService` (kind capability, creator/scope
  visibility, cancel and resume);
* review: :func:`~app.modules.curation.reviewer.review_case` and
  :func:`~app.modules.curation.approval.create_cost_approval` (human only,
  ``curation.approve``, bound to the case revision);
* publication: :class:`~app.modules.jobs.publication.Publication`, with approvals created by
  :func:`~app.modules.security.authorization.create_approval` for the exact ``plan_sha256``
  the reviewer was shown;
* audit: :meth:`~app.modules.provenance.recorder.AuditRecorder.status`;
* Knowledge Operations (T19E): :class:`~app.modules.operations.knowledge.KnowledgeOperations`
  (the five T19C read Tools) for direct bounded results and for the ``knowledge_operation``
  job kind (``register_knowledge_jobs``; ``ops.run``).

Nothing here parses HTTP, and no route holds an algorithm of its own. The only logic added
here is the H5 publication gate (:class:`PublicationGate`) and the read-only UI mapping of a
job to staged/approved/published (:meth:`F2WOperations.job_view`).

**H5.** Real publication stays gated until the owner's H5 decision: a publication target
must be on this deployment's Tiled *and* in the gate's rehearsal allowlist, which only
trusted Python wiring (a test fixture or rehearsal harness) can set. The default gate
allows no target, so the routes can plan, approve and roll back only against fixture or
disposable targets.

Importing this module has no side effects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
import time
from typing import Any, cast
import uuid

from ..modules.contracts.changes import ApprovalRecord, ChangePlan
from ..modules.contracts.context import LOCAL_OPERATOR, ActorKind, RequestContext
from ..modules.contracts.errors import ContractError, ErrorCode
from ..modules.contracts.jobs import JobEvent, JobHandle, JobState
from ..modules.contracts.tools import ToolResult
from ..modules.jobs.keys import OperationInputs
from ..modules.jobs.service import JobService
from ..modules.security.authorization import (
    DeploymentSettings,
    create_approval,
    local_operator_context,
    require,
    target_in_deployment,
)
from ..modules.security.tiled_identity import TiledCredential, TiledIdentityPort, normalize_tiled_target

Clock = Callable[[], datetime]

#: Lifetime of a publication approval made through this interface on ALS (owner decision
#: O-T2, 2026-10-30: 24 h on ALS, no expiry on ``:local``; chat approvals never expire, H2 Q8).
ALS_APPROVAL_TTL = timedelta(hours=24)
#: Kept for callers that import the old name; equals the ALS lifetime.
DEFAULT_APPROVAL_TTL = ALS_APPROVAL_TTL
#: Sentinel: resolve the approval lifetime from the deployment profile.
PROFILE_APPROVAL_TTL: Any = ...


def default_approval_ttl(settings: DeploymentSettings) -> timedelta | None:
    """Approval lifetime for ``settings``: ``None`` (never expires) on ``:local``, 24 h on ALS."""
    return None if settings.is_local else ALS_APPROVAL_TTL
H5_MESSAGE = ("publication to {target} is gated at H5: only approved rehearsal targets (fixture or disposable "
              "Tiled) are enabled on this deployment")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _base(target: str) -> str | None:
    try:
        return normalize_tiled_target(target.split("#", 1)[0])
    except ValueError:
        return None


@dataclass(frozen=True)
class PublicationGate:
    """H5 gate: the publication targets this deployment may plan, approve or roll back.

    Attributes:
        rehearsal_targets: Tiled base URLs (optionally ``#namespace``) of fixture or disposable
            servers. Empty by default; there is no environment variable or request field for it.
    """

    rehearsal_targets: frozenset[str] = field(default_factory=frozenset)

    def allows(self, target: str) -> bool:
        """Return whether ``target`` is an approved rehearsal target."""
        if target in self.rehearsal_targets:
            return True
        base = _base(target)
        if base is None:
            return False
        for allowed in self.rehearsal_targets:
            name = _base(allowed)
            if name is None:
                continue
            namespace = allowed.split("#", 1)[1] if "#" in allowed else None
            in_base = base == name or base.startswith(name + "/")
            if in_base and (namespace is None or target.endswith("#" + namespace)):
                return True
        return False

    def check(self, target: str, settings: DeploymentSettings) -> None:
        """Refuse a target outside the deployment (``DENIED``) or not a rehearsal target (H5)."""
        if not target_in_deployment(target, settings):
            raise ContractError(ErrorCode.DENIED, "target is not on this deployment's Tiled")
        if not self.allows(target):
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, H5_MESSAGE.format(target=target))


def _json(model: Any) -> Any:
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


class F2WOperations:
    """Jobs, review, publication and audit for one deployment.

    Args:
        settings: Startup ``DeploymentSettings`` (profile, Tiled target, writer identity).
        jobs: The job service (kinds registered by trusted wiring only).
        publication: The publication job, or ``None`` when publication is not configured.
        curation: The uncertainty/review store, or ``None``.
        audit: The write-ahead audit recorder, or ``None``.
        identity_port: ALS identity port (fresh role check when an approval is recorded).
        publication_gate: H5 gate; the default allows no target.
        clock: Current UTC time (approval times and expiries).
        approval_ttl: Lifetime of publication approvals created here; by default resolved from
            the profile (``None`` = never expires on ``:local``, 24 h on ALS, O-T2).
        knowledge: The deployment's ``KnowledgeOperations`` (T19E), or ``None`` when no graph
            port is configured (the knowledge methods then raise ``UNSUPPORTED_CAPABILITY``).
    """

    def __init__(self, settings: DeploymentSettings, jobs: JobService, *, publication: Any = None,
                 curation: Any = None, audit: Any = None, identity_port: TiledIdentityPort | None = None,
                 publication_gate: PublicationGate | None = None, clock: Clock = _utcnow,
                 approval_ttl: timedelta | None = PROFILE_APPROVAL_TTL, knowledge: Any = None) -> None:
        """Bind the services; see the class docstring for the arguments.

        Raises:
            ValueError: If ``publication`` is registered on another ``JobService``.
        """
        if publication is not None and publication.jobs is not jobs:
            raise ValueError("publication must be registered on the same JobService")
        self.settings = settings
        self.jobs = jobs
        self.publication = publication
        self.curation = curation
        self.audit = audit
        self.identity_port = identity_port
        self.gate = publication_gate or PublicationGate()
        self.clock = clock
        self.approval_ttl: timedelta | None = (default_approval_ttl(settings) if approval_ttl is PROFILE_APPROVAL_TTL
                                                  else approval_ttl)
        self.knowledge = knowledge

    # --- contexts ---------------------------------------------------------------------

    def cli_context(self, *, request_id: str | None = None) -> RequestContext:
        """The CLI caller's context: the local operator on ``:local``; refused on ALS.

        ALS has no CLI sign-in; an ALS caller uses the HTTP API with a browser session.
        """
        if not self.settings.is_local:
            raise ContractError(ErrorCode.DENIED, "the operations CLI runs on the local profile only")
        return local_operator_context(self.settings, request_id=request_id or uuid.uuid4().hex,
                                      actor_kind=ActorKind.CLI)

    def notify_scope_change(self, change: Mapping[str, Any]) -> None:
        """A credential was revoked or a role/scope changed: invalidate dependent approvals."""
        self.jobs.notify_scope_change(dict(change))

    # --- jobs ---------------------------------------------------------------------------

    def submit_job(self, ctx: RequestContext, kind: str, inputs: OperationInputs | Mapping[str, Any], *,
                   settings_revision: str | None = None) -> JobHandle:
        """Register a job of a registered ``kind`` (idempotent for identical inputs)."""
        if not isinstance(inputs, OperationInputs):
            try:
                inputs = OperationInputs.from_dict(inputs)
            except (TypeError, ValueError) as exc:
                raise ContractError(ErrorCode.INVALID_INPUT, f"invalid job inputs: {exc}") from None
        return self.jobs.submit(ctx, kind, inputs, settings_revision=settings_revision)

    def submit_paper_job(self, ctx: RequestContext, kind: str, params: Mapping[str, Any]) -> JobHandle:
        """Submit an enrichment, topic-sweep or PDF-parse job (B5/J-11, RT-27..RT-32; ``jobs.admin``).

        Status and stop are the ordinary job routes (``GET /v1/jobs/{job_id}``, ``.../cancel``).

        Raises:
            ContractError: ``INVALID_INPUT`` (unknown kind or bad parameters); ``DENIED`` (no ``jobs.admin``);
                ``UNSUPPORTED_CAPABILITY`` when no handler is registered for the kind on this deployment.
        """
        from ..modules.jobs.paper_jobs import PAPER_JOB_CAPABILITY, job_inputs

        inputs = job_inputs(kind, params)
        require(ctx, PAPER_JOB_CAPABILITY)
        if not self.jobs.has_kind(kind):
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, f"{kind} jobs are not registered on this deployment")
        return self.jobs.submit(ctx, kind, inputs)

    def list_jobs(self, ctx: RequestContext, *, state: str | None = None, kind: str | None = None,
                  limit: int = 100) -> list[JobHandle]:
        """Most recently updated jobs the caller may see (creator or ``jobs.admin``, corpus scope)."""
        try:
            wanted = JobState(state) if state else None
        except ValueError:
            raise ContractError(ErrorCode.INVALID_INPUT, f"unknown job state {state!r}") from None
        limit = max(1, min(int(limit), 1000))
        return [job.handle() for job in self.jobs.list_visible(ctx, state=wanted, kind=kind, limit=limit)]

    def get_job(self, ctx: RequestContext, job_id: str) -> JobHandle:
        """The job's handle."""
        return self.jobs.get(ctx, job_id)

    def job_events(self, ctx: RequestContext, job_id: str, *, after: int = -1) -> list[JobEvent]:
        """Durable events after sequence ``after``."""
        return self.jobs.events(ctx, job_id, after=after)

    def job_progress(self, ctx: RequestContext, job_id: str) -> dict[str, Any]:
        """Per-phase progress."""
        return self.jobs.progress(ctx, job_id)

    def cancel_job(self, ctx: RequestContext, job_id: str) -> JobHandle:
        """Cancel now, or request a stop at the next boundary when a worker holds the job."""
        return self.jobs.cancel(ctx, job_id)

    def resume_job(self, ctx: RequestContext, job_id: str, *,
                   expected_revision: int | None = None) -> JobHandle:
        """Re-queue a paused, review-resolved or failed job."""
        return self.jobs.resume(ctx, job_id, expected_revision=expected_revision)

    def job_view(self, ctx: RequestContext, job_id: str) -> dict[str, Any]:
        """UI view of one job: handle, progress, and staged/approved/published state.

        ``ui_state`` is one of ``queued``, ``running``, ``paused``, ``awaiting_review``,
        ``staged`` (a publication plan waits for approval), ``approved`` (a valid approval is
        recorded for the current plan and nothing is imported yet), ``published`` (receipted and
        the active pointer),
        ``superseded`` (receipted but no longer the active pointer), ``completed``,
        ``failed``, ``cancelled`` and ``quarantined``. Staged never reads as published.
        """
        handle = self.jobs.get(ctx, job_id)
        job = self.jobs.store.get(job_id)
        progress = self.jobs.progress(ctx, job_id)
        state = handle.state
        ui_state = {
            JobState.QUEUED: "queued", JobState.RUNNING: "running", JobState.PAUSED_BUDGET: "paused",
            JobState.PAUSED_PROVIDER: "paused", JobState.AWAITING_REVIEW: "awaiting_review",
            JobState.SUCCEEDED: "completed", JobState.FAILED: "failed", JobState.CANCELLED: "cancelled",
        }[state]
        view: dict[str, Any] = {"job": _json(handle), "ui_state": ui_state, "pause_reason": job.pause_reason,
                                "stop_requested": job.stop_requested, "progress": progress, "publication": None}
        pub = self.publication
        if pub is None or job.kind != "publish":
            return view
        store = pub.store
        record = store.current_plan(job_id)
        quarantined = store.quarantined(job_id)
        detail: dict[str, Any] = {"target": job.inputs.params.get("target"),
                                  "snapshot_ref": job.inputs.params.get("snapshot_ref"),
                                  "plan": None, "approvals": [], "receipt_id": None, "active": False,
                                  "quarantine": [{"subject": q.get("subject"), "reason": q.get("reason")}
                                                 for q in quarantined]}
        if record is not None:
            plan = record.plan
            detail["plan"] = {"plan_id": plan.plan_id, "plan_sha256": plan.plan_sha256, "target": plan.target,
                              "creates": plan.creates, "links": plan.links, "supersessions": plan.supersessions,
                              "corpus_scope": list(plan.corpus_scope)}
            valid = store.valid_approvals(job_id, plan)
            detail["approvals"] = [{"approval_id": a.approval_id, "approved_by": a.approved_by,
                                    "expires_at": a.expires_at.isoformat() if a.expires_at else None}
                                   for a in valid]
            receipt = store.receipt_for(plan.plan_sha256, plan.target)
            if receipt is not None:
                pointer = store.pointer(plan.target)
                detail["receipt_id"] = receipt.receipt_id
                detail["active"] = bool(pointer and pointer.receipt_id == receipt.receipt_id)
            if state is JobState.AWAITING_REVIEW:
                view["ui_state"] = "approved" if valid else "staged"
            elif state is JobState.QUEUED and valid and receipt is None:
                view["ui_state"] = "approved"  # re-queued after the approval, not yet imported
            elif state is JobState.SUCCEEDED and receipt is not None:
                view["ui_state"] = "published" if detail["active"] else "superseded"
        if quarantined and state is not JobState.SUCCEEDED:
            view["ui_state"] = "quarantined"
        view["publication"] = detail
        return view

    # --- curation review --------------------------------------------------------------------

    def _curation(self) -> Any:
        if self.curation is None:
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, "curation review is not configured")
        return self.curation

    def _case_scope(self, ctx: RequestContext) -> list[str]:
        """Corpora the caller reviews: all stored corpora for the local operator (as review_case)."""
        store = self._curation()
        if ctx.principal.subject == LOCAL_OPERATOR and ctx.mode.value == "local_trusted":
            return [r[0] for r in store.db.query("SELECT DISTINCT corpus FROM uncertainty_cases")]
        return sorted(ctx.corpus_scope)

    def curation_summary(self, ctx: RequestContext) -> dict[str, Any]:
        """Review queue counts by state and route, and the remote-cost ledger summary."""
        require(ctx, "curation.approve")
        store = self._curation()
        return {**store.summary(self._case_scope(ctx)), "budget": store.ledger.summary()}

    def list_cases(self, ctx: RequestContext, *, state: str | None = None, route: str | None = None,
                   limit: int = 100) -> list[dict[str, Any]]:
        """Bounded case summaries within the caller's scope."""
        require(ctx, "curation.approve")
        return cast(list[dict[str, Any]], self._curation().list_cases(self._case_scope(ctx), state=state, route=route, limit=limit))

    def get_case(self, ctx: RequestContext, case_id: str) -> dict[str, Any]:
        """One case with its attempts and history (``revision`` is what a decision binds to)."""
        require(ctx, "curation.approve")
        store = self._curation()
        case = store.get_case(case_id, self._case_scope(ctx))
        return {"case": case, "attempts": store.attempts(case_id), "events": store.events(case_id)}

    def review_case(self, ctx: RequestContext, case_id: str, *, bound_revision: str, accept: bool,
                    resolution: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
        """Accept or reject a case (``review_case``; write-ahead audited when audit is configured)."""
        from ..modules.curation.reviewer import review_case

        return review_case(ctx, self._curation(), case_id, bound_revision=bound_revision, accept=accept,
                           resolution=resolution, reason=reason, recorder=self.audit)

    def create_cost_approval(self, ctx: RequestContext, *, scope: Iterable[str], models: Iterable[str],
                             cap_usd: float, expires_at: datetime) -> Any:
        """Record a human's remote-cost approval (``create_cost_approval``)."""
        from ..modules.curation.approval import create_cost_approval

        return create_cost_approval(ctx, self._curation(), scope=scope, models=models, cap_usd=cap_usd,
                                    expires_at=expires_at, now=self.clock())

    def revoke_cost_approval(self, ctx: RequestContext, approval_id: str) -> dict[str, Any]:
        """Revoke a cost approval (human with ``curation.approve`` over its corpora)."""
        from ..modules.curation.approval import load_cost_approval

        if ctx.actor_kind not in (ActorKind.INTERACTIVE, ActorKind.CLI):
            raise ContractError(ErrorCode.DENIED, "cost approvals are revoked by a human")
        require(ctx, "curation.approve")
        store = self._curation()
        approval, _status = load_cost_approval(store, approval_id)
        if ctx.principal.subject != LOCAL_OPERATOR and not set(approval.scope) <= set(ctx.corpus_scope):
            raise ContractError(ErrorCode.DENIED, "corpus is outside the caller's scope")
        store.revoke_cost_approval(approval_id)
        return {"approval_id": approval_id, "status": "revoked"}

    # --- publication -------------------------------------------------------------------------

    def _publication(self) -> Any:
        if self.publication is None:
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                "publication is not configured on this deployment (H5)")
        return self.publication

    def submit_publication(self, ctx: RequestContext, snapshot_ref: str, target: str) -> JobHandle:
        """Submit a publication job for a frozen snapshot to a rehearsal target (H5 gate)."""
        require(ctx, "graph.publish")
        pub = self._publication()
        self.gate.check(target, self.settings)
        return cast(JobHandle, pub.submit(ctx, snapshot_ref, target))

    def current_plan(self, ctx: RequestContext, job_id: str) -> ChangePlan:
        """The plan a reviewer is asked to approve."""
        return cast(ChangePlan, self._publication().current_plan(ctx, job_id))

    def approve_publication(self, ctx: RequestContext, job_id: str, plan_sha256: str, *,
                            approver_credential: TiledCredential | None = None,
                            expires_at: datetime | None = None) -> dict[str, Any]:
        """Approve the job's current plan, bound to the ``plan_sha256`` the reviewer was shown.

        The approval is created by ``security.authorization.create_approval`` (human, verified
        admin after a fresh role check on ALS, profile- and target-bound) and handed to
        ``Publication.submit_approval``, which re-checks staleness and re-queues the job.

        Raises:
            ContractError: ``STALE_APPROVAL`` when the current plan is not ``plan_sha256``;
                ``DENIED``; ``UNSUPPORTED_CAPABILITY`` (H5 gate); ``NOT_FOUND``.
        """
        pub = self._publication()
        plan = pub.current_plan(ctx, job_id)
        if plan.plan_sha256 != plan_sha256:
            raise ContractError(ErrorCode.STALE_APPROVAL,
                                "the plan changed since it was shown; review the current plan")
        self.gate.check(plan.target, self.settings)
        now = self.clock()
        approval: ApprovalRecord = create_approval(
            ctx, plan, self.settings, approval_id="appr-" + uuid.uuid4().hex, clock=self.clock,
            identity_port=self.identity_port, approver_credential=approver_credential, operation_id=job_id,
            expires_at=expires_at or (now + self.approval_ttl if self.approval_ttl is not None else None),
        )
        handle = pub.submit_approval(ctx, job_id, approval)
        return {"approval": _json(approval), "job": _json(handle)}

    def apply_approval(self, ctx: RequestContext, plan: ChangePlan, approval: ApprovalRecord) -> JobHandle:
        """Hand an existing approval to the publication job that planned ``plan`` (SDK ``publish_plan``).

        The approval must have been created by ``security.authorization.create_approval`` (a human,
        verified on ALS) and must name the job in ``operation_id``. ``approval.authorizes(plan)`` is
        checked first; the job's ``Publication.submit_approval`` re-checks the human context,
        ``curation.approve``, the scope and the staleness of plan, snapshot, schema, vocabulary and
        target, then re-queues the job. The import itself runs in the job (the worker); the
        ``ImportReceipt`` is read from ``job_view`` once the job has succeeded. Nothing writes a graph
        here.

        Raises:
            ContractError: ``INVALID_INPUT`` (no ``operation_id``); ``STALE_APPROVAL`` (the approval does
                not cover ``plan`` now); ``UNSUPPORTED_CAPABILITY`` (H5 gate); ``DENIED``; ``NOT_FOUND``.
        """
        pub = self._publication()
        if not approval.operation_id:
            raise ContractError(ErrorCode.INVALID_INPUT, "the approval names no publication job (operation_id)")
        if not approval.authorizes(plan, self.clock()):
            raise ContractError(ErrorCode.STALE_APPROVAL, "the approval does not cover this plan (or has expired)")
        self.gate.check(plan.target, self.settings)
        return cast(JobHandle, pub.submit_approval(ctx, approval.operation_id, approval))

    def publication_pointer(self, ctx: RequestContext, target: str) -> dict[str, Any] | None:
        """The active publication pointer of ``target`` (``None`` when nothing is active)."""
        require(ctx, "chat.read_support")
        pointer = self._publication().pointer(target)
        if pointer is None:
            return None
        return {"target": pointer.name, "version": pointer.version, **pointer.as_dict()}

    def rollback_publication(self, ctx: RequestContext, target: str, *, reason: str) -> dict[str, Any]:
        """Restore the previous active pointer of a rehearsal target (human, ``graph.publish``)."""
        require(ctx, "graph.publish")
        pub = self._publication()
        self.gate.check(target, self.settings)
        pointer = pub.rollback(ctx, target, reason=reason)
        return {"target": pointer.name, "version": pointer.version, **pointer.as_dict()}

    def rollback_publication_replacement(self, ctx: RequestContext, target: str, *, reason: str) -> JobHandle:
        """Roll back (or, repeated, forward) as a replacement plan to the previous pointer's version (T10B).

        Returns the publication job; it plans, waits for ``/v1/publications/{job_id}/approval`` and then
        revives the retired records of that version, moving the Tiled release marker and the pointer together.
        """
        require(ctx, "graph.publish")
        pub = self._publication()
        self.gate.check(target, self.settings)
        return cast(JobHandle, pub.rollback_replacement(ctx, target, reason=reason))

    def submit_corpus_retire(self, ctx: RequestContext, scope: str, target: str, *, confirm_scope: str,
                             resolutions: Sequence[Mapping[str, Any]] = ()) -> JobHandle:
        """Submit a corpus removal (retire-all; the corpus name typed in as ``confirm_scope``)."""
        require(ctx, "graph.publish")
        pub = self._publication()
        self.gate.check(target, self.settings)
        return cast(JobHandle, pub.submit_retire(ctx, scope, target, confirm_scope=confirm_scope,
                                                 resolutions=resolutions))

    def submit_corpus_purge(self, ctx: RequestContext, scope: str, target: str, *,
                            include_previous: bool = False) -> JobHandle:
        """Submit a purge of the retired records of a corpus (its own plan and approval, Q3)."""
        require(ctx, "graph.publish")
        pub = self._publication()
        self.gate.check(target, self.settings)
        return cast(JobHandle, pub.submit_purge(ctx, scope, target, include_previous=include_previous))

    # --- knowledge operations (T19E over the T19C suites) ------------------------------------------

    def _knowledge(self) -> Any:
        if self.knowledge is None:
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                "knowledge operations are not configured on this deployment")
        return self.knowledge

    def run_knowledge_operation(self, ctx: RequestContext, operation: str, args: Mapping[str, Any]) -> ToolResult:
        """Run one T19C operation inline and return the bounded typed result the agent Tool returns.

        ``status`` is ``OK`` for a complete scan and ``PARTIAL`` when the inline bound or the
        request deadline stopped it (never ``OK``). Reads need ``chat.ask``; the scope rule is the
        T19C one (a graph outside a non-empty ``ctx.corpus_scope`` is ``DENIED``).
        """
        return cast(ToolResult, self._knowledge().result(ctx, operation, args))

    def graph_statistics(self, ctx: RequestContext, graph_id: str) -> ToolResult:
        """``kg_compute_statistics`` of one graph."""
        return self.run_knowledge_operation(ctx, "kg_compute_statistics", {"graph_id": graph_id})

    def compare_graphs(self, ctx: RequestContext, graph_ids: Sequence[str]) -> ToolResult:
        """``kg_compare_snapshots`` over 2-12 graphs in the given order."""
        return self.run_knowledge_operation(ctx, "kg_compare_snapshots", {"graph_ids": list(graph_ids)})

    def paper_subgraph(self, ctx: RequestContext, graph_id: str, paper: str, *, max_nodes: int | None = None,
                       max_edges: int | None = None) -> ToolResult:
        """``kg_paper_subgraph``: what one graph holds about one Work."""
        args: dict[str, Any] = {"graph_id": graph_id, "paper": paper}
        if max_nodes is not None:
            args["max_nodes"] = max_nodes
        if max_edges is not None:
            args["max_edges"] = max_edges
        return self.run_knowledge_operation(ctx, "kg_paper_subgraph", args)

    def recent_entities(self, ctx: RequestContext, graph_id: str, *, since: str | None = None,
                        limit: int | None = None) -> ToolResult:
        """``kg_recent_entities`` of a live (Tiled) graph; JSON snapshots are ``UNSUPPORTED_CAPABILITY``."""
        args: dict[str, Any] = {"graph_id": graph_id}
        if since:
            args["since"] = since
        if limit is not None:
            args["limit"] = limit
        return self.run_knowledge_operation(ctx, "kg_recent_entities", args)

    def audit_corpus(self, ctx: RequestContext, corpus: str, *, sections: Sequence[str] | None = None
                     ) -> ToolResult:
        """``curate_audit_corpus`` of one configured extracted-terms corpus."""
        args: dict[str, Any] = {"corpus": corpus}
        if sections:
            args["sections"] = list(sections)
        return self.run_knowledge_operation(ctx, "curate_audit_corpus", args)

    def submit_knowledge_operation(self, ctx: RequestContext, operation: str, args: Mapping[str, Any], *,
                                   corpus_scope: Sequence[str] | None = None) -> JobHandle:
        """Submit one operation as a ``knowledge_operation`` job (``ops.run``; whole population, no inline bound).

        The arguments are schema-checked first. ``corpus_scope`` defaults to the graph ids /
        corpus the arguments name (``knowledge.job_scope``); ``JobService`` checks it against
        the caller's scope. Returns the ``JobHandle``; read the bounded report with
        :meth:`knowledge_job_result` once the job succeeded.
        """
        from ..modules.operations.knowledge import OPERATION_JOB_KIND, job_params, job_scope

        knowledge = self._knowledge()
        knowledge.validate(operation, args)
        if not self.jobs.has_kind(OPERATION_JOB_KIND):
            raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY,
                                "knowledge operation jobs are not registered on this deployment")
        scope = tuple(corpus_scope) if corpus_scope is not None else job_scope(args)
        inputs = OperationInputs(corpus_scope=scope, params=job_params(operation, dict(args)))
        return self.jobs.submit(ctx, OPERATION_JOB_KIND, inputs)

    def knowledge_job_result(self, ctx: RequestContext, job_id: str) -> dict[str, Any]:
        """The handle of a ``knowledge_operation`` job and, once computed, its bounded report.

        ``result`` is the stage's bounded ``OperationReport`` (``coverage.completeness`` says
        whether the whole population was read) or ``None`` while the job has not completed;
        ``artifact_refs`` names the private full report when an artifact store is configured.
        """
        from ..modules.operations.knowledge import OPERATION_JOB_KIND

        handle = self.jobs.get(ctx, job_id)
        if handle.kind != OPERATION_JOB_KIND:
            raise ContractError(ErrorCode.INVALID_INPUT, f"job {job_id} is not a {OPERATION_JOB_KIND} job")
        stage = self.jobs.store.stage(job_id, "compute")
        result = dict(stage.result) if stage is not None and stage.result else None
        refs = list(stage.artifact_refs) if stage is not None else []
        return {"job": _json(handle), "result": result, "artifact_refs": refs}

    def register_knowledge_jobs(self, context_for_job: Callable[[Any], RequestContext], *,
                                artifact_store: Any = None, **kwargs: Any) -> Any:
        """Register the ``knowledge_operation`` job kind over this deployment's operations (trusted wiring)."""
        from ..modules.operations.reports import register_operation_jobs

        knowledge = self._knowledge()
        return register_operation_jobs(self.jobs, knowledge.dispatch, context_for_job,
                                       artifact_store=artifact_store if artifact_store is not None
                                       else knowledge.artifact_store, **kwargs)

    # --- status ----------------------------------------------------------------------------------

    def audit_status(self, ctx: RequestContext) -> dict[str, Any]:
        """Audit-path health; ``degraded`` is true whenever critical mutations would be refused."""
        require(ctx, "jobs.admin")
        if self.audit is None:
            return {"configured": False, "healthy": False, "degraded": True, "reason": "audit is not configured",
                    "spilled": 0, "dropped": 0, "pending_projection": 0, "last_seq": -1}
        status = self.audit.status()
        return {"configured": True, "healthy": status.healthy, "degraded": not status.healthy,
                "reason": status.reason, "spilled": status.spilled, "dropped": status.dropped,
                "pending_projection": status.pending_projection, "last_seq": status.last_seq}

    def ops_status(self, ctx: RequestContext) -> dict[str, Any]:
        """Ingestion-monitor DTO (O-07): visible jobs by state, review queue, audit, publication."""
        require(ctx, "jobs.admin")
        by_state: dict[str, int] = {}
        for handle in self.list_jobs(ctx, limit=1000):
            by_state[handle.state.value] = by_state.get(handle.state.value, 0) + 1
        review = None
        if self.curation is not None and ctx.has("curation.approve"):
            review = self.curation_summary(ctx)
        return {
            "jobs_by_state": by_state,
            "kinds": sorted(self.jobs._kinds),  # noqa: SLF001 - registered kind names only
            "review_queue": review,
            "audit": self.audit_status(ctx),
            "publication": {"configured": self.publication is not None,
                            "rehearsal_targets": len(self.gate.rehearsal_targets)},
        }


def build_operations(settings: DeploymentSettings, root: str | Path, *,
                     identity_port: TiledIdentityPort | None = None, epoch: Callable[[], float] = time.time,
                     clock: Clock = _utcnow, owner: str = "api",
                     publication_gate: PublicationGate | None = None,
                     credential_lookup: Callable[[str], Any] | None = None,
                     knowledge: Any = None) -> F2WOperations:
    """Build the default operations over SQLite stores under ``root`` (created if missing).

    Registers **no** job kind and no publication: real pipeline handlers need H3 and real
    publication needs H5. ``knowledge`` (T19E) binds the Knowledge Operations; the
    ``knowledge_operation`` kind is registered by ``F2WOperations.register_knowledge_jobs``.
    Trusted wiring registers kinds on ``ops.jobs`` (and builds a
    :class:`~app.modules.jobs.publication.Publication` on it) when those gates allow. Every
    kind registered there gets the service's default
    :class:`~app.modules.security.authorization.JobAuthority` (``settings``, ``identity_port``,
    ``credential_lookup``), so its stages re-authorize the initiating subject (AUD-01).

    Raises:
        StartupConfigError: ALS settings without ``identity_port`` or ``credential_lookup``.
    """
    from ..modules.curation.store import CurationStore
    from ..modules.jobs.store import JobStore
    from ..modules.provenance.recorder import AuditLog, AuditRecorder

    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    jobs = JobService(JobStore(base / "jobs.sqlite3", clock=epoch), owner=owner, settings=settings,
                      identity_port=identity_port, credential_lookup=credential_lookup, clock=clock)
    curation = CurationStore(base / "curation.sqlite3", clock=epoch)
    audit = AuditRecorder(AuditLog(base / "audit.sqlite3", clock=epoch))
    return F2WOperations(settings, jobs, curation=curation, audit=audit, identity_port=identity_port,
                         publication_gate=publication_gate, clock=clock, knowledge=knowledge)


def job_event_json(event: JobEvent) -> dict[str, Any]:
    """JSON payload of one job event (SSE ``data``)."""
    return event.model_dump(mode="json")


__all__ = ("ALS_APPROVAL_TTL", "DEFAULT_APPROVAL_TTL", "PROFILE_APPROVAL_TTL", "F2WOperations", "PublicationGate",
           "build_operations", "default_approval_ttl", "job_event_json")
