"""The trusted interface through which an authorized reviewer resolves cases (U-05, U-11).

Only a human context (interactive or CLI) holding ``curation.approve`` and the case's corpus
scope can resolve, accept or reject. A model, worker or tool call has no path to this function.
The decision binds to the case ``revision`` the reviewer saw: if the evidence, schema or
vocabulary changed since, the decision is refused as **stale** and a new review is required. An
accepted resolution returns the candidate to validation (``needs_validation``); it never points
publication at an uncertainty queue.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts.context import LOCAL_OPERATOR, ActorKind, RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..provenance.recorder import AuditRecorder
from .review_schema import ReviewAnswer, ReviewParseError, validate_resolution
from .store import CurationStore


def _scope(ctx: RequestContext, store: CurationStore, case_id: str) -> set:
    if ctx.principal.subject == LOCAL_OPERATOR and ctx.mode.value == "local_trusted":
        row = store.db.one("SELECT corpus FROM uncertainty_cases WHERE id=?", (case_id,))
        return {row[0]} if row else set()
    return set(ctx.corpus_scope)


def review_case(
    ctx: RequestContext,
    store: CurationStore,
    case_id: str,
    *,
    bound_revision: str,
    accept: bool,
    resolution: Optional[Dict[str, Any]] = None,
    reason: str = "",
    recorder: Optional[AuditRecorder] = None,
) -> Dict[str, Any]:
    """Resolve (``accept=True``) or reject a case awaiting review.

    Args:
        ctx: Request context; the actor must be a human with ``curation.approve``.
        store: Curation store holding the case.
        case_id: Case to review.
        bound_revision: The case ``revision`` the reviewer was shown.
        accept: True to resolve the case, False to reject it.
        resolution: The verdict to apply (a ``ReviewAnswer``-shaped mapping). Omitted when accepting, the
            attached validated model proposal is used; a case without one cannot be accepted blindly.
        reason: Free-text reason recorded with the decision.
        recorder: Audit recorder; the decision is a critical mutation and is write-ahead audited.

    Raises:
        ContractError: ``DENIED``, ``NOT_FOUND``, ``STALE_APPROVAL`` (revision moved), ``CONFLICT`` (not awaiting
            review), ``INVALID_INPUT`` (verdict fails validation).
    """
    if ctx.actor_kind not in (ActorKind.INTERACTIVE, ActorKind.CLI):
        raise ContractError(ErrorCode.DENIED, "case review is done by a human, not a worker, delegate or model")
    if not ctx.has("curation.approve"):
        raise ContractError(ErrorCode.DENIED, "curation.approve is required")
    scope = _scope(ctx, store, case_id)
    case = store.get_case(case_id, scope)
    if case["revision"] != bound_revision:
        raise ContractError(
            ErrorCode.STALE_APPROVAL, "the candidate, schema or vocabulary changed since this was reviewed"
        )
    verdict_data: Optional[Dict[str, Any]] = None
    if accept:
        verdict_data = resolution
        if verdict_data is None:
            attempt_id = case.get("proposal_attempt_id")
            proposal = next((a for a in store.attempts(case_id) if a["id"] == attempt_id), None)
            if proposal is None:
                raise ContractError(
                    ErrorCode.INVALID_INPUT, "no validated proposal to accept; supply a resolution"
                )
            verdict_data = proposal["response"]
        try:
            answer = ReviewAnswer.model_validate(verdict_data)
        except ValueError as exc:
            raise ContractError(ErrorCode.INVALID_INPUT, "resolution does not match the review schema") from exc
        checked = validate_resolution(case, answer)
        if not checked["accepted"]:
            raise ContractError(
                ErrorCode.INVALID_INPUT, "resolution fails validation: " + ",".join(checked["errors"])
            )

    def apply() -> Dict[str, Any]:
        return store.apply_decision(
            case_id,
            scope,
            bound_revision=bound_revision,
            accept=accept,
            resolution=verdict_data,
            reviewer=ctx.principal.subject,
            reason=reason,
        )

    if recorder is None:
        return apply()
    return recorder.guard(
        kind="case_review",
        operation_id=f"review:{case_id}:{bound_revision[:12]}",
        actor=ctx.principal.subject,
        target=case["corpus"],
        payload={"case_id": case_id, "accepted": accept, "revision": bound_revision},
        run_id=ctx.run_id,
        mutation=apply,
    )


__all__ = ("ReviewParseError", "review_case")
